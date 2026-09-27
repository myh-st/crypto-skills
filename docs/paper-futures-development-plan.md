# Paper Futures Development Plan

## Objective

Build a local-first paper futures trading platform on top of the existing crypto research, evaluation, and frontend code.

The vertical slice must work end to end before broadening strategy coverage:

~~~text
Real Futures Data
      |
      v
Feature/Signal Gate
      |
      v
GPT-6 Luna + Crypto Skill
      |
      v
TradingIntent
      |
      v
Deterministic Risk Engine
      |
      v
Paper Execution
      |
      v
Persistent Virtual Portfolio
      |
      v
Journal / Metrics / Export / UI
~~~

Real-money order submission is out of scope for this branch.

---

## 1. Target Repository Layout

The exact names may be adjusted to fit existing code, but responsibilities must remain separated.

~~~text
crypto_trading/
  __init__.py
  contracts.py
  config.py

  market/
    base.py
    futures.py
    archive.py

  signals/
    features.py
    strategies.py
    gate.py

  risk/
    engine.py
    sizing.py
    policies.py
    reason_codes.py

  execution/
    paper.py
    fills.py
    fees.py
    funding.py
    liquidation.py

  portfolio/
    accounting.py
    models.py
    metrics.py

  runtime/
    cycle.py
    scheduler.py
    service.py
    locks.py

  storage/
    repository.py
    sqlite.py
    migrations.py

  providers/
    base.py
    openai_responses.py
    foundry_responses.py
    openai_compatible.py
    secrets.py

  export/
    bundle.py
    report.py
~~~

Existing crypto_eval remains the point-in-time evaluation package. Do not merge execution/portfolio behavior into crypto_eval.

The existing skill remains read-only reasoning. Do not put exchange order APIs into the skill.

---

## 2. Contracts First

Before runtime implementation, define versioned schemas and Python validators for:

- TradingIntent
- RiskDecision
- PaperOrder
- Fill
- Position
- PortfolioSnapshot
- FundingEvent
- TradingCycle
- ExperimentConfig
- ExperimentMetrics
- ExportManifest

Suggested schema paths:

~~~text
schemas/trading-intent.schema.json
schemas/risk-decision.schema.json
schemas/paper-order.schema.json
schemas/fill.schema.json
schemas/position.schema.json
schemas/portfolio-snapshot.schema.json
schemas/trading-cycle.schema.json
schemas/trading-experiment.schema.json
schemas/trading-export-manifest.schema.json
~~~

### TradingIntent requirements

A TradingIntent is not an order.

Required fields should cover:

- schema_version
- intent_id
- experiment_id
- run_id
- decision_id
- market_snapshot_hash
- symbol
- venue/instrument
- side
- decision_state
- entry specification
- invalidation/stop
- targets
- horizon
- requested leverage
- confidence
- reason codes
- model/provider metadata
- prompt version
- skill commit
- created_at

Execution-critical values must be typed fields, not extracted from prose.

---

## 3. Provider Settings and Secret Handling

### UI requirements

Settings > AI Providers:

- Add Provider
- Edit Provider
- Test Connection
- Save
- Delete
- Set Default

Fields:

- provider type
- display name
- base URL
- model/deployment
- reasoning effort
- timeout
- API version or provider-specific non-secret options
- credential status
- masked key state

Initial provider types:

1. OpenAI
2. Microsoft Foundry / Azure OpenAI Responses
3. Generic Responses-compatible endpoint

### Backend rules

Provider configuration and credentials are separate.

Persist non-secret configuration in application storage.

Secrets must use:

1. OS credential store when supported; or
2. environment variable reference.

Never persist raw secrets in:

- browser storage
- application SQLite
- experiment config
- logs
- exports
- prompts
- prediction records

If a secure secret store is not available, the UI may save provider metadata but must require the secret again through environment/runtime configuration.

### Test Connection

Test Connection must return a structured result such as:

~~~json
{
  "reachable": true,
  "authenticated": true,
  "model_available": true,
  "responses_api": true,
  "structured_output": true,
  "reasoning_mode": "supported",
  "latency_ms": 840
}
~~~

