"""The orchestrator: a small state machine driven by bus messages. Each stage is one model call or one sandbox run."""

from __future__ import annotations

import difflib
import uuid
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from codesentry.config import Settings
from codesentry.guardrails import TerminalError, add_usage, check_edits, ensure_budget, gate
from codesentry.models import (
    FinalReport,
    Iteration,
    PullRequestRef,
    PullSnapshot,
    SandboxBundle,
    SandboxReport,
    Stage,
    StageMessage,
    Task,
    TaskEvent,
    TaskStatus,
    Triage,
    Verdict,
    utcnow,
)
from codesentry.ports import Bus, CodeHost, Llm, LlmRequest, LlmResponse, Sandbox, Store
from codesentry.prompts import JUDGE_SYSTEM, TRIAGE_SYSTEM, judge_prompt, render_comment, triage_prompt
from codesentry.telemetry import context_from, current_trace_id, current_traceparent

TEST_PATH = "tests/test_codesentry_generated.py"
NAMESPACE = uuid.UUID("7f1c6a52-3a0e-4f5e-9a55-2f3c6e1b7d10")
CLAIM_LEASE_S = 900


def task_id_for(pull_request: PullRequestRef) -> str:
    """Same commit, same task: a redelivered webhook lands on the task that already exists."""
    return uuid.uuid5(NAMESPACE, f"{pull_request.repo}#{pull_request.number}@{pull_request.head_sha}").hex


