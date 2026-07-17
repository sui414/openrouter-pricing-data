# OpenRouter Pricing Time-Series Dataset

Daily scrape of LLM pricing, per-provider economics, performance, and usage data
from [OpenRouter](https://openrouter.ai), to build a longitudinal dataset for
analyzing model/provider market share and routing price-execution quality.

Runs daily at **15:00 UTC (8 AM Pacific)** via GitHub Actions
([scrape.yml](.github/workflows/scrape.yml)); each run commits the day's data.
Manual run: Actions tab → "Daily OpenRouter scrape" → Run workflow, or locally
`python3 scraper/scrape.py` (stdlib only, no dependencies).

## Data sources (all unauthenticated JSON APIs, no HTML scraping)

| API | Contents |
|---|---|
| `/api/v1/models` | Public model list: headliner info, list pricing, Artificial Analysis benchmark indices |
| `/api/frontend/v1/catalog/models` | Full catalog (~800 models incl. hidden/deprecated) with versioned `permaslug` |
| `/api/frontend/v1/all-providers` | Provider directory: data policies, HQ, status pages |
| `/api/frontend/v1/stats/endpoint` | Per-provider endpoints: pricing + discount, quantization, p50–p99 latency/throughput (30-min window), status |
| `/api/frontend/v1/stats/effective-pricing` | Average price actually paid after caching, per provider, + provider token volumes |
| `/api/frontend/v1/stats/model-activity` | Rolling 31-day daily usage: tokens, requests, tool calls |

## Layout

```
data/
├── raw/YYYY-MM-DD/            # gzipped raw API responses, exactly as returned
│   ├── models.json.gz         #   /api/v1/models
│   ├── catalog.json.gz        #   catalog/models
│   ├── providers.json.gz      #   all-providers
│   └── model_stats.jsonl.gz   #   1 line per (model, variant): {endpoints, effective_pricing, activity, errors}
├── csv/                       # normalized tables (all prices in $/token; ×1e6 = $/M tokens)
│   ├── models/date=YYYY-MM-DD.csv            # 1 row per public model: list pricing, context, modality, benchmarks, headliner_json
│   ├── endpoints/date=YYYY-MM-DD.csv         # 1 row per provider endpoint: prices, discount, quantization, latency/throughput percentiles
│   ├── effective_pricing/date=YYYY-MM-DD.csv # 1 row per model×provider: effective prices ($/M tokens), cache hit rate, total tokens
│   └── model_activity.csv                    # long table upserted daily, keyed (permaslug, variant, date)
└── manifest/YYYY-MM-DD.json   # run metadata: timing, row counts, failures
```

Notes:
- `endpoints` / `models` prices are **$ per token** (multiply by 1e6 for $/M);
  `effective_pricing` values are already **$ per million tokens** (as displayed on the site).
- Performance percentiles are a 30-minute snapshot at scrape time — the daily
  cadence turns them into a time series. Latency in ms, throughput in tokens/s.
- `model_activity.csv`'s trailing day is partial at scrape time; it is
  overwritten (finalized) by the next day's run.
- Alias models (slugs starting with `~`, e.g. `~anthropic/claude-fable-latest`)
  resolve to the same endpoints as their concrete model, so those endpoint rows
  appear twice per day — dedupe on `endpoint_id` (or drop `~` slugs) when
  aggregating.
- Loading a day in pandas:
  `pd.read_csv("data/csv/endpoints/date=2026-07-17.csv")`, or glob all
  partitions and `pd.concat`.
