#!/usr/bin/env python3
"""Turns on Cloud Run sandboxes for the job through the Admin API.

The attribute is in Preview: Terraform doesn't know it and older gcloud versions refuse it. Run this again after any
Terraform change to the job. Usage: gcloud auth print-access-token | scripts/enable-sandbox.py PROJECT_ID [REGION]
"""

import json
import sys
import urllib.request

project = sys.argv[1]
region = sys.argv[2] if len(sys.argv) > 2 else "us-central1"
token = sys.stdin.read().strip()
url = (
    f"https://{region}-run.googleapis.com/apis/run.googleapis.com/v1/"
    f"namespaces/{project}/jobs/codesentry-sandbox"
)
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
    job = json.load(response)

job["metadata"].setdefault("annotations", {})["run.googleapis.com/launch-stage"] = "BETA"
job["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["sandboxLauncher"] = True

request = urllib.request.Request(url, data=json.dumps(job).encode(), headers=headers, method="PUT")
with urllib.request.urlopen(request, timeout=30) as response:
    updated = json.load(response)

print(updated["spec"]["template"]["spec"]["template"]["spec"]["containers"][0].get("sandboxLauncher"))
