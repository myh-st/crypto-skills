# Crypto Research Console and PAPER Futures Lab

A dependency-free, static frontend that demonstrates the research-first
workflow described in the `crypto-market-trading-analysis` skill: from an
analysis request, to a structured research report, to evidence inspection,
to a tracked decision record. The same static build supports a fixture-only
preview and opt-in loopback runtimes for read-only Binance Spot analysis and
forward paper evaluation, plus a separate PAPER futures research experience.
The futures page uses server-backed portfolio state, deterministic fixtures or
read-only public Binance USD-M data, typed provider adapters, deterministic
risk controls, and simulated fills only.

## Running it locally

No package manager or build step is required. To run the fixture-only preview
without any runtime or credentials:

From the repository root:

```bash
python3 -m http.server 8000
```

Then open **http://localhost:8000/frontend/** in a browser. This preview stays
in fixture mode and makes no external API calls.

### Connected local runtime

From the repository root, configure your key in the server environment only
(never in frontend files or browser storage) and start the local runtime:

```bash
# Set OPENAI_API_KEY in this server shell from your local secret store.
python3 -m crypto_eval serve --host 127.0.0.1 --port 8765 --interval 1h
```

Open **http://127.0.0.1:8765/frontend/**. The server serves the same-origin
API and static app on loopback. Use **New Analysis** with a Binance USDT Spot
symbol such as `BTC` or `BTCUSDT`; Analyze fetches closed public Spot klines,
freezes the skill/control predictions on one shared snapshot, and displays the
structured report plus pending horizon status. The Evaluations page can fetch
and score separate outcome candles only after the configured horizon closes.
The live chart uses only the frozen candles, plots their actual UTC time range,
OHLCV, and supplied decision/snapshot levels; **Fit all levels** expands the
price scale when a decision level is outside the visible candle range.

Runtime model defaults are `gpt-6-luna`, `https://api.openai.com/v1/responses`,
and reasoning effort `max`. Optional server-only environment settings are
`OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`, and
`OPENAI_TIMEOUT_SECONDS`. An Analyze request makes two model calls (paired
skill/control) and may incur charges. The frontend never receives the API key.

Only the connected Analyze flow uses real data. Existing Overview cards,
watchlist, demo decisions, and demo outcome counts remain visibly synthetic;
they are not market performance evidence. Spot OHLCV does not provide funding,
open interest, news, options, on-chain, macro, portfolio accounting, or trading.
The runtime places no orders. Use `docs/evaluation.md` for the archive and
forward CLI workflows.

## Running the paper futures runtime

From the repository root, run:

```bash
python3 -m crypto_eval paper-server
```

Then open **http://127.0.0.1:8765/**. With the PAPER server running, **Overview**
is the portfolio cockpit; the experiment controls live in **Research › Paper
Trading Lab**. The server
binds to loopback only and stores its SQLite database under the user's local
application-data directory. The first run uses deterministic fixture candles
and local mocked Jev/GPT adapters; no external model request is made. Select
**Binance USD-M public data** to use unauthenticated market-data endpoints.

External Jev and Responses-compatible providers accept an environment-variable
name, not an API key in the browser. Define the variable in the shell before
starting the server. Provider **Test connection** and real provider inference
are explicit external requests and may incur vendor usage; they are not needed
for fixture-based UI or integration tests. Credentials are never returned by
the API or included in exports.

The frontend must be served over HTTP (not opened as a `file://` URL) because
it uses native ES modules and fetches `schemas/decision-state.schema.json`
from the repository root to keep its decision-state vocabulary aligned with
[`schemas/decision-state.schema.json`](../schemas/decision-state.schema.json).
If that fetch fails for any reason, the app falls back to an embedded copy of
the same enum so the UI still works.

## What you can do

- **Overview** — with the PAPER server: the portfolio cockpit (equity, trading
  and economic PnL, drawdown, open risk, AI spend, attention queue, open
  positions, automation controls, changes since your last visit). Without a
  PAPER runtime it falls back to the research workspace, whose fixture cards are
  clearly labeled.
