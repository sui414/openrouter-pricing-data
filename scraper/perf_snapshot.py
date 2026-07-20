#!/usr/bin/env python3
"""Hourly OpenRouter performance snapshot.

Fetches /api/frontend/v1/stats/endpoint for every (permaslug, variant) that had
active endpoints in the latest daily scrape, with a cache-buster param so we get
origin-fresh data instead of the 5-minute CDN-cached copy. Appends one row per
provider endpoint to data/csv/perf_hourly/date=<utc-date>.csv (~24 runs/day per
file). Completed prior-day files are gzipped.

Stdlib only. Safe to re-run: rows carry scraped_at, analysis dedupes on
(endpoint_id, scraped_at).
"""

import csv
import glob
import gzip
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://openrouter.ai"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) openrouter-pricing-research",
    "Accept": "application/json",
}
WORKERS = 6
RETRIES = 3
TIMEOUT = 30

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

FIELDS = ["scraped_at", "model_slug", "permaslug", "variant", "endpoint_id",
          "provider_slug", "price_prompt", "price_completion", "discount",
          "p50_throughput", "p75_throughput", "p90_throughput", "p95_throughput",
          "p99_throughput", "p50_latency", "p75_latency", "p90_latency",
          "p95_latency", "p99_latency", "request_count", "stats_window_minutes",
          "success_count", "derankable_error_count", "rate_limited_count", "status"]


def fetch_json(path, params):
    params = dict(params)
    params["_cb"] = random.randint(1, 10 ** 9)  # bypass 5-min CDN cache
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 404:
                raise
            time.sleep(2 ** attempt)
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise last_err


def active_pairs():
    """(permaslug, variant) pairs with endpoints in the latest daily scrape,
    keeping the first non-alias model_slug for labeling."""
    parts = sorted(glob.glob(str(DATA / "csv" / "endpoints" / "date=*.csv")))
    if not parts:
        raise SystemExit("no daily endpoints partition found; run scrape.py first")
    pairs = {}
    with open(parts[-1], newline="") as f:
        for row in csv.DictReader(f):
            key = (row["permaslug"], row["variant"])
            if key not in pairs or pairs[key].startswith("~"):
                pairs[key] = row["model_slug"]
    return [(slug, ps, v) for (ps, v), slug in sorted(pairs.items())]


def gzip_completed_days(dirpath, today_name):
    for p in glob.glob(str(dirpath / "date=*.csv")):
        path = Path(p)
        if path.name != today_name:
            with open(path, "rb") as src, open(str(path) + ".gz", "wb") as dst:
                with gzip.GzipFile(fileobj=dst, mode="wb", mtime=0) as gz:
                    gz.write(src.read())
            path.unlink()


def main():
    now = datetime.now(timezone.utc)
    scraped_at = now.isoformat(timespec="seconds")
    pairs = active_pairs()
    print(f"[{scraped_at}] perf snapshot for {len(pairs)} model×variant pairs")

    def fetch(task):
        slug, permaslug, variant = task
        try:
            data = fetch_json("/api/frontend/v1/stats/endpoint",
                              {"permaslug": permaslug, "variant": variant}).get("data") or []
        except urllib.error.HTTPError as e:
            if e.code == 404:  # variant retired since the last daily scrape
                return slug, permaslug, variant, [], None
            return slug, permaslug, variant, None, f"HTTPError: {e}"[:200]
        except Exception as e:
            return slug, permaslug, variant, None, f"{type(e).__name__}: {e}"[:200]
        return slug, permaslug, variant, data, None

    rows, failures = [], []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for slug, permaslug, variant, data, err in pool.map(fetch, pairs):
            if err:
                failures.append({"permaslug": permaslug, "variant": variant, "error": err})
                continue
            for e in data:
                s = e.get("stats") or {}
                h = e.get("status_heuristics") or {}
                pricing = e.get("pricing") or {}
                rows.append({
                    "scraped_at": scraped_at, "model_slug": slug,
                    "permaslug": permaslug, "variant": variant,
                    "endpoint_id": e.get("id", ""),
                    "provider_slug": e.get("provider_slug", ""),
                    "price_prompt": pricing.get("prompt", ""),
                    "price_completion": pricing.get("completion", ""),
                    "discount": pricing.get("discount", ""),
                    "p50_throughput": s.get("p50_throughput", ""),
                    "p75_throughput": s.get("p75_throughput", ""),
                    "p90_throughput": s.get("p90_throughput", ""),
                    "p95_throughput": s.get("p95_throughput", ""),
                    "p99_throughput": s.get("p99_throughput", ""),
                    "p50_latency": s.get("p50_latency", ""),
                    "p75_latency": s.get("p75_latency", ""),
                    "p90_latency": s.get("p90_latency", ""),
                    "p95_latency": s.get("p95_latency", ""),
                    "p99_latency": s.get("p99_latency", ""),
                    "request_count": s.get("request_count", ""),
                    "stats_window_minutes": s.get("window_minutes", ""),
                    "success_count": h.get("success", ""),
                    "derankable_error_count": h.get("derankableError", ""),
                    "rate_limited_count": h.get("rateLimited", ""),
                    "status": e.get("status", ""),
                })

    outdir = DATA / "csv" / "perf_hourly"
    outdir.mkdir(parents=True, exist_ok=True)
    fname = f"date={now.strftime('%Y-%m-%d')}.csv"
    outfile = outdir / fname
    new_file = not outfile.exists()
    with open(outfile, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)
    gzip_completed_days(outdir, fname)

    print(f"appended {len(rows)} endpoint rows, {len(failures)} failures")
    if failures:
        print(json.dumps(failures[:10]))
    return 1 if len(failures) > len(pairs) * 0.5 else 0


if __name__ == "__main__":
    sys.exit(main())
