#!/usr/bin/env python3
"""Hourly capture run reconstructing a 5-minute series from one Actions slot.

GitHub throttles sub-hourly crons to ~1 firing/hour, so (borrowing the trick
from pluriholonomic/ai-routing) the hourly job stays alive and takes N samples
at a fixed interval inside a single run:

  every sample (default 11 @ 300s):  OpenRouter perf sweep (cache-busted)
  every 3rd sample (15-min grain):   vast.ai marketplace aggregates

Outputs:
  data/csv/perf_5min/date=<d>/run=<HHMM>.csv.gz   all samples of this run
  data/csv/perf_hourly/date=<d>.csv               first sample only (continuity
                                                  with the pre-capture series)
  data/csv/vast_hourly/date=<d>.csv               via vast_snapshot (appended)
  data/raw/vast/<d>.jsonl.gz                      via vast_snapshot (first of day)

Usage: capture.py [--samples N] [--interval-seconds S]
"""

import argparse
import csv
import gzip
import io
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import perf_snapshot
import vast_snapshot

DATA = Path(__file__).resolve().parent.parent / "data"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=11)
    ap.add_argument("--interval-seconds", type=int, default=300)
    args = ap.parse_args()

    start = datetime.now(timezone.utc)
    run_label = start.strftime("%H%M")
    date = start.strftime("%Y-%m-%d")
    pairs = perf_snapshot.active_pairs()
    print(f"[{start.isoformat(timespec='seconds')}] capture: {args.samples} samples "
          f"@ {args.interval_seconds}s, {len(pairs)} model×variant pairs", flush=True)

    all_rows, total_failures = [], 0
    for i in range(args.samples):
        tick = time.time()
        scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            rows, failures = perf_snapshot.collect_rows(pairs, scraped_at)
            all_rows.extend(rows)
            total_failures += len(failures)
            print(f"  sample {i + 1}/{args.samples}: {len(rows)} rows, "
                  f"{len(failures)} failures", flush=True)
        except Exception as e:
            print(f"  sample {i + 1} FAILED: {e}", flush=True)
            total_failures += len(pairs)
        if i == 0:
            # maintain the original hourly series
            outdir = DATA / "csv" / "perf_hourly"
            outdir.mkdir(parents=True, exist_ok=True)
            fname = f"date={date}.csv"
            outfile = outdir / fname
            new_file = not outfile.exists()
            with open(outfile, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=perf_snapshot.FIELDS)
                if new_file:
                    w.writeheader()
                w.writerows(rows)
            perf_snapshot.gzip_completed_days(outdir, fname)
        if i % 3 == 0:
            try:
                vast_snapshot.main()
            except Exception as e:
                print(f"  vast sample failed: {e}", flush=True)
        if i < args.samples - 1:
            time.sleep(max(0.0, args.interval_seconds - (time.time() - tick)))

    outdir = DATA / "csv" / "perf_5min" / f"date={date}"
    outdir.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=perf_snapshot.FIELDS)
    w.writeheader()
    w.writerows(all_rows)
    with open(outdir / f"run={run_label}.csv.gz", "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            gz.write(buf.getvalue().encode())

    print(f"capture done: {len(all_rows)} total rows, {total_failures} failures")
    return 1 if total_failures > args.samples * len(pairs) * 0.5 else 0


if __name__ == "__main__":
    sys.exit(main())
