# Goal: AI Paper Futures Trading Research Platform

## Branch

feature/ai-paper-futures-trading

Base commit at goal creation:

887ced89866fb53748519170d19b3080cadab230

## Product Goal

Extend the current read-only crypto research and evaluation system into a local-first, always-on paper futures trading research platform.

The first production-like mode is:

- real market data
- GPT-6 Luna or another configured Responses-compatible model
- crypto-market-trading-analysis skill
- deterministic signal, risk, execution, and portfolio accounting
- simulated futures orders only
- persistent local state
- scheduled analysis on closed candles
- measurable evaluation and export

The system must answer whether an AI-assisted strategy has repeatable positive expectancy after realistic costs. It must not optimize for trading frequency or claim profitability from fixture/demo results.

## Existing Components To Preserve

The branch starts with working components already present in the repository:

- crypto_eval point-in-time evaluation harness
- immutable prediction and outcome separation
- trigger-aware scoring and baselines
- crypto_eval/market_data.py
- crypto_eval/openai_runner.py
- frontend research console
- crypto-market-trading-analysis skill
- repository validators and deterministic CI

Do not rebuild these from scratch. Extend them without weakening their current point-in-time and schema guarantees.

## Required Runtime Boundary

~~~text
Market / Futures Data
        |
        v
Feature + Signal Engine
        |
        v
GPT / Skill Research Layer
        |
        v
Structured TradingIntent
        |
        v
Deterministic Risk Engine
        |
        v
Paper Execution Engine
        |
        v
Virtual Portfolio + Journal
        |
        v
Evaluation + Export
~~~

The LLM may propose or filter trades. It must never directly own exchange credentials, bypass risk rules, calculate authoritative account state, or send live orders.

## Initial Operating Profile

- Mode: PAPER
- Starting balance: 100 USDT
- Decision timeframe: 15m
- Context timeframes: 1h and 4h
- Execution/position monitoring: 1m or better public market updates when available
- Scheduler: run once after each fully closed 15m candle, default 60-second delay
- Primary leverage: 3x
- Shadow leverage cohorts: 1x, 2x, 3x, 5x, 10x
- Initial symbols: BTCUSDT, ETHUSDT, SOLUSDT, SUIUSDT, SEIUSDT where supported by the selected futures provider
- Risk per trade default: 1% of equity
- Max concurrent positions default: 3
- Live trading: disabled

All defaults must be configurable. Unsupported symbols or unavailable market lanes must be marked unavailable rather than silently substituted.

## Acceptance Criteria

### A. Local Runtime

- [ ] The app can run continuously on a local machine without requiring the browser tab to stay active.
- [ ] Runtime state survives browser refresh and backend restart.
- [ ] Scheduler state is persisted and restart-safe.
- [ ] Duplicate scheduler invocations for the same experiment, symbol, and candle close are idempotently rejected.
- [ ] Start, pause, resume, and stop controls are functional.
- [ ] The UI displays runtime state, last cycle, next cycle, and last error.

### B. AI Provider Settings

- [ ] Add a provider settings UI and backend service.
- [ ] Support OpenAI Responses API.
- [ ] Support Microsoft Foundry / Azure OpenAI Responses-compatible endpoints through a provider adapter.
- [ ] Support a generic OpenAI-compatible Responses endpoint when capability validation succeeds.
- [ ] Provider configuration includes display name, provider type, base URL, model/deployment, reasoning effort, timeout, and non-secret options.
- [ ] API keys or tokens are never stored in browser localStorage, source files, SQLite config tables, logs, exports, predictions, or reports.
- [ ] Persist secrets in an OS credential store where supported; otherwise require environment-only credentials and disable insecure Save Secret behavior.
- [ ] The browser receives only a credential reference and masked status, never the secret value.
- [ ] Test Connection performs a minimal authenticated capability check and reports authentication, endpoint reachability, model availability, structured-output compatibility, reasoning support where detectable, and latency.
- [ ] Invalid provider configuration fails explicitly with sanitized errors.
- [ ] Users can select the default provider/model for experiments.

### C. Futures Market Data

- [ ] Introduce an exchange-neutral futures market-data interface.
- [ ] Support closed OHLCV candles and instrument metadata.
- [ ] Support mark price and index price where the provider exposes them.
- [ ] Support funding rate/history.
- [ ] Support open interest where available.
- [ ] Support order book/spread or explicitly mark it unavailable.
- [ ] Timestamp every observation and enforce cutoff semantics.
- [ ] Reject stale, future, missing, malformed, duplicated, or non-contiguous required bars.
- [ ] Cache/archive normalized inputs with provider metadata and content hashes.
- [ ] CI uses mocks/fixtures only and never calls a live exchange.
- [ ] Do not implement a Bitkub futures adapter unless current official Bitkub APIs actually provide the required futures/perpetual functionality.

### D. Paper Futures Accounting

