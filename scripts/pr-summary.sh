#!/usr/bin/env bash
# One line per open pull request on the demo shop: number, comments, outcome of the latest run and title.
set -euo pipefail

gh pr list -R listerineh/codesentry-demo-shop --state open --limit 50 --json number,title,comments --jq '
  .[] | [
    .number,
    (.comments | length),
    ((.comments[-1].body // "") | capture("\"outcome\": \"(?<o>[a-z_]+)\"").o // "pending"),
    .title
  ] | @tsv'
