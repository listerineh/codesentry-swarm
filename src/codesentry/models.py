"""Typed contracts between agents. Everything crossing a boundary is validated by one of these models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class Stage(StrEnum):
    TRIAGE = "triage"
    RUN = "run"
    JUDGE = "judge"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    NEEDS_HUMAN = "needs_human"

    @property
    def terminal(self) -> bool:
        return self in (TaskStatus.COMPLETED, TaskStatus.NEEDS_HUMAN)


class PullRequestRef(BaseModel):
    repo: str
    number: int
    head_sha: str
    base_sha: str
    title: str = ""


class PullSnapshot(BaseModel):
    """What the host returned for a pull request. Its contents are untrusted input."""

    diff: str
    files: dict[str, str]


# ── Model outputs: sent to Gemini as response_schema and validated again on the way back ────────────────────


class FileEdit(BaseModel):
    path: str = Field(description="Repository-relative path of an existing source file.")
    content: str = Field(description="Full new content of the file.")


class Triage(BaseModel):
    hypothesis: str = Field(description="One sentence on what is wrong and why.")
    test_code: str = Field(description="A complete pytest module that fails while the bug exists.")
    edits: list[FileEdit] = Field(description="Full-file edits that fix the bug. Never edit tests.")


class Verdict(BaseModel):
    approved: bool
    summary: str = Field(description="Two sentences for the pull request comment.")
    risk: Literal["low", "medium", "high"]
    feedback: str | None = Field(description="What to change in the next attempt. Null when approved.")


# ── Sandbox ─────────────────────────────────────────────────────────────────────────────────────────────────


class SandboxBundle(BaseModel):
    """Everything the sandbox receives: code and tests, never a credential."""

    files: dict[str, str]
    test_path: str
    test_code: str
    edits: list[FileEdit]
    traceparent: str | None = None


class SandboxStep(BaseModel):
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    tests_run: int = 0
    duration_ms: int = 0
    timed_out: bool = False


class SandboxReport(BaseModel):
    """red: the new test against the pull request. green: the whole suite with the fix applied."""

    red: SandboxStep
    green: SandboxStep | None = None


# ── Task state ──────────────────────────────────────────────────────────────────────────────────────────────


class Iteration(BaseModel):
    number: int
    triage: Triage | None = None
    patch_diff: str = ""
    traceparent: str | None = None  # where the sandbox's result rejoins the trace
    policy_violation: str | None = None
    report: SandboxReport | None = None
    gate_passed: bool = False
    gate_reason: str = ""
    verdict: Verdict | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Task(BaseModel):
    id: str
    pull_request: PullRequestRef
    status: TaskStatus = TaskStatus.QUEUED
    stage: Stage = Stage.TRIAGE
    iterations: list[Iteration] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    escalation_reason: str | None = None
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def iteration(self, number: int) -> Iteration:
        for item in self.iterations:
            if item.number == number:
                return item
        item = Iteration(number=number)
        self.iterations.append(item)
        return item


class TaskEvent(BaseModel):
    seq: int = 0
    task_id: str
    kind: str
    message: str
    iteration: int | None = None
    at: datetime = Field(default_factory=utcnow)


class StageMessage(BaseModel):
    """One unit of work on the bus. Delivery is at-least-once, so handlers dedupe on (task, stage, iteration)."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    stage: Stage
    iteration: int = Field(ge=1)
    traceparent: str | None = None


class FinalReport(BaseModel):
    """The typed JSON commented on the pull request."""

    task_id: str
    repo: str
    pull_request: int
    head_sha: str
    outcome: Literal["fix_proposed", "needs_human"]
    iterations: int
    hypothesis: str | None
    summary: str
    risk: Literal["low", "medium", "high"] | None
    exit_code: int | None
    tests_run: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    trace_id: str | None
