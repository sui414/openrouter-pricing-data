# AI Inference Market Dataset

Longitudinal dataset of **LLM inference pricing, routing performance, usage,
and GPU compute costs**, built for research into: model/provider market share
over time, price-cut & discount dynamics, routing price-execution quality, and
the correlation between inference prices and underlying GPU rental costs.

Everything is collected by GitHub Actions from public, unauthenticated JSON
APIs — no HTML scraping, no API keys, no inference spend. All collector
scripts are stdlib-only Python 3 (zero dependencies). Each run commits its
data to this repo; `git pull` is the only sync needed.

## Running jobs

| Job | Schedule (UTC) | Script | What it collects |
|---|---|---|---|
| Daily sweep | `0 15 * * *` ([scrape.yml](.github/workflows/scrape.yml)) | [scrape.py](scraper/scrape.py) | Full OpenRouter catalog: every model × variant × provider endpoint |
| | | [ornn_snapshot.py](scraper/ornn_snapshot.py) | ORNN GPU + token price indices |
| Hourly capture | `30 * * * *` ([hourly.yml](.github/workflows/hourly.yml)) | [capture.py](scraper/capture.py) | 11 samples @ 5-min spacing: OpenRouter endpoint perf/pricing; every 3rd sample: vast.ai GPU marketplace |
| Route probes | parked | [docs/probe-panel-plan.md](docs/probe-panel-plan.md) | Paid micro-probes measuring billed-vs-quoted price (awaiting API key; design + `$0.50/day` budget approved) |

GitHub cron lags 0–60 min past the nominal time; `scraped_at` timestamps in
the data are authoritative.

## Data sources & methodology

### 1. OpenRouter (inference market: prices, routing, performance, usage)

| Endpoint | Cadence | Notes |
|---|---|---|
| `/api/v1/models` (public) | daily | Headliner info, list pricing, Artificial Analysis benchmark indices |
| `/api/frontend/v1/catalog/models` | daily | Full catalog (~800 models incl. hidden/deprecated) with versioned `permaslug`; variants enumerated from `:free`-style suffixes + per-variant catalog entries |
| `/api/frontend/v1/all-providers` | daily | Provider directory: data policies, HQ country |
| `/api/frontend/v1/stats/endpoint` | daily (all models) + **5-min** (active models) | Per-provider endpoints: pricing incl. discounts, quantization, p50–p99 latency & throughput over a rolling 30-min window, request counts, status |
| `/api/frontend/v1/stats/effective-pricing` | daily | Price actually paid after caching, per provider + provider token volumes (= within-model provider market share) |
| `/api/frontend/v1/stats/model-activity` | daily | Rolling 31-day daily usage per model×variant: tokens, requests, tool calls |
| `/api/frontend/v1/rankings/models?view=month` | daily | Public per-model daily token panel (= cross-model market share, no auth needed) |

