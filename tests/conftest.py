from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from codesentry.adapters.local import FixtureCodeHost, MemoryBus, load_pull_request
from codesentry.config import Settings
from codesentry.models import PullRequestRef, Task
from codesentry.wiring import Runtime, build_runtime

ROOT = Path(__file__).resolve().parents[1]


def settings_for(scenario: str = "discount", **overrides) -> Settings:
    return Settings(
        _env_file=None,
        scenario=scenario,
        replay_dir=str(ROOT / "demo/replays"),
        fixtures_dir=str(ROOT / "demo/fixtures"),
        **overrides,
    )


@pytest.fixture
def pull_request() -> PullRequestRef:
    return load_pull_request(ROOT / "demo/fixtures/discount")


@pytest.fixture
def runtime_for() -> Callable[..., Runtime]:
    return lambda scenario="discount", **overrides: build_runtime(settings_for(scenario, **overrides))


async def run_to_end(runtime: Runtime, pull_request: PullRequestRef) -> Task:
    task, _ = await runtime.swarm.submit(pull_request)
    bus = runtime.swarm.bus
    assert isinstance(bus, MemoryBus)
    await bus.drain(runtime.swarm.handle)
    final = await runtime.swarm.store.get(task.id)
    assert final is not None
    return final


def comments(runtime: Runtime) -> list[str]:
    host = runtime.swarm.code_host
    assert isinstance(host, FixtureCodeHost)
    return host.comments
