"""Runs inside the sandbox. Standard library only, plus pytest: the sandbox image holds nothing else.

Reads a bundle as JSON, runs the new test against the pull request (red) and the whole suite with the fix (green), and
prints one JSON report. Test code runs in a child process and can only reach this report through its exit code.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path, PurePosixPath

OUTPUT_LIMIT = 8_000


def safe_path(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe path: {relative}")
    return root / path


def write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = safe_path(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def clip(text: str) -> str:
    # The end is what matters: pytest prints the assertion and the summary last.
    return text if len(text) <= OUTPUT_LIMIT else f"… truncated\n{text[-OUTPUT_LIMIT:]}"


def count_tests(report: Path) -> int:
    try:
        root = ElementTree.parse(report).getroot()
    except (OSError, ElementTree.ParseError):
        return 0
    return sum(int(suite.get("tests", "0")) for suite in root.iter("testsuite"))


def pytest_step(root: Path, target: str, timeout: float, run_dir: Path, name: str) -> dict:
    report = run_dir / f"{name}.xml"
    # An empty config: the suite runs the same wherever the workspace lands, and a pull request can't set addopts.
    config = run_dir / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "pytest",
        target,
        "-q",
        "--tb=native",
        "--no-header",
        "-c",
        str(config),
        f"--rootdir={root}",
        "-p",
        "no:cacheprovider",
        f"--junitxml={report}",
    ]
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(run_dir),
        "PYTHONPATH": str(root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    started = time.perf_counter()
    try:
        done = subprocess.run(
            command,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as expired:
        return {
            "exit_code": 124,
            "stdout": clip(str(expired.stdout or "")),
            "stderr": "killed: hard timeout",
            "tests_run": 0,
            "duration_ms": int(timeout * 1000),
            "timed_out": True,
        }
    return {
        "exit_code": done.returncode,
        "stdout": clip(done.stdout),
        "stderr": clip(done.stderr),
        "tests_run": count_tests(report),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "timed_out": False,
    }


def run(bundle: dict, timeout: float) -> dict:
    with tempfile.TemporaryDirectory(prefix="codesentry-") as raw:
        base = Path(raw)
        root, run_dir = base / "workspace", base / "run"
        root.mkdir()
        run_dir.mkdir()
        write_tree(root, bundle["files"])
        write_tree(root, {bundle["test_path"]: bundle["test_code"]})

        red = pytest_step(root, bundle["test_path"], timeout, run_dir, "red")
        if red["exit_code"] != 1:
            return {"red": red, "green": None}

        write_tree(root, {edit["path"]: edit["content"] for edit in bundle["edits"]})
        return {"red": red, "green": pytest_step(root, ".", timeout, run_dir, "green")}


def main() -> None:
    bundle = json.load(sys.stdin)
    timeout = float(os.environ.get("CODESENTRY_STEP_TIMEOUT", "30"))
    print(json.dumps(run(bundle, timeout)))


if __name__ == "__main__":
    main()
