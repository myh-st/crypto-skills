# AI Portfolio Trading OS — Implementation Plan

## 1. Objective

Transform the current PAPER futures research lab into a usable AI-assisted portfolio/trading platform.

The product outcome is not "more AI". It is a coherent operating loop:

```text
Observe
  -> Discover
  -> Decide
  -> Trade
  -> Manage
  -> Override
  -> Review
  -> Learn
  -> Improve
```

The user and AI jointly manage a PAPER portfolio. AI can act autonomously when configured, but the user can always inspect, constrain, override, or take control.

This plan incorporates the latest repo state, Deep Research findings, and the product direction discussed after that research.

## 2. Implementation Priorities

The priority is not analytics polish first.

Build the trusted trading loop first:

```text
real exchange catalog/data
  -> choose instrument
  -> create AI/manual PAPER plan
  -> risk preview
  -> simulated order/fill
  -> position/holding
  -> manage protection/control
  -> close
  -> reconcile portfolio
  -> journal
```

Then layer Portfolio Brain, strategy tournament, learning, and richer analytics.

## 3. Current Baseline

Current main already includes:

- `paper-server`
- persistent SQLite runtime
- Gate USDT perpetual live REST/WS
- backend SSE relay
- Lightweight Charts 5.2.1
- Jev + GPT-6 Luna real integrations
- deterministic PAPER risk/execution
- scheduler and monitor
- AI budget/cost ledger
- optional read-only Gate account mirror
- export/evaluation
- current Overview and Paper Trading UI

Known frontend debt:

- `frontend/modules/views/paperTrading.js` is too large and mixes concerns.
- Overview contains remaining fixture-centric market presentation.
- Position management is incomplete.
- No first-class Spot PAPER domain.
- No exchange-backed unified selector for daily trading.
- No structured AI Re-plan.
- No per-position authority mode.
- Research/runtime internals are too visible in the daily workflow.

## 4. Target Repository Shape

Suggested additions/refactors:

```text
crypto_eval/
  market_catalog.py
  spot_market.py
  spot_accounting.py
  portfolio_brain.py
  position_manager.py
  paper_orders.py
  attention.py
  activity.py
  learning.py

frontend/modules/components/
  tradingStatusBar.js
  portfolioKpiStrip.js
  attentionQueue.js
  marketSelector.js
  liveTradingChart.js
  aiPlanPanel.js
  paperTradeTicket.js
  positionsTable.js
  positionDetailDrawer.js
  ordersPanel.js
  experimentToolbar.js
  aiCostSummary.js
  activityTimeline.js
  replanPanel.js
  strategyTournamentSummary.js

frontend/modules/views/
  overview.js
  portfolio.js
  trade.js
  activity.js
  research.js
  evaluations.js
  settings.js
```

Do not force these names when existing modules cleanly cover the same responsibility.

The goal is responsibility separation, not file-count growth.

## 5. Navigation Refactor

Recommended top-level navigation:

```text
Overview
Portfolio
Trade
Activity
Research
Evaluations
Settings
```

Map current routes:

- Runs / Decisions / Watchlist -> Research
- Paper Trading -> Trade + Portfolio
- provider/runtime controls -> Settings
- AI/risk/order journal -> Activity
- experiment-arm comparisons -> Evaluations

Preserve deep links where practical.

## 6. Overview Vertical Slice

Refactor Overview into the daily cockpit.

Primary strip:

- Equity
- Trading PnL
- Economic PnL
- Drawdown
- Open Risk
- AI Spend

Status line:

- LIVE/STALE market
- PAPER execution
- scheduler RUNNING/PAUSED
- next scan
- AI budget remaining

Attention queue:

- position near stop/liquidation
- AI re-plan proposal
- protect-profit opportunity
- stale feed
- scheduler failure
- budget warning
- provider failure
- order filled/expired/canceled

Open positions:

compact rows with Manage action.

Remove or demote fixture cards whenever live runtime data is available.

## 7. Normalized Market Catalog

### Backend

Create a normalized market-instrument contract.

Required fields:

```json
{
  "instrument_id": "gate:spot:BTC_USDT",
  "exchange": "gate",
  "market_type": "spot",
  "exchange_symbol": "BTC_USDT",
  "display_symbol": "BTC/USDT",
  "base": "BTC",
  "quote": "USDT",
  "settle": null,
  "status": "tradable",
  "price_tick": "0.01",
  "quantity_step": "0.000001",
  "min_quantity": "0.00001",
  "min_notional": "1",
  "max_leverage": null,
  "maintenance_rate": null,
  "funding_rate": null,
  "volume_24h_quote": "123456789",
  "change_24h": 0.031,
  "spread_bps": 1.2,
  "refreshed_at": "..."
}
```