- **Portfolio** — PAPER Spot and PAPER Perpetual sections, allocation, exposure,
  equity, economics, and orders; the real Gate mirror is separate and read-only.
- **Trade** — exchange-backed Spot/Perpetual selector, live chart with PAPER plan
  overlays, current AI plan, and a risk-first *Simulate Long/Short/Buy/Sell*
  ticket sized by the server. The position drawer handles protection, reduce and
  close, authority modes, and structured AI re-plans.
- **Activity** — the unified AI / USER / SYSTEM journal plus attention history.
- **Research** — links to the research tools and the analysis workspace (market
  cards, performance chart, recent decisions, watchlist).
- **New Analysis** — a searchable asset composer with Spot/Futures/Investment,
  a horizon selector, question examples, optional capital and risk controls,
  quick templates, recent searches, and collapsed request metadata.
- **Runs** — fixture runs created in this browser session plus live forward
  paper analyses and their pending/scored statuses.
- **Decisions** — decision records saved from reports, each tracked toward a
  `thesis_result` of `pending` / `confirmed` / `invalidated` / `mixed`.
- **Watchlist** — assets a demo research agent is "watching", each with a
  short agent note and a link back to its latest report.
- **Evaluations** — fixture outcome summaries remain clearly labeled; the
  connected runtime separately lists forward cases and allows scoring only
  after each configured horizon closes.
- **Data Sources** — the list of demo data feeds used to generate evidence,
  all explicitly labeled "not connected".
- **Settings** — local-only presentation preferences (default horizon/risk
  lens, density, advanced-panel default) stored in `localStorage`, plus a
  "Reset demo data" action.
- **Research › Paper Trading Lab** — loopback runtime controls, EXP-001 settings, provider
  references, portfolio/equity, aligned arm and leverage evaluation, risk
  events, activity, and a secret-free experiment bundle. This page reads and
  writes the local server API; it does not use the browser demo store.

### The core interaction to try

1. In the fixture preview, submit a prompt (or click a quick-start card) to
   create an illustrative local report. In connected mode, submit a Binance
   Spot symbol in **New Analysis** to fetch a real closed-candle snapshot and
   invoke the paired skill/control runners server-side.
2. On the report, review the decision/confidence header, entry zone,
   invalidation, targets and horizon, the demo price-level chart, up to five
   rationale points, market structure, leverage, and the scenario map.
3. Click **Open evidence** to inspect the evidence ledger in its own panel,
   separate from the report, and filter it by evidence type, stance
   (bull/bear/neutral), or quality.
4. In fixture mode, click **Save decision** to create a local decision record.
   Live forward predictions are already frozen and are scored separately under
   **Evaluations** after their horizons close.

The Overview chart has working Price / Market Cap / Volume selectors,
time-range buttons, and asset-series toggles. The report chart has working
time-range buttons, labeled candlesticks and volume, and price overlays for
the current price, primary/secondary entry zones, invalidation, and targets.
Fixture charts use synthetic values; connected report charts use only the
closed candles supplied in their frozen point-in-time snapshot.

## Fixture-console limitations (read before relying on anything shown)

- Fixture price paths, overview cards, evidence, and demo agent notes are
  synthetically generated by `modules/generator.js`.
- The static fixture preview makes no external calls. Connected mode calls
  the local API for read-only Binance Spot candles and server-side GPT-6 Luna
  analysis. Credentials stay on the server; output is validated and API
  failures are shown explicitly rather than silently replaced with fixtures.
- Outputs are research-only and are not investment recommendations. Live
  forward decisions are point-in-time model outputs, not validated forecasts.
- The original research console does not invoke a model in fixture mode and has
  no portfolio accounting; **Paper Trading** separately tracks a virtual wallet
  and simulated fills. No mode places real-money orders.
