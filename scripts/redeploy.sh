#!/usr/bin/env bash
# Ships the current commit: new images, a fresh plan, apply, and sandboxes back on (Terraform drops that attribute).
# Usage: scripts/redeploy.sh PROJECT_ID
set -euo pipefail
cd "$(dirname "$0")/.."

project="$1"
tag="$(git rev-parse --short HEAD)"

./scripts/bootstrap-gcp.sh "$project" 2>&1 | tail -2
./scripts/plan.sh "$project" "$tag" | grep -E 'Failures' || true
terraform -chdir=infra show -no-color tfplan | grep -E '^Plan:'
./scripts/apply.sh | grep -E 'Apply complete|Error'
gcloud auth print-access-token | ./scripts/enable-sandbox.py "$project"
