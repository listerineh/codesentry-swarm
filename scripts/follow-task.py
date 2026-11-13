"""Follows one task live over the API's SSE stream, then prints what it cost and where its trace is.

Usage: CODESENTRY_API_TOKEN=... uv run python scripts/follow-task.py API_URL REPO PR_NUMBER HEAD_SHA
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

from codesentry.models import PullRequestRef
from codesentry.swarm import task_id_for

COLORS = {
    "queued": "37",
    "hypothesis": "36",
    "sandbox": "33",
    "policy": "31",
    "rejected": "31",
    "completed": "32",
    "needs_human": "35",
}
TASK_WAIT_S = 120


def request(url: str, token: str) -> urllib.request.Request:
    return urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    )


def get_task(api: str, token: str, task_id: str) -> dict | None:
    try:
        with urllib.request.urlopen(request(f"{api}/tasks/{task_id}", token), timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def say(kind: str, message: str) -> None:
    color = COLORS.get(kind, "37")
    print(f"\033[2m{time.strftime('%H:%M:%S')}\033[0m  \033[{color}m{kind:<12}\033[0m {message}", flush=True)


def main() -> None:
    api, repo, number, head_sha = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
    token = os.environ["CODESENTRY_API_TOKEN"]
    task_id = task_id_for(PullRequestRef(repo=repo, number=number, head_sha=head_sha, base_sha=""))
    say("queued", f"waiting for the webhook: task {task_id}")

    started = time.monotonic()
    while get_task(api, token, task_id) is None:
        if time.monotonic() - started > TASK_WAIT_S:
            sys.exit(
                "No task after 2 minutes: check Recent Deliveries in the repo's webhook settings and redeliver."
            )
        time.sleep(2)

    kind = ""
    with urllib.request.urlopen(request(f"{api}/tasks/{task_id}/events", token), timeout=660) as stream:
        for raw in stream:
            line = raw.decode().strip()
            if line.startswith("event: "):
                kind = line.removeprefix("event: ")
                if kind == "done":
                    break
            elif line.startswith("data: ") and kind != "done":
                event = json.loads(line.removeprefix("data: "))
                iteration = f"[{event['iteration']}] " if event.get("iteration") else ""
                say(event["kind"], iteration + event["message"])

    task = get_task(api, token, task_id) or {}
    usage = task.get("usage", {})
    elapsed = time.monotonic() - started
    print()
    print(f"status       {task.get('status')}")
    print(f"iterations   {len(task.get('iterations', []))}")
    print(f"tokens       {usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out")
    print(f"cost         ${usage.get('cost_usd', 0):.4f}")
    print(f"elapsed      {elapsed:.0f} s since the webhook")
    trace_id = task.get("trace_id")
    if trace_id:
        print(f"trace        {trace_id}")
        print(
            f"cloud trace  https://console.cloud.google.com/traces/explorer;traceId={trace_id}?project=codesentry-swarm"
        )
    print(
        f"firestore    https://console.cloud.google.com/firestore/databases/-default-/data/panel/tasks/{task_id}?project=codesentry-swarm"
    )


if __name__ == "__main__":
    main()
