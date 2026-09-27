# GPT-6 Luna VS Code Development Prompt

You are the lead engineer implementing the AI Paper Futures Trading Research Platform in this repository.

## Repository

https://github.com/myh-st/crypto-skills

## Required Branch

feature/ai-paper-futures-trading

Do not develop this feature on main.

## Reasoning

Use your highest available reasoning effort.

## Mission

Implement the paper-futures vertical slice described by the repository plans.

This is not a planning-only task.

Do not stop after:

- reading the repo
- writing an architecture document
- adding TODOs
- adding schemas
- creating empty modules
- mocking a dashboard
- completing only the happy path

Continue working until the end-to-end paper trading flow is functional and validated, or until there is a genuine external blocker that cannot be solved from the repository/runtime.

## Read First

Before editing code, read these files completely:

1. .goals/ai-paper-futures-trading/goal.md
2. docs/paper-futures-development-plan.md
3. docs/paper-futures-experiment-v1.md
4. docs/architecture.md
5. docs/evaluation.md
6. frontend/README.md
7. crypto_eval/market_data.py
8. crypto_eval/openai_runner.py
9. crypto_eval/contracts.py
10. crypto_eval/runner.py
11. crypto_eval/scoring.py
12. schemas/analysis-output.schema.json
13. schemas/decision-record.schema.json
14. schemas/decision-state.schema.json
15. skills/crypto-market-trading-analysis/SKILL.md
16. all relevant references under skills/crypto-market-trading-analysis/references/
17. current frontend modules and tests
18. current GitHub Actions workflow and repository validator

Also inspect the current git status, recent commits, project structure, and existing tests before changing anything.

The repository is the source of truth. Reuse current contracts/components instead of creating parallel implementations without need.

## Product Outcome

A user should be able to run the app locally and:

1. configure an AI provider;
2. enter an API credential through Settings without exposing it to the browser/database/logs;
3. test the provider connection;
4. save provider metadata securely;
5. create a PAPER futures experiment;
6. choose symbols;
7. select 15m decision timeframe;
8. use 1h and 4h context;
9. choose starting balance;
10. choose risk per trade;
11. choose leverage like a normal trading terminal;
12. choose schedule;
13. validate the experiment;
14. start/pause/resume/stop it;
15. leave the local runtime running without keeping the browser open;
16. process fully closed candles only;
17. collect real futures market data;
18. calculate deterministic features/signals;
19. invoke GPT only when signal gating requires it;
20. produce a validated TradingIntent;
21. pass that intent through deterministic risk rules;
22. create simulated futures orders/fills only when approved;
23. maintain a persistent virtual portfolio;
24. monitor open positions without unnecessary LLM calls;
25. calculate fees, slippage, funding, margin, PnL and liquidation;
26. view results in the frontend;
27. compare leverage cohorts;
28. inspect trade/risk history;
29. export the whole experiment for offline analysis.

LIVE exchange execution must remain impossible in this branch.

## Existing Work To Preserve

The repository already contains:

- point-in-time evaluation harness
- frozen predictions
- trigger-aware scoring
- baselines
- Binance spot market data
- OpenAI Responses runner
- frontend research console
- structured skill contracts

Do not replace these wholesale.

Extend them.

Keep crypto_eval focused on point-in-time decision evaluation.

Keep crypto-market-trading-analysis as a read-only reasoning skill.

Add paper execution/portfolio responsibilities in separate modules.

## Required High-Level Architecture

~~~text
Real Futures Market Data
        |
        v
Normalized Point-in-Time Snapshot
        |
        v
Deterministic Features / Signal Gate
        |
        +------ SKIP_LLM
        |
        v
AI Research + Crypto Skill
        |
        v
Validated TradingIntent
        |
        v
Deterministic Risk Engine
        |
        v
Paper Execution
        |
        v
Portfolio Accounting
        |
        v
Journal / Metrics / Evaluation / Export
~~~

The LLM is not the risk engine.

The LLM is not the account ledger.

The LLM is not the exchange adapter.

## Implementation Priorities

Implement in this order unless repository inspection shows a dependency requires a small reordering.

### 1. Baseline Verification

Run existing validators/tests before editing.

Record failures that already exist.

