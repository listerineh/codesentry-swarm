"""Google Cloud adapters. Imports stay inside each class so the offline demo never needs these packages."""

from __future__ import annotations

import asyncio
import datetime
import gzip
import io
import tarfile
import time
from typing import Any

import httpx
from pydantic import BaseModel

from codesentry.config import Settings
from codesentry.guardrails import LlmUnavailable
from codesentry.models import (
    PullRequestRef,
    PullSnapshot,
    SandboxBundle,
    Stage,
    StageMessage,
    Task,
    TaskEvent,
)
from codesentry.ports import LlmRequest, LlmResponse

MAX_FILE_BYTES = 256_000
MAX_TREE_BYTES = 5_000_000
TEXT_SUFFIXES = (".py", ".toml", ".cfg", ".ini", ".txt", ".md")


class GeminiLlm:
    """Gemini with a response schema. The schema gives valid JSON; Pydantic checks it again and the gate checks meaning."""

    def __init__(self, settings: Settings) -> None:
        from google import genai
        from google.genai.types import HttpOptions

        self._client = genai.Client(
            vertexai=True,
            project=settings.project_id or None,
            location=settings.location,
            http_options=HttpOptions(api_version="v1"),
        )

    async def generate[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        from google.genai import errors
        from google.genai.types import GenerateContentConfig

        config = GenerateContentConfig(
            system_instruction=request.system,
            response_mime_type="application/json",
            response_schema=request.schema,
            temperature=0.2,
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=request.model, contents=request.prompt, config=config
            )
        except errors.ServerError as error:
            raise LlmUnavailable(str(error)) from error
        except errors.ClientError as error:
            if error.code == 429:
                raise LlmUnavailable(str(error)) from error
            raise
        usage = response.usage_metadata
        return LlmResponse(
            parsed=request.schema.model_validate_json(response.text or ""),
            model=request.model,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            # Thinking is billed as output.
            output_tokens=((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0))
            if usage
            else 0,
        )


class FirestoreStore:
    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore

        self._client = firestore.AsyncClient(project=settings.project_id)
        self._tasks = self._client.collection("tasks")

    async def create(self, task: Task) -> bool:
        from google.api_core.exceptions import AlreadyExists

        try:
            await self._tasks.document(task.id).create(task.model_dump(mode="json"))
        except AlreadyExists:
            return False
        return True

    async def get(self, task_id: str) -> Task | None:
        snapshot = await self._tasks.document(task_id).get()
        return Task.model_validate(snapshot.to_dict()) if snapshot.exists else None

    async def save(self, task: Task) -> None:
        await self._tasks.document(task.id).set(task.model_dump(mode="json"))

    async def claim(self, task_id: str, key: str, lease_s: float) -> bool:
        from google.cloud import firestore

        reference = self._tasks.document(task_id).collection("claims").document(key)

        @firestore.async_transactional
        async def take(transaction: Any) -> bool:
            snapshot = await reference.get(transaction=transaction)
            if snapshot.exists and (snapshot.to_dict() or {}).get("expires_at", 0) > time.time():
                return False
            transaction.set(reference, {"expires_at": time.time() + lease_s})
            return True

        return await take(self._client.transaction())

    async def release(self, task_id: str, key: str) -> None:
        await self._tasks.document(task_id).collection("claims").document(key).delete()

    async def append_event(self, event: TaskEvent) -> TaskEvent:
        stored = event.model_copy(update={"seq": time.time_ns()})
        events = self._tasks.document(event.task_id).collection("events")
        await events.document(str(stored.seq)).set(stored.model_dump(mode="json"))
        return stored

    async def events_after(self, task_id: str, seq: int) -> list[TaskEvent]:
        events = self._tasks.document(task_id).collection("events")
        query = events.where("seq", ">", seq).order_by("seq")
        return [TaskEvent.model_validate(item.to_dict()) async for item in query.stream()]


class PubSubBus:
    """One topic per stage. The W3C traceparent travels as an attribute so the whole task stays on a single trace."""

    def __init__(self, settings: Settings) -> None:
        from google.cloud import pubsub_v1

        self._client = pubsub_v1.PublisherClient()
        self._topics = {
            stage: self._client.topic_path(settings.project_id, f"{settings.topic_prefix}-{stage.value}")
            for stage in Stage
        }

    async def publish(self, message: StageMessage) -> None:
        attributes = {"traceparent": message.traceparent} if message.traceparent else {}
        future = self._client.publish(
            self._topics[message.stage], message.model_dump_json().encode(), **attributes
        )
        await asyncio.to_thread(future.result, timeout=30)


class CloudRunJobSandbox:
    """Starts one job execution per iteration and returns None: the report lands in Cloud Storage and Eventarc tells us.

    The job gets two short-lived signed URLs and has no IAM roles, so stolen job credentials are worth nothing.
    """

    def __init__(self, settings: Settings) -> None:
        from google.cloud import run_v2, storage

        self._settings = settings
        self._storage = storage.Client(project=settings.project_id)
        self._jobs = run_v2.JobsAsyncClient()

    def _signed_url(self, blob: Any, method: str) -> str:
        import google.auth
        import google.auth.transport.requests

        credentials, _ = google.auth.default()
        credentials.refresh(google.auth.transport.requests.Request())
        return blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=15),
            method=method,
            service_account_email=self._settings.signer_service_account,
            access_token=credentials.token,
        )

    def _prepare(self, task_id: str, iteration: int, bundle: SandboxBundle) -> tuple[str, str]:
        bucket = self._storage.bucket(self._settings.bucket)
        source = bucket.blob(f"bundles/{task_id}/{iteration}.json")
        source.upload_from_string(bundle.model_dump_json(), content_type="application/json")
        return self._signed_url(source, "GET"), self._signed_url(
            bucket.blob(result_object(task_id, iteration)), "PUT"
        )

    async def execute(self, task_id: str, iteration: int, bundle: SandboxBundle) -> None:
        from google.cloud import run_v2

        bundle_url, result_url = await asyncio.to_thread(self._prepare, task_id, iteration, bundle)
        name = self._jobs.job_path(
            self._settings.project_id, self._settings.job_region, self._settings.job_name
        )
        overrides = run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(
                    env=[
                        run_v2.EnvVar(name="BUNDLE_URL", value=bundle_url),
                        run_v2.EnvVar(name="RESULT_URL", value=result_url),
                    ]
                )
            ]
        )
        await self._jobs.run_job(request=run_v2.RunJobRequest(name=name, overrides=overrides))