- **Fixture state is browser-local and ephemeral.** Demo runs, decisions, and settings
  persist in `localStorage` for convenience across reloads, but are scoped to
  one browser and can be cleared any time via **Settings → Reset demo data**
  or by clearing site data.
- **Demo evaluation counts remain illustrative only.** Live forward cases are
  stored and scored by the server-side `crypto_eval` runtime; the synthetic
  fixture summaries are kept separate and never presented as those scores.

## Data contract alignment

Presentation data is generated in the shape of the repository's existing
contracts so the UI is not just prose:

- Reports mirror [`schemas/analysis-output.schema.json`](../schemas/analysis-output.schema.json)
  (`state`, `bias`, `confidence`, `preferred_entry`, `invalidation`, `targets`,
  `reasons` capped at 5, `risk`, `scenario_map`, `monitoring_conditions`, …).
- Evidence mirrors [`schemas/evidence-ledger.schema.json`](../schemas/evidence-ledger.schema.json)
  (`claim`, `metric`, `source`, `evidence_type`, `quality`, `supports`, …).
- Fixture saved decisions mirror [`schemas/decision-record.schema.json`](../schemas/decision-record.schema.json)
  (`decision_state`, `entry_zone`, `invalidation`, `targets`, `decisive_evidence`,
  `trigger`, `outcome`, `thesis_result`, …). Live forward outcomes instead use
  the append-only `crypto_eval` prediction/outcome contracts.
- The decision-state vocabulary is fetched at runtime from
  [`schemas/decision-state.schema.json`](../schemas/decision-state.schema.json)
  rather than re-declared, so the UI cannot silently drift from the canonical
  list.

This is presentation-shape alignment, not JSON Schema validation — the
frontend intentionally has no dependencies (including a schema validator).

## Service boundary

Views use `modules/services.js` as the explicit boundary. It probes the
same-origin `/api/status`: a 404 selects the original fixture experience; a
valid loopback runtime connects Analyze, forward-case listing, and matured
outcome scoring. The PAPER futures page uses `modules/paperApi.js` as a separate
same-origin client. An unreachable or malformed analysis runtime disables
analysis instead of silently generating fixture output, while overview demo
cards remain synthetic. Provider credentials are resolved server-side; the
browser sends only environment-variable references and never stores or sends an
Authorization header. No endpoint for real-money execution exists.

## Verifying changes

```bash
# Syntax-check every ES module (no dependencies required)
find frontend -name "*.js" -exec node --check {} \;

# Pure-logic, chart, and service-boundary tests (Node's built-in test runner)
node --test frontend/tests/*.test.mjs

# Structural smoke tests fitting the repository's existing Python tooling
python3 -m unittest discover -s tests -v
```

The CI-safe tests use offline market fixtures and mocked Jev/GPT adapters; they
do not contact model vendors or exchange order endpoints. Automated checks cover
structural contracts, service routing, generator logic, and real-vs-synthetic
chart boundaries. **Manual browser verification is still required** for
interaction and layout:

1. Serve the fixture preview (`python3 -m http.server 8000`) and open
   `http://localhost:8000/frontend/`, or start the connected runtime above.
2. At a desktop width (≈1280px+), confirm the persistent dark sidebar shows
   all destinations and highlights the active one while navigating.
3. In fixture mode, run submit → report → evidence drawer → save decision.
   In connected mode, Analyze a Spot symbol and verify the report displays its
   data cutoff and pending forward status.
4. Resize to a mobile width (≈375px) and confirm the sidebar collapses behind
   the top-right menu toggle, the layout reflows to a single column, and the
   same core interaction still works.
5. For the paper workflow, start `python3 -m crypto_eval paper-server`, open
   `http://127.0.0.1:8765/`, test both offline fixture providers, start EXP-001,
   run a closed-bar cycle, inspect the risk/portfolio panels, and download the
   export. Select a public market source only when a network smoke is intended.
