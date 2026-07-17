#!/usr/bin/env python3
"""OpenRouter daily pricing/performance scraper.

Fetches model, provider, pricing, effective-pricing, performance, and usage
data from OpenRouter's public + frontend JSON APIs and writes:

  data/raw/<date>/          gzipped raw API responses (schema-drift-proof archive)
  data/csv/<table>/         normalized per-day CSV partitions
  data/csv/model_activity.csv  rolling upsert of daily usage history
  data/manifest/<date>.json    scrape metadata + failures

Stdlib only. Idempotent: re-running on the same day overwrites that day's outputs.
"""

import csv
import gzip
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def fetch_json(path, params=None):
    """GET a JSON endpoint with retries. Raises on final failure."""
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 404:
                raise  # not retryable
            time.sleep(2 ** attempt)
        except Exception as e:  # URLError, timeout, bad JSON
            last_err = e
            time.sleep(2 ** attempt)
    raise last_err


def write_gz_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 keeps output bytes stable across same-day re-runs
    with open(path, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            gz.write(json.dumps(obj, separators=(",", ":")).encode())


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def parse_variant(model_id):
    """'author/slug:free' -> ('author/slug', 'free'); no suffix -> standard."""
    if ":" in model_id:
        slug, variant = model_id.split(":", 1)
        return slug, variant
    return model_id, "standard"


def fetch_model_stats(task):
    """Fetch the three per-model stats endpoints for one (slug, permaslug, variant)."""
    slug, permaslug, variant = task
    params = {"permaslug": permaslug, "variant": variant}
    rec = {"model_slug": slug, "permaslug": permaslug, "variant": variant,
           "endpoints": None, "effective_pricing": None, "activity": None, "errors": []}
    for key, path in (("endpoints", "/api/frontend/v1/stats/endpoint"),
                      ("effective_pricing", "/api/frontend/v1/stats/effective-pricing"),
                      ("activity", "/api/frontend/v1/stats/model-activity")):
        try:
            rec[key] = fetch_json(path, params).get("data")
        except Exception as e:
            code = getattr(e, "code", None)
            if code == 404:
                continue  # no data for this model/variant; not an error worth flagging
            rec["errors"].append({"endpoint": key, "error": f"{type(e).__name__}: {e}"[:300]})
    return rec


# ---------------------------------------------------------------- CSV builders

def build_models_rows(pub_models, slug_to_permaslug, scrape_date, scraped_at):
    rows = []
    for m in pub_models:
        slug, variant = parse_variant(m["id"])
        pricing = m.get("pricing") or {}
        arch = m.get("architecture") or {}
        bench = (m.get("benchmarks") or {}).get("artificial_analysis") or {}
        rows.append({
            "scrape_date": scrape_date, "scraped_at": scraped_at,
            "id": m["id"], "model_slug": slug, "variant": variant,
            "permaslug": slug_to_permaslug.get(slug, ""),
            "canonical_slug": m.get("canonical_slug", ""),
            "name": m.get("name", ""), "author": slug.split("/")[0],
            "created_at": datetime.fromtimestamp(m["created"], tz=timezone.utc).isoformat() if m.get("created") else "",
            "context_length": m.get("context_length", ""),
            "modality": arch.get("modality", ""),
            "tokenizer": arch.get("tokenizer", ""),
            "prompt_price": pricing.get("prompt", ""),
            "completion_price": pricing.get("completion", ""),
            "cache_read_price": pricing.get("input_cache_read", ""),
            "cache_write_price": pricing.get("input_cache_write", ""),
            "image_price": pricing.get("image", ""),
            "request_price": pricing.get("request", ""),
            "is_moderated": (m.get("top_provider") or {}).get("is_moderated", ""),
            "knowledge_cutoff": m.get("knowledge_cutoff", ""),
            "aa_intelligence": bench.get("intelligence_index", ""),
            "aa_coding": bench.get("coding_index", ""),
            "aa_agentic": bench.get("agentic_index", ""),
            "headliner_json": json.dumps(
                {k: m.get(k) for k in ("pricing", "architecture", "context_length",
                                       "top_provider", "supported_parameters",
                                       "default_parameters", "reasoning", "benchmarks",
                                       "per_request_limits", "expiration_date")},
                separators=(",", ":")),
        })
    return rows


MODELS_FIELDS = ["scrape_date", "scraped_at", "id", "model_slug", "variant", "permaslug",
                 "canonical_slug", "name", "author", "created_at", "context_length",
                 "modality", "tokenizer", "prompt_price", "completion_price",
                 "cache_read_price", "cache_write_price", "image_price", "request_price",
                 "is_moderated", "knowledge_cutoff", "aa_intelligence", "aa_coding",
                 "aa_agentic", "headliner_json"]


def build_endpoint_rows(stats_records, scrape_date, scraped_at):
    rows = []
    for rec in stats_records:
        for e in rec.get("endpoints") or []:
            pricing = e.get("pricing") or {}
            stats = e.get("stats") or {}
            heur = e.get("status_heuristics") or {}
            policy = e.get("data_policy") or {}
            rows.append({
                "scrape_date": scrape_date, "scraped_at": scraped_at,
                "model_slug": rec["model_slug"], "permaslug": rec["permaslug"],
                "variant": rec["variant"],
                "endpoint_id": e.get("id", ""), "endpoint_name": e.get("name", ""),
                "provider_name": e.get("provider_name", ""),
                "provider_slug": (e.get("provider_info") or {}).get("slug", ""),
                "provider_display_name": e.get("provider_display_name", ""),
                "provider_region": e.get("provider_region", ""),
                "quantization": e.get("quantization", ""),
                "context_length": e.get("context_length", ""),
                "max_completion_tokens": e.get("max_completion_tokens", ""),
                "max_prompt_tokens": e.get("max_prompt_tokens", ""),
                "price_prompt": pricing.get("prompt", ""),
                "price_completion": pricing.get("completion", ""),
                "price_cache_read": pricing.get("input_cache_read", ""),
                "price_cache_write": pricing.get("input_cache_write", ""),
                "price_image": pricing.get("image", ""),
                "price_request": pricing.get("request", ""),
                "price_web_search": pricing.get("web_search", ""),
                "price_internal_reasoning": pricing.get("internal_reasoning", ""),
                "discount": pricing.get("discount", ""),
                "is_free": e.get("is_free", ""), "is_byok": e.get("is_byok", ""),
                "is_hidden": e.get("is_hidden", ""), "is_disabled": e.get("is_disabled", ""),
                "is_deranked": e.get("is_deranked", ""),
                "status": e.get("status", ""),
                "p50_throughput": stats.get("p50_throughput", ""),
                "p75_throughput": stats.get("p75_throughput", ""),
                "p90_throughput": stats.get("p90_throughput", ""),
                "p95_throughput": stats.get("p95_throughput", ""),
                "p99_throughput": stats.get("p99_throughput", ""),
                "p50_latency": stats.get("p50_latency", ""),
                "p75_latency": stats.get("p75_latency", ""),
                "p90_latency": stats.get("p90_latency", ""),
                "p95_latency": stats.get("p95_latency", ""),
                "p99_latency": stats.get("p99_latency", ""),
                "request_count": stats.get("request_count", ""),
                "stats_window_minutes": stats.get("window_minutes", ""),
                "success_count": heur.get("success", ""),
                "derankable_error_count": heur.get("derankableError", ""),
                "rate_limited_count": heur.get("rateLimited", ""),
                "capacity_tpm": e.get("capacity_tpm", ""),
                "limit_rpm": e.get("limit_rpm", ""), "limit_rpd": e.get("limit_rpd", ""),
                "supports_reasoning": e.get("supports_reasoning", ""),
                "supports_tool_parameters": e.get("supports_tool_parameters", ""),
                "data_policy_training": policy.get("training", ""),
                "data_policy_retains_prompts": policy.get("retainsPrompts", ""),
                "pricing_json": json.dumps(pricing, separators=(",", ":")),
            })
    return rows


ENDPOINT_FIELDS = ["scrape_date", "scraped_at", "model_slug", "permaslug", "variant",
                   "endpoint_id", "endpoint_name", "provider_name", "provider_slug",
                   "provider_display_name", "provider_region", "quantization",
                   "context_length", "max_completion_tokens", "max_prompt_tokens",
                   "price_prompt", "price_completion", "price_cache_read",
                   "price_cache_write", "price_image", "price_request",
                   "price_web_search", "price_internal_reasoning", "discount",
                   "is_free", "is_byok", "is_hidden", "is_disabled", "is_deranked",
                   "status", "p50_throughput", "p75_throughput", "p90_throughput",
                   "p95_throughput", "p99_throughput", "p50_latency", "p75_latency",
                   "p90_latency", "p95_latency", "p99_latency", "request_count",
                   "stats_window_minutes", "success_count", "derankable_error_count",
                   "rate_limited_count", "capacity_tpm", "limit_rpm", "limit_rpd",
                   "supports_reasoning", "supports_tool_parameters",
                   "data_policy_training", "data_policy_retains_prompts", "pricing_json"]


def build_effective_pricing_rows(stats_records, scrape_date, scraped_at):
    rows = []
    for rec in stats_records:
        ep = rec.get("effective_pricing")
        if not ep:
            continue
        base = {
            "scrape_date": scrape_date, "scraped_at": scraped_at,
            "model_slug": rec["model_slug"], "permaslug": rec["permaslug"],
            "variant": rec["variant"],
            "weighted_input_price": ep.get("weightedInputPrice", ""),
            "weighted_output_price": ep.get("weightedOutputPrice", ""),
            "weighted_cache_hit_rate": ep.get("weightedCacheHitRate", ""),
        }
        summaries = ep.get("providerSummaries") or []
        if not summaries:
            rows.append({**base, "provider_name": "", "provider_slug": "",
                         "effective_input_price": "", "effective_output_price": "",
                         "cache_hit_rate": "", "total_tokens": ""})
        for s in summaries:
            rows.append({**base,
                         "provider_name": s.get("providerName", ""),
                         "provider_slug": s.get("providerSlug", ""),
                         "effective_input_price": s.get("effectiveInputPrice", ""),
                         "effective_output_price": s.get("effectiveOutputPrice", ""),
                         "cache_hit_rate": s.get("cacheHitRate", ""),
                         "total_tokens": s.get("totalTokens", "")})
    return rows


EFFECTIVE_FIELDS = ["scrape_date", "scraped_at", "model_slug", "permaslug", "variant",
                    "weighted_input_price", "weighted_output_price",
                    "weighted_cache_hit_rate", "provider_name", "provider_slug",
                    "effective_input_price", "effective_output_price",
                    "cache_hit_rate", "total_tokens"]


ACTIVITY_FIELDS = ["permaslug", "variant", "model_slug", "date",
                   "total_prompt_tokens", "total_completion_tokens",
                   "total_native_tokens_reasoning", "total_native_tokens_cached",
                   "request_count", "total_tool_calls",
                   "requests_with_tool_call_errors", "num_media_prompt",
                   "num_media_completion", "image_output_requests",
                   "last_scraped_at"]


def upsert_model_activity(stats_records, scraped_at):
    """Merge the rolling 31-day activity windows into one long CSV, keyed by
    (permaslug, variant, date). Later scrapes overwrite earlier values for the
    same day (the trailing partial day gets finalized on the next run)."""
    path = DATA / "csv" / "model_activity.csv"
    existing = {}
    if path.exists():
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                existing[(row["permaslug"], row["variant"], row["date"])] = row
    for rec in stats_records:
        activity = rec.get("activity")
        analytics = activity.get("analytics", []) if isinstance(activity, dict) else (activity or [])
        for a in analytics:
            date = (a.get("date") or "").split(" ")[0]
            key = (rec["permaslug"], rec["variant"], date)
            existing[key] = {
                "permaslug": rec["permaslug"], "variant": rec["variant"],
                "model_slug": rec["model_slug"], "date": date,
                "total_prompt_tokens": a.get("total_prompt_tokens", ""),
                "total_completion_tokens": a.get("total_completion_tokens", ""),
                "total_native_tokens_reasoning": a.get("total_native_tokens_reasoning", ""),
                "total_native_tokens_cached": a.get("total_native_tokens_cached", ""),
                "request_count": a.get("count", ""),
                "total_tool_calls": a.get("total_tool_calls", ""),
                "requests_with_tool_call_errors": a.get("requests_with_tool_call_errors", ""),
                "num_media_prompt": a.get("num_media_prompt", ""),
                "num_media_completion": a.get("num_media_completion", ""),
                "image_output_requests": a.get("image_output_requests", ""),
                "last_scraped_at": scraped_at,
            }
    rows = [existing[k] for k in sorted(existing)]
    write_csv(path, rows, ACTIVITY_FIELDS)
    return len(rows)


# ------------------------------------------------------------------------ main

def main():
    started = datetime.now(timezone.utc)
    scrape_date = started.strftime("%Y-%m-%d")
    scraped_at = started.isoformat(timespec="seconds")
    raw_dir = DATA / "raw" / scrape_date
    print(f"[{scraped_at}] scrape start")

    pub = fetch_json("/api/v1/models")["data"]
    catalog = fetch_json("/api/frontend/v1/catalog/models")["data"]
    providers = fetch_json("/api/frontend/v1/all-providers")["data"]
    print(f"public models: {len(pub)}, catalog models: {len(catalog)}, providers: {len(providers)}")

    write_gz_json(raw_dir / "models.json.gz", pub)
    write_gz_json(raw_dir / "catalog.json.gz", catalog)
    write_gz_json(raw_dir / "providers.json.gz", providers)

    # enumerate (slug, permaslug, variant) tasks; variants come from public model
    # id suffixes (":free") and from catalog entries (one entry per variant)
    variants = {}
    for m in pub:
        slug, variant = parse_variant(m["id"])
        variants.setdefault(slug, set()).add(variant)
    for c in catalog:
        ep_variant = (c.get("endpoint") or {}).get("variant")
        if c.get("slug") and ep_variant:
            variants.setdefault(c["slug"], set()).add(ep_variant)
    slug_to_permaslug, tasks, seen = {}, [], set()
    for c in catalog:
        slug, permaslug = c.get("slug"), c.get("permaslug")
        if not slug or not permaslug or slug in seen:
            continue
        seen.add(slug)
        slug_to_permaslug[slug] = permaslug
        for variant in sorted(variants.get(slug, {"standard"})):
            tasks.append((slug, permaslug, variant))
    print(f"model×variant tasks: {len(tasks)}")

    stats_records, done = [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_model_stats, t): t for t in tasks}
        for fut in as_completed(futures):
            stats_records.append(fut.result())
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(tasks)} models fetched")
    stats_records.sort(key=lambda r: (r["model_slug"], r["variant"]))

    # raw bundle: one JSON line per (model, variant)
    raw_path = raw_dir / "model_stats.jsonl.gz"
    with open(raw_path, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            for rec in stats_records:
                gz.write((json.dumps(rec, separators=(",", ":")) + "\n").encode())

    # normalized CSVs
    csv_dir = DATA / "csv"
    part = f"date={scrape_date}.csv"
    models_rows = build_models_rows(pub, slug_to_permaslug, scrape_date, scraped_at)
    write_csv(csv_dir / "models" / part, models_rows, MODELS_FIELDS)
    endpoint_rows = build_endpoint_rows(stats_records, scrape_date, scraped_at)
    write_csv(csv_dir / "endpoints" / part, endpoint_rows, ENDPOINT_FIELDS)
    effective_rows = build_effective_pricing_rows(stats_records, scrape_date, scraped_at)
    write_csv(csv_dir / "effective_pricing" / part, effective_rows, EFFECTIVE_FIELDS)
    activity_total = upsert_model_activity(stats_records, scraped_at)

    failures = [{"model_slug": r["model_slug"], "variant": r["variant"], "errors": r["errors"]}
                for r in stats_records if r["errors"]]
    finished = datetime.now(timezone.utc)
    manifest = {
        "scrape_date": scrape_date,
        "started_at": scraped_at,
        "finished_at": finished.isoformat(timespec="seconds"),
        "duration_seconds": round((finished - started).total_seconds(), 1),
        "counts": {
            "public_models": len(pub), "catalog_models": len(catalog),
            "providers": len(providers), "tasks": len(tasks),
            "models_csv_rows": len(models_rows),
            "endpoints_csv_rows": len(endpoint_rows),
            "effective_pricing_csv_rows": len(effective_rows),
            "model_activity_total_rows": activity_total,
            "failures": len(failures),
        },
        "failures": failures,
    }
    mpath = DATA / "manifest" / f"{scrape_date}.json"
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, indent=1))
    print(json.dumps(manifest["counts"], indent=1))
    print(f"done in {manifest['duration_seconds']}s, failures: {len(failures)}")

    if len(failures) > len(tasks) * 0.5:
        print("more than half of model fetches failed — flagging run as failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
