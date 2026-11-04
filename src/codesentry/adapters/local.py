"""Offline adapters: memory store, in-process bus with Pub/Sub's delivery rules, fixtures, scripted models and sandboxes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from codesentry.models import (
    PullRequestRef,
    PullSnapshot,
    SandboxBundle,
    SandboxReport,
    SandboxStep,
    StageMessage,
    Task,
    TaskEvent,
)
from codesentry.ports import Llm, LlmRequest, LlmResponse

logger = logging.getLogger(__name__)
SANDBOX_ENTRY = Path(__file__).resolve().parents[1] / "sandbox_entry.py"


class MemoryStore:
    def __init__(self) -> None:
        self.tasks: dict[str, str] = {}
        self.claims: dict[tuple[str, str], float] = {}
        self.events: dict[str, list[TaskEvent]] = {}

    async def create(self, task: Task) -> bool:
        if task.id in self.tasks:
            return False
        await self.save(task)
        return True

    async def get(self, task_id: str) -> Task | None:
        raw = self.tasks.get(task_id)
        return Task.model_validate_json(raw) if raw else None

    async def save(self, task: Task) -> None:
        self.tasks[task.id] = task.model_dump_json()

    async def claim(self, task_id: str, key: str, lease_s: float) -> bool:
        now = time.monotonic()
        if self.claims.get((task_id, key), 0) > now:
            return False
        self.claims[(task_id, key)] = now + lease_s
        return True

    async def release(self, task_id: str, key: str) -> None:
        self.claims.pop((task_id, key), None)

    async def append_event(self, event: TaskEvent) -> TaskEvent:
        log = self.events.setdefault(event.task_id, [])
        stored = event.model_copy(update={"seq": len(log) + 1})
        log.append(stored)
        return stored

    async def events_after(self, task_id: str, seq: int) -> list[TaskEvent]:
        return [event for event in self.events.get(task_id, []) if event.seq > seq]


class MemoryBus:
    """At-least-once delivery, retries with backoff and a dead-letter list, like a Pub/Sub subscription."""

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        backoff_s: float = 0.0,
        duplicate_every: int = 0,
    ) -> None:
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self.duplicate_every = duplicate_every
        self.queue: asyncio.Queue[tuple[StageMessage, int]] = asyncio.Queue()
        self.dead_letters: list[StageMessage] = []
        self.published = 0
        self._worker: asyncio.Task | None = None

    async def publish(self, message: StageMessage) -> None:
        self.published += 1
        await self.queue.put((message, 1))
        if self.duplicate_every and self.published % self.duplicate_every == 0:
            await self.queue.put((message, 1))

    async def drain(self, handler: Callable[[StageMessage], Awaitable[None]]) -> None:
        while not self.queue.empty():
            await self._deliver(handler)

    def start(self, handler: Callable[[StageMessage], Awaitable[None]]) -> None:
        async def loop() -> None:
            while True:
                await self._deliver(handler)

        self._worker = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()

    async def _deliver(self, handler: Callable[[StageMessage], Awaitable[None]]) -> None:
        message, attempt = await self.queue.get()
        try:
            await handler(message)
        except Exception:
            logger.exception("delivery %s failed", attempt)
            if attempt >= self.max_attempts:
                self.dead_letters.append(message)
                return
            await asyncio.sleep(self.backoff_s * 2 ** (attempt - 1))
            await self.queue.put((message, attempt + 1))


class FixtureCodeHost:
    def __init__(self, fixture: Path) -> None:
        self.fixture = fixture
        self.comments: list[str] = []

    async def snapshot(self, pull_request: PullRequestRef) -> PullSnapshot:
        root = self.fixture / "repo"
        files = {
            path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        return PullSnapshot(diff=(self.fixture / "pr.diff").read_text(encoding="utf-8"), files=files)

    async def comment(self, pull_request: PullRequestRef, body: str) -> None:
        self.comments.append(body)


def load_pull_request(fixture: Path) -> PullRequestRef:
    return PullRequestRef.model_validate_json((fixture / "pull_request.json").read_text(encoding="utf-8"))


class ReplayLlm:
    """Answers from files, one per call: demo/replays/<scenario>/<key>.json."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    async def generate[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        raw = json.loads((self.directory / f"{request.key}.json").read_text(encoding="utf-8"))
        return LlmResponse(
            parsed=request.schema.model_validate(raw["output"]),
            model=raw.get("model", request.model),
            input_tokens=raw["usage"]["input_tokens"],
            output_tokens=raw["usage"]["output_tokens"],
        )


class RecordingLlm:
    """Wraps a real model and saves each answer in replay format, so the backup demo can be real responses."""

    def __init__(self, inner: Llm, directory: Path) -> None:
        self.inner = inner
        self.directory = directory

    async def generate[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        response = await self.inner.generate(request)
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": f"recorded from {response.model}",
            "model": response.model,
            "usage": {"input_tokens": response.input_tokens, "output_tokens": response.output_tokens},
            "output": response.parsed.model_dump(mode="json"),
        }
        (self.directory / f"{request.key}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return response


@dataclass(frozen=True)
class _Command:
    argv: list[str]
    env: dict[str, str]


async def _run_entry(command: _Command, bundle: SandboxBundle, deadline_s: float) -> SandboxReport:
    process = await asyncio.create_subprocess_exec(
        *command.argv,
        env=command.env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    payload = bundle.model_dump_json().encode()
    try:
        async with asyncio.timeout(deadline_s):
            stdout, stderr = await process.communicate(payload)
    except TimeoutError:
        process.kill()
        await process.wait()
        return SandboxReport(red=SandboxStep(exit_code=124, stderr="killed: hard timeout", timed_out=True))
    if process.returncode != 0:
        return SandboxReport(red=SandboxStep(exit_code=3, stderr=stderr.decode(errors="replace")[-2_000:]))
    return SandboxReport.model_validate_json(stdout)


class SubprocessSandbox:
    """Fast and offline, with a scrubbed environment and a timeout. NOT a security boundary: trusted fixtures only."""

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s

    async def execute(self, task_id: str, iteration: int, bundle: SandboxBundle) -> SandboxReport:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "TMPDIR": tempfile.gettempdir(),
            "CODESENTRY_STEP_TIMEOUT": str(self.timeout_s / 2),
        }
        command = _Command([sys.executable, "-I", str(SANDBOX_ENTRY)], env)
        return await _run_entry(command, bundle, self.timeout_s)


class DockerSandbox:
    """The local stand-in for Cloud Run: no network, no capabilities, read-only root, hard limits, nothing mounted."""

    def __init__(self, image: str, timeout_s: float, runtime: str | None = None) -> None:
        self.image = image
        self.timeout_s = timeout_s
        self.runtime = runtime

    def argv(self) -> list[str]:
        argv = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec=false,size=64m",  # noqa: S108
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "256m",
            "--cpus",
            "1",
            "--env",
            f"CODESENTRY_STEP_TIMEOUT={self.timeout_s / 2}",
        ]
        if self.runtime:
            argv += ["--runtime", self.runtime]
        return [*argv, self.image]

    async def execute(self, task_id: str, iteration: int, bundle: SandboxBundle) -> SandboxReport:
        command = _Command(
            self.argv(), {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        )
        return await _run_entry(command, bundle, self.timeout_s)
