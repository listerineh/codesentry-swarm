#!/usr/bin/env bash
# Opens a pull request whose suite probes the sandbox from the inside. Read the findings in the result file.
set -euo pipefail

repo="listerineh/codesentry-demo-shop"
branch="escape-$(date +%H%M%S)"
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

git clone -q "git@github.com:${repo}.git" "$work"
cd "$work"
git switch -q -c "$branch"
cp "$here/demo/fixtures/discount/repo/pricing.py" pricing.py
cp "$here/demo/fixtures/discount/repo/tests/test_pricing.py" tests/test_pricing.py
cp "$here/demo/escape/test_escape.py" tests/test_escape.py
git add -A
git commit -q -m "test: probe the sandbox from the inside"
git push -q -u origin "$branch"
gh pr create -R "$repo" --head "$branch" --title "test: probe the sandbox" --body "Checks what code under test can reach."
