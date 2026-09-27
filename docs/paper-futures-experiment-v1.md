# Paper Futures Experiment v1

## Purpose

Run a prospective, real-market, paper-execution experiment that can be reviewed without risking funds.

This experiment is for measuring system behavior and initial expectancy, not for claiming profitability.

## Mode

- Market data: real
- Execution: paper
- Real exchange orders: disabled
- Starting balance: 100 USDT
- Decision timeframe: 15m
- Context: 1h + 4h
- Execution path / position monitor: 1m or better
- Default analysis delay: 60 seconds after each 15m candle close

## Universe

Initial requested symbols:

- BTCUSDT
- ETHUSDT
- SOLUSDT
- SUIUSDT
- SEIUSDT

The runtime must verify provider support before the experiment starts. Unsupported instruments are recorded as excluded with a reason.

## Data Warm-up

Before forward collection begins, preload enough point-in-time-safe market history for features:

- 1m: 7-14 days
- 15m: 60 days preferred
- 1h: 90 days
- 4h: 180 days
- 1d: up to 365 days if used

The warm-up history is context only. It must not include observations later than a historical case cutoff when replaying older cases.

## AI Configuration

Default research arm:

- model: configurable, initially GPT-6 Luna where available
- reasoning: max if the selected endpoint supports it
- crypto skill: enabled for the skill arm
- structured output: required
- web/tool access during frozen market decision: disabled unless explicitly modeled as point-in-time evidence

Provider configuration is part of the experiment manifest, but credentials are never exported.

## Signal Gate

Default: enabled.

Run the model when at least one eligible condition occurs:

- breakout candidate
- pullback into defined level
- volatility expansion
- momentum regime change
- abnormal volume
- funding/OI regime change when supported
- open position requires reassessment
- manual force analysis

Persist SKIP_LLM decisions and their reason.

## Risk Defaults

These are starting research defaults, not claims of optimal values:

- risk per trade: 1% equity
- max concurrent positions: 3
- primary leverage: 3x
- max daily loss: 5%
- max portfolio drawdown stop: 15%
- maximum consecutive losses before pause: 5
- isolated margin
- long and short enabled
- symbol allowlist: experiment universe only

All values are frozen in experiment config.

## Shadow Leverage Cohorts

Replay the same approved trade intent through:

- 1x
- 2x
- 3x
- 5x
- 10x

Cohorts do not independently call the model.

They share the same:

- intent
- entry rule
- stop
- target
- market path
- fee schedule
- funding observations
- slippage model

Only leverage-dependent margin/liquidation behavior differs.

## Strategy Arms

When runtime capacity allows, collect four arms:

### Quant

Deterministic signal/strategy only.

### Luna

Same frozen input, model without crypto skill treatment.

### Luna + Skill

Same model/config/input with the crypto skill treatment.

### Hybrid

Quant signal gate/features plus Luna + skill.

Do not mix different model configurations inside a paired comparison.

## Experiment Stages

### Stage 0 - Deterministic Verification

Duration: fixtures only.

Pass conditions:

- accounting invariants pass
- no duplicate orders/cycles
- stop/TP path behavior verified
- fees/funding/slippage verified
- export reconciles

### Stage 1 - Live Data Smoke

Duration: 7 days.

Purpose:

- verify scheduler stability
- verify data freshness
- verify restart recovery
- verify no duplicate cycles
- verify provider/model errors are safe
- verify paper portfolio accounting

Do not judge strategy quality from this stage.

### Stage 2 - Initial Forward Evaluation

Duration: 30 days.

Use results for diagnosis:

- net PnL after modeled costs
- number of signals
- model invocation rate
- trade count
- trigger rate
- expectancy/trade
- profit factor
- max drawdown
- fee/funding/slippage drag
- risk blocks
- performance by asset
- performance by leverage

A positive result is not sufficient for promotion by itself.

### Stage 3 - Stronger Evidence

Duration: 60-90 days or longer.

Goal:

- collect more trades
- cover multiple market regimes
- compare arms
- compare leverage cohorts
- review confidence intervals and failure cases

Do not declare persistent alpha solely from a fixed number of trades. Sample count and regime diversity must be reported alongside metrics.

## Primary Metrics

- ending equity
- net return
- net PnL
- expectancy per trade
- profit factor
- win rate
- average win
- average loss
- Sharpe where sampling supports it
- Sortino where sampling supports it
- maximum drawdown
- MFE
- MAE
- exposure
- liquidation count
- stop-out count
- fee drag
- funding drag
- slippage drag
- risk-block count
- model invocation count
- model cost when known

Every aggregate must show its sample denominator.

## Diagnostic Breakdowns

When sample size permits:

- asset
- hour/session
- market regime
- BTC regime
- volatility regime
- signal family
- decision state
- confidence
- long vs short
- leverage cohort
- model/strategy arm

Avoid drawing conclusions from tiny buckets.

## Operational Metrics

Track:

- scheduler cycles expected/completed/skipped/failed
- stale-data blocks
- provider errors
- LLM errors
- average model latency
- average cycle latency
- DB/storage errors
- duplicate-cycle prevention events
- kill-switch events
- restart recoveries

## Versioning Rule

Do not tune an active experiment and continue counting it as the same experiment.

If any material item changes, create EXP-002 or a new version:

- prompt
- skill commit
- strategy logic
- risk policy
- leverage policy
- fee/slippage model
- model/provider
- data provider
- universe
- schedule
- signal gate

## Export Review

At least weekly and at the end of each stage, export the experiment bundle.

The export must allow an external reviewer to answer:

- What did the system know at each decision?
- Why was the LLM called or skipped?
- What did each strategy arm decide?
- What did the risk engine change/block?
- What execution path was simulated?
- What costs were charged?
- What caused each exit?
- What was the portfolio state afterward?
- Which leverage cohort helped or hurt?
- Did model-assisted arms outperform deterministic baselines on aligned cases?

## Initial Decision Rule

Do not promote to testnet because of headline PnL.

Promotion from PAPER to a future TESTNET integration should require:

- stable scheduler and restart behavior
- zero unresolved accounting divergence
- zero duplicate execution defects
- all risk/kill-switch tests passing
- reproducible export
- sufficient forward data to justify continuing research

TESTNET validates exchange mechanics; it is not a reward for a profitable paper run.

## Review Questions After 30 Days

1. Does Hybrid improve expectancy over Quant on aligned eligible cases?
2. Does Luna + Skill improve decision quality over Luna without skill?
3. Which signals are rejected by the LLM, and was rejection beneficial?
4. Which risk controls prevent the largest losses?
5. Are losses concentrated by symbol, session, or regime?
6. Do fees/funding/slippage eliminate apparent gross edge?
7. Which leverage cohort has the best return-to-drawdown tradeoff?
8. Does 10x mostly amplify noise/liquidation risk?
9. Is the 15m cadence producing too many low-quality decisions?
10. Should the next experiment change signal gating, risk, universe, or model treatment?
