#!/usr/bin/env bash
# Writes infra/tfplan and runs the checks on it. Applying is a separate, reviewed step.
# Usage: scripts/plan.sh PROJECT_ID IMAGE_TAG [REGION]
set -euo pipefail
cd "$(dirname "$0")/../infra"

project="$1"
tag="$2"
region="${3:-us-central1}"
repo="${region}-docker.pkg.dev/${project}/codesentry"

GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"
export GOOGLE_OAUTH_ACCESS_TOKEN

terraform init -input=false -backend-config="bucket=${project}-tfstate" | tail -2
terraform plan -no-color -input=false -out=tfplan -var "project_id=${project}" -var "region=${region}" \
  -var "api_image=${repo}/api:${tag}" -var "sandbox_image=${repo}/sandbox:${tag}" | tail -4

echo "--- resources"
terraform show -no-color tfplan | grep -E '^  # ' | sed -E 's/^  # //; s/\[.*//; s/ will be .*//' | sort | uniq -c
echo "--- actions"
terraform show -no-color tfplan | grep -E '^  # ' | grep -vc 'will be created' || true
echo "--- tflint"
tflint --no-color || true
echo "--- trivy"
trivy config --quiet --skip-check-update . | grep -E 'Failures|HIGH|CRITICAL' || true
