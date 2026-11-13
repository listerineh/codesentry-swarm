#!/usr/bin/env bash
# The on-stage moment: opens a fresh pull request with a new bug (loyalty points) and follows the swarm live until it
# comments. Every run gets its own branch, so it can be repeated. The API token is read into the environment and never
# printed. Usage: scripts/live-demo.sh [SCENARIO]   (any folder in demo/shop-prs; default loyalty)
set -euo pipefail

scenario="${1:-loyalty}"
project="codesentry-swarm"
repo="listerineh/codesentry-demo-shop"
here="$(cd "$(dirname "$0")/.." && pwd)"
dir="$here/demo/shop-prs/$scenario"
branch="live-${scenario}-$(date +%H%M%S)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

api="$(gcloud run services describe codesentry-api --project "$project" --region us-central1 --format 'value(status.url)')"
CODESENTRY_API_TOKEN="$(gcloud secrets versions access latest --secret codesentry-api-token --project "$project")"
export CODESENTRY_API_TOKEN

# Wakes the API so GitHub's webhook doesn't hit a cold start. Any answer will do: Cloud Run reserves paths ending in z,
# so /healthz never reaches the app, and / answers 404 from the app itself.
python3 -c "import urllib.request
try:
    urllib.request.urlopen('$api/', timeout=30)
except Exception:
    pass"

git clone -q "git@github.com:${repo}.git" "$work"
cd "$work"
git switch -q -c "$branch"
cp -R "$dir/files/." .
title="$(sed -n 1p "$dir/pr.txt")"
git add -A
git commit -q -m "$title"
git push -q -u origin "$branch"
url="$(gh pr create -R "$repo" --head "$branch" --title "$title" --body "$(sed -n 2p "$dir/pr.txt")")"
number="${url##*/}"
sha="$(git rev-parse HEAD)"
echo "PR #${number}: ${url}"
echo

cd "$here"
uv run --extra gcp python scripts/follow-task.py "$api" "$repo" "$number" "$sha"
echo
echo "comment      ${url}"
