"""Entrypoint of the Cloud Run Job. Standard library only.

This outer process holds the signed URLs. The code under test runs one level down, inside a Cloud Run sandbox: no
network, no environment, no metadata server. The job already has its own microVM; the sandbox protects these URLs.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request

SANDBOX = "/usr/local/gcp/bin/sandbox"
ENTRY = "/app/sandbox_entry.py"


def main() -> None:
    with urllib.request.urlopen(os.environ["BUNDLE_URL"], timeout=30) as response:
        bundle = response.read()
    step_timeout = os.environ.get("CODESENTRY_STEP_TIMEOUT", "30")
    done = subprocess.run(
        [
            SANDBOX,
            "do",
            "--write",
            "-e",
            f"CODESENTRY_STEP_TIMEOUT={step_timeout}",
            "--",
            "/usr/local/bin/python3",
            ENTRY,
        ],
        input=bundle,
        capture_output=True,
        timeout=float(step_timeout) * 2 + 30,
        check=False,
    )
    if done.returncode == 0:
        result = done.stdout
    else:
        result = json.dumps(
            {"red": {"exit_code": 3, "stderr": done.stderr.decode(errors="replace")[-2_000:]}, "green": None}
        ).encode()
    request = urllib.request.Request(
        os.environ["RESULT_URL"], data=result, method="PUT", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30):
        pass


if __name__ == "__main__":
    main()