Perpetual fills futures-only values; Spot leaves them null.

Add:

- `GET /api/markets?exchange=gate&market_type=spot|perpetual`
- optional `GET /api/markets/{instrument_id}`

Cache metadata server-side with explicit freshness.

### Frontend

Market selector:

- Spot / Perpetual tabs
- search
- favorites
- recent
- symbol
- 24h change
- volume
- spread/liquidity
- tradable state

No hard-coded asset catalog as production authority.

## 8. Spot PAPER Runtime

### Data

Add Gate Spot public:

- currency pairs
- tickers
- candles
- best bid/ask
- trades/orderbook only where needed

Use backend-owned transport and normalize into existing market event style.

### Accounting

Create Spot-specific wallet/holding accounting:

- quote balances
- base balances
- average cost
- realized PnL
- unrealized PnL
- allocation
- fees

No:

- leverage
- funding
- liquidation
- maintenance margin

### Orders

Support:

- market buy/sell
- limit buy/sell
- partial fill representation
- cancel/amend pending PAPER orders
- stop/plan layer if strategy uses it

### Risk

Spot risk rules:

- maximum allocation per asset
- maximum total deployed capital
- minimum cash reserve
- concentration
- available quote/base
- stale feed
- min order constraints

## 9. Unified PAPER Order API

Add an exchange-neutral request contract.

Example:

```json
{
  "client_request_id": "...",
  "experiment_id": "EXP-004",
  "instrument_id": "gate:perpetual:BTC_USDT",
  "source": "manual_ui",
  "order_type": "market",
  "action": "long",
  "risk_pct": 0.01,
  "limit_price": null,
  "stop_price": "66950",
  "targets": ["68300"],
  "requested_leverage": 3
}
```

For Spot:

```json
{
  "instrument_id": "gate:spot:BTC_USDT",
  "source": "manual_ui",
  "order_type": "market",
  "action": "buy",
  "quote_amount": "25",
  "limit_price": null
}
```

Add:

- `POST /api/orders/preview`
- `POST /api/orders`
- `GET /api/orders`
- `PATCH /api/orders/{id}`
- `DELETE /api/orders/{id}`

The browser never determines authoritative futures quantity.

Server risk/execution does.

## 10. Trade Ticket

### Futures

Default to risk-first:

- Long / Short
- Market / Limit
- Risk %
- Stop
- Target(s)
- Leverage

Derived:

- quantity
- notional
- margin
- max loss
- fee
- slippage
- liquidation buffer

### Spot

- Buy / Sell
- Market / Limit
- quote/base amount
- optional allocation target
- optional protection plan

Derived:

- quantity
- fee
- resulting allocation
- cash remaining

Button labels must explicitly say PAPER:

- Simulate Long
- Simulate Short
- Simulate Buy
- Simulate Sell

## 11. Position / Holding Manager

Create a single detail drawer with market-type-specific sections.

Backend additions:

- `PATCH /api/positions/{id}/protection`
- `POST /api/positions/{id}/reduce`
- `POST /api/positions/{id}/close`
- `POST /api/positions/{id}/management-mode`
- `POST /api/positions/{id}/replan`

Protection update must return:

- previous values
- new values
- risk before
- risk after
- snapshot used
- audit event ID

Risk-increasing changes require stronger confirmation in UI.

Risk-reducing changes should be fast.

## 12. Management Authority

Persist:

```text
AUTO_PAPER
RECOMMEND_ONLY
MANUAL_OVERRIDE
PAUSED
```

Each position/holding records:

- management_mode
- owner_source
- last_ai_review
- next_ai_review
- last_user_override
- active_plan_version

AUTO_PAPER:

AI may mutate PAPER position within deterministic policy.

RECOMMEND_ONLY:

AI creates proposal only.

MANUAL_OVERRIDE:

AI may recommend but cannot mutate.

PAUSED:

no AI management action; monitor remains deterministic.

## 13. AI Plan and Re-plan Contract

Add a versioned `PositionPlan`.

Suggested fields:

- plan_id
- position_id
- version
- state
- thesis_status
- entry thesis
- invalidation
- stop
- targets
- size/action
- management_mode
- portfolio impact
- risk_before
- risk_after
- reason_codes
- concise reasons
- evidence refs
- created_by
- created_at

Re-plan API returns proposal only unless AUTO_PAPER policy explicitly permits application.

Example response:

```json
{
  "proposal_id": "...",
  "current": {
    "stop": 148.0,
    "targets": [156, 163],
    "remaining_pct": 1.0
  },
  "proposed": {
    "stop": 150.5,
    "targets": [156, 160],
    "remaining_pct": 0.75
  },
  "risk_before_usdt": 0.86,
  "risk_after_usdt": 0.41,
  "portfolio_exposure_before": 0.47,
  "portfolio_exposure_after": 0.41,
  "reason_codes": [
    "MOMENTUM_WEAKENED",
    "BTC_RISK_INCREASED",
    "PROTECT_PROFIT"
  ]
}
```