**Freshness methodology**: OpenRouter's frontend API sits behind a 5-minute
Cloudflare CDN cache. The capture job appends a random `_cb` query parameter,
forcing a cache MISS so every sample is origin-fresh (origin recomputes stats
every few seconds). GitHub throttles sub-hourly crons to ~1 firing/hour, so
the hourly capture job stays alive ~55 minutes and takes 11 samples at 5-min
spacing inside one run — reconstructing a true 5-minute series from a single
hourly Actions slot (technique borrowed from
[pluriholonomic/ai-routing](https://github.com/pluriholonomic/ai-routing)).

Rolling-window sources (`model-activity`, `rankings`) are **upserted** into
long tables keyed by (entity, date): each day's scrape overwrites the
overlapping window, so trailing partial days get finalized by the next run and
history accumulates beyond the API's own lookback.

### 2. vast.ai (GPU marketplace — spot-like offer book)

`PUT console.vast.ai/api/v0/search/asks/`, unauthenticated. The server caps
responses at 64 offers, so each sweep paginates with a `dph_total` price
cursor until exhaustion (~600 on-demand + ~800 interruptible offers). Sampled
**every 15 minutes** (inside the hourly capture): per-GPU-type aggregates
(min/p25/median/p75/max `$/GPU-hr`, offer & GPU counts, by rental type). Full
per-offer raw dump once daily. Prices are normalized to per-GPU `$/hr`
(`dph_total / num_gpus`).

### 3. ORNN (curated GPU & token price indices)

`dashboard.ornnai.com/api/*`, unauthenticated. **Daily grain** (the public
tier serves daily closes only; intraday params are ignored):

- GPU rental index (`$/hr`): H100 SXM, H200, A100 SXM4, RTX 5090, B200
  (RTX PRO 6000 WS is premium-gated; auto-collected if it opens up)
- Ornn Token Price Index (`$/M tokens`): Anthropic, OpenAI, Google, DeepSeek

Public history windows are limited (~3 months GPU, ~6 weeks tokens), so both
are upserted daily into long CSVs that accumulate the unlimited series.

## Layout

```
data/
├── raw/                                # gzipped raw API responses (schema-drift-proof archive)
│   ├── YYYY-MM-DD/                     #   daily: models, catalog, providers, rankings +
│   │   └── model_stats.jsonl.gz        #   1 line per model×variant {endpoints, effective_pricing, activity}
│   ├── vast/YYYY-MM-DD.jsonl.gz        #   daily full vast.ai offer book (trimmed fields)
│   └── ornn/YYYY-MM-DD.json.gz         #   daily ORNN responses
├── csv/                                # normalized, analysis-ready
│   ├── models/date=*.csv               # daily; 1 row/public model: list prices, context, modality, benchmarks, headliner_json
│   ├── endpoints/date=*.csv            # daily; 1 row/provider endpoint: prices, discount, quantization, perf percentiles, data policy
│   ├── effective_pricing/date=*.csv    # daily; 1 row/model×provider: effective prices, cache hit rate, token volumes
│   ├── model_activity.csv              # upserted daily; (permaslug, variant, date) usage panel
│   ├── model_rankings.csv              # upserted daily; (date, permaslug, variant) token rankings panel
│   ├── perf_5min/date=*/run=HHMM.csv.gz# 5-min grain; per-endpoint perf + live prices, 11 samples/run, ~24 runs/day
│   ├── perf_hourly/date=*.csv          # hourly grain (first sample of each run); prior days gzipped in place
│   ├── vast_hourly/date=*.csv          # 15-min grain; per-GPU-type price aggregates; prior days gzipped in place
│   ├── ornn_gpu_index.csv              # upserted daily; (gpu_type, timestamp) index panel
│   └── ornn_token_index.csv            # upserted daily; (lab, timestamp) index panel
└── manifest/YYYY-MM-DD.json            # daily run metadata: timing, row counts, failures
```

## Units & gotchas

- `models` / `endpoints` / `perf_*` prices are **USD per token** (×1e6 =
  `$/M tokens`); `effective_pricing` and the ORNN token index are already
  **USD per M tokens**.
- Endpoint `discount` is provider-set promotional pricing; listed prices are
  already post-discount (base = price / (1 − discount)).
- Latency in ms, throughput in tokens/s; percentiles are rolling 30-min
  windows — the 5-min series oversamples that window by design (request_count
  moves every few seconds, percentiles evolve continuously).
- Alias models (slugs starting `~`, e.g. `~anthropic/claude-fable-latest`)
  resolve to the same endpoints as their concrete model — dedupe on
  `endpoint_id` when aggregating.
- Token counts come from each provider's own tokenizer; cross-provider token
  comparisons are approximate.
- The trailing day in upserted panels is partial until the next day's run
  finalizes it.

## Loading

```python
import pandas as pd, glob
endpoints = pd.concat(pd.read_csv(f) for f in glob.glob("data/csv/endpoints/date=*.csv"))
perf5 = pd.concat(pd.read_csv(f) for f in glob.glob("data/csv/perf_5min/date=*/run=*.csv.gz"))
share = pd.read_csv("data/csv/model_rankings.csv")
gpus = pd.read_csv("data/csv/vast_hourly/date=2026-07-20.csv")
```

Manual runs: Actions tab → pick workflow → Run workflow. Local:
`python3 scraper/scrape.py` or `python3 scraper/capture.py --samples 2 --interval-seconds 10`.
