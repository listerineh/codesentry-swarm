#!/usr/bin/env bash
# Applies infra/tfplan, the plan written and reviewed with scripts/plan.sh.
set -euo pipefail
cd "$(dirname "$0")/../infra"

GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"
export GOOGLE_OAUTH_ACCESS_TOKEN

terraform apply -no-color -input=false tfplan | tail -12
