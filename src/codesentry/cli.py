"""`codesentry demo` is the live demo. It needs no network and no cloud account."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path

import httpx
from opentelemetry.sdk.trace import ReadableSpan
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.tree import Tree

from codesentry.adapters.local import FixtureCodeHost, MemoryBus, load_pull_request
from codesentry.config import Settings
from codesentry.models import TaskEvent, TaskStatus
from codesentry.wiring import Runtime, build_runtime

SCENARIOS = {
    "discount": "The happy path: a wrong first idea, a corrected hypothesis, a green fix.",
    "exhausted": "Three failed attempts, then a human.",
    "budget": "The token budget trips mid-task.",
    "hostile": "A fix that touches CI and a test that tries to escape.",
}
STYLES = {
    "hypothesis": "bold cyan",
    "sandbox": "yellow",
    "rejected": "red",
    "policy": "bold red",
    "completed": "bold green",
    "needs_human": "bold magenta",
}


def span_tree(spans: list[ReadableSpan]) -> Tree:
    ordered = sorted(spans, key=lambda span: span.start_time or 0)
    trace_id = format(ordered[0].context.trace_id, "032x") if ordered else "-"
    root = Tree(f"[bold]trace {trace_id}[/bold]")
    nodes = {}
    for span in ordered:
        duration = ((span.end_time or 0) - (span.start_time or 0)) / 1e6
        attributes = span.attributes or {}
        tokens = ""
        if "gen_ai.usage.input_tokens" in attributes:
            tokens = f" [dim]{attributes['gen_ai.usage.input_tokens']} in / {attributes['gen_ai.usage.output_tokens']} out[/dim]"
        label = f"{span.name} [dim]{duration:.0f} ms[/dim]{tokens}"
        parent = nodes.get(span.parent.span_id) if span.parent else None
        nodes[span.context.span_id] = (parent or root).add(label)
    return root


async def _demo(runtime: Runtime, console: Console, pace: float) -> int:
    swarm, settings = runtime.swarm, runtime.settings
    bus, host = swarm.bus, swarm.code_host
    assert isinstance(bus, MemoryBus)
    assert isinstance(host, FixtureCodeHost)
    pull_request = load_pull_request(host.fixture)

    console.print(
        Panel(Syntax((host.fixture / "pr.diff").read_text(), "diff"), title=f"PR #{pull_request.number}")
    )
    task, _ = await swarm.submit(pull_request)
    console.print(f"[green]202 Accepted[/green]  task_id={task.id}\n")

    cursor = 0

    async def show_events() -> None:
        nonlocal cursor
        for event in await swarm.store.events_after(task.id, cursor):
            cursor = event.seq
            _print_event(console, event)
            await asyncio.sleep(pace)

    while not bus.queue.empty():
        message, _attempt = bus.queue.get_nowait()
        console.rule(f"[bold]{message.stage.value}[/bold] · iteration {message.iteration}", style="dim")
        await swarm.handle(message)
        await show_events()

    final = await swarm.store.get(task.id)
    assert final is not None
    if host.comments:
        console.print(Panel(host.comments[-1], title="Pull request comment"))
    runtime.provider.force_flush()
    if runtime.memory_spans:
        console.print(span_tree(runtime.memory_spans.spans))
    limit = settings.max_tokens_per_task
    console.print(
        f"\n[bold]{final.status.value}[/bold] · {final.usage.tokens:,} of {limit:,} tokens · "
        f"${final.usage.cost_usd:.5f} · {len(final.iterations)} iteration(s)"
    )
    if settings.llm == "replay":
        console.print("[dim]Scripted model answers (demo/replays). Sandbox runs are real.[/dim]")
    return 0 if final.status in (TaskStatus.COMPLETED, TaskStatus.NEEDS_HUMAN) else 1


def _print_event(console: Console, event: TaskEvent) -> None:
    style = STYLES.get(event.kind, "white")
    console.print(f"  [{style}]{event.kind:<12}[/{style}] {event.message}")


def demo(args: argparse.Namespace) -> int:
    overrides = {"scenario": args.scenario, "llm": args.llm, "sandbox": args.sandbox, "mode": "local"}
    if args.scenario == "budget":
        overrides["max_tokens_per_task"] = args.max_tokens or 4_000
    elif args.max_tokens:
        overrides["max_tokens_per_task"] = args.max_tokens
    if args.record:
        overrides["record_dir"] = args.record
    console = Console()
    console.print(f"[bold]CodeSentry Swarm[/bold] · {args.scenario}: {SCENARIOS[args.scenario]}")
    return asyncio.run(_demo(build_runtime(Settings(**overrides)), console, args.pace))


def serve(args: argparse.Namespace) -> int:
    import uvicorn

    from codesentry.api import create_app

    uvicorn.run(create_app(build_runtime(Settings())), host=args.host, port=args.port)
    return 0


def webhook(args: argparse.Namespace) -> int:
    """Sends the fixture pull request to a running server, signed like GitHub would."""
    settings = Settings()
    pull_request = load_pull_request(Path(settings.fixtures_dir) / "discount")
    body = json.dumps(
        {
            "action": "opened",
            "repository": {"full_name": pull_request.repo},
            "pull_request": {
                "number": pull_request.number,
                "title": pull_request.title,
                "head": {
                    "sha": f"{pull_request.head_sha[:-8]}{int(time.time()):08x}",
                    "repo": {"full_name": pull_request.repo},
                },
                "base": {"sha": pull_request.base_sha},
            },
        }
    ).encode()
    signature = "sha256=" + hmac.new(settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    response = httpx.post(
        f"{args.url}/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": signature},
    )
    print(response.status_code, response.text)
    return 0 if response.is_success else 1


def app() -> None:
    parser = argparse.ArgumentParser(prog="codesentry")
    commands = parser.add_subparsers(required=True)

    run = commands.add_parser("demo", help="run a scenario end to end in this terminal")
    run.add_argument("--scenario", choices=SCENARIOS, default="discount")
    run.add_argument("--llm", choices=["replay", "gemini"], default="replay")
    run.add_argument("--sandbox", choices=["subprocess", "docker"], default="subprocess")
    run.add_argument("--pace", type=float, default=0.8, help="seconds between events, for the stage")
    run.add_argument("--max-tokens", type=int, default=None)
    run.add_argument(
        "--record", default=None, help="with --llm gemini, save answers as a replay in this folder"
    )
    run.set_defaults(func=demo)

    server = commands.add_parser("serve", help="run the API with the in-process bus")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8080)
    server.set_defaults(func=serve)

    send = commands.add_parser("webhook", help="send the demo pull request to a running server")
    send.add_argument("--url", default="http://127.0.0.1:8080")
    send.set_defaults(func=webhook)

    args = parser.parse_args()
    raise SystemExit(args.func(args))
