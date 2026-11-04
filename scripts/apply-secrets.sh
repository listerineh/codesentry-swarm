#!/usr/bin/env bash
# First deploy, phase one: Cloud Run can't start until its secrets have a value, so they are created first.
# Generates the webhook secret and the API token; neither is printed. Safe to run twice.
# Usage: scripts/apply-secrets.sh PROJECT_ID IMAGE_TAG [REGION]
set -euo pipefail
cd "$(dirname "$0")/../infra"

project="$1"
tag="$2"
region="${3:-us-central1}"
repo="${region}-docker.pkg.dev/${project}/codesentry"

GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"
export GOOGLE_OAUTH_ACCESS_TOKEN

terraform apply -no-color -input=false -auto-approve \
  -target=google_project_service.apis -target=google_secret_manager_secret.secrets \
  -var "project_id=${project}" -var "region=${region}" \
  -var "api_image=${repo}/api:${tag}" -var "sandbox_image=${repo}/sandbox:${tag}" | tail -3

for secret in codesentry-webhook-secret codesentry-api-token; do
  if [[ -z "$(gcloud secrets versions list "$secret" --project "$project" --limit 1 --format 'value(name)')" ]]; then
    openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add "$secret" --project "$project" --data-file=-
  fi
done
