#!/usr/bin/env bash
# Points the demo shop's pull request events at the deployed API. The secret goes from Secret Manager straight to GitHub.
# Usage: scripts/connect-webhook.sh PROJECT_ID [REPO]
set -euo pipefail
cd "$(dirname "$0")/.."

project="$1"
repo="${2:-listerineh/codesentry-demo-shop}"
url="$(gcloud run services describe codesentry-api --project "$project" --region us-central1 --format 'value(status.url)')"

gcloud secrets versions access latest --secret codesentry-webhook-secret --project "$project" |
  python3 -c '
import json, sys
print(json.dumps({
    "name": "web",
    "active": True,
    "events": ["pull_request"],
    "config": {"url": sys.argv[1] + "/webhooks/github", "content_type": "json", "secret": sys.stdin.read()},
}))' "$url" |
  gh api "repos/${repo}/hooks" --method POST --input - --jq '.id'
