# AI Portfolio Trading OS (PAPER)

A local, loopback-only cockpit where the AI and the user jointly discover, plan, execute,
manage, review, and learn from **PAPER** trades across Spot and Perpetual markets. It is
built on the PAPER futures runtime described in
[`paper-futures-runtime.md`](paper-futures-runtime.md). That runtime (Gate REST/WebSocket,
Jev, GPT-6 Luna, deterministic risk, scheduler, monitor, AI cost ledger, budget guard,
Keychain, export, real-integration check) is unchanged and remains the foundation.

This is a research sandbox, not investment advice. Real Gate money-moving operations are
**blocked by design**: order create/amend/cancel, leverage or margin changes, transfers,
and withdrawals have no network path. The authenticated Gate client is GET-only with an
endpoint allowlist enforced before transport. The new modules make public GET requests
only, and a test asserts they contain no write transport.

## Start

```bash
python3 -m crypto_eval paper-server          # http://127.0.0.1:8765/
```

- **Fixture mode.** The default `EXP-001` uses the offline fixture catalog and fixture
  prices, labeled `FIXTURE`. Use it for mechanics and UI checks.
- **Real Gate public data.** Set **Research › Paper Trading Lab › Market data** to
  *Gate.io USDT perpetual* while the experiment is stopped. The Spot and Perpetual
  catalogs, quotes, and charts then come from Gate public endpoints.
- **Manual perpetual tickets** need Gate or fixture mode. Binance mode fails closed
  because the catalog is Gate-based.

## Information architecture

| Page | Purpose |
|------|---------|
| **Overview** | Equity, trading PnL, economic PnL after AI cost, drawdown, open risk, AI spend; attention queue; open positions; automation controls; changes since last visit; latest AI actions. |
| **Portfolio** | PAPER Spot and PAPER Perpetual shown together but categorized: allocation, exposure by asset, equity curve, economics, orders. The **REAL Gate Account Mirror — READ ONLY** is separate and is never merged into PAPER equity. A Portfolio Brain review is available on demand. |
| **Trade** | Spot/Perpetual exchange-backed selector with search, favorites, and recent picks; live chart with plan overlays (entry/avg, stop, TP1–3, liquidation for perps, pending limits, fills); current AI plan; risk-first PAPER ticket; the instrument's position, orders, and activity. On mobile it becomes Summary / Chart / Position / Actions tabs. |
| **Activity** | Unified AI / USER / SYSTEM journal with filters by symbol, source, event, market, and severity; attention history. |
| **Research** | Analysis runs, decisions, watchlist, data sources, and the **Paper Trading Lab** (experiment configuration, cycles, routing, arms, raw events). |
| **Evaluations** | Strategy tournament, learning tags, improvement hypotheses, research evaluations. |
| **Settings** | Portfolio policy, AI providers, exchange accounts (read-only keys in the OS store), cost and budgets, price book, preferences. |

Any `Manage` / `Review` control opens the **Position Detail Drawer**. `#/position/<ref>`
deep-links to it.

## Domain

### Instrument catalog (`market_catalog.py`)

`MarketInstrument` (`schemas/market-instrument.schema.json`) is normalized from Gate Spot
currency pairs + tickers and Gate USDT perpetual contracts + tickers.

- **Fields:** tick, step, min/max quantity and notional, fees, leverage and maintenance
  (perps), funding, 24h change, quote volume, spread, liquidity, status.
- **Caching:** metadata and statistics are cached server-side (stats ~30 s). Freshness is
  explicit and served stale-with-flag when the exchange is unavailable.
- **Fail closed:** new orders are refused for unknown, delisted, untradable, or
  metadata-older-than-6h instruments.
- **Fresh quotes:** every new entry needs a quote no older than 30 s — WebSocket for
  subscribed perps, otherwise REST.

### Spot PAPER (`spot_accounting.py`)

Spot is its own domain, not a perpetual at 1x.

- **Balances:** USDT quote cash plus base holdings, with a fee-inclusive average cost.
  Sells realize PnL against average cost; unrealized PnL = (mark − average cost) × quantity.
- **No futures mechanics:** there is no leverage, margin, funding, or liquidation.
- **Orders:** market buy/sell and limit buy/sell with reserved quote or base. Limit fills
  are partial, bounded by the 1-minute bar volume × participation. Pending orders can be
  cancelled or amended (cancel/replace).
- **Optional stop/target plan:** monitored on closed 1-minute bars; the stop wins a bar
  that touches both.
- **`SpotRiskEngine` checks:** fresh quote, exchange minimums, available quote and base,
  per-asset allocation, deployed capital, and the minimum cash reserve.