Do not expose provider response bodies containing sensitive headers or secrets.

A failed capability check must prevent Save as Active unless the user explicitly saves it as disabled/incomplete.

---

## 4. Market Data Architecture

Introduce a provider-neutral futures data model.

Required normalized lanes:

- OHLCV
- ticker/last
- mark price
- index price
- funding rate/history
- open interest
- order book/spread when available
- contract/instrument rules
- provider timestamps

Every observation carries:

- provider
- venue
- symbol/instrument
- observed_at
- retrieved_at
- data_cutoff
- quality/freshness
- content hash where persisted

### Initial futures provider

Choose one officially documented futures/perpetual public API that satisfies required data lanes. Gate futures is a valid candidate and also exposes a separate testnet API, but the implementation must follow current official documentation rather than assumptions.

Do not couple the core engine to Gate-specific symbol or contract semantics.

Bitkub support is not required for futures unless current official API documentation confirms the necessary futures functionality.

### Point-in-time rule

The 15m decision cycle sees only observations available by the cycle data cutoff.

Never use:

- current unfinished candle
- later funding event
- later news
- later OI snapshot
- later order book state
- realized outcome

to reconstruct an earlier decision.

---

## 5. Multi-Timeframe Data Defaults

Use these as configurable starting defaults:

| Lane | Initial retention/use |
|---|---:|
| 1m | 7-14 days |
| 15m | 30-60 days |
| 1h | 90 days |
| 4h | 180 days |
| 1d | up to 365 days |

Decision timeframe: 15m.

Context timeframes: 1h and 4h.

1m is primarily for execution-path fidelity, stop/target ordering, and detailed paper fills.

The model should not receive all retained bars. Compute bounded features and provide only relevant recent candles/feature summaries.

---

## 6. Feature and Signal Engine

The first version should be deliberately small.

### Feature families

- trend structure
- momentum
- breakout/range position
- ATR/realized volatility
- volume participation
- relative strength against BTC
- funding context
- open-interest change where available
- spread/liquidity state where available

### Signal gate

Each closed 15m candle produces deterministic features.

The gate decides:

- SKIP_LLM
- ANALYZE

Initial reasons may include:

- breakout candidate
- pullback into level
- volatility expansion
- momentum regime change
- unusual volume
- position requires reassessment
- manual force-analysis

All gate outcomes are persisted.

Skipping the LLM is not a failed trade; it is a first-class decision.

---

## 7. AI Research Layer

Reuse crypto_eval/openai_runner.py concepts instead of writing another ad-hoc model client.

Provider abstraction should produce the same validated research output regardless of OpenAI, Foundry, or compatible endpoint.

The LLM may:

- synthesize market regime
- challenge deterministic signals
- generate structured TradingIntent
- decline a weak trade
- select timing state
- identify evidence conflicts

The LLM must not:

- calculate authoritative portfolio balance
- bypass leverage/risk limits
- alter historical market data
- retrieve exchange secrets
- submit orders
- choose unsupported instruments
- fabricate missing funding/OI/order-book data

---

## 8. Risk-Based Position Sizing

Do not size by leverage alone.

Example:

~~~text
Equity = 100 USDT
Max risk = 1% = 1 USDT
Entry = 100
Stop = 98
Stop distance = 2%
~~~

Risk engine derives quantity such that stop loss is approximately 1 USDT before modeled costs.

Leverage determines margin requirement, not the acceptable loss budget.

Sizing must account for:

- entry
- stop
- fees
- slippage allowance
- instrument minimum quantity/notional
- maximum position concentration
- available margin

If the requested setup cannot satisfy constraints, return REJECTED or RESIZED.

---

## 9. Risk Engine

Risk decisions:

- APPROVED
- RESIZED
- REJECTED
- DEFERRED

Structured reason codes include at least:

- RISK_PER_TRADE_EXCEEDED
- POSITION_TOO_LARGE
- LEVERAGE_EXCEEDED
- INSUFFICIENT_MARGIN
- LIQUIDATION_TOO_CLOSE
- DAILY_LOSS_LIMIT
- DRAWDOWN_LIMIT
- MAX_CONCURRENT_POSITIONS
- MAX_CONSECUTIVE_LOSSES
- SYMBOL_NOT_ALLOWED
- STALE_MARKET_DATA
- SPREAD_TOO_WIDE
- SLIPPAGE_TOO_HIGH
- FUNDING_TOO_EXPENSIVE
- DATA_INCOMPLETE
- DUPLICATE_CYCLE
- KILL_SWITCH_ACTIVE

Risk settings are versioned as part of each experiment.

---

## 10. Paper Futures Engine

### Account model

Initial:

- USDT collateral
- isolated margin
- one-way positions
- long/short
- no cross-margin in v1

Track:

- wallet balance
- realized PnL
- unrealized PnL
- equity
- available margin
- used margin
- maintenance requirement
- liquidation estimate
- fees
- funding

### Order simulation

Support:

- market
- limit
- stop
- take-profit
- reduce-only

Model:

- order creation
- accepted/rejected
- partial/complete fill
- cancellation
- position update
- exit
- fee event
- funding event

### Execution fidelity

For forward paper mode:

- decision on closed 15m bar
- use 1m bars or finer current market data for fill/stop/target path
- if event ordering remains unknowable, choose conservative ordering
- never use favorable look-ahead within the same candle

Slippage models must be named/versioned and configurable.

---

## 11. Leverage Cohorts

Primary virtual account may run 3x.

For research, replay each eligible approved setup through shadow cohorts:

- 1x
- 2x
- 3x
- 5x
- 10x

Use:

- same signal
- same entry policy
- same stop
- same market path
- same fees/funding assumptions
- same base risk policy except leverage-specific margin/liquidation mechanics

Do not let each cohort ask the LLM independently.

The objective is to isolate leverage effects from decision variance.

---

## 12. Scheduler

### Default cycle

For a 15m strategy:

~~~text
18:15:00 candle closes
18:16:00 cycle starts
~~~

Default delay: 60 seconds.

Each cycle key should deterministically include:

- experiment_id
- symbol
- timeframe
- candle_close_time

A cycle key can execute at most once.

### Cycle flow

1. acquire cycle lock
2. resolve closed candle cutoff
3. fetch/validate market snapshot
4. persist snapshot/hash
5. compute features
6. run signal gate
7. invoke LLM only when needed
8. create TradingIntent
9. run risk engine
10. create paper order/fill when approved
11. update/reconcile portfolio
12. persist metrics/events
13. release lock

### Position monitor

Run separately from the analysis schedule.

It must:

- update mark price
- evaluate stop/TP
- evaluate liquidation
- apply funding at the correct time
- update unrealized PnL

It does not require an LLM call every minute.

---

## 13. Persistence

SQLite is sufficient for v1.

Use explicit repository interfaces.

Suggested logical tables:

- experiments
- experiment_versions
- provider_configs
- schedules
- cycles
- market_snapshots
- signal_snapshots
- research_decisions
- trading_intents
- risk_decisions
- paper_orders
- fills
- positions
- funding_events
- portfolio_snapshots
- daily_metrics
- runtime_events
- exports

Do not store secrets.

Include schema version/migrations.

Use transactions where one event updates multiple accounting objects.

---

## 14. Local Runtime

The backend must be able to run without an open browser.

Provide a simple documented command such as:

~~~bash
python -m crypto_trading.runtime
~~~

or the equivalent chosen implementation.

Requirements:

- bind to loopback by default
- serve API/frontend or coordinate with existing frontend
- scheduler thread/process survives browser refresh
- graceful shutdown
- startup reconciliation
- structured logs
- health endpoint
- runtime status endpoint

If adding runtime dependencies such as FastAPI is justified, keep them minimal and pinned. Do not build unnecessary custom networking if a small maintained dependency is safer.

---

## 15. Frontend Changes

Preserve current visual language.

### Settings

AI Providers:
- provider list
- provider form
- masked secret status
- Test Connection
- Save
- default selector