Do not silently attribute pre-existing failures to your changes.

### 2. Trading Contracts

Implement versioned schemas and Python contracts for at least:

- TradingIntent
- RiskDecision
- PaperOrder
- Fill
- Position
- PortfolioSnapshot
- TradingCycle
- ExperimentConfig
- ExportManifest

Execution-critical fields must be structured.

Never parse a prose rationale to decide quantity, side, leverage, stop or target.

### 3. Persistent Storage

Implement a local repository layer.

SQLite is appropriate for v1.

Persist:

- experiments
- schedules
- market snapshots/hashes
- features/signals
- AI decisions
- TradingIntent
- risk decisions
- paper orders
- fills
- positions
- funding events
- portfolio snapshots
- runtime events
- metrics
- exports

Implement schema versioning/migrations.

Do not store API secrets in SQLite.

### 4. Accounting Core

Implement and thoroughly test:

- wallet balance
- equity
- realized PnL
- unrealized PnL
- available margin
- used margin
- maintenance margin
- long/short
- isolated margin
- leverage
- fees
- slippage
- funding
- liquidation
- stop loss
- take profit
- reduce-only
- partial fill representation

Maintain accounting invariants after every state transition.

### 5. Deterministic Risk Engine

Implement before allowing a paper fill.

Required controls:

- risk per trade
- risk-based position sizing
- maximum leverage
- maximum position notional/concentration
- maximum concurrent positions
- maximum daily loss
- maximum portfolio drawdown
- maximum consecutive losses
- symbol allowlist
- stale data guard
- spread/slippage guard when data exists
- funding guard when funding exists
- minimum liquidation buffer
- kill switch
- duplicate-cycle guard

Risk result must be one of:

- APPROVED
- RESIZED
- REJECTED
- DEFERRED

Risk rejection must never create a fill.

### 6. Futures Market Data

Create an exchange-neutral futures provider interface.

Normalize where supported:

- OHLCV
- last/ticker
- mark price
- index price
- funding
- open interest
- order book/spread
- contract rules

Select an initial provider based on current official API documentation.

Gate futures is acceptable if it cleanly satisfies the required public market-data lanes.

Do not hardwire the entire engine to Gate semantics.

Do not pretend Bitkub supports futures/perpetual APIs if official current documentation does not.

Fail closed on stale, malformed, missing, future, duplicated, or non-contiguous required data.

All CI tests must use fixtures/mocks, never the live provider.

### 7. Multi-Timeframe Pipeline

Default experiment:

- decision: 15m
- trend context: 1h
- regime context: 4h
- execution/path monitor: 1m or finer when available

Initial history defaults:

- 1m: 7-14 days
- 15m: 30-60 days, prefer 60 for initial warm-up
- 1h: 90 days
- 4h: 180 days
- 1d: up to 365 days only if needed

The 15m model decision must use only a fully closed decision candle.

Default analysis starts 60 seconds after candle close.

Do not send all retained history to the LLM.

Generate bounded features and relevant recent context.

### 8. Feature and Signal Gate

Build a small deterministic feature layer.

Start with:

- trend
- momentum
- breakout/range position
- ATR/volatility
- volume participation
- BTC relative strength
- funding context if available
- OI context if available

Do not add dozens of indicators.

Produce structured reason codes.

Signal gate output:

- ANALYZE
- SKIP_LLM

Persist both.

Allow manual force analysis.

### 9. AI Provider Settings

Extend the frontend Settings page and backend.

Support:

- OpenAI Responses API
- Microsoft Foundry / Azure OpenAI Responses-compatible endpoint
- generic Responses-compatible endpoint when capability checks pass

Provider fields:

- provider type
- display name
- base URL
- model/deployment
- reasoning effort
- timeout
- provider-specific non-secret settings
- masked credential status

Implement Test Connection.

It should test as much as safely possible:

- endpoint reachability
- authentication
- model/deployment validity
- Responses API compatibility
- structured JSON output
- reasoning configuration where supported
- latency

Never send the raw key back to the browser.

Never write the raw key to logs.

Never store the raw key in localStorage.

Never store the raw key in application SQLite.

Prefer the OS credential store.

If secure persistence is not available, support environment-only secrets and make the UI clearly state that the secret is not persisted.

