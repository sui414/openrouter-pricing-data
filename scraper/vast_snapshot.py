#!/usr/bin/env python3
"""Hourly vast.ai GPU marketplace snapshot.

Sweeps all live offers (on-demand + interruptible/bid) via the unauthenticated
search API, paginating with a dph_total price cursor (server caps 64 offers per
response). Writes:

  data/csv/vast_hourly/date=<utc-date>.csv   per-GPU-type hourly aggregates (appended)
  data/raw/vast/<utc-date>.jsonl.gz          full trimmed offers, once per day
                                             (first run of the day)

Stdlib only.
"""

import csv
import glob
import gzip
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "https://console.vast.ai/api/v0/search/asks/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
}
RETRIES = 3
TIMEOUT = 40
MAX_PAGES = 400

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

OFFER_FIELDS = ["id", "machine_id", "host_id", "gpu_name", "num_gpus", "gpu_ram",
                "dph_total", "dph_base", "min_bid", "storage_cost", "inet_up_cost",
                "inet_down_cost", "geolocation", "verification", "reliability2",
                "cuda_max_good", "total_flops", "inet_down", "inet_up", "duration",
                "rented", "static_ip", "hosting_type", "gpu_arch", "driver_version"]

AGG_FIELDS = ["scraped_at", "rental_type", "gpu_name", "num_offers", "total_gpus",
              "dph_per_gpu_min", "dph_per_gpu_p25", "dph_per_gpu_median",
              "dph_per_gpu_p75", "dph_per_gpu_max", "verified_median",
              "median_reliability"]


def search(q):
    body = json.dumps({"q": q}).encode()
    last_err = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(URL, data=body, method="PUT", headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r).get("offers", [])
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise last_err


def sweep(rental_type):
    """All live offers for one rental type, paginated by price cursor."""
    base_q = {"rentable": {"eq": True}, "limit": 64, "type": rental_type,
              "order": [["dph_total", "asc"], ["id", "asc"]]}
    seen, floor, pages = {}, None, 0
    while pages < MAX_PAGES:
        q = json.loads(json.dumps(base_q))
        if floor is not None:
            q["dph_total"] = {"gte": floor}
        offers = search(q)
        if not offers:
            break
        new = [o for o in offers if o["id"] not in seen]
        for o in offers:
            seen[o["id"]] = o
        pages += 1
        floor = offers[-1]["dph_total"]
        if not new:
            if len(offers) < 64:
                break
            floor += 1e-9  # identical-price plateau wider than one page
    return list(seen.values())


def percentile(sorted_vals, p):
    if not sorted_vals:
        return ""
    k = (len(sorted_vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo), 6)


def aggregate(offers, rental_type, scraped_at):
    by_gpu = {}
    for o in offers:
        by_gpu.setdefault(o.get("gpu_name") or "unknown", []).append(o)
    rows = []
    for gpu, lst in sorted(by_gpu.items()):
        # normalize to per-GPU hourly price so multi-GPU rigs are comparable
        prices = sorted(o["dph_total"] / max(o.get("num_gpus") or 1, 1) for o in lst)
        verified = sorted(o["dph_total"] / max(o.get("num_gpus") or 1, 1)
                          for o in lst if o.get("verification") == "verified")
        rel = sorted(o.get("reliability2") or 0 for o in lst)
        rows.append({
            "scraped_at": scraped_at, "rental_type": rental_type, "gpu_name": gpu,
            "num_offers": len(lst),
            "total_gpus": sum(o.get("num_gpus") or 0 for o in lst),
            "dph_per_gpu_min": percentile(prices, 0),
            "dph_per_gpu_p25": percentile(prices, 0.25),
            "dph_per_gpu_median": percentile(prices, 0.5),
            "dph_per_gpu_p75": percentile(prices, 0.75),
            "dph_per_gpu_max": percentile(prices, 1),
            "verified_median": percentile(verified, 0.5),
            "median_reliability": percentile(rel, 0.5),
        })
    return rows


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
    date = now.strftime("%Y-%m-%d")

    all_rows, raw_out = [], []
    for rental_type in ("on-demand", "bid"):
        offers = sweep(rental_type)
        print(f"[{scraped_at}] vast.ai {rental_type}: {len(offers)} offers")
        all_rows.extend(aggregate(offers, rental_type, scraped_at))
        for o in offers:
            rec = {k: o.get(k) for k in OFFER_FIELDS}
            rec["rental_type"] = rental_type
            rec["scraped_at"] = scraped_at
            raw_out.append(rec)

    outdir = DATA / "csv" / "vast_hourly"
    outdir.mkdir(parents=True, exist_ok=True)
    fname = f"date={date}.csv"
    outfile = outdir / fname
    new_file = not outfile.exists()
    with open(outfile, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=AGG_FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(all_rows)
    gzip_completed_days(outdir, fname)

    # full raw offers once per day (first run after 00:00 UTC)
    rawdir = DATA / "raw" / "vast"
    rawdir.mkdir(parents=True, exist_ok=True)
    rawfile = rawdir / f"{date}.jsonl.gz"
    if not rawfile.exists():
        with open(rawfile, "wb") as f:
            with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
                for rec in raw_out:
                    gz.write((json.dumps(rec, separators=(",", ":")) + "\n").encode())
        print(f"wrote daily raw offers: {rawfile.name} ({len(raw_out)} offers)")

    print(f"appended {len(all_rows)} aggregate rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