Runtime:
- local service status
- storage status
- scheduler status

### New Experiment

Required fields:

- mode: PAPER
- market provider
- symbols
- decision TF
- context TFs
- starting balance
- risk/trade
- max positions
- primary leverage
- shadow leverage cohorts
- signal gating on/off
- AI provider/model
- schedule
- data windows

Controls:

- Validate
- Save
- Start
- Pause
- Resume
- Stop
- Export

Every visible control must function.

### Portfolio

Show:

- balance
- equity
- realized PnL
- unrealized PnL
- max drawdown
- fees
- funding
- open positions
- equity curve
- drawdown chart

### Activity

Show:

- cycle time
- symbol
- gate decision
- AI decision
- risk result
- order/fill
- position/PnL
- error/reason code

### Evaluation

Compare:

- Quant
- Luna-only
- Luna + Skill
- Hybrid
- leverage cohorts

Only show comparisons with aligned periods/data and visible sample counts.

---

## 16. Export Bundle

Implement Export Experiment as ZIP.

Required:

~~~text
manifest.json
config.json
summary.md
metrics.json
trades.csv
positions.csv
equity.csv
pnl-by-day.csv
pnl-by-asset.csv
pnl-by-leverage.csv
decisions.jsonl
signals.jsonl
risk-events.csv
~~~

manifest.json must include:

- experiment ID/version
- export timestamp
- code/skill commit
- config hashes
- data provider IDs
- model/provider config excluding secrets
- schema versions
- file names and SHA-256 hashes

The bundle must be sufficient for offline review without the live local database.

---

## 17. Experiment Arms

Where practical, support:

### A. Quant

Deterministic strategy/signals only.

### B. Luna

Model receives the frozen snapshot without crypto skill instructions.

### C. Luna + Skill

Same model and snapshot with crypto skill instructions.

### D. Hybrid

Deterministic signal engine + Luna + crypto skill.

For paired model comparisons:

- identical dataset/snapshot
- identical model ID
- identical inference config
- only treatment instructions differ
- immutable predictions
- separate later outcomes

Do not compare different market windows as if paired.

---

## 18. Evaluation Layers

Keep these distinct.

### Decision quality

Did direction/timing/levels make sense against later market path?

### Signal quality

Did the deterministic signal predict the intended market behavior?

### Execution quality

Difference between intended and simulated fill/exit; slippage and path effects.

### Strategy PnL

Results of one strategy arm under a declared execution/risk model.

### Portfolio PnL

Cash/equity evolution with sizing, margin, concurrent positions, fees, funding, and liquidation.

Never call decision-quality return a portfolio backtest.

---

## 19. Security and Threat Model

### Secrets

- browser never reads raw secrets back
- server logs redact auth headers
- exports exclude credentials
- prompt data excludes secrets

### Prompt injection

Treat:

- news
- project text
- social posts
- API text metadata

as untrusted evidence.

Untrusted text cannot:

- call execution functions
- alter risk configuration
- access secret storage
- change schedule
- enable live mode

### Runaway behavior

Protect against:

- duplicate scheduled cycles
- model retry loops
- repeated rejected orders
- API error storms
- stale market feed
- storage divergence

Use circuit breakers and explicit retry budgets.

---

## 20. Test Matrix

### Unit

- long PnL
- short PnL
- fee calculation
- funding calculation
- slippage
- market fill
- limit fill
- partial fill
- stop
- target
- liquidation
- maintenance margin
- risk sizing
- leverage limit
- daily loss
- drawdown
- consecutive loss
- stale data
- duplicate cycle
- kill switch
- provider config validation
- secret redaction

### Invariants

- equity reconciliation
- no duplicate realization
- no overfill
- reduce-only cannot grow exposure
- rejected risk cannot fill
- duplicate cycle cannot trade
- export totals reconcile with DB

### Integration

Deterministic path:

~~~text
fixture market data
  -> signal
  -> mock LLM
  -> TradingIntent
  -> risk
  -> paper fill
  -> market path
  -> exit
  -> portfolio
  -> metrics
  -> export