def result_object(task_id: str, iteration: int) -> str:
    return f"results/{task_id}/{iteration}.json"


def parse_result_object(name: str) -> tuple[str, int] | None:
    parts = name.split("/")
    if len(parts) != 3 or parts[0] != "results" or not parts[2].endswith(".json"):
        return None
    number = parts[2].removesuffix(".json")
    return (parts[1], int(number)) if number.isdecimal() else None


async def read_result(settings: Settings, name: str) -> bytes:
    from google.cloud import storage

    client = storage.Client(project=settings.project_id)
    return await asyncio.to_thread(client.bucket(settings.bucket).blob(name).download_as_bytes)


def read_tarball(raw: bytes) -> dict[str, str]:
    """In-memory only: nothing from the archive touches a disk, so links and traversal entries are inert."""
    files: dict[str, str] = {}
    total = 0
    with gzip.open(io.BytesIO(raw)) as stream, tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            if not member.isfile() or member.size > MAX_FILE_BYTES:
                continue
            relative = member.name.split("/", 1)[-1]
            if not relative.endswith(TEXT_SUFFIXES) or ".." in relative.split("/"):
                continue
            content = archive.extractfile(member)
            if content is None:
                continue
            total += member.size
            if total > MAX_TREE_BYTES:
                raise ValueError("repository is too large for the sandbox")
            files[relative] = content.read().decode("utf-8", errors="replace")
    return files


class GitHubHost:
    def __init__(self, settings: Settings) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {settings.github_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
            follow_redirects=True,
        )

    async def snapshot(self, pull_request: PullRequestRef) -> PullSnapshot:
        base = f"/repos/{pull_request.repo}"
        diff = await self._client.get(
            f"{base}/pulls/{pull_request.number}", headers={"Accept": "application/vnd.github.diff"}
        )
        diff.raise_for_status()
        archive = await self._client.get(f"{base}/tarball/{pull_request.head_sha}")
        archive.raise_for_status()
        return PullSnapshot(diff=diff.text, files=read_tarball(archive.content))

    async def comment(self, pull_request: PullRequestRef, body: str) -> None:
        response = await self._client.post(
            f"/repos/{pull_request.repo}/issues/{pull_request.number}/comments", json={"body": body}
        )
        response.raise_for_status()


async def verify_push_token(authorization: str | None, settings: Settings) -> bool:
    """Only our Pub/Sub and Eventarc service account may call the internal endpoints."""
    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    if not authorization or not authorization.startswith("Bearer "):
        return False
    try:
        # The audience is checked below: Pub/Sub and Eventarc may use either of the service's two run.app URLs.
        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token, authorization.removeprefix("Bearer "), google_requests.Request()
        )
    except (ValueError, GoogleAuthError):
        return False
    return (
        str(claims.get("aud", "")).startswith(settings.push_audience)
        and claims.get("email") == settings.push_service_account
        and bool(claims.get("email_verified"))
    )