UI:

- Apply
- Edit
- Reject

No long chat transcript required.

## 14. Portfolio Brain

Create deterministic + AI portfolio context.

Inputs:

- total PAPER equity
- cash
- spot allocation
- futures gross/net exposure
- risk-at-stop
- asset concentration
- correlated positions
- direction concentration
- drawdown
- market/BTC regime
- open orders
- AI budget
- recent arm/regime performance

Output:

`PortfolioDecision`

Possible actions/reason codes:

- ALLOW
- RESIZE
- BLOCK_CONCENTRATION
- BLOCK_CORRELATED_EXPOSURE
- HOLD_CASH
- PREFER_SPOT
- PREFER_PERPETUAL
- DE_RISK
- PROTECT_PROFIT
- REBALANCE

The Portfolio Brain does not bypass `RiskEngine`.

Think of it as an additional policy/decision layer before deterministic execution.

## 15. Autonomous PAPER Position Management

The existing scheduler scans opportunities.

Extend it with a position-review queue.

A position can be reviewed when:

- scheduled management candle closes;
- volatility/regime materially changes;
- price approaches stop/target;
- thesis evidence changes;
- portfolio exposure changes materially;
- user explicitly requests re-plan.

Do not invoke Luna on every price tick.

Use:

```text
deterministic monitor
  -> management trigger
  -> Jev
  -> optional Luna
  -> re-plan proposal/action
```

Budget guard remains mandatory.

## 16. Strategy Tournament

Expand aligned experiment arms to include:

- Quant
- Jev
- Luna
- Luna + Skill
- Hybrid
- Hybrid + Portfolio Brain

Where appropriate:

- primary portfolio
- shadow portfolio
- leverage cohorts

Add metrics:

- economic PnL after AI cost
- incremental PnL vs Quant
- incremental AI cost
- incremental economic value
- performance by regime
- performance by asset
- opportunity rejection value
- management-action value

Do not create causal metrics from unaligned samples.

## 17. Post-trade Learning

Add a `PostTradeReview`.

Inputs:

- original plan
- management events
- user interventions
- market path
- PnL/cost
- MFE/MAE
- portfolio context

Output:

- outcome category
- failure/benefit tags
- concise lesson
- candidate hypothesis for future experiment

Do not auto-edit active strategy/prompt from a single review.

Store candidate improvements separately.

## 18. Activity / Audit Model

Create unified event contract:

```json
{
  "event_id": "...",
  "timestamp": "...",
  "experiment_id": "...",
  "instrument_id": "...",
  "position_id": "...",
  "source": "AI|USER|SYSTEM",
  "category": "SIGNAL|DECISION|RISK|ORDER|FILL|MANAGEMENT|COST|ALERT|OUTCOME",
  "severity": "INFO|WATCH|ACTION|CRITICAL",
  "title": "...",
  "summary": "...",
  "payload_ref": "..."
}
```

Frontend timeline uses this instead of stitching many low-level tables together.

Keep raw technical events available for debugging separately.

## 19. Attention Engine

Derive attention items from runtime state.

Must be deterministic for critical system risk.

Examples:

- stale feed
- stopped scheduler
- provider unavailable
- budget near/exhausted
- position close to stop/liquidation
- pending AI proposal
- unreviewed manual override
- concentration breach

Deduplicate and auto-resolve attention items when the underlying condition clears.

## 20. Realtime UI

Preserve backend Gate WebSocket ownership.

Browser consumes:

- market SSE
- new runtime/activity SSE or equivalent

Use REST reconciliation periodically.

Do not open authenticated exchange connections in the browser.

## 21. Frontend Component Refactor

Refactor `paperTrading.js` first without changing behavior.

Suggested contracts:

```text
TradingStatusBar
  <- runtime, experiment, market health, budget

PortfolioKpiStrip
  <- portfolio, economics, risk

MarketSelector
  <- instruments, current selection

LiveTradingChart
  <- market stream, position, orders, plan

AiPlanPanel
  <- current plan / proposal

PaperTradeTicket
  <- instrument, preview, portfolio

PositionsTable
  <- unified position views

PositionDetailDrawer
  <- position, protection, management mode, plan

OrdersPanel
  <- pending/filled/canceled orders

AttentionQueue
  <- prioritized attention items

ActivityTimeline
  <- unified activity events
```

Keep pure render helpers testable.

## 22. Overview vs Trade vs Portfolio

