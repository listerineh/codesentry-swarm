#!/usr/bin/env python3
"""Prints the spans of one trace from Cloud Trace.

Usage: gcloud auth print-access-token | scripts/show-trace.py PROJECT_ID TRACE_ID
"""

import json
import sys
import urllib.error
import urllib.request

project, trace_id = sys.argv[1:3]
token = sys.stdin.read().strip()
url = f"https://cloudtrace.googleapis.com/v1/projects/{project}/traces/{trace_id}"
request = urllib.request.Request(
    url, headers={"Authorization": f"Bearer {token}", "x-goog-user-project": project}
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        spans = json.load(response).get("spans", [])
except urllib.error.HTTPError as error:
    print(error.code, error.read().decode()[:300])
    raise SystemExit(1) from error

print(len(spans), "spans")
for span in sorted(spans, key=lambda item: item.get("startTime", "")):
    labels = span.get("labels", {})
    tokens = labels.get("gen_ai.usage.output_tokens", "")
    print(span.get("startTime", "")[11:23], span.get("name"), tokens)