Do not invent your own reversible plaintext encryption and call it secure.

### 10. AI Research / TradingIntent

Reuse and extend the existing Responses runner architecture.

The configured model must return structured validated output.

The research layer can:

- reject/no-trade
- confirm a deterministic signal
- change timing state
- propose long/short setup
- define entry/stop/targets
- provide reason codes and concise rationale

Convert the result to a TradingIntent contract.

The risk engine remains authoritative.

### 11. Paper Execution

Support simulated:

- market
- limit
- stop
- take profit
- reduce-only

Use a deterministic execution model.

For ambiguous stop/target ordering:

- prefer 1m path data;
- if still ambiguous, use conservative ordering;
- never assume favorable intrabar ordering.

Version fee/slippage models.

Use actual observed funding where available.

### 12. Leverage Research

Primary account default: 3x.

Shadow cohorts:

- 1x
- 2x
- 3x
- 5x
- 10x

Run the same approved intent through each cohort without additional LLM calls.

This is an experiment, not a reason to encourage maximum leverage.

Keep each cohort's accounting independent and reproducible.

### 13. Scheduler

Implement a persistent local scheduler.

It must run without the browser tab.

For 15m:

- calculate canonical closed candle time;
- default run = close + 60 seconds;
- use deterministic cycle ID;
- lock against duplicate processing;
- record last and next cycle;
- handle restart safely.

Add:

- Start
- Pause
- Resume
- Stop

Position monitoring is separate from AI analysis.

It may run every 1m or stream-driven without invoking the model every minute.

### 14. Experiment Versioning

An experiment freezes:

- code/skill commit
- provider/model
- reasoning settings
- prompt version
- signal strategy version
- risk policy version
- data provider
- symbols
- timeframes
- data windows
- fee/slippage/funding rules
- starting balance
- primary leverage
- shadow leverage
- schedule

Material configuration changes create a new experiment version.

Never mutate old results so they look like they came from the new policy.

### 15. Frontend

Preserve the existing design language.

Do not redesign the whole product.

Add the minimum operational pages/sections necessary for the workflow.

Settings:
- provider management
- Test Connection
- secure credential status
- runtime status

Experiment setup:
- PAPER mode
- symbols
- TF
- 1h/4h context
- balance
- risk
- leverage buttons/selectors
- shadow leverage
- schedule
- provider/model
- Validate
- Save
- Start/Pause/Resume/Stop

Portfolio:
- balance
- equity
- realized/unrealized PnL
- fees
- funding
- drawdown
- open positions
- equity curve
- drawdown chart

Activity:
- signal
- AI decision
- TradingIntent
- risk decision
- order/fill
- exit
- error

Evaluation:
- Quant
- Luna
- Luna + Skill
- Hybrid
- leverage cohorts

Always show the current execution label, for example:

LIVE DATA / PAPER EXECUTION

Do not show fake performance as if real.

No dead buttons.

### 16. Export

Implement Export Experiment ZIP.

Required minimum files:

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

No secrets.

Include hashes and schema/version metadata.

Exported totals must reconcile with the database.

### 17. Experiment v1

Implement defaults from:

docs/paper-futures-experiment-v1.md

Important:

- $100 virtual starting balance
- 15m decisions
- 1h/4h context
- 3x primary leverage
- x1/x2/x3/x5/x10 shadow cohorts
- 1% risk/trade default
- 3 concurrent positions default
- BTC/ETH/SOL/SUI/SEI when provider supports them
- signal-gated LLM calls
- 7-day smoke
- 30-day first forward evaluation
- later 60-90 day evidence collection

Do not hard-code experimental defaults as universal trading truths.

## Security Requirements

This branch must not be capable of live trading.

No exchange order-creation code should point at a real trading endpoint.

No withdrawal functionality.

No wallet/private-key handling.

No exchange credentials need to be introduced for the paper vertical slice unless a read-only authenticated market lane is truly required.

Treat news/social/web text as untrusted content.

Model-generated text cannot invoke arbitrary tools or runtime commands.

All model outputs cross a schema validator before reaching deterministic components.

Backend binds to loopback by default.

Redact secrets from all errors and logs.

## Testing Requirements

Add comprehensive tests.

