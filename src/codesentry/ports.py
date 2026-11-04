"""The seams that let the same orchestrator run on Google Cloud and on a laptop with no network."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from codesentry.models import (
    PullRequestRef,
    PullSnapshot,
    SandboxBundle,
    SandboxReport,
    StageMessage,
    Task,
    TaskEvent,
)


@dataclass(frozen=True)
class LlmRequest[T: BaseModel]:
    agent: str
    key: str  # replay file name, e.g. "triage-2"
    model: str
    system: str
    prompt: str
    schema: type[T]


@dataclass(frozen=True)
class LlmResponse[T: BaseModel]:
    parsed: T
    model: str
    input_tokens: int
    output_tokens: int


class Llm(Protocol):
    async def generate[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]: ...


class Store(Protocol):
    async def create(self, task: Task) -> bool: ...
    async def get(self, task_id: str) -> Task | None: ...
    async def save(self, task: Task) -> None: ...
    async def claim(self, task_id: str, key: str, lease_s: float) -> bool: ...
    async def release(self, task_id: str, key: str) -> None: ...
    async def append_event(self, event: TaskEvent) -> TaskEvent: ...
    async def events_after(self, task_id: str, seq: int) -> list[TaskEvent]: ...


class Bus(Protocol):
    async def publish(self, message: StageMessage) -> None: ...


class Sandbox(Protocol):
    async def execute(self, task_id: str, iteration: int, bundle: SandboxBundle) -> SandboxReport | None:
        """Return the report, or None when it will arrive later through Swarm.complete_run."""
        ...


class CodeHost(Protocol):
    async def snapshot(self, pull_request: PullRequestRef) -> PullSnapshot: ...
    async def comment(self, pull_request: PullRequestRef, body: str) -> None: ...
