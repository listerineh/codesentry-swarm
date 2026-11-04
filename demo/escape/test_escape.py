"""Reports what code under test can reach from inside the sandbox. It always fails, so its findings reach the logs."""

import os
import socket


def probe(host: str, port: int) -> str:
    try:
        socket.create_connection((host, port), timeout=3).close()
    except OSError as error:
        return f"blocked ({error})"
    return "REACHED"


def test_report_what_is_reachable():
    findings = {
        "metadata_ip": probe("169.254.169.254", 80),
        "metadata_host": probe("metadata.google.internal", 80),
        "internet": probe("example.com", 443),
        "signed_urls_visible": [name for name in ("BUNDLE_URL", "RESULT_URL") if name in os.environ],
        "credentials_visible": [
            name for name in os.environ if "TOKEN" in name or "KEY" in name or "SECRET" in name
        ],
    }
    raise AssertionError(f"SANDBOX_FINDINGS {findings}")