### Unified PAPER orders

- **Request:** `paper-order-request`. The perpetual ticket sends side, stop, 1–3 targets,
  risk %, and leverage — never a quantity. The server derives quantity, notional, margin,
  max loss, fees, slippage, and liquidation with the same `RiskEngine` and fill path as AI
  trades.
- **Preview:** `paper-order-preview` is server-authoritative. Buttons read *Simulate
  Long / Short / Buy / Sell*.
- **Idempotency:** requests are idempotent by `client_request_id`.
- **Risk rejection:** creates no order and no fill; it records a risk event and an
  activity entry.
- **Cancel vs close:** *Cancel* applies to pending orders, *Close* to positions. Amend is
  cancel/replace with full re-validation. Manual pending orders survive an AI pause or stop.

### Position manager and authority

Every open position or holding has a plan (`position-plan`) and a management mode.

| Mode | AI may |
|------|--------|
| `AUTO_PAPER` | manage within deterministic policy: tighten protection, reduce ≤ the configured fraction, close only when the thesis is broken; never increase risk |
| `RECOMMEND_ONLY` | propose only; nothing changes until the user applies |
| `MANUAL_OVERRIDE` | observe and advise; cannot mutate |
| `PAUSED` | nothing; deterministic stop/target monitoring continues |

- **Defaults:** AI-opened positions start `AUTO_PAPER`; user-opened positions start
  `RECOMMEND_ONLY` (both configurable).
- **Who can change authority:** only the user, and every change is journaled.
- **Confirmations** are required only for:
  - full close;
  - reduce > 50%;
  - risk-increasing protection changes;
  - `MANUAL_OVERRIDE` → `AUTO_PAPER`;
  - resuming AI management with open positions.

  The API answers `409` with the exact instrument and size so the UI can ask once.
- **Scale-out targets:** handled in the monitor, at most one partial per bar; the final
  target closes the remainder.
- **Audit:** every mutation writes an immutable `management_events` row plus an activity
  event.

### Structured AI re-plan

- **Quick intents:** tighten risk, protect profit, give it more room, reduce exposure,
  exit if thesis weakened, reassess from scratch.
- **Pipeline:** fresh quote → closed-bar features → deterministic baseline → Jev
  (`jev_replan`) → optional Luna (`gpt_replan`, strict JSON schema) → validated proposal.
- **Proposal contents** (`position-replan-proposal`): before/after stop, targets, size,
  risk at stop, locked PnL at stop, portfolio exposure, control mode, reason codes, and
  evidence.
- **Actions:** *Apply*, *Edit* (re-validated), *Reject*.
- **Refusals:**
  - stale data returns `blocked` and mutates nothing;
  - a budget block skips the paid call;
  - a position that changed after the proposal was made makes the proposal
    `STALE_PROPOSAL`.
- **Outputs:** typed fields only; no chain-of-thought is requested or shown.

### Autonomous position review

The monitor loop (every `monitor_interval_seconds`) runs:

1. Spot limit fills and Spot plans.
2. Management metadata.
3. Post-trade reviews.
4. The review queue.
5. Snapshots and attention.

**Review triggers:**
- the management interval elapsed;
- the stop or a target is within the configured proximity;
- portfolio exposure changed materially.

Reviews are rate-limited per position and run only while the scheduler is running. They
are skipped for `PAUSED` positions or when AI management is paused. Pipeline:
deterministic trigger → Jev → optional Luna → proposal. It is **never per price tick**.
Auto-apply happens only in `AUTO_PAPER` and only within policy.

### Portfolio Brain (`portfolio_brain.py`)

**Inputs:** equity, drawdown, cash, and each exposure's risk at stop and notional,
grouped into BTC / ETH / ALT correlation buckets.

**Typed outputs:**
- entry decision (`portfolio-brain-decision`): `ALLOW`, `RESIZE`,
  `BLOCK_CONCENTRATION`, `BLOCK_CORRELATED_EXPOSURE`, `HOLD_CASH`, `DE_RISK`;
- advisories: `PREFER_SPOT`, `PREFER_PERPETUAL`;
- portfolio review actions: `PROTECT_PROFIT`, `REBALANCE`, `REDUCE_CONCENTRATION`,
  `DE_RISK`, `HOLD`.

**Behavior:**
- It is binding for AI entries in the cycle and advisory (warnings) for manual tickets.
- It can only shrink (`size_multiplier` ≤ 1) or block. The `RiskEngine` still sizes and
  validates every order.
- Example: a good AVAX long is blocked when alt-long risk is already above the
  correlated limit.

