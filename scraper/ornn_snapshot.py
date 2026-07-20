#!/usr/bin/env python3
"""Daily ORNN index snapshot (dashboard.ornnai.com).

Two public index families, both daily-grain with a limited public history
window (~3 months for GPUs, ~6 weeks for tokens) — scraping daily accumulates
the full series:

  /api/gpu/<type>/index-history   GPU rental price index ($/hr)
  /api/otpi/<lab>/index-history   Ornn Token Price Index ($/M tokens per lab)

Outputs (upserted, keyed on (series, timestamp) — history overlaps every day):
  data/csv/ornn_gpu_index.csv
  data/csv/ornn_token_index.csv
  data/raw/ornn/<date>.json.gz    raw responses

Stdlib only.
"""

import csv
import gzip
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://dashboard.ornnai.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "Accept": "application/json"}
GPU_TYPES = ["H100 SXM", "H200", "A100 SXM4", "RTX 5090", "B200", "RTX PRO 6000 WS"]
LABS = ["Anthropic", "OpenAI", "Google", "DeepSeek"]
RETRIES = 3

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def fetch(path):
    last_err = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(BASE + path, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise last_err


def upsert(path, key_fields, fieldnames, new_rows):
    rows = {}
    if path.exists():
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                rows[tuple(row[k] for k in key_fields)] = row
    for row in new_rows:
        rows[tuple(row[k] for k in key_fields)] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows[k] for k in sorted(rows))
    return len(rows)


def main():
    now = datetime.now(timezone.utc)
    scraped_at = now.isoformat(timespec="seconds")
    raw, gpu_rows, tok_rows, failures = {}, [], [], []

    for gpu in GPU_TYPES:
        try:
            resp = fetch(f"/api/gpu/{urllib.parse.quote(gpu)}/index-history?range=1Y")
            raw[f"gpu:{gpu}"] = resp
            for p in resp.get("data") or []:
                gpu_rows.append({"gpu_type": gpu, "timestamp": p["timestamp"],
                                 "index_value": p["index_value"], "last_scraped_at": scraped_at})
        except Exception as e:
            failures.append(f"gpu:{gpu}: {e}")

    for lab in LABS:
        try:
            resp = fetch(f"/api/otpi/{urllib.parse.quote(lab)}/index-history")
            raw[f"otpi:{lab}"] = resp
            for p in resp.get("data") or []:
                tok_rows.append({"lab": lab.lower(), "timestamp": p["timestamp"],
                                 "index_per_mtok": p["index_value"], "last_scraped_at": scraped_at})
        except Exception as e:
            failures.append(f"otpi:{lab}: {e}")

    n_gpu = upsert(DATA / "csv" / "ornn_gpu_index.csv", ("gpu_type", "timestamp"),
                   ["gpu_type", "timestamp", "index_value", "last_scraped_at"], gpu_rows)
    n_tok = upsert(DATA / "csv" / "ornn_token_index.csv", ("lab", "timestamp"),
                   ["lab", "timestamp", "index_per_mtok", "last_scraped_at"], tok_rows)

    rawdir = DATA / "raw" / "ornn"
    rawdir.mkdir(parents=True, exist_ok=True)
    with open(rawdir / f"{now.strftime('%Y-%m-%d')}.json.gz", "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            gz.write(json.dumps(raw, separators=(",", ":")).encode())

    print(f"[{scraped_at}] ornn: gpu_index total rows {n_gpu}, token_index total rows {n_tok}, "
          f"failures: {failures or 'none'}")
    return 1 if len(failures) == len(GPU_TYPES) + len(LABS) else 0


if __name__ == "__main__":
    sys.exit(main())
