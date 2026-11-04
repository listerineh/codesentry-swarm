#!/usr/bin/env bash
# Everything that doesn't touch Google Cloud: lint, tests, the Docker sandbox and terraform validate. Never applies.
set -euo pipefail
cd "$(dirname "$0")/.."

uv sync --frozen --extra gcp
uv run ruff check .
uv run ruff format --check .
uv run pytest

if docker info >/dev/null 2>&1; then
  docker build -q -f Dockerfile.sandbox -t codesentry-sandbox:local .
  docker build -q -t codesentry-api:local .
  uv run codesentry demo --pace 0 --sandbox docker
  uv run codesentry demo --pace 0 --sandbox docker --scenario hostile
else
  echo "Docker isn't running: skipped the Docker sandbox"
fi

if command -v terraform >/dev/null; then
  terraform -chdir=infra init -backend=false -input=false >/dev/null
  terraform -chdir=infra validate
fi
