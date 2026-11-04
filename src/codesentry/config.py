from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEMO_ROOT = Path(__file__).resolve().parents[2] / "demo"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CODESENTRY_", env_file=".env", extra="ignore")

    mode: Literal["local", "gcp"] = "local"
    llm: Literal["replay", "gemini"] = "replay"
    sandbox: Literal["subprocess", "docker", "cloud-run-job"] = "subprocess"

    # Guardrails
    max_iterations: int = 3
    max_tokens_per_task: int = 60_000
    llm_timeout_s: float = 45
    sandbox_timeout_s: float = 60
    breaker_threshold: int = 3
    breaker_cooldown_s: float = 60

    # Models: see docs/verified-facts.md before changing these
    triage_model: str = "gemini-3.8-flash"
    judge_model: str = "gemini-3.5-flash-lite"
    fallback_model: str = "gemini-3.5-flash-lite"

    # Demo
    replay_dir: str = str(DEMO_ROOT / "replays")
    scenario: str = "discount"
    fixtures_dir: str = str(DEMO_ROOT / "fixtures")
    record_dir: str | None = None
    sandbox_image: str = "codesentry-sandbox:local"
    sandbox_runtime: str | None = None  # e.g. "runsc" where gVisor is installed

    # Secrets: environment only, never in the repository
    webhook_secret: str = ""
    api_token: str = ""
    github_token: str = ""

    # Google Cloud
    project_id: str = ""
    location: str = "global"
    topic_prefix: str = "codesentry"
    bucket: str = ""
    job_name: str = "codesentry-sandbox"
    job_region: str = "us-central1"
    signer_service_account: str = ""
    push_audience: str = ""
    push_service_account: str = ""

    @model_validator(mode="after")
    def _gcp_needs_secrets(self) -> Self:
        if self.mode == "gcp":
            required = (
                "webhook_secret",
                "api_token",
                "github_token",
                "project_id",
                "bucket",
                "push_audience",
            )
            missing = [name for name in required if not getattr(self, name)]
            if missing:
                raise ValueError(f"gcp mode requires: {', '.join(missing)}")
            if self.sandbox != "cloud-run-job":
                raise ValueError("gcp mode only runs untrusted code in the cloud-run-job sandbox")
        return self
