# Local PAPER Futures Research Runtime

This runtime is a research sandbox, not an exchange connector or investment
recommendation. It has no order-submission endpoint, exchange account
credentials, testnet adapter, or live-money execution mode. Its only exchange
traffic is optional, unauthenticated Binance USD-M market-data GET requests.

## Start

From the repository root:

```bash
python3 -m crypto_eval paper-server
```

Open <http://127.0.0.1:8765/> and choose **Paper Trading**. The HTTP server
binds to `127.0.0.1` by default and can be bound to `::1`; non-loopback hosts
are rejected. SQLite is stored under the operating system's user application
data directory. To use a disposable local database for a smoke test:

```bash
python3 -m crypto_eval paper-server --database .paper-futures-smoke.sqlite3
```

The web process owns a 15-minute scheduler and an independent bar-monitor
worker. They continue while the browser is closed, subject to the local server
process remaining up. Running experiments resume after a server restart when
`auto_resume` is enabled. A cycle key is unique per experiment, symbol, and
closed 15m candle; a retry or duplicate click returns the stored cycle without
re-running Jev or GPT. Pausing stops new decision cycles but does not stop the
paper position monitor. Stopping prevents new cycles and leaves the monitor
available for open positions.

The public 1m monitor backfills up to 20,000 closed bars after a process gap.
Larger or incomplete gaps are surfaced as monitor errors rather than simulated
with a fabricated path.

## EXP-001 defaults

The first run is `PAPER`, uses offline fixture candles and local fixture AI
adapters, and makes no external model requests. Its defaults are $100 USDT,
BTC/ETH/SOL/SUI/SEI USDT-margined perpetuals, 15m decisions, 1h/4h context,
closed candle plus 60 seconds, 1% risk per trade, three primary positions,
3x primary leverage, a 5 USDT minimum paper notional, and parallel
1x/2x/3x/5x/10x leverage wallets. The evaluation arms are Quant, Jev, Luna,
Luna + Skill, Quant + Jev, and Hybrid.
All configured arms read one frozen snapshot per symbol/cycle; one Jev result
is shared by Jev-dependent arms. GPT calls are keyed by their distinct,
declared research treatment, not retried as duplicate cycle work.

Material EXP-001 configuration and provider settings are frozen after the
first cycle. This avoids silently changing a prospective experiment's risk,
data, model, or pricing assumptions while retaining the same experiment ID.

## Market data and point-in-time boundary

Choose either:

- **Deterministic fixture** — seeded, synthetic, reproducible 1m/15m/1h/4h
  candles, funding, and OI observations. Fixture output is labeled `FIXTURE`;
  it is for mechanics, UI, and test verification only.
- **Binance USD-M public** — read-only requests to public `/fapi/v1/klines`,
  `/fapi/v1/premiumIndex`, and `/futures/data/openInterestHist` endpoints.
  API keys are not required. Unsupported symbols, stale observations,
  incomplete bars, and unavailable optional lanes fail closed or remain
  explicitly unavailable. Before starting in this mode, the runtime checks
  the selected universe against exchange metadata and records excluded
  instruments with a reason.

The decision state contains only closed 15m bars and closed 1h/4h context no
later than `data_cutoff`. The 1m path is used only for paper execution and
position monitoring; monitor bars, future labels, outcomes, and PnL are never
included in Jev or GPT prompts. Public OI is used only from completed 1h
intervals. News, options, on-chain, liquidation maps, and order-book depth are
not fabricated when absent.

## Decision, sizing, and paper execution

```text
closed market snapshot → deterministic features / quant gate
    → Jev Choice / Score / Noul vector
    → versioned deterministic escalation policy
    → optional Responses-compatible GPT + local trading skill
    → validated TradingIntent
    → deterministic risk and isolated-margin PAPER execution
```

