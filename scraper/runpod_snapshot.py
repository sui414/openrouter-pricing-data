#!/usr/bin/env python3
"""Daily RunPod posted-price snapshot.

Public GraphQL: gpuTypes with secure (vetted datacenter) and community
(marketplace host) posted prices per GPU type. If RUNPOD_API_KEY is set
(GitHub Actions secret / local env), also collects authenticated fields:
spot minimum bid, on-demand floor, and stock status. Auth uses a Bearer
header so the key never appears in URLs or error messages.

Upserted into data/csv/runpod_daily.csv keyed (date, gpu_id). Raw archived.
Stdlib only.
"""

import csv
import gzip
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "https://api.runpod.io/graphql"
QUERY_PUBLIC = "query { gpuTypes { id displayName memoryInGb securePrice communityPrice } }"
QUERY_AUTH = ("query { gpuTypes { id displayName memoryInGb securePrice communityPrice "
              "lowestPrice(input:{gpuCount:1}) { minimumBidPrice uninterruptablePrice stockStatus } } }")
RETRIES = 3

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIELDS = ["date", "gpu_id", "display_name", "memory_gb",
          "secure_price", "community_price",
          "spot_bid_floor", "on_demand_floor", "stock_status", "scraped_at"]


def fetch():
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    body = json.dumps({"query": QUERY_AUTH if key else QUERY_PUBLIC}).encode()
    last_err = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(URL, data=body, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)["data"]["gpuTypes"]
        except Exception as e:
            last_err = type(e)(str(e)[:200])  # never propagate request internals
            time.sleep(2 ** attempt)
    raise last_err


def main():
    now = datetime.now(timezone.utc)
    scraped_at = now.isoformat(timespec="seconds")
    date = now.strftime("%Y-%m-%d")
    gpus = fetch()

    rawdir = DATA / "raw" / "runpod"
    rawdir.mkdir(parents=True, exist_ok=True)
    with open(rawdir / f"{date}.json.gz", "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            gz.write(json.dumps(gpus, separators=(",", ":")).encode())

    path = DATA / "csv" / "runpod_daily.csv"
    rows = {}
    if path.exists():
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                rows[(row["date"], row["gpu_id"])] = row
    for g in gpus:
        lp = g.get("lowestPrice") or {}
        rows[(date, g["id"])] = {
            "date": date, "gpu_id": g["id"], "display_name": g.get("displayName", ""),
            "memory_gb": g.get("memoryInGb", ""), "secure_price": g.get("securePrice", ""),
            "community_price": g.get("communityPrice", ""),
            "spot_bid_floor": lp.get("minimumBidPrice", ""),
            "on_demand_floor": lp.get("uninterruptablePrice", ""),
            "stock_status": lp.get("stockStatus", ""), "scraped_at": scraped_at}
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows[k] for k in sorted(rows))
    print(f"[{scraped_at}] runpod: {len(gpus)} gpu types, {len(rows)} total rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
