from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from codesentry.guardrails import (
    BudgetExceeded,
    CircuitBreaker,
    GuardedLlm,
    LlmContractError,
    LlmUnavailable,
    check_edits,
    cost_usd,
    ensure_budget,
    gate,
)
from codesentry.models import FileEdit, PullRequestRef, SandboxReport, SandboxStep, Task, Usage
from codesentry.ports import LlmRequest, LlmResponse

FAILED, PASSED = SandboxStep(exit_code=1), SandboxStep(exit_code=0, tests_run=4)


@pytest.mark.parametrize(
    ("report", "passed"),
    [
        (SandboxReport(red=FAILED, green=PASSED), True),
        (SandboxReport(red=PASSED), False),  # the bug wasn't reproduced
        (SandboxReport(red=SandboxStep(exit_code=2)), False),  # collection error, not a failing test
        (SandboxReport(red=FAILED, green=FAILED), False),
        (SandboxReport(red=FAILED), False),
        (
            SandboxReport(red=FAILED, green=SandboxStep(exit_code=0, tests_run=0)),
            False,
        ),  # os._exit(0) from a test
        (SandboxReport(red=SandboxStep(exit_code=124, timed_out=True)), False),
    ],
)
def test_gate_is_decided_by_exit_codes(report, passed):
    assert gate(report)[0] is passed


KNOWN = {"pricing.py": "", "tests/test_pricing.py": "", ".github/workflows/ci.yml": "", "conftest.py": ""}


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("pricing.py", True),
        ("../pricing.py", False),
        ("/etc/passwd.py", False),
        (".github/workflows/ci.yml", False),
        ("tests/test_pricing.py", False),
        ("conftest.py", False),
        ("new_module.py", False),
    ],
)
def test_fixes_only_touch_existing_source(path, allowed):
    assert (check_edits([FileEdit(path=path, content="x")], KNOWN) is None) is allowed


def test_a_fix_with_no_edits_is_a_violation():
    assert check_edits([], KNOWN) is not None


def _task(tokens: int) -> Task:
    pull_request = PullRequestRef(repo="a/b", number=1, head_sha="h", base_sha="b")
    return Task(id="t", pull_request=pull_request, usage=Usage(input_tokens=tokens))


def test_budget_stops_before_the_call_that_would_exceed_it():
    ensure_budget(_task(90), 100, "x" * 16)
    with pytest.raises(BudgetExceeded):
        ensure_budget(_task(99), 100, "x" * 16)


def test_cost_uses_the_price_table_and_is_pessimistic_for_unknown_models():
    assert cost_usd("gemini-3.5-flash-lite", 1_000_000, 1_000_000) == pytest.approx(2.80)
    assert cost_usd("mystery", 1_000_000, 0) == pytest.approx(1.50)


def test_breaker_opens_then_half_opens_after_cooldown():
    now = [0.0]
    breaker = CircuitBreaker(2, 10, clock=lambda: now[0])
    breaker.failure()
    assert not breaker.is_open
    breaker.failure()
    assert breaker.is_open
    now[0] = 11
    assert not breaker.is_open
    breaker.failure()  # one failure in half-open reopens it
    assert breaker.is_open


class Output(BaseModel):
    value: int


REQUEST = LlmRequest(agent="a", key="k", model="big", system="", prompt="", schema=Output)


class Script:
    def __init__(self, *steps):
        self.steps = list(steps)
        self.models = []

    async def generate(self, request):
        self.models.append(request.model)
        step = self.steps.pop(0)
        if step == "hang":
            await asyncio.sleep(10)
        if isinstance(step, Exception):
            raise step
        return LlmResponse(
            parsed=request.schema.model_validate(step), model=request.model, input_tokens=1, output_tokens=1
        )


def guarded(script, threshold=3):
    return GuardedLlm(script, CircuitBreaker(threshold, 60), timeout_s=0.01, fallback_model="light")


async def test_an_unavailable_model_falls_back_to_the_light_one():
    script = Script(LlmUnavailable("429"), {"value": 1})
    response = await guarded(script).generate(REQUEST)
    assert response.model == "light"
    assert script.models == ["big", "light"]


async def test_a_hard_timeout_counts_as_unavailable():
    script = Script("hang", {"value": 1})
    assert (await guarded(script).generate(REQUEST)).model == "light"


async def test_an_open_breaker_skips_the_primary_model():
    script = Script(LlmUnavailable("x"), {"value": 1}, {"value": 2})
    llm = guarded(script, threshold=1)
    await llm.generate(REQUEST)
    await llm.generate(REQUEST)
    assert script.models == ["big", "light", "light"]


async def test_schema_violations_retry_once_then_stop():
    assert (await guarded(Script({"value": "nope"}, {"value": 1})).generate(REQUEST)).parsed.value == 1
    with pytest.raises(LlmContractError):
        await guarded(Script({"value": "nope"}, {"wrong": 1})).generate(REQUEST)