Avoid duplicate dashboards.

Overview:
- summary + attention + open positions.

Portfolio:
- allocation/exposure/equity/risk.

Trade:
- selected instrument + chart + plan + ticket + symbol-specific position/orders.

Activity:
- journal.

Evaluations:
- comparative analytics.

## 23. Mobile / Accessibility

Desktop >= 1100:
- chart + right-side plan/ticket;
- positions below.

Tablet:
- stacked chart/ticket.

Mobile:
- tabs: Summary / Chart / Position / Actions.

Critical mobile actions:

- view risk
- edit stop
- reduce/close
- pause automation
- review/apply/reject re-plan
- acknowledge critical alert

Accessibility:

- explicit plus/minus and labels for PnL;
- no red/green-only meaning;
- visible focus;
- dialog/drawer focus management;
- meaningful button labels;
- no aria-live spam from price ticks;
- charts have textual data summary.

## 24. Confirmation Semantics

Avoid confirmation fatigue.

No extra confirmation for:
- viewing
- re-plan request
- risk-reducing stop update when clearly safe
- pause AI management

Confirm:
- position close
- large reduction
- risk-increasing stop expansion
- switching from manual to autonomous management
- stopping all automation with open positions if behavior changes monitoring

Confirmation must include:
- exact instrument
- exact action
- size/percentage
- projected paper execution/risk impact

## 25. Data Retention

Refine current single retention setting to per-timeframe policy:

- 1m: 14 days default
- 15m: 60 days
- 1h: 90 days
- 4h: 180 days
- 1d: 365 days

Immutable ledgers remain:

- decisions
- orders
- fills
- positions
- risk events
- management events
- AI usage/cost
- portfolio snapshots
- outcomes

Pruning raw bars must not alter historical results.

## 26. Export Additions

Include:

- portfolio-snapshots.csv
- spot-holdings.csv
- spot-wallets.csv
- position-plans.jsonl
- position-replans.jsonl
- management-events.jsonl
- activity.csv
- attention-events.csv
- post-trade-reviews.jsonl
- strategy-tournament.csv

Continue:

- hashes
- row counts
- schema versions
- secret scan
- provider/model metadata
- AI cost evidence

## 27. File-Level Work Map

P0 likely changes:

- `frontend/modules/views/paperTrading.js`
- `frontend/modules/views/overview.js`
- `frontend/modules/paperApi.js`
- `frontend/modules/contracts.js`
- `frontend/modules/components/liveChart.js`
- `frontend/modules/components/portfolioSummary.js`
- `frontend/app.js`
- `frontend/styles.css`
- `crypto_eval/paper_server.py`
- `crypto_eval/paper_runtime.py`
- `schemas/paper-dashboard.schema.json`

P0 new modules:

- market catalog
- Spot market/accounting
- position management
- portfolio brain
- attention/activity
- components described above
- schemas described in the goal

## 28. Implementation Sequence / Commit Strategy

Recommended commits:

1. refactor frontend without behavior changes
2. new navigation + Overview/Portfolio shell
3. normalized Gate catalog
4. Market selector
5. Spot market/accounting contracts
6. Spot PAPER order engine
7. unified order API + manual ticket
8. position management API + drawer
9. management authority state
10. AI re-plan
11. Portfolio Brain
12. activity/attention
13. strategy tournament/learning
14. mobile/a11y
15. export/docs/hardening

Keep commits logically reviewable.

Do not stop development after each commit.

## 29. Validation

Preserve:

```bash
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
node --test frontend/tests/*.test.mjs
find frontend -name '*.js' -exec node --check {} \;
python3 -m crypto_eval demo --out-dir <fresh-dir>
```

Add local browser acceptance.

Real provider/Gate public-market acceptance remains local only.

No paid/network calls in CI.

## 30. End-to-End Acceptance Scenario

The key acceptance test should be:

```text
Start local app
  -> Gate LIVE
  -> choose Spot ETH/USDT
  -> AI analyzes / or user Simulate Buy
  -> Spot holding appears
  -> Portfolio allocation updates
  -> request AI Re-plan
  -> inspect structured proposal
  -> apply partial sell
  -> journal records USER/AI/SYSTEM events

switch Perpetual BTC/USDT
  -> live chart
  -> Simulate Long / AI AUTO_PAPER entry
  -> position opens
  -> set AI-managed
  -> AI later proposes tighter stop
  -> apply automatically or review based on mode
  -> user takes manual override
  -> reduce 25%
  -> return control to AI
  -> position closes
  -> PnL/cost/economic PnL reconcile
  -> post-trade review generated
  -> strategy tournament updated
  -> export bundle reconciles
```

At every point:

- real Gate account remains read-only;
- no real-money write leaves the process.
