from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider

from codesentry.adapters.local import (
    DockerSandbox,
    FixtureCodeHost,
    MemoryBus,
    MemoryStore,
    RecordingLlm,
    ReplayLlm,
    SubprocessSandbox,
)
from codesentry.config import Settings
from codesentry.guardrails import CircuitBreaker, GuardedLlm
from codesentry.ports import Llm, Sandbox
from codesentry.swarm import Swarm
from codesentry.telemetry import TRACER_NAME, MemoryExporter, build_provider


@dataclass
class Runtime:
    settings: Settings
    swarm: Swarm
    provider: TracerProvider
    memory_spans: MemoryExporter | None


def build_llm(settings: Settings) -> Llm:
    if settings.llm == "replay":
        inner: Llm = ReplayLlm(Path(settings.replay_dir) / settings.scenario)
    else:
        from codesentry.adapters.gcp import GeminiLlm

        inner = GeminiLlm(settings)
        if settings.record_dir:
            inner = RecordingLlm(inner, Path(settings.record_dir) / settings.scenario)
    breaker = CircuitBreaker(settings.breaker_threshold, settings.breaker_cooldown_s)
    return GuardedLlm(inner, breaker, settings.llm_timeout_s, settings.fallback_model)


def build_sandbox(settings: Settings) -> Sandbox:
    match settings.sandbox:
        case "subprocess":
            return SubprocessSandbox(settings.sandbox_timeout_s)
        case "docker":
            return DockerSandbox(settings.sandbox_image, settings.sandbox_timeout_s, settings.sandbox_runtime)
        case "cloud-run-job":
            from codesentry.adapters.gcp import CloudRunJobSandbox

            return CloudRunJobSandbox(settings)


def build_runtime(settings: Settings) -> Runtime:
    provider, memory = build_provider(settings)
    if settings.mode == "gcp":
        from codesentry.adapters.gcp import FirestoreStore, GitHubHost, PubSubBus

        store, bus, host = FirestoreStore(settings), PubSubBus(settings), GitHubHost(settings)
    else:
        store, bus = MemoryStore(), MemoryBus(backoff_s=0.2)
        host = FixtureCodeHost(Path(settings.fixtures_dir) / "discount")
    swarm = Swarm(
        settings=settings,
        store=store,
        bus=bus,
        llm=build_llm(settings),
        sandbox=build_sandbox(settings),
        code_host=host,
        tracer=provider.get_tracer(TRACER_NAME),
    )
    return Runtime(settings, swarm, provider, memory)