The escalation thresholds are stored with the experiment's
`jev-escalation.v1` policy: critical confidence, evidence conflict, borderline
setup, funding/OI/liquidity concern, and leverage stress. Jev's
`escalation_needed` answer contributes evidence but cannot independently
authorize or bypass the router.

Features include multi-timeframe EMAs, ATR, RSI, volume z-score, and a
prior-range breakout. Fast-path price levels are derived from observed ATR;
GPT cannot set quantity, leverage, or override risk. The risk engine enforces
freshness, symbol allowlist, signal eligibility, max positions, daily loss,
drawdown, loss streak, 1–10x leverage caps, minimum notional, risk-per-trade sizing, fees,
slippage, available isolated margin, and liquidation-before-stop rejection.
Risk rejection never creates an order or fill.

Market entries include configured slippage and taker fees. Limit entries stay
pending until a future execution bar touches the limit; maker fees and bounded
fills are recorded. Stops, liquidation, and reduce-only market exits use
adverse slippage; targets are conservatively checked after stop/liquidation
conditions. If one bar touches both stop and target, the stop wins. Funding is
settled at 8-hour boundaries using the latest observation available to the
monitor. Closed PnL is recorded once; cash, unrealized PnL, margin, fees,
funding, and slippage are persisted separately.

The main wallet, six arm wallets, and leverage cohorts are independently
accounted in SQLite. Arm comparisons reuse the same snapshot and execution
assumptions. No outcome-quality score is shown until a later, separate outcome
window is observable. Fixture results and missing sample sizes are explicit;
unavailable cost estimates and empty samples display as unavailable rather
than as invented values.

## Provider configuration

The Settings form saves provider metadata and an environment-variable *name*
only. It never accepts a credential value. The local API, browser store,
SQLite, prompts, logs, and export contain no raw credential values; exports
omit environment-variable names as well. Define the referenced value in the
server process environment before starting the server. For example, set your
own variable in your shell, then restart the server; do not paste credentials
into source, browser forms, logs, or this document.

Supported providers:

- **TypeSafe Jev** — isolated adapter for `POST /v1/systemone` with
  `state`, configurable model (default `jev-latest`), and atomic Choice,
  Score, and Noul questions. Choice/Score confidence and probability
  distributions are validated and persisted in a versioned decision vector.
  Its `Authorization` value can be configured as Bearer or raw key to match
  the exact scheme shown for the TypeSafe workspace; the connection test
  validates the selected format.
- **OpenAI Responses** — `/responses` structured JSON Schema requests.
- **Microsoft Foundry / Azure OpenAI** — Responses-compatible endpoint and
  `api-key` header; configure the exact endpoint/API version for the selected
  resource.
- **Generic Responses-compatible** — capability-checked with a structured
  connection test before use.

The provider `Test connection` controls make one explicit vendor request and
may incur usage. Fixture-provider tests are local and free of network calls.
Request timeouts and response failures are reduced to sanitized status
messages. The runtime does not silently approve trades when Jev/GPT is
unavailable: the configured Jev fallback is `SKIP`, `DEFER`, or an explicit
GPT fallback, and risk remains fail-closed.

Provider price rates are optional, versioned configuration. Without a pricing
version and usage counts, cost is reported as unpriced. No current vendor rate
is hard-coded as historical cost.

## Export

**Export bundle** downloads a ZIP containing a manifest, sanitized config,
summary, primary/arm/leverage wallets, orders, fills, trades, positions, equity,
daily / asset / leverage PnL, frozen decisions, signals, Jev vectors, escalation
events, provider usage/cost/latency, and risk events. The manifest reports
sample denominators and reconciliation checks. Provider environment-variable
references and all secret values are omitted.

## Validation

```bash
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
node --test frontend/tests/*.test.mjs
```

All AI and exchange behaviors in tests use fakes or deterministic fixtures.
The local browser smoke can use the same fixture providers; it does not require
credentials or make a paid model call. A public Binance data smoke is optional
and separate from provider testing.