- [ ] Implement persistent virtual wallet balance, equity, available margin, used margin, positions, orders, and fills.
- [ ] Support long and short.
- [ ] Support isolated-margin paper positions first.
- [ ] Support market and limit order simulation.
- [ ] Support reduce-only exits.
- [ ] Support stop loss and take profit.
- [ ] Support partial fill representation even if the first deterministic model fills fully.
- [ ] Include trading fees.
- [ ] Include configurable slippage.
- [ ] Include funding cash flows when required data is available.
- [ ] Use mark price for unrealized PnL and liquidation logic when available.
- [ ] Represent maintenance margin and liquidation.
- [ ] Never credit an ambiguous same-candle target before an equally possible stop; use 1m path data or conservative ordering.
- [ ] Preserve accounting invariants after every event.

### E. Trading Intent Contract

- [ ] Add schema-valid structured TradingIntent records.
- [ ] Include symbol, side, decision state, entry, stop, targets, horizon, requested leverage, confidence, reason codes, model/run IDs, market snapshot hash, skill commit, and timestamp.
- [ ] TradingIntent is a proposal, not an executable order.
- [ ] Invalid or incomplete intents are rejected before risk evaluation.
- [ ] Free-form rationale is never the source of truth for execution fields.

### F. Deterministic Risk Engine

- [ ] Risk checks run after intent creation and before any simulated fill.
- [ ] Default max portfolio leverage is configurable and starts conservatively.
- [ ] Risk-based sizing derives quantity from equity, stop distance, and max risk per trade.
- [ ] Leverage changes margin usage; it must not silently increase the allowed loss budget.
- [ ] Implement max risk per trade.
- [ ] Implement max position notional/concentration.
- [ ] Implement max concurrent positions.
- [ ] Implement max daily loss.
- [ ] Implement max drawdown.
- [ ] Implement max consecutive losses.
- [ ] Implement stale-data guard.
- [ ] Implement spread/slippage guard when data exists.
- [ ] Implement funding guard where funding exists.
- [ ] Implement minimum liquidation-distance guard.
- [ ] Implement symbol allowlist.
- [ ] Implement deterministic kill switch.
- [ ] Risk rejection creates a structured reason code and can never create a fill.

### G. Signal and Hybrid Research Layer

- [ ] Add a small deterministic feature/signal layer rather than asking the LLM to invent all alpha.
- [ ] Start with compact features for trend, momentum, breakout/volatility expansion, volume participation, and relative strength.
- [ ] Funding and OI are contextual features when present.
- [ ] Do not add a large indicator zoo without an evaluation reason.
- [ ] Add a signal gate so the LLM does not need to run on every 15m candle.
- [ ] Preserve an explicit option to force analysis for experiments.
- [ ] Support experiment arms: Quant, Luna-only, Luna+Skill, and Hybrid when practical.
- [ ] Arms under comparison use identical point-in-time inputs and the same execution/risk assumptions.

### H. Scheduling

- [ ] Primary strategy schedule is candle-close driven.
- [ ] For 15m, default cycles occur at candle close plus 60 seconds.
- [ ] Never analyze an in-progress decision candle.
- [ ] Persist next run and last processed candle.
- [ ] A restart may catch up only according to an explicit catch-up policy; it must never duplicate already processed candles.
- [ ] Position monitoring may run at 1m or stream frequency without invoking the LLM each time.
- [ ] Scheduler errors are visible in the UI and journal.

### I. Data Windows

Initial configurable defaults:

- [ ] 1m execution path: 7-14 days retained locally
- [ ] 15m decision history: 30-60 days
- [ ] 1h context history: 90 days
- [ ] 4h regime history: 180 days
- [ ] 1d macro regime history: up to 365 days when used
- [ ] Feature generation uses the full required history.
- [ ] LLM prompts receive a bounded snapshot/feature summary rather than every retained bar.
- [ ] Every experiment records exact data-window settings.

### J. Frontend

- [ ] Preserve the current visual direction and navigation.
- [ ] Add Paper Portfolio / Trading Experiment functionality without turning the UI into a generic chatbot.
- [ ] Settings supports provider configuration and Test Connection.
- [ ] Trading experiment form supports symbols, 15m timeframe, context timeframes, starting balance, risk, leverage, schedule, and mode.
- [ ] Leverage selection visually resembles a trading terminal but clearly states PAPER.
- [ ] Show LIVE DATA / PAPER EXECUTION prominently when applicable.
- [ ] Show balance, equity, realized/unrealized PnL, drawdown, fees, funding, and open positions.
- [ ] Show trading activity and structured risk blocks.
- [ ] Show equity curve and drawdown charts.
- [ ] Show leverage cohort comparison from real experiment results.
- [ ] No dead controls, fake buttons, or unlabeled fabricated performance numbers.

### K. Persistence

