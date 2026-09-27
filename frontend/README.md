# Crypto Research Console (demo frontend)

A dependency-free, static frontend that demonstrates the research-first
workflow described in the `crypto-market-trading-analysis` skill: from an
analysis request, to a structured research report, to evidence inspection,
to a tracked decision record. **It is a UI prototype with demo data only —
no live research, market data, provider, or trading integration exists.**

## Running it locally

No package manager, build step, or credentials are required — only the
Python 3 standard library that is already used elsewhere in this repository.

From the repository root:

```bash
python3 -m http.server 8000
```

Then open **http://localhost:8000/frontend/** in a browser.

The frontend must be served over HTTP (not opened as a `file://` URL) because
it uses native ES modules and fetches `schemas/decision-state.schema.json`
from the repository root to keep its decision-state vocabulary aligned with
[`schemas/decision-state.schema.json`](../schemas/decision-state.schema.json).
If that fetch fails for any reason, the app falls back to an embedded copy of
the same enum so the UI still works.

## What you can do

- **Overview** — start an analysis, review six fixture market cards with
  sparklines, compare BTC/ETH/SOL/SEI in a labeled performance chart, and
  inspect recent decisions, the agent-aware watchlist, and activity.
- **New Analysis** — a searchable asset composer with Spot/Futures/Investment,
  a horizon selector, question examples, optional capital and risk controls,
  quick templates, recent searches, and collapsed request metadata.
- **Runs** — every demo run created in this browser session.
- **Decisions** — decision records saved from reports, each tracked toward a
  `thesis_result` of `pending` / `confirmed` / `invalidated` / `mixed`.
- **Watchlist** — assets a demo research agent is "watching", each with a
  short agent note and a link back to its latest report.
- **Evaluations** — a small, clearly separate summary of demo decision
  outcomes. This is illustrative only and is **not** the repository's
  `crypto_eval` evaluation harness.
- **Data Sources** — the list of demo data feeds used to generate evidence,
  all explicitly labeled "not connected".
- **Settings** — local-only presentation preferences (default horizon/risk
  lens, density, advanced-panel default) stored in `localStorage`, plus a
  "Reset demo data" action.

### The core interaction to try

1. From **Overview** or **New Analysis**, submit a prompt (or click a
   quick-start card). A demo run and report are generated instantly.
2. On the report, review the decision/confidence header, entry zone,
   invalidation, targets and horizon, the demo price-level chart, up to five
   rationale points, market structure, leverage, and the scenario map.
3. Click **Open evidence** to inspect the evidence ledger in its own panel,
   separate from the report, and filter it by evidence type, stance
   (bull/bear/neutral), or quality.
4. Click **Save decision** to create a decision record; it now appears under
   **Decisions** and is reflected on **Overview** and **Evaluations**.

The Overview chart has working Price / Market Cap / Volume selectors,
time-range buttons, and asset-series toggles. The report chart has working
time-range buttons, labeled candlesticks and volume, and price overlays for
the current price, primary/secondary entry zones, invalidation, and targets.
All chart values and OHLC/volume paths are synthetic fixtures.

## Demo limitations (read before relying on anything shown)

- **All market values, price paths, evidence, and agent notes are
  synthetically generated** by a seeded pseudo-random generator
  (`modules/generator.js`). Nothing is fetched from Binance, CoinMarketCap, or
  any other real provider.
- The visible **FIXTURE** notice distinguishes this local fixture mode from
  live data. Venue, model, as-of, and provider selections are recorded as
  request metadata only; they do not trigger external calls.
- **No live research is performed.** The console does not invoke a model,
  reasoning pipeline, or the `crypto-market-trading-analysis` skill itself; it
  only fabricates plausible-looking output in that skill's shape.
- **No investment recommendation is being made.** Every report and decision
  record is explicitly labeled as demo output.
- **No order placement, portfolio accounting, or PnL simulation exists.**
  "Save decision" only writes a structured, demo `decision-record`-shaped
  object to `localStorage`; it never places a trade or computes real returns.
- **State is browser-local and ephemeral.** Runs, decisions, and settings
  persist in `localStorage` for convenience across reloads, but are scoped to
  one browser and can be cleared any time via **Settings → Reset demo data**
  or by clearing site data.
- **Evaluations here are illustrative only** and separate from the
  repository's `crypto_eval` evaluation harness (`crypto_eval/`, `eval/`),
  which this frontend does not read from or write to.

## Data contract alignment

Presentation data is generated in the shape of the repository's existing
contracts so the UI is not just prose:

- Reports mirror [`schemas/analysis-output.schema.json`](../schemas/analysis-output.schema.json)
  (`state`, `bias`, `confidence`, `preferred_entry`, `invalidation`, `targets`,
  `reasons` capped at 5, `risk`, `scenario_map`, `monitoring_conditions`, …).
- Evidence mirrors [`schemas/evidence-ledger.schema.json`](../schemas/evidence-ledger.schema.json)
  (`claim`, `metric`, `source`, `evidence_type`, `quality`, `supports`, …).
- Saved decisions mirror [`schemas/decision-record.schema.json`](../schemas/decision-record.schema.json)
  (`decision_state`, `entry_zone`, `invalidation`, `targets`, `decisive_evidence`,
  `trigger`, `outcome`, `thesis_result`, …).
- The decision-state vocabulary is fetched at runtime from
  [`schemas/decision-state.schema.json`](../schemas/decision-state.schema.json)
  rather than re-declared, so the UI cannot silently drift from the canonical
  list.

This is presentation-shape alignment, not JSON Schema validation — the
frontend intentionally has no dependencies (including a schema validator).

## Service boundary

Views call the fixture-only services in `modules/services.js` for analysis,
market snapshots, runs, decisions, and evaluation summaries. Replace those
implementations with API-backed services when an application runtime becomes
available; the current build makes no external API calls and marks all
generated content as fixture data. It only fetches the local decision-state
schema from the same origin.

## Verifying changes

```bash
# Syntax-check every ES module (no dependencies required)
find frontend -name "*.js" -exec node --check {} \;

# Pure-logic, chart, and service-boundary tests (Node's built-in test runner)
node --test frontend/tests/generator.test.mjs frontend/tests/charts.test.mjs frontend/tests/services.test.mjs

# Structural smoke tests fitting the repository's existing Python tooling
python3 -m unittest discover -s tests -v
```

Automated checks above cover structure and generator logic only. **Manual
browser verification is still required** for interaction and layout:

1. Serve the app (`python3 -m http.server 8000` from the repo root) and open
   `http://localhost:8000/frontend/`.
2. At a desktop width (≈1280px+), confirm the persistent dark sidebar shows
   all eight destinations and highlights the active one while navigating.
3. Run the core interaction above (submit → report → evidence drawer →
   save decision) and confirm the Decisions/Overview/Evaluations views update.
4. Resize to a mobile width (≈375px) and confirm the sidebar collapses behind
   the top-right menu toggle, the layout reflows to a single column, and the
   same core interaction still works.
