# Goal: Hybrid Jev + GPT Paper Futures Trading Platform

## Canonical Direction

This goal supersedes conflicting parts of the earlier paper-futures plan on this branch.

The system is now a **hybrid decision architecture**:

```text
Real Futures Market Data
        |
        v
Deterministic Features / Quant Signals
        |
        v
Jev System-One Decision Layer
(fast typed probabilistic decisions)
        |
        +--------------------------+
        |                          |
        | confident/simple         | uncertain/conflicting/high-impact
        v                          v
Fast policy decision          GPT-6 Luna + crypto skill
        |                          |
        +-------------+------------+
                      v
              TradingIntent
                      |
                      v
        Deterministic Risk Engine
                      |
                      v
             PAPER Execution
                      |
                      v
      Portfolio / Journal / Evaluation
```

Jev is not a replacement for deterministic risk controls and is not a replacement for GPT-6 Luna when extended reasoning is needed.

## Why Jev

TypeSafe Jev is designed for state + typed questions -> structured decisions with probabilities/confidence.

Use Jev for atomic, repeatable decisions such as:

- regime classification
- setup quality
- breakout validity
- pullback quality
- momentum strength
- volatility regime
- leverage stress
- liquidity/spread concern
- funding/OI concern
- signal conflict
- whether the case should escalate to Luna

Do not ask Jev one vague question such as "Should I trade this?". Decompose the decision into atomic questions and combine results in code.

## Required Decision Roles

### Deterministic code

Authoritative for:

- data cutoff/freshness
- feature computation
- position sizing
- max risk/trade
- leverage limits
- margin
- liquidation math
- fees
- funding accounting
- order simulation
- portfolio accounting
- scheduler/idempotency
- kill switches

### Jev

Fast probabilistic judgment layer.

Returns typed outputs and probabilities only.

Jev never:

- stores exchange/API secrets
- submits orders
- changes risk policy
- changes portfolio accounting
- bypasses deterministic guards

### GPT-6 Luna + crypto skill

Escalation / System-Two layer for:

- conflicting evidence
- unusual regime
- complex market context
- catalyst/news interpretation
- thesis construction
- nuanced invalidation/target reasoning
- high-impact uncertainty

Luna is invoked only when escalation policy says it is justified, unless an experiment arm explicitly forces it.

## Provider Settings

The local app must support secure configuration for:

### TypeSafe / Jev

- provider type: TypeSafe
- API base URL, default: https://api.typesafe.ai
- model, default configurable and initially jev-latest
- API credential reference
- timeout
- test connection
- active/inactive status

The current documented Jev API uses:

- POST /v1/systemone
- Authorization: Bearer <API_KEY>
- state
- model
- questions

The implementation must verify current API docs while coding and isolate vendor-specific request/response handling behind an adapter.

### GPT providers

- OpenAI Responses API
- Microsoft Foundry / Azure OpenAI Responses-compatible endpoint
- generic Responses-compatible endpoint when capability validation passes

### Secret rules

Raw credentials must never be persisted in:

- browser localStorage
- frontend persistent state
- application SQLite
- logs
- prompts
- predictions
- exports

Prefer OS credential storage. If unavailable, use environment-variable references and make the UI state explicit.

## Provider Validation

Settings must provide Test Connection for both Jev and GPT providers.

Jev validation should verify:

- endpoint reachable
- authentication accepted
- configured model accepted
- Noul works
- Choice works
- Score works
- confidence/probabilities parse correctly where applicable
- latency measurement
- sanitized error handling

GPT validation should verify:

- endpoint reachable
- authentication
- model/deployment
- Responses-compatible call
- structured output
- requested reasoning mode where supported
- latency

## PoC Runtime Defaults

- execution mode: PAPER
- starting balance: 100 USDT
- decision timeframe: 15m
- context timeframes: 1h + 4h
- execution/path monitor: 1m or better
- schedule: closed 15m candle + 60s
- primary leverage: 3x
- shadow cohorts: 1x, 2x, 3x, 5x, 10x
- risk/trade: 1%
- max concurrent positions: 3
- initial symbols: BTCUSDT, ETHUSDT, SOLUSDT, SUIUSDT, SEIUSDT where provider supports them
- LIVE trading: disabled

## Jev Decision Contract

For every eligible 15m case, construct one bounded state snapshot and ask atomic questions in a single Jev call where practical.

Initial question families:

- market_regime: Choice(bull, bear, sideways, unstable)
- trend_alignment: Score
- momentum_quality: Score
- breakout_valid: Noul
- pullback_quality: Score
- volume_confirmation: Score
- leverage_stress: Score
- funding_concern: Noul
- oi_confirmation: Score when OI exists
- liquidity_risk: Score when spread/orderbook exists
- signal_conflict: Noul
- setup_quality: Score
- escalation_needed: Noul

Every answer is persisted with model version, probabilities/confidence where returned, question schema version, snapshot hash, and timestamp.

## Escalation Policy

Implement escalation as deterministic code over Jev outputs.

Initial configurable examples:

Escalate to Luna if one or more is true:

- Jev confidence below configured threshold on critical dimensions
- signal_conflict is high
- setup quality is borderline but non-zero
- regime is unstable
- quant direction and Jev direction materially disagree
- funding/OI/liquidity conditions conflict
- open position requires nuanced reassessment
- experiment arm explicitly requires Luna
- manual force-escalate

Do not hard-code the threshold as a universal truth. Version it as part of experiment configuration.

## Experimental Arms

The evaluation harness should support aligned comparisons of:

1. Quant only
2. Jev only
3. Luna only
4. Luna + Skill
5. Quant + Jev
6. Quant + Jev + Luna + Skill (Hybrid)

The Hybrid arm is the primary research hypothesis.

All paired arms must use identical point-in-time market inputs and the same deterministic execution/risk assumptions.

## Cost / Latency Metrics

Persist per decision:

- Jev input tokens / usage if returned
- Jev latency
- Jev call count
- GPT input/output/reasoning usage where available
- GPT latency
- GPT call count
- escalation rate
- total AI cost estimate using versioned pricing config
- cycle end-to-end latency

Do not hard-code vendor pricing into historical results without a pricing/version timestamp.

## Frontend Requirements

### Settings

Sections:

- AI Providers
  - TypeSafe / Jev
  - OpenAI
  - Microsoft Foundry / Azure
  - custom compatible provider
- Market Data
- Runtime
- Risk Defaults
- Export / Data Retention

For each provider:

- Add
- Test
- Save
- Enable/Disable
- Set default
- masked credential status

### Experiment Builder

Expose:

- mode = PAPER
- symbols
- TF 15m
- context 1h/4h
- starting balance
- primary leverage
- shadow leverage
- risk/trade
- max positions
- Jev enabled
- Jev model
- GPT escalation enabled
- GPT provider/model
- escalation policy
- schedule
- signal gate
- experiment arm(s)

### Runtime Dashboard

Show:

- next 15m cycle
- last cycle
- Quant gate
- Jev decision
- escalation yes/no
- Luna result if invoked
- risk decision
- paper execution
- portfolio state

### Evaluation Dashboard

Compare:

- PnL / expectancy / drawdown
- Jev-only vs Luna-only vs Hybrid
- escalation rate
- AI cost
- latency
- decision quality
- performance by regime/asset/leverage

No fake metrics unless clearly labeled FIXTURE/DEMO.

## Export

Experiment bundle must additionally include:

- jev-decisions.jsonl
- escalation-events.jsonl
- ai-usage.csv
- ai-cost-by-provider.csv
- latency.csv

alongside the existing trade/portfolio/signal/risk export files.

No credentials may appear in exports.

## Tests

Add tests for:

- Jev request construction
- Choice/Score/Noul parsing
- probabilities/confidence persistence
- TypeSafe auth failure
- TypeSafe timeout/rate limit/malformed response
- secret redaction
- Jev unavailable fallback policy
- escalation threshold boundaries
- no escalation for confident simple case
- escalation for uncertain/conflicting case
- identical frozen inputs across experiment arms
- no duplicate model call for one cycle unless explicit retry policy allows it
- cost/latency accounting
- all existing paper trading/risk/accounting invariants

CI must mock Jev and GPT. No paid API calls in CI.

## Definition of Done

The branch is done when a user can:

1. run the local app continuously;
2. configure Jev and GPT provider credentials from Settings without secret leakage;
3. test both providers;
4. start a 15m PAPER experiment;
5. ingest real futures market data;
6. compute deterministic signals;
7. run Jev atomic decisions;
8. escalate only selected cases to Luna + skill;
9. create validated TradingIntent;
10. pass it through deterministic risk controls;
11. simulate leveraged futures execution;
12. persist/recover state;
13. inspect decision routing, costs, latency and PnL;
14. compare Quant/Jev/Luna/Hybrid experiment arms;
15. export the experiment for offline analysis;
16. keep real-money trading impossible.
