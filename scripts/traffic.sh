#!/usr/bin/env bash
# Fills the dashboard: opens one pull request per bug in demo/shop-prs, then re-triggers each one every round with an
# empty commit (a new head commit is a new task). loyalty is left out: it's the live demo's.
# Usage: scripts/traffic.sh [ROUNDS] [SECONDS_BETWEEN_ROUNDS] [SECONDS_BETWEEN_PRS]
set -euo pipefail

rounds="${1:-3}"
round_gap="${2:-300}"
pr_gap="${3:-20}"
repo="listerineh/codesentry-demo-shop"
here="$(cd "$(dirname "$0")/.." && pwd)"
stamp="$(date +%H%M%S)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

git clone -q "git@github.com:${repo}.git" "$work"
cd "$work"
branches=()

for dir in "$here"/demo/shop-prs/*/; do
  name="$(basename "$dir")"
  [[ "$name" == "loyalty" ]] && continue
  branch="suite-${name}-${stamp}"
  title="$(sed -n 1p "$dir/pr.txt")"
  body="$(sed -n 2p "$dir/pr.txt")"
  git switch -q -C "$branch" origin/main
  cp -R "$dir/files/." .
  git add -A
  git commit -q -m "$title"
  git push -q -u origin "$branch"
  echo "$(date +%T) round 1: $(gh pr create -R "$repo" --head "$branch" --title "$title" --body "$body")"
  branches+=("$branch")
  sleep "$pr_gap"
done

for round in $(seq 2 "$rounds"); do
  echo "$(date +%T) waiting ${round_gap}s for round ${round}"
  sleep "$round_gap"
  for branch in "${branches[@]}"; do
    git switch -q "$branch"
    git commit -q --allow-empty -m "chore: run CodeSentry again (round ${round})"
    git push -q
    echo "$(date +%T) round ${round}: ${branch}"
    sleep "$pr_gap"
  done
done

echo "$(date +%T) done: ${#branches[@]} pull requests, ${rounds} rounds"