class Swarm:
    def __init__(
        self,
        *,
        settings: Settings,
        store: Store,
        bus: Bus,
        llm: Llm,
        sandbox: Sandbox,
        code_host: CodeHost,
        tracer: trace.Tracer,
    ) -> None:
        self.settings = settings
        self.store = store
        self.bus = bus
        self.llm = llm
        self.sandbox = sandbox
        self.code_host = code_host
        self.tracer = tracer

    async def submit(self, pull_request: PullRequestRef) -> tuple[Task, bool]:
        """Create the task and enqueue the first stage. Returns the task and whether it is new."""
        task = Task(id=task_id_for(pull_request), pull_request=pull_request)
        with self.tracer.start_as_current_span("task.submit", kind=SpanKind.PRODUCER) as span:
            span.set_attribute("codesentry.task_id", task.id)
            task.trace_id = current_trace_id()
            if not await self.store.create(task):
                existing = await self.store.get(task.id)
                return existing or task, False
            await self._event(task, "queued", f"{pull_request.repo}#{pull_request.number} accepted")
            await self.bus.publish(
                StageMessage(
                    task_id=task.id, stage=Stage.TRIAGE, iteration=1, traceparent=current_traceparent()
                )
            )
        return task, True

    async def handle(self, message: StageMessage) -> None:
        with self.tracer.start_as_current_span(
            f"agent.{message.stage.value}",
            context=context_from(message.traceparent),
            kind=SpanKind.CONSUMER,
            attributes={"codesentry.task_id": message.task_id, "codesentry.iteration": message.iteration},
        ) as span:
            task = await self.store.get(message.task_id)
            if task is None or task.status.terminal:
                return
            claim = f"{message.stage.value}-{message.iteration}"
            if not await self.store.claim(task.id, claim, CLAIM_LEASE_S):
                span.set_attribute("codesentry.duplicate", True)
                return
            task.status, task.stage = TaskStatus.RUNNING, message.stage
            try:
                match message.stage:
                    case Stage.TRIAGE:
                        await self._triage(task, message.iteration)
                    case Stage.RUN:
                        await self._run(task, message.iteration)
                    case Stage.JUDGE:
                        await self._judge(task, message.iteration)
            except TerminalError as error:
                span.set_status(Status(StatusCode.ERROR, str(error)))
                await self._escalate(task, str(error))
            except Exception:
                await self.store.release(task.id, claim)  # let the bus retry, then dead-letter
                raise

    async def complete_run(self, task_id: str, iteration: int, report: SandboxReport) -> None:
        """Called when a sandbox report arrives, inline or later through Eventarc."""
        task = await self.store.get(task_id)
        if task is None or task.status.terminal:
            return
        if not await self.store.claim(task.id, f"complete-{iteration}", CLAIM_LEASE_S):
            return
        current = task.iteration(iteration)
        # Eventarc delivers this outside any trace, so the context saved before the sandbox started brings it back.
        with self.tracer.start_as_current_span(
            "sandbox.result",
            context=context_from(current.traceparent),
            kind=SpanKind.CONSUMER,
            attributes={"codesentry.task_id": task.id, "codesentry.iteration": iteration},
        ) as span:
            current.report = report
            current.gate_passed, current.gate_reason = gate(report)
            span.set_attribute("codesentry.red_exit_code", report.red.exit_code)
            span.set_attribute("codesentry.gate_passed", current.gate_passed)
            await self._event(task, "sandbox", f"before the fix: exit {report.red.exit_code}", iteration)
            if report.green:
                span.set_attribute("codesentry.green_exit_code", report.green.exit_code)
                await self._event(task, "sandbox", f"with the fix: exit {report.green.exit_code}", iteration)
            await self._next(task, Stage.JUDGE, iteration)

    # ── Stages ──────────────────────────────────────────────────────────────────────────────────────────────

    async def _triage(self, task: Task, number: int) -> None:
        snapshot = await self.code_host.snapshot(task.pull_request)
        previous = task.iteration(number - 1) if number > 1 else None
        prompt = triage_prompt(task.pull_request, snapshot, previous)
        response = await self._ask(
            task,
            LlmRequest(
                agent="triage",
                key=f"triage-{number}",
                model=self.settings.triage_model,
                system=TRIAGE_SYSTEM,
                prompt=prompt,
                schema=Triage,
            ),
        )
        current = task.iteration(number)
        current.triage = response.parsed
        await self._event(task, "hypothesis", response.parsed.hypothesis, number)
        await self._next(task, Stage.RUN, number)

    async def _run(self, task: Task, number: int) -> None:
        current = task.iteration(number)
        triage = _required(current.triage, "triage")
        snapshot = await self.code_host.snapshot(task.pull_request)

        current.policy_violation = check_edits(triage.edits, snapshot.files)
        if current.policy_violation:
            current.gate_passed, current.gate_reason = False, f"policy: {current.policy_violation}"
            await self._event(task, "policy", current.gate_reason, number)
            await self._next(task, Stage.JUDGE, number)
            return

        current.patch_diff = _diff(snapshot, triage)
        with self.tracer.start_as_current_span("sandbox.execute", kind=SpanKind.CLIENT):
            current.traceparent = current_traceparent()
            await self.store.save(task)
            bundle = SandboxBundle(
                files=snapshot.files,
                test_path=TEST_PATH,
                test_code=triage.test_code,
                edits=triage.edits,
                traceparent=current.traceparent,
            )
            report = await self.sandbox.execute(task.id, number, bundle)
        if report:
            await self.complete_run(task.id, number, report)

    async def _judge(self, task: Task, number: int) -> None:
        current = task.iteration(number)
        if current.policy_violation:
            current.verdict = Verdict(
                approved=False, summary=current.gate_reason, risk="high", feedback=current.gate_reason
            )
        else:
            response = await self._ask(
                task,
                LlmRequest(
                    agent="judge",
                    key=f"judge-{number}",
                    model=self.settings.judge_model,
                    system=JUDGE_SYSTEM,
                    prompt=judge_prompt(current),
                    schema=Verdict,
                ),
            )
            current.verdict = response.parsed

        verdict = _required(current.verdict, "verdict")
        if verdict.approved and current.gate_passed:
            await self._finish(task, current)
            return
        reason = verdict.feedback or current.gate_reason
        await self._event(task, "rejected", reason, number)
        if number >= self.settings.max_iterations:
            await self._escalate(task, f"no passing fix after {number} iterations: {reason}")
            return
        await self._next(task, Stage.TRIAGE, number + 1)

    # ── Helpers ─────────────────────────────────────────────────────────────────────────────────────────────

    async def _ask[T: Any](self, task: Task, request: LlmRequest[T]) -> LlmResponse[T]:
        ensure_budget(task, self.settings.max_tokens_per_task, request.system + request.prompt)
        with self.tracer.start_as_current_span(f"chat {request.model}", kind=SpanKind.CLIENT) as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.provider.name", "gcp.vertex_ai")
            span.set_attribute("gen_ai.request.model", request.model)
            response = await self.llm.generate(request)
            span.set_attribute("gen_ai.response.model", response.model)
            span.set_attribute("gen_ai.usage.input_tokens", response.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", response.output_tokens)
        task.usage = add_usage(task.usage, response)
        return response

    async def _next(self, task: Task, stage: Stage, iteration: int) -> None:
        task.stage = stage
        await self.store.save(task)
        await self.bus.publish(
            StageMessage(task_id=task.id, stage=stage, iteration=iteration, traceparent=current_traceparent())
        )

    async def _finish(self, task: Task, current: Iteration) -> None:
        task.status = TaskStatus.COMPLETED
        report = self._report(task, current, "fix_proposed")
        await self.code_host.comment(task.pull_request, render_comment(report, current.patch_diff))
        await self.store.save(task)
        await self._event(
            task, "completed", f"fix proposed after {current.number} iteration(s)", current.number
        )

    async def _escalate(self, task: Task, reason: str) -> None:
        task.status = TaskStatus.NEEDS_HUMAN
        task.escalation_reason = reason
        current = task.iterations[-1] if task.iterations else Iteration(number=1)
        report = self._report(task, current, "needs_human")
        await self.code_host.comment(task.pull_request, render_comment(report, ""))
        await self.store.save(task)
        await self._event(task, "needs_human", reason, current.number)

    def _report(self, task: Task, current: Iteration, outcome: Any) -> FinalReport:
        green = current.report.green if current.report else None
        verdict = current.verdict
        summary = task.escalation_reason or (verdict.summary if verdict else "")
        return FinalReport(
            task_id=task.id,
            repo=task.pull_request.repo,
            pull_request=task.pull_request.number,
            head_sha=task.pull_request.head_sha,
            outcome=outcome,
            iterations=current.number,
            hypothesis=current.triage.hypothesis if current.triage else None,
            summary=summary,
            risk=verdict.risk if verdict else None,
            exit_code=green.exit_code if green else None,
            tests_run=green.tests_run if green else 0,
            input_tokens=task.usage.input_tokens,
            output_tokens=task.usage.output_tokens,
            cost_usd=round(task.usage.cost_usd, 6),
            trace_id=task.trace_id,
        )

    async def _event(self, task: Task, kind: str, message: str, iteration: int | None = None) -> None:
        await self.store.append_event(
            TaskEvent(task_id=task.id, kind=kind, message=message, iteration=iteration)
        )
        task.updated_at = utcnow()


def _required[T](value: T | None, name: str) -> T:
    if value is None:
        raise RuntimeError(
            f"stage ran before {name} was stored"
        )  # a bus retry reorders nothing we can repair here
    return value


def _diff(snapshot: PullSnapshot, triage: Triage) -> str:
    chunks = []
    for edit in triage.edits:
        chunks.extend(
            difflib.unified_diff(
                snapshot.files[edit.path].splitlines(keepends=True),
                edit.content.splitlines(keepends=True),
                f"a/{edit.path}",
                f"b/{edit.path}",
            )
        )
    return "".join(chunks)
