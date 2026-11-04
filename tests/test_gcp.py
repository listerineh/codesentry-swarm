"""What can be checked without a Google Cloud project: parsing, request shape and the SDK's view of our schemas."""

from __future__ import annotations

import base64
import gzip
import io
import tarfile
from types import SimpleNamespace

import httpx
import pytest
from conftest import settings_for

pytest.importorskip("google.genai")

from google.genai import types
from pydantic import ValidationError

from codesentry import api
from codesentry.adapters import gcp
from codesentry.adapters.gcp import GeminiLlm, parse_result_object, read_tarball, result_object
from codesentry.guardrails import LlmUnavailable
from codesentry.models import Stage, StageMessage, Triage, Verdict
from codesentry.ports import LlmRequest
from codesentry.wiring import build_runtime


def tarball(entries: dict[str, str], extra=None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            data = content.encode()
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if extra:
            archive.addfile(extra)
    return gzip.compress(buffer.getvalue())


def test_tarballs_are_read_in_memory_and_filtered():
    link = tarfile.TarInfo("repo-abc/link.py")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    files = read_tarball(
        tarball({"repo-abc/a.py": "x", "repo-abc/image.png": "b", "repo-abc/../evil.py": "y"}, link)
    )
    assert files == {"a.py": "x"}


def test_result_objects_round_trip_and_reject_anything_else():
    assert parse_result_object(result_object("abc", 2)) == ("abc", 2)
    assert parse_result_object("bundles/abc/2.json") is None
    assert parse_result_object("results/abc/x.json") is None


@pytest.mark.parametrize("schema", [Triage, Verdict])
def test_the_sdk_accepts_our_schemas(schema):
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema)
    assert config.response_schema is schema


class FakeModels:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def gemini(result) -> tuple[GeminiLlm, FakeModels]:
    llm = GeminiLlm.__new__(GeminiLlm)
    models = FakeModels(result)
    llm._client = SimpleNamespace(aio=SimpleNamespace(models=models))
    return llm, models


REQUEST = LlmRequest(agent="judge", key="judge-1", model="m", system="s", prompt="p", schema=Verdict)
GOOD = '{"approved": true, "summary": "ok", "risk": "low", "feedback": null}'


async def test_gemini_sends_the_schema_and_counts_thinking_as_output():
    usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=7)
    llm, models = gemini(SimpleNamespace(text=GOOD, usage_metadata=usage))
    response = await llm.generate(REQUEST)
    config = models.calls[0]["config"]
    assert config.response_mime_type == "application/json" and config.response_schema is Verdict
    assert response.parsed.approved and response.output_tokens == 12


@pytest.mark.parametrize("text", ['{"approved": "maybe"}', "", None])
async def test_gemini_output_is_validated_even_with_a_schema(text):
    llm, _ = gemini(SimpleNamespace(text=text, usage_metadata=None))
    with pytest.raises(ValidationError):
        await llm.generate(REQUEST)


async def test_rate_limits_are_transient():
    from google.genai import errors

    llm, _ = gemini(
        errors.ClientError(429, {"error": {"message": "slow down", "status": "RESOURCE_EXHAUSTED"}})
    )
    with pytest.raises(LlmUnavailable):
        await llm.generate(REQUEST)


@pytest.mark.parametrize(
    ("claims", "allowed"),
    [
        (
            {
                "aud": "https://codesentry-api-123.us-central1.run.app",
                "email": "pusher@x",
                "email_verified": True,
            },
            True,
        ),
        (
            {"aud": "https://codesentry-api-abc-uc.a.run.app", "email": "pusher@x", "email_verified": True},
            True,
        ),
        ({"aud": "https://other.run.app", "email": "pusher@x", "email_verified": True}, False),
        ({"aud": "https://codesentry-api-1.run.app", "email": "evil@x", "email_verified": True}, False),
        ({"aud": "https://codesentry-api-1.run.app", "email": "pusher@x", "email_verified": False}, False),
    ],
)
async def test_push_tokens_must_come_from_our_identity_for_our_service(monkeypatch, claims, allowed):
    from google.oauth2 import id_token

    monkeypatch.setattr(id_token, "verify_oauth2_token", lambda *_: claims)
    settings = settings_for(push_audience="https://codesentry-api-", push_service_account="pusher@x")
    assert await gcp.verify_push_token("Bearer token", settings) is allowed


async def test_internal_endpoints_refuse_callers_without_a_valid_token(monkeypatch):
    settings = settings_for(webhook_secret="s")
    settings.mode = "gcp"  # skip the real clients: only the route guard is under test
    runtime = build_runtime(settings_for(webhook_secret="s"))
    runtime.settings = settings
    handled = []

    async def handle(message):
        handled.append(message)

    runtime.swarm.handle = handle

    async def verify(authorization, _settings):
        return authorization == "Bearer good"

    monkeypatch.setattr(gcp, "verify_push_token", verify)
    transport = httpx.ASGITransport(app=api.create_app(runtime))
    data = base64.b64encode(
        StageMessage(task_id="t", stage=Stage.RUN, iteration=1).model_dump_json().encode()
    )
    envelope = {"message": {"data": data.decode()}}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/internal/pubsub", json=envelope)).status_code == 401
        good = {"Authorization": "Bearer good"}
        assert (await client.post("/internal/pubsub", json=envelope, headers=good)).status_code == 204
        assert (await client.post("/internal/pubsub", json={}, headers=good)).status_code == 400
    assert handled[0].stage is Stage.RUN
