from __future__ import annotations

import pytest

from codesentry.adapters.local import DockerSandbox, SubprocessSandbox
from codesentry.guardrails import gate
from codesentry.models import FileEdit, SandboxBundle
from codesentry.sandbox_entry import clip, safe_path

SOURCE = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def bundle(test: str = TEST, fix: str = FIXED) -> SandboxBundle:
    return SandboxBundle(
        files={"calc.py": SOURCE, "tests/__init__.py": ""},
        test_path="tests/test_new.py",
        test_code=test,
        edits=[FileEdit(path="calc.py", content=fix)],
    )


async def test_red_then_green():
    report = await SubprocessSandbox(30).execute("t", 1, bundle())
    assert report.red.exit_code == 1 and "AssertionError" in report.red.stdout
    assert report.green.exit_code == 0 and report.green.tests_run == 1
    assert gate(report)[0]


async def test_a_test_that_passes_before_the_fix_skips_green():
    report = await SubprocessSandbox(30).execute("t", 1, bundle(test="def test_ok():\n    assert True\n"))
    assert report.red.exit_code == 0 and report.green is None
    assert not gate(report)[0]


async def test_a_fix_that_exits_zero_to_fake_success_is_caught():
    fake = "import os\nos._exit(0)\n"
    report = await SubprocessSandbox(30).execute("t", 1, bundle(fix=fake))
    assert report.green.exit_code == 0 and report.green.tests_run == 0
    assert not gate(report)[0]


async def test_a_test_that_hangs_is_killed():
    hang = "import time\n\ndef test_hang():\n    time.sleep(30)\n"
    report = await SubprocessSandbox(1).execute("t", 1, bundle(test=hang))
    assert report.red.timed_out
    assert not gate(report)[0]


async def test_the_environment_stays_outside(monkeypatch):
    monkeypatch.setenv("CODESENTRY_GITHUB_TOKEN", "secret")
    leak = "import os\n\ndef test_env():\n    assert 'CODESENTRY_GITHUB_TOKEN' in os.environ\n"
    report = await SubprocessSandbox(30).execute("t", 1, bundle(test=leak))
    assert report.red.exit_code == 1


async def test_the_surrounding_project_config_never_leaks_in(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "--no-such-flag"\n')
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    report = await SubprocessSandbox(30).execute("t", 1, bundle())
    assert report.red.exit_code == 1


async def test_a_pull_request_cannot_set_pytest_options():
    hostile = bundle()
    hostile.files["pytest.ini"] = "[pytest]\naddopts = --no-such-flag\n"
    report = await SubprocessSandbox(30).execute("t", 1, hostile)
    assert report.red.exit_code == 1


def test_long_output_keeps_its_end():
    assert clip("a" * 20_000 + "assert 1 == 2").endswith("assert 1 == 2")


@pytest.mark.parametrize("path", ["../evil.py", "/tmp/evil.py"])
def test_bundles_cannot_escape_the_workspace(path, tmp_path):
    with pytest.raises(ValueError, match="unsafe"):
        safe_path(tmp_path, path)


def test_docker_runs_with_no_network_or_capabilities():
    argv = DockerSandbox("image:tag", 60, runtime="runsc").argv()
    joined = " ".join(argv)
    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "no-new-privileges",
        "--pids-limit",
        "--runtime runsc",
    ):
        assert flag in joined
    assert "-v" not in argv and "--volume" not in argv