The tournament adds a **`hybrid_brain`** arm. It reuses Hybrid's frozen decision and
shared AI calls and applies the Brain against its own wallet.

### AI Spot allocations (optional)

`Settings › Portfolio policy › ai_spot` is disabled by default.

- **Triggers:** `prefer_spot` (the Brain advises Spot, e.g., adverse funding) or
  `all_long`.
- **Sizing:** an approved long primary decision can open a PAPER Spot allocation
  (a % of Spot equity) with the decision's stop and target as the plan.
- **Behavior:**
  - source AI, `AUTO_PAPER`;
  - binding Brain, `SpotRiskEngine`;
  - pauses and emergency stop respected;
  - idempotent per cycle;
  - never fails the decision cycle.

### Automation

Overview controls:
- Start / Pause / Resume / Stop experiment.
- **Stop new entries**: AI entries only.
- **Pause AI management**.
- **Emergency stop**: blocks every new PAPER entry, AI or user; reducing and closing
  remain allowed.

Deterministic monitoring of open positions never stops.

### Activity and attention (`activity.py`)

**Activity** (`activity-event`) merges events the OS writes (orders, Spot fills,
management, re-plans, settings, reviews) with a read-time projection of the futures
runtime ledgers (signals, Jev, plans, Brain, risk, fills, closes, funding, runtime state).
Routine no-signal cycles and individual provider calls are not journaled as noise.

**Attention** (`attention-event`) is derived deterministically, deduplicated by key,
counted, and auto-resolved (`condition_cleared`) when the condition clears. INFO items
expire or resolve on acknowledgement.

| Severity | Conditions |
|----------|-----------|
| CRITICAL | feed stale with open perps, liquidation buffer, reconciliation failure, scheduler stall, budget exhausted |
| ACTION | near stop, protect-profit, pending re-plan, stale pending order, budget warning, emergency stop |
| WATCH | weakening thesis, correlated concentration, long manual override |
| INFO | recent fills and closes |

### Learning and tournament (`learning.py`)

**Post-trade reviews.** Each closed position or holding gets a typed review
(`post-trade-review`) with:
- outcome;
- R multiple;
- MFE/MAE from the observed 1-minute path (or `unavailable`);
- costs;
- AI cost;
- the decision stack;
- management actions;
- tags from `learning-tag`.

Human and AI override tags compare the actual result with the original plan's
counterfactual over the observed path.

**Hypotheses.** Hypotheses per tag are stored as candidates for the next experiment
version. The active strategy is never rewritten automatically.

**Tournament.** The tournament compares the aligned arms. For each arm it reports:
- closed-trade sample size, net PnL, expectancy, win rate, profit factor, max drawdown;
- fee / funding / slippage drag;
- AI calls and cost (shared Jev cost is attributed to every Jev-dependent arm);
- economic PnL (needs an FX policy);
- incremental PnL and cost vs Quant;
- breakdowns by regime, asset, and session;
- AI-filter helped/hurt counts.

**No arm is promoted on headline PnL.** Promotion requires ≥ 30 aligned closed trades
per arm, positive economic value after AI cost, and a drawdown no worse than the
baseline.

### Economics

- **Currencies:** trading PnL is USDT; AI cost is USD.
- **Economic PnL** = trading PnL − AI cost in USDT. It is shown only when a USD→USDT
  cost FX policy is configured (Settings › Cost & Budgets); otherwise it is reported
  unavailable with the reason.
- **Budget guard:** the existing hard budget guard is authoritative for every paid call,
  including re-plans.

## API (PAPER-local)

| Method | Route |
|--------|-------|
| GET | `/api/markets?market_type=&q=&quote=&tradable=&limit=&sort=`, `/api/markets/{instrument_id}`, `/api/markets/{instrument_id}/quote` |
| GET | `/api/market/candles?instrument_id=&interval=` (Spot/fixture charts), `/api/market/ticker?instrument_id=` |
| GET | `/api/portfolio`, `/api/portfolio/settings`, `/api/positions?status=`, `/api/positions/{ref}` |
| PATCH | `/api/positions/{ref}/protection` |
| POST | `/api/positions/{ref}/reduce`, `/close`, `/management-mode`, `/replan` |
| GET / POST | `/api/replans`, `/api/replans/{id}/apply`, `/api/replans/{id}/reject` |
| GET / POST / PATCH / DELETE | `/api/orders`, `/api/orders/preview`, `/api/orders/{ref}` |
| POST | `/api/portfolio/review`, `/api/portfolio/settings`, `/api/automation`, `/api/experiment/validate` |
| GET / POST | `/api/attention`, `/api/attention/{id}/ack`, `/api/activity?…`, `/api/tournament`, `/api/reviews`, `/api/management-events` |
| GET | `/api/runtime/summary`, `/api/runtime/stream` (SSE: portfolio headline, attention counts, new activity) |
| GET / POST | `/api/holdings`, `/api/holdings/manual`, `/api/holdings/manual/{id}/delete`, `/api/holdings/sync`, `/api/holdings/settings`, `/api/holdings/gate/validate` (real spot holdings; READ-ONLY Gate sync, never merged with PAPER; see [holdings.md](holdings.md)) |

