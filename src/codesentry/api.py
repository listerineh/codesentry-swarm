"""HTTP edge. The webhook answers 202 right away; agents only ever run behind the bus."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from codesentry.adapters.local import MemoryBus
from codesentry.models import PullRequestRef, SandboxReport, StageMessage, Task, TaskEvent
from codesentry.wiring import Runtime

HANDLED_ACTIONS = {"opened", "reopened", "synchronize"}
SSE_POLL_S = 1.0
SSE_MAX_POLLS = 1_800
SPAN_FLUSH_MS = 5_000


class Accepted(BaseModel):
    task_id: str
    status_url: str
    events_url: str
    duplicate: bool


def valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def same_repository(payload: dict) -> bool:
    """A pull request from a fork would spend our budget and run a stranger's code, so only branches of the repo count."""
    head_repo = ((payload.get("pull_request") or {}).get("head") or {}).get("repo") or {}
    base_repo = payload.get("repository") or {}
    return bool(head_repo.get("full_name")) and head_repo["full_name"] == base_repo.get("full_name")


def pull_request_from(payload: dict) -> PullRequestRef:
    pull_request = payload["pull_request"]
    return PullRequestRef(
        repo=payload["repository"]["full_name"],
        number=pull_request["number"],
        head_sha=pull_request["head"]["sha"],
        base_sha=pull_request["base"]["sha"],
        title=pull_request.get("title", ""),
    )


def create_app(runtime: Runtime) -> FastAPI:
    settings, swarm = runtime.settings, runtime.swarm

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        bus = swarm.bus
        if isinstance(bus, MemoryBus):
            bus.start(swarm.handle)
        yield
        if isinstance(bus, MemoryBus):
            await bus.stop()

    app = FastAPI(title="CodeSentry Swarm", lifespan=lifespan)

    async def reader(authorization: Annotated[str | None, Header()] = None) -> None:
        if settings.api_token and not hmac.compare_digest(
            authorization or "", f"Bearer {settings.api_token}"
        ):
            raise HTTPException(401, "invalid token")

    async def internal_caller(authorization: Annotated[str | None, Header()] = None) -> None:
        from codesentry.adapters.gcp import verify_push_token

        if not await verify_push_token(authorization, settings):
            raise HTTPException(401, "invalid caller")

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/github", status_code=202)
    async def github_webhook(
        request: Request,
        response: Response,
        x_hub_signature_256: Annotated[str | None, Header()] = None,
        x_github_event: Annotated[str | None, Header()] = None,
    ) -> Accepted | dict[str, str]:
        body = await request.body()
        if not valid_signature(settings.webhook_secret, body, x_hub_signature_256):
            raise HTTPException(401, "invalid signature")
        if x_github_event != "pull_request":
            return {"status": "ignored"}
        payload = json.loads(body)
        if payload.get("action") not in HANDLED_ACTIONS:
            return {"status": "ignored"}
        if not same_repository(payload):
            return {"status": "ignored"}
        try:
            pull_request = pull_request_from(payload)
        except (KeyError, TypeError, ValidationError) as error:
            raise HTTPException(422, "not a pull request payload") from error
        task, created = await swarm.submit(pull_request)
        response.headers["Location"] = f"/tasks/{task.id}"
        return Accepted(
            task_id=task.id,
            status_url=f"/tasks/{task.id}",
            events_url=f"/tasks/{task.id}/events",
            duplicate=not created,
        )

    @app.get("/tasks/{task_id}", dependencies=[Depends(reader)])
    async def get_task(task_id: str) -> Task:
        task = await swarm.store.get(task_id)
        if task is None:
            raise HTTPException(404, "unknown task")
        return task

    @app.get("/tasks/{task_id}/events", dependencies=[Depends(reader)])
    async def task_events(
        task_id: str, last_event_id: Annotated[str | None, Header()] = None
    ) -> StreamingResponse:
        if await swarm.store.get(task_id) is None:
            raise HTTPException(404, "unknown task")
        cursor = int(last_event_id) if last_event_id and last_event_id.isdecimal() else 0
        return StreamingResponse(_stream(task_id, cursor), media_type="text/event-stream")

    async def _stream(task_id: str, cursor: int) -> AsyncIterator[str]:
        for _ in range(SSE_MAX_POLLS):
            events = await swarm.store.events_after(task_id, cursor)
            for event in events:
                cursor = event.seq
                yield _sse(event)
            task = await swarm.store.get(task_id)
            if task is not None and task.status.terminal and not events:
                yield "event: done\ndata: {}\n\n"
                return
            if not events:
                yield ": keep-alive\n\n"
            await asyncio.sleep(SSE_POLL_S)

    if settings.mode == "gcp":
        _mount_internal(app, runtime, Depends(internal_caller))
    return app


def _sse(event: TaskEvent) -> str:
    return f"id: {event.seq}\nevent: {event.kind}\ndata: {event.model_dump_json()}\n\n"


def _mount_internal(app: FastAPI, runtime: Runtime, caller: object) -> None:
    from codesentry.adapters.gcp import parse_result_object, read_result

    swarm = runtime.swarm

    async def flush() -> None:
        """Cloud Run throttles the CPU once a response is sent, so a batched span may never leave after it."""
        await asyncio.to_thread(runtime.provider.force_flush, SPAN_FLUSH_MS)

    @app.post("/internal/pubsub", status_code=204, dependencies=[caller])
    async def pubsub_push(request: Request) -> None:
        """A non-2xx answer makes Pub/Sub retry with backoff and, after max attempts, dead-letter the message."""
        envelope = await request.json()
        try:
            message = StageMessage.model_validate_json(base64.b64decode(envelope["message"]["data"]))
        except (KeyError, ValueError) as error:
            raise HTTPException(400, "bad envelope") from error
        try:
            await swarm.handle(message)
        finally:
            await flush()

    @app.post("/internal/sandbox-result", status_code=204, dependencies=[caller])
    async def sandbox_result(request: Request) -> None:
        """Eventarc: an object was finalized in the results bucket."""
        name = (await request.json()).get("name", "")
        parsed = parse_result_object(name)
        if parsed is None:
            return
        task_id, iteration = parsed
        report = SandboxReport.model_validate_json(await read_result(runtime.settings, name))
        try:
            await swarm.complete_run(task_id, iteration, report)
        finally:
            await flush()