~~~

### Failure

- LLM timeout
- malformed structured output
- provider unauthorized
- rate limit
- market-data gap
- stale mark
- missing funding
- DB write failure
- process restart
- scheduler duplicate
- unsupported symbol
- ambiguous intrabar stop/target

### Frontend

- provider add/test/save
- masked credentials
- create experiment
- leverage selection
- start/pause/resume/stop
- status refresh
- portfolio rendering
- export
- errors
- demo/live-data labels

---

## 21. CI Gates

Keep:

~~~bash
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
~~~

Also run frontend tests/checks already present.

Add trading tests.

CI rules:

- no paid OpenAI invocation
- no Microsoft Foundry invocation
- no live exchange request
- no real/testnet order
- deterministic fixtures only
- no secret required

---

## 22. Implementation Sequence

### Phase 0 - Discovery

- inspect current branch
- map existing frontend/runtime/eval contracts
- run current tests
- document exact baseline

### Phase 1 - Contracts + Storage

- trading schemas
- experiment config
- SQLite repository/migrations
- accounting models
- invariants/tests

### Phase 2 - Paper Engine + Risk

- position sizing
- risk engine
- order/fill simulator
- margin/liquidation
- fees/slippage/funding
- portfolio snapshots

Exit gate: a deterministic fixture can open/close long and short positions and reconcile exactly.

### Phase 3 - Futures Data

- provider-neutral futures interface
- initial provider
- archive/cache
- 1m/15m/1h/4h data
- mark/index/funding/OI where available

Exit gate: point-in-time snapshot can be reproduced by hash.

### Phase 4 - Provider Settings

- provider abstraction
- secret store
- Settings UI
- Test Connection
- model selection

Exit gate: provider can be validated without exposing a key to browser/storage/logs.

### Phase 5 - Signals + AI Intent

- compact feature engine
- signal gate
- skill/control/hybrid runner
- TradingIntent validation

Exit gate: same frozen snapshot produces auditable signals/intent and risk can approve/reject deterministically.

### Phase 6 - Scheduler

- candle-close scheduler
- cycle lock/idempotency
- position monitor
- restart recovery

Exit gate: run locally for an extended fixture clock without duplicate cycles/trades.

### Phase 7 - Frontend Operations

- experiment setup
- scheduler controls
- portfolio
- activity
- charts
- evaluation
- clear PAPER labels

Exit gate: complete workflow from UI without dead controls.

### Phase 8 - Export + Evaluation

- ZIP export
- experiment report
- leverage cohorts
- strategy-arm comparison

Exit gate: exported numbers reconcile with database and can be reviewed offline.

### Phase 9 - Hardening

- failure tests
- security review
- secret scanning
- restart/reconciliation
- docs
- CI

Do not proceed to exchange Testnet until this branch is stable.

---

## 23. Future Phase: Exchange Testnet

After PAPER is stable, create a separate goal/branch.

Gate currently documents separate live and TestNet API base URLs and perpetual futures APIs. A future adapter can use Testnet to validate exchange integration mechanics.

Testnet goals:

- API auth
- leverage setting
- create/cancel order
- client order ID/idempotency
- partial fills
- WebSocket user stream
- reconnect
- rate-limit handling
- clock drift
- reconciliation

Do not use testnet PnL as evidence that a strategy has market alpha.

---

## 24. Future Phase: Live Trading

Not part of this branch.

Before live trading:

- prospective paper evidence
- stable accounting
- stable reconciliation
- no unresolved critical security issues
- explicit user approval
- trade-only API key
- withdrawals disabled
- IP allowlisting where supported
- hard notional/daily-loss limits
- kill switch
- manual emergency stop

LIVE_TRADING must default to false even after a future live adapter exists.

---

## 25. Definition of Complete Engineering Work

Do not stop after:

- architecture
- TODOs
- schemas only
- mocked UI
- one successful happy path

The implementation is complete only when the vertical slice actually runs, persists, schedules, simulates, evaluates, exports, and passes tests while live order execution remains impossible.
