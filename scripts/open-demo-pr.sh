#!/usr/bin/env bash
# Opens a fresh pull request with the discount bug on the demo shop. Each run gets its own branch, so it can be repeated.
set -euo pipefail

repo="listerineh/codesentry-demo-shop"
branch="demo-$(date +%H%M%S)"
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

git clone -q "git@github.com:${repo}.git" "$work"
cd "$work"
git switch -q -c "$branch"
cp "$here/demo/fixtures/discount/repo/pricing.py" pricing.py
cp "$here/demo/fixtures/discount/repo/tests/test_pricing.py" tests/test_pricing.py
git add -A
git commit -q -m "feat(checkout): discounts take a percentage"
git push -q -u origin "$branch"
gh pr create -R "$repo" --head "$branch" --title "feat(checkout): discounts take a percentage" --body "The discount is now a parameter."