- [ ] Use SQLite for the local-first implementation unless an existing storage choice makes another option clearly better.
- [ ] Keep repository/service interfaces so PostgreSQL can be added later.
- [ ] Persist experiments, schedules, snapshots, signals, decisions, intents, risk decisions, orders, fills, positions, funding events, portfolio snapshots, and metrics.
- [ ] Store hashes/IDs needed to reproduce a decision.
- [ ] Database migrations or schema versioning are explicit.
- [ ] Secret material is not stored in the application database.

### L. Experiment Versioning

Every experiment freezes:

- [ ] experiment ID
- [ ] start/end or active period
- [ ] symbols/universe
- [ ] strategy version
- [ ] prompt version
- [ ] skill commit
- [ ] provider/model
- [ ] reasoning configuration
- [ ] signal configuration
- [ ] risk-policy version
- [ ] leverage configuration
- [ ] fee/slippage/funding model
- [ ] data-provider IDs
- [ ] data-window settings

Changing a strategy/prompt/risk policy produces a new experiment version instead of rewriting prior history.

### M. Evaluation

- [ ] Keep prediction/decision quality separate from strategy PnL.
- [ ] Keep signal quality separate from execution quality.
- [ ] Track net PnL after fees/slippage/funding.
- [ ] Track return, expectancy/trade, profit factor, win rate, average win/loss, Sharpe, Sortino, max drawdown, MFE, MAE, exposure, fee drag, funding drag, and slippage drag when denominators are valid.
- [ ] Report sample counts with every aggregate.
- [ ] Segment results by asset, regime, strategy arm, confidence, and leverage when sample size permits.
- [ ] Never claim alpha from demo fixtures or statistically tiny samples.

### N. Export

- [ ] Add Export Experiment.
- [ ] Export is reproducible and excludes secrets.
- [ ] Minimum bundle:

~~~text
EXP-ID/
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

- [ ] Include schema/version identifiers and file hashes in manifest.json.
- [ ] Export can be shared for offline analysis without the local database.

### O. Security

- [ ] No live trading in this branch.
- [ ] No withdrawal/custody capability.
- [ ] LLM never receives provider or exchange credentials.
- [ ] News/web text is untrusted data and cannot invoke execution functions.
- [ ] Validate all model outputs before use.
- [ ] Sanitize logs and HTTP errors.
- [ ] Bind local backend to loopback by default.
- [ ] Add CSRF/origin protection if mutating browser APIs require it.
- [ ] Do not expose a public unauthenticated runtime by default.
- [ ] Future exchange keys must be least-privilege, trade-only, withdrawal-disabled, and IP-allowlisted where supported.

### P. Testnet Preparation

- [ ] Keep adapter boundaries compatible with a later Gate futures testnet adapter.
- [ ] Do not use testnet results as strategy-performance evidence.
- [ ] Testnet is for authentication, order lifecycle, partial fill, cancel/replace, WebSocket/reconnect, rate limit, clock drift, and reconciliation tests.
- [ ] LIVE remains feature-flagged off.

### Q. Tests and CI

- [ ] Existing validators continue to pass.
- [ ] Existing crypto_eval tests continue to pass.
- [ ] Add unit tests for long/short PnL, fees, funding, slippage, margin, liquidation, stop/TP, sizing, risk limits, idempotency, and accounting invariants.
- [ ] Add provider-setting tests with secret redaction.
- [ ] Add scheduler/restart/duplicate-cycle tests.
- [ ] Add deterministic end-to-end paper trade fixture.
- [ ] Add frontend tests for Settings, schedule controls, leverage configuration, portfolio display, and export.
- [ ] CI performs no paid model calls and no live exchange calls.
- [ ] CI cannot submit any real order.

## Accounting Invariants

At minimum:

- equity = wallet balance + unrealized PnL
- a fill cannot exceed remaining order quantity
- a reduce-only fill cannot increase exposure
- a rejected risk decision cannot create an order/fill
- a closed trade cannot realize PnL twice
- an experiment cycle cannot process the same symbol/candle twice
- leverage must remain within instrument and risk-policy limits
- exports must reconcile to persisted portfolio/trade totals

## Initial Experiment

See docs/paper-futures-experiment-v1.md.

## Detailed Development Plan

See docs/paper-futures-development-plan.md.

## VS Code Agent Prompt

See docs/vscode-gpt6-luna-paper-futures-prompt.md.

## Definition of Done

This branch is complete when a user can:

1. run the app locally;
2. configure and validate a supported AI provider without exposing its secret;
3. configure a 15m paper-futures experiment;
4. select leverage and risk settings;
5. start a candle-close schedule;
6. consume real futures market data;
7. produce validated signals and AI research decisions;
8. pass decisions through deterministic risk controls;
9. create realistic virtual long/short futures positions;
10. monitor positions without repeated unnecessary LLM calls;
11. persist all experiment/trading state across restarts;
12. inspect results in the UI;
13. compare leverage cohorts and experiment arms;
14. export a complete experiment bundle;
15. pass repository validation, tests, and offline CI;
16. keep real-money trading disabled.
