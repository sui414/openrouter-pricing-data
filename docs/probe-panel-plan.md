# Route-calibration probe panel (parked — approved design, not yet armed)

Status 2026-07-19: user approved design + budget (~$0.50/day cap) but deferred
execution ("not yet"). Prereq to arm: OpenRouter API key with small credit
balance added as GitHub Actions secret `OPENROUTER_API_KEY`.

Adapted from a friend's "H96 paid router calibration" protocol (see the
`orcap-strategic-routing-simulation` folder, local-only / gitignored — his IP,
do not commit or republish). Idea: the scraped quote menus tell us what
providers *advertise*; small owned requests measure what the router *actually
does* and what is *actually billed*.

## Design (per daily run)

- Panel: ~8 multi-provider open-weight models (picked by provider count ×
  usage from our dataset; keep stable over time, amend by dated note).
- 4 request shapes: short chat / input-heavy / output-heavy / tool-call.
  Tiny requests: ~50–200 input tokens, max_tokens 16–64, unique nonce per
  request (no shared prompts across independent draws).
- 6 arms per model×shape:
  1–3. default routing ×3 (independent sessions) → observed router choice
  4. `provider: {sort: "price"}` → does explicit price-sort hit public cheapest?
  5. pin cheapest endpoint (`provider.order=[tag], allow_fallbacks:false`) → quote firmness
  6. pin second-cheapest endpoint → non-cheapest firmness
  (sticky session pair from the original design deferred.)
- Before any paid call: freeze the public endpoint menu (reuse perf_snapshot
  fetch), compute worst-case cost from menu prices; abort if > $0.50 stop-loss.
- After each call: resolve `GET /api/v1/generation?id=` → actual billed cost
  (`total_cost`), native token counts, selected provider, latency.

## Outputs

- `data/csv/probes/date=YYYY-MM-DD.csv`: one row per probe — model, shape, arm,
  frozen-menu snapshot ref, predicted cheapest quote, expected cost at menu
  price, selected provider, billed cost, billed/quoted ratio, native tokens,
  latency, http status. Persist prompt *hash* only — never prompts,
  completions, or session ids (friend's privacy contract, keep it).
- Raw generation metadata: `data/raw/probes/YYYY-MM-DD.jsonl.gz`.

## Analysis targets (phase 3)

- Billed-to-quote ratio distribution (quote firmness), esp. for discounted endpoints.
- Cost regret: billed vs cheapest compatible public quote (execution quality).
- Router choice model: fit `Pr(i) ∝ quote_i^(-eta)` vs OpenRouter's documented
  inverse-square default; compare predicted vs observed default selections.
- Price-sort validation and pin success rates.

## Cost estimate

8 models × 4 shapes × 6 arms = 192 probes/day ≈ $0.20–0.50/day ≈ ≤$15/mo.
