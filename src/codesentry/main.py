"""ASGI entrypoint for Cloud Run: `uvicorn codesentry.main:app`."""

from codesentry.api import create_app
from codesentry.config import Settings
from codesentry.wiring import build_runtime

app = create_app(build_runtime(Settings()))