- **References:** `perp:<position_id>`, `spot:<holding_id>`, `perp:<order_id>`,
  `spot:<order_id>`.
- **Confirmation-required actions** return `409 {code, confirmation_required, details}`.
- **Legacy:** raw futures position IDs are still accepted by `/reduce`.

## Contracts

`schemas/`: `market-instrument`, `spot-wallet`, `spot-holding`, `unified-position-view`,
`paper-order-request`, `paper-order-preview`, `paper-order-view`,
`position-protection-update`, `position-management-mode`, `position-plan`,
`position-replan-request`, `position-replan-proposal`, `portfolio-state`,
`portfolio-brain-decision`, `activity-event`, `attention-event`, `post-trade-review`,
`learning-tag`. `tests/test_portfolio_schemas.py` validates live runtime output against
them.

## Export additions

- **Files:** `portfolio-snapshots.csv`, `spot-wallets.csv`, `spot-holdings.csv`,
  `spot-orders.csv`, `spot-fills.csv`, `position-plans.jsonl`, `position-replans.jsonl`,
  `management-events.jsonl`, `activity.csv`, `attention-events.csv`,
  `post-trade-reviews.jsonl`, `improvement-hypotheses.jsonl`, `brain-decisions.jsonl`,
  `strategy-tournament.csv/json`, `portfolio-settings.json`.
- **Manifest:** schema versions, row counts, and per-file SHA-256.
- **Secret scan:** configured credential values are checked against every file, and the
  export is refused on a match.

## Validation

```bash
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v          # includes test_portfolio_os / test_portfolio_schemas
node --test frontend/tests/*.test.mjs             # includes portfolio-os.test.mjs
find frontend -name '*.js' -exec node --check {} \;
```

CI is offline: fixtures and fakes only. Local acceptance may use real Gate public data and
the configured Jev/Foundry providers:

```bash
python3 -m crypto_eval real-integration-check   # runtime: Gate REST/WS, Jev, Luna, PAPER smoke
python3 -m crypto_eval portfolio-real-check     # Portfolio OS: real Jev -> Luna re-plans, budget block, export scan
```

`portfolio-real-check` runs in an isolated database under the app data directory:

1. It opens a PAPER perp long and a Spot buy on live Gate quotes.
2. It requests a perp re-plan that must go through **real** Jev and GPT-6 Luna, with
   one real `jev_replan` and one real `gpt_replan` in the cost ledger. It then applies
   or rejects the proposal.
3. It runs a Spot re-plan through the real AI route.
4. It tightens the daily budget and proves that the next paid calls are refused before
   transport.
5. It exports a bundle that must pass the secret scan.

It writes a JSON summary next to the database.

`portfolio-real-check --full-loop [--max-candles N]` drives the full live loop:
- **Setup:** it tests the real providers, takes the 20 most liquid tradable Gate
  perpetuals as the universe, and sets an explicit FX policy.
- **Entry:** it runs the real decision stack (quant → Jev → optional Luna →
  Portfolio Brain → RiskEngine) on each new closed 15m candle until one produces an
  AI-approved PAPER entry.
- **Management:** that position then goes through AI review, human override and
  reduce, return to AI, and close.
- **Checks:** the journal sources, the post-trade review, and economic PnL.
- **No qualifying setup:** if the live market shows none within N candles, the entry is
  reported `NOT_VERIFIED`; no fixture is ever substituted.

It applies the same explicit acceptance budget as `real-integration-check`: $3.00 per
GPT call and $5.00 per day. The default $0.50 per-call cap is below the worst-case
reservation of a reasoning=max Luna call with a 32k-token output cap, so with default
budgets the guard refuses Luna re-plans before transport and the proposal falls back to
Jev plus the deterministic baseline, labeled `AI_BUDGET_BLOCK`.

## Known limits

- Perpetual limit orders fill fully on touch (existing runtime behavior); Spot limits
  model partial fills.
- Spot fees are charged in the quote currency (PAPER simplification).
- The Spot chart is backend REST polling (~5 s); perps use the backend WebSocket via SSE.
- Correlation uses coarse BTC / ETH / ALT buckets, not measured correlations.
