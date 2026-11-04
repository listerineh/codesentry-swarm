#!/usr/bin/env bash
# What must exist before Terraform: the build APIs, the image registry, the state bucket and the images.
# Usage: scripts/bootstrap-gcp.sh PROJECT_ID [REGION]
set -euo pipefail
cd "$(dirname "$0")/.."

project="$1"
region="${2:-us-central1}"
state_bucket="${project}-tfstate"
repo="${region}-docker.pkg.dev/${project}/codesentry"
tag="$(git rev-parse --short HEAD)"

gcloud services enable artifactregistry.googleapis.com cloudbuild.googleapis.com storage.googleapis.com \
  --project "$project"

gcloud artifacts repositories describe codesentry --location "$region" --project "$project" >/dev/null 2>&1 ||
  gcloud artifacts repositories create codesentry --repository-format docker --location "$region" --project "$project"

gcloud storage buckets describe "gs://${state_bucket}" >/dev/null 2>&1 ||
  gcloud storage buckets create "gs://${state_bucket}" --project "$project" --location "$region" \
    --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://${state_bucket}" --versioning

gcloud builds submit --project "$project" --config infra/cloudbuild.yaml \
  --substitutions "_REPO=${repo},_TAG=${tag}" .

echo "api_image=${repo}/api:${tag}"
echo "sandbox_image=${repo}/sandbox:${tag}"
