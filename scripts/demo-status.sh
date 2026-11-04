#!/usr/bin/env bash
# What just happened in the deployed swarm: comments on the demo shop, job runs and recent errors.
# Usage: scripts/demo-status.sh PROJECT_ID [PR_NUMBER]
set -uo pipefail

project="$1"
pr="${2:-$(gh pr list -R listerineh/codesentry-demo-shop --limit 1 --json number --jq '.[0].number')}"

echo "--- comments on PR ${pr}"
gh pr view "$pr" -R listerineh/codesentry-demo-shop --json comments --jq '.comments[].body' | cut -c1-110 | head -40
echo "--- job executions"
gcloud run jobs executions list --job codesentry-sandbox --project "$project" --region us-central1 --limit 3 \
  --format 'table(name,status.succeededCount,status.failedCount)'
echo "--- errors"
gcloud logging read 'resource.type=~"cloud_run_.*" AND severity>=ERROR' --project "$project" --limit 3 --freshness 10m \
  --format 'value(textPayload)' | grep -vE '^ +|^Traceback|^$|^During|^The above' | cut -c1-180 | sort | uniq -c