### Accounting

- long profit/loss
- short profit/loss
- fees
- funding
- slippage
- stop
- target
- liquidation
- margin
- partial fill
- reduce-only

### Risk

- sizing
- leverage cap
- risk/trade
- concentration
- concurrent positions
- daily loss
- drawdown
- consecutive losses
- stale data
- spread/slippage
- funding
- liquidation distance
- kill switch

### Runtime

- canonical 15m close
- close + delay scheduling
- duplicate-cycle prevention
- restart recovery
- pause/resume
- position monitor
- provider failures
- DB failure handling

### Providers

- OpenAI config
- Foundry config
- compatible endpoint config
- Test Connection success/failure
- authentication failure
- timeout/rate limit
- structured-output failure
- secret redaction

### Market data

- gap
- stale
- future data
- duplicate bars
- unsupported symbol
- missing lane
- point-in-time cutoff

### Integration

Create a deterministic end-to-end fixture:

~~~text
market fixture
  -> features
  -> signal gate
  -> mock AI decision
  -> TradingIntent
  -> risk
  -> paper fill
  -> future fixture bars
  -> stop/target/exit
  -> PnL
  -> portfolio
  -> metrics
  -> export
~~~

### Frontend

Test all visible controls added by this work.

No fake buttons.

## Required Invariants

At minimum verify continuously or in tests:

- equity = wallet balance + unrealized PnL
- fill quantity <= remaining order quantity
- reduce-only cannot increase exposure
- rejected risk cannot create a fill
- closed trade cannot realize twice
- same experiment/symbol/candle cannot execute twice
- leverage cannot exceed policy/instrument limit
- export totals reconcile with stored totals
- outcome data cannot leak into the decision snapshot

## Validation Commands

At minimum run:

~~~bash
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
node --test frontend/tests/*.test.mjs
~~~

Also run any new test/lint/typecheck/build commands introduced by your implementation.

Do not add live network calls to CI.

## Browser Verification

Run the local application.

Verify at least:

1. Settings loads.
2. Provider form works.
3. Test Connection failure is understandable with a missing/invalid credential.
4. Secret never appears in browser state/log output.
5. Paper experiment can be created.
6. TF 15m can be selected.
7. leverage can be selected.
8. experiment can start/pause/resume/stop.
9. status updates correctly.
10. portfolio renders.
11. open position details render.
12. activity/risk blocks render.
13. charts render with actual persisted experiment data or explicit fixture labels.
14. export downloads/creates a valid bundle.
15. mobile/narrow layout remains usable.

If browser automation is available, use it.

Fix visual/runtime defects rather than merely documenting them.

## Working Style

Work continuously.

Do not ask for approval after every phase.

Do not stop merely because one phase is complete.

Use small, logical commits while continuing development.

Before destructive operations or changes outside this branch/repository, stop.

Otherwise make reasonable engineering decisions and continue.

Prefer simple, auditable implementations over clever abstraction.

Avoid over-engineering infrastructure that the local v1 does not need.

But do not fake required behavior.

## Git Discipline

Confirm you are on:

feature/ai-paper-futures-trading

Do not force-push.

Do not rewrite unrelated existing history.

Preserve unrelated user work.

Commit meaningful checkpoints.

Push the completed branch when validation passes.

Do not merge into main unless explicitly instructed by the user.

## Completion Report

At the end, report concisely:

### Implemented
Major components completed.

### Runtime Flow
Exact local commands to start backend/frontend/runtime.

### Provider Setup
How to configure OpenAI / Foundry / compatible endpoint without exposing secrets.

### Paper Experiment
How to create and run EXP-001.

### Validation
Exact commands and pass/fail counts.

### Visual QA
Pages/workflows verified.

### Security
Confirm live execution remains disabled and how secrets are stored.

### Remaining Gaps
Only real blockers or intentionally deferred items, especially Testnet/LIVE.

### Git
Branch, final commit SHA, and push status.

## Final Success Criterion

The work is successful when the repository can run an always-on local PAPER experiment with real futures market data and a configurable AI provider, execute realistic simulated futures positions under deterministic risk controls, persist/recover state, show meaningful results, and export a complete audit bundle — while remaining incapable of real-money trading.
