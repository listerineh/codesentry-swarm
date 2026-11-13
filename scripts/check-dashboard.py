#!/usr/bin/env python3
"""Says which dashboard charts have data in the last hour, by querying each chart's own filter.

Usage: gcloud auth print-access-token | scripts/check-dashboard.py PROJECT_ID
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "infra" / "dashboard.json"
WINDOW_S = 3600


def filters(node: object, title: str = ""):
    if isinstance(node, dict):
        title = node.get("title", title)
        for key, value in node.items():
            if key == "filter" and isinstance(value, str):
                yield title, value
            else:
                yield from filters(value, title)
    elif isinstance(node, list):
        for item in node:
            yield from filters(item, title)


def main() -> None:
    project = sys.argv[1]
    token = sys.stdin.read().strip()
    end = time.time()
    interval = {
        "interval.startTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end - WINDOW_S)),
        "interval.endTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end)),
    }
    for title, metric_filter in filters(json.loads(DASHBOARD.read_text())):
        query = urllib.parse.urlencode({"filter": metric_filter, **interval, "view": "HEADERS"})
        url = f"https://monitoring.googleapis.com/v3/projects/{project}/timeSeries?{query}"
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                series = json.load(response).get("timeSeries", [])
            status = f"{len(series)} series" if series else "NO DATA"
        except urllib.error.HTTPError as error:
            status = f"error {error.code}: {error.read().decode()[:120]}"
        print(f"{status:<14} {title}")


if __name__ == "__main__":
    main()
