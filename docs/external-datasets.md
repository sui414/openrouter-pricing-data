# External datasets (HuggingFace survey, 2026-07-20)

Complementary open datasets for the analysis phase. Not mirrored into this repo
(size); load directly from HF at analysis time, e.g.
`pd.read_parquet("hf://datasets/afhubbard/gpu-prices/prices/dt=2026-07-01/...")`.

## High value — actively maintained

### [afhubbard/gpu-prices](https://huggingface.co/datasets/afhubbard/gpu-prices) (CC-BY-4.0)
Daily parquet snapshots since **2026-01-12** (~6-month backfill vs our
2026-07-20 start). SKU-level GPU cloud pricing across hyperscalers (AWS, GCP,
Azure) **and** neoclouds (Lambda, RunPod, vast.ai, DataCrunch, Nebius, …), with
normalized `gpu_type`, canonicalized regions/coords, spot flags, and a quality
column. Much broader provider coverage than our vast.ai leg — the primary
external source for the GPU-cost correlation analysis and for backfilling
H1 2026.

### [venvoo/openrouter-uptime](https://huggingface.co/datasets/venvoo/openrouter-uptime) (MIT)
Per-endpoint OpenRouter uptime readings (~16 polls/day) + outage incident
edges, since **2026-07-04**, ongoing. Fills the reliability series we chose
not to collect (we only keep 30m/1d uptime points in daily endpoint rows).
Useful for outage → routing/market-share shift event studies.

### [gpurentalprices/gpu-rental-prices](https://huggingface.co/datasets/gpurentalprices/gpu-rental-prices) (CC-BY-4.0)
Daily posted-price scrapes (RunPod etc.) since 2026-07-05. Young; overlaps
afhubbard — keep as a cross-check for posted (non-marketplace) prices.

## Noted, low value

- `danielrosehill/Open-Router-API-Pricing-Analysis` — one-time Nov 2025
  OpenRouter pricing snapshot; point-in-time historical reference only.
- `labofsahil/LLM-Pricing-Data` (2024), `exalsius/gpu-prices` (2025-05) — stale.
- `ArtificialAnalysis/*` — eval benchmark datasets, not price series (we get
  their model-quality indices via OpenRouter's models API already).

## Blocked

- `t4run/openrouter-market-history` — friend's 5-min OpenRouter panel
  (private, 401). Would be the best OpenRouter backfill; ask for read access.

Attribution: CC-BY-4.0 sources require citation when published.
