"""Hard limits around the probabilistic parts. None of these decisions are left to a model."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import PurePosixPath

from pydantic import BaseModel, ValidationError

from codesentry.models import FileEdit, SandboxReport, Task, Usage
from codesentry.ports import Llm, LlmRequest, LlmResponse

# USD per 1M tokens (input, output), global endpoint, read on 2026-11-03 from cloud.google.com/vertex-ai/generative-ai/pricing.
# gemini-3.8-flash is introductory pricing through 2026-12-31; from 2027-01-01 it is (1.50, 7.50).
PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
}
UNKNOWN_PRICE = max(PRICES.values())


class TerminalError(Exception):
    """Retrying won't help: stop and hand the task to a human."""


class BudgetExceeded(TerminalError):
    pass


class LlmContractError(TerminalError):
    """The model kept answering outside its contract."""


class LlmUnavailable(Exception):
    """Transient: timeouts, 429s, 5xx."""


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    input_price, output_price = PRICES.get(model, UNKNOWN_PRICE)
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def ensure_budget(task: Task, limit: int, prompt: str) -> None:
    projected = task.usage.tokens + estimate_tokens(prompt)
    if projected > limit:
        raise BudgetExceeded(f"token budget exhausted: {projected:,} projected of {limit:,}")


def add_usage(usage: Usage, response: LlmResponse) -> Usage:
    return Usage(
        input_tokens=usage.input_tokens + response.input_tokens,
        output_tokens=usage.output_tokens + response.output_tokens,
        cost_usd=usage.cost_usd + cost_usd(response.model, response.input_tokens, response.output_tokens),
    )


class CircuitBreaker:
    def __init__(
        self, threshold: int, cooldown_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if self._clock() - self._opened_at >= self.cooldown_s:
            self._opened_at = None  # half-open: the next call decides
            self._failures = self.threshold - 1
            return False
        return True

    def success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = self._clock()


class GuardedLlm:
    """Hard timeout, breaker, fallback to the light model, and one retry when the output breaks its contract."""

    def __init__(self, inner: Llm, breaker: CircuitBreaker, timeout_s: float, fallback_model: str) -> None:
        self._inner = inner
        self._breaker = breaker
        self._timeout_s = timeout_s
        self._fallback_model = fallback_model

    async def generate[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        if self._breaker.is_open:
            return await self._call(replace(request, model=self._fallback_model))
        try:
            response = await self._call(request)
        except LlmUnavailable:
            self._breaker.failure()
            return await self._call(replace(request, model=self._fallback_model))
        self._breaker.success()
        return response

    async def _call[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        for attempt in (1, 2):
            try:
                async with asyncio.timeout(self._timeout_s):
                    return await self._inner.generate(request)
            except TimeoutError as error:
                raise LlmUnavailable(f"{request.agent} timed out after {self._timeout_s}s") from error
            except ValidationError as error:
                if attempt == 2:
                    raise LlmContractError(f"{request.agent} broke its schema twice: {error}") from error
        raise AssertionError("unreachable")


def check_edits(edits: list[FileEdit], known_files: dict[str, str]) -> str | None:
    """A fix may only change existing source files. Returns the violation, if any."""
    if not edits:
        return "the fix edits no files"
    for edit in edits:
        path = PurePosixPath(edit.path)
        if path.is_absolute() or ".." in path.parts:
            return f"{edit.path}: path escapes the repository"
        if any(part.startswith(".") for part in path.parts):
            return f"{edit.path}: hidden paths such as CI workflows are off limits"
        if path.suffix != ".py":
            return f"{edit.path}: only Python source may change"
        if path.parts[0] == "tests" or path.name.startswith("test_") or path.name == "conftest.py":
            return f"{edit.path}: a fix can't rewrite the tests that judge it"
        if edit.path not in known_files:
            return f"{edit.path}: not a file in this pull request's tree"
    return None


def gate(report: SandboxReport) -> tuple[bool, str]:
    """The pass/fail call, from exit codes alone: test fails before the fix, whole suite passes after it."""
    red, green = report.red, report.green
    if red.timed_out or (green and green.timed_out):
        return False, "the sandbox hit its hard timeout"
    if red.exit_code == 0:
        return False, "the test passes on the pull request, so the hypothesis wasn't reproduced"
    if red.exit_code != 1:
        return False, f"the test didn't run cleanly (pytest exit {red.exit_code})"
    if green is None:
        return False, "the fix wasn't tested"
    if green.exit_code != 0:
        return False, f"the suite fails with the fix applied (pytest exit {green.exit_code})"
    if green.tests_run == 0:
        return False, "pytest reported success without running a test"
    return True, f"red then green: {green.tests_run} tests pass with the fix"
