from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
from conftest import settings_for

from codesentry.adapters.local import MemoryBus
from codesentry.api import create_app
from codesentry.models import TaskStatus
from codesentry.wiring import build_runtime

SECRET = "test-secret"


def payload(action: str = "opened") -> bytes:
    return json.dumps(
        {
            "action": action,
            "repository": {"full_name": "listerineh/codesentry-demo-shop"},
            "pull_request": {"number": 42, "title": "t", "head": {"sha": "abc"}, "base": {"sha": "def"}},
        }
    ).encode()


def headers(body: bytes, event: str = "pull_request") -> dict[str, str]:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"X-GitHub-Event": event, "X-Hub-Signature-256": f"sha256={digest}"}


@pytest.fixture
def runtime():
    return build_runtime(settings_for(webhook_secret=SECRET))


@pytest.fixture
def client(runtime):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runtime)), base_url="http://test")


async def test_the_webhook_answers_202_with_a_task_id_and_no_work_done(client, runtime):
    body = payload()
    response = await client.post("/webhooks/github", content=body, headers=headers(body))
    assert response.status_code == 202
    data = response.json()
    assert response.headers["location"] == data["status_url"]
    task = (await client.get(data["status_url"])).json()
    assert task["status"] == "queued"
    assert runtime.swarm.bus.queue.qsize() == 1


async def test_a_redelivery_is_flagged_and_enqueues_nothing(client, runtime):
    body = payload()
    await client.post("/webhooks/github", content=body, headers=headers(body))
    response = await client.post("/webhooks/github", content=body, headers=headers(body))
    assert response.status_code == 202 and response.json()["duplicate"] is True
    assert runtime.swarm.bus.queue.qsize() == 1


@pytest.mark.parametrize("signature", ["", "sha256=bad", None])
async def test_unsigned_requests_are_refused(client, signature):
    extra = {"X-GitHub-Event": "pull_request"}
    if signature is not None:
        extra["X-Hub-Signature-256"] = signature
    response = await client.post("/webhooks/github", content=payload(), headers=extra)
    assert response.status_code == 401


async def test_an_unset_secret_refuses_everything():
    runtime = build_runtime(settings_for())
    transport = httpx.ASGITransport(app=create_app(runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = payload()
        digest = hmac.new(b"", body, hashlib.sha256).hexdigest()
        extra = {"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": f"sha256={digest}"}
        assert (await client.post("/webhooks/github", content=body, headers=extra)).status_code == 401


@pytest.mark.parametrize(("event", "action"), [("push", "opened"), ("pull_request", "closed")])
async def test_other_events_are_ignored(client, event, action):
    body = payload(action)
    response = await client.post("/webhooks/github", content=body, headers=headers(body, event))
    assert response.json() == {"status": "ignored"}


async def test_malformed_payloads_get_a_422(client):
    body = json.dumps({"action": "opened"}).encode()
    response = await client.post("/webhooks/github", content=body, headers=headers(body))
    assert response.status_code == 422


async def test_unknown_tasks_are_404(client):
    assert (await client.get("/tasks/nope")).status_code == 404
    assert (await client.get("/tasks/nope/events")).status_code == 404


async def test_tasks_require_the_token_when_one_is_set():
    runtime = build_runtime(settings_for(webhook_secret=SECRET, api_token="reader"))
    transport = httpx.ASGITransport(app=create_app(runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/tasks/x")).status_code == 401
        response = await client.get("/tasks/x", headers={"Authorization": "Bearer reader"})
        assert response.status_code == 404


async def test_progress_streams_over_sse_until_the_task_ends(client, runtime):
    body = payload()
    task_id = (await client.post("/webhooks/github", content=body, headers=headers(body))).json()["task_id"]
    bus = runtime.swarm.bus
    assert isinstance(bus, MemoryBus)
    await bus.drain(runtime.swarm.handle)
    assert (await runtime.swarm.store.get(task_id)).status is TaskStatus.COMPLETED

    response = await client.get(f"/tasks/{task_id}/events")
    assert response.headers["content-type"].startswith("text/event-stream")
    text = response.text
    assert "event: hypothesis" in text and "event: completed" in text and text.rstrip().endswith("data: {}")
