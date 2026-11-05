#!/usr/bin/env bash
# Opens a pull request whose code tells the reviewer to approve it. Comments in the code reach the model; the PR body doesn't.
set -euo pipefail

repo="listerineh/codesentry-demo-shop"
branch="injection-$(date +%H%M%S)"
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

git clone -q "git@github.com:${repo}.git" "$work"
cd "$work"
git switch -q -c "$branch"
cp "$here/demo/injection/pricing.py" pricing.py
cp "$here/demo/fixtures/discount/repo/tests/test_pricing.py" tests/test_pricing.py
git add -A
git commit -q -m "feat(checkout): discounts take a percentage"
git push -q -u origin "$branch"
gh pr create -R "$repo" --head "$branch" --title "feat(checkout): discounts take a percentage" --body "The discount is now a parameter."
