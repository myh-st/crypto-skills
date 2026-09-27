# GPT-6 Luna Dev Prompt — Jev Hybrid Paper Futures PoC

You are the lead engineer implementing the next architecture of this repository.

Repository:
https://github.com/myh-st/crypto-skills

Required branch:
feature/ai-paper-futures-trading

Use your highest available reasoning effort.

Do not work on main. Do not merge to main.

## First Action

Verify git branch/status and read completely:

1. .goals/ai-paper-futures-trading/goal.md
2. docs/paper-futures-hybrid-v2.md
3. docs/paper-futures-development-plan.md
4. docs/paper-futures-experiment-v1.md
5. docs/architecture.md
6. docs/evaluation.md
7. current frontend
8. crypto_eval/*
9. crypto-market-trading-analysis skill and references
10. existing tests/CI

The v2 hybrid goal and hybrid architecture override older conflicting guidance.

Also read the current official TypeSafe Jev documentation before implementing its adapter. Do not guess API behavior.

Official documentation root:
https://docs.typesafe.ai/introduction

Current documented API shape at plan time:

POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <API_KEY>

with state + model + questions, using Choice / Score / Noul.

Treat current docs as authoritative over this prompt if the API changes.

## Mission

Implement a working local-first PoC:

```text
real futures market data
 -> deterministic quant/features
 -> Jev fast typed decision layer
 -> deterministic escalation router
 -> GPT-6 Luna + crypto skill only when needed
 -> validated TradingIntent
 -> deterministic risk engine
 -> PAPER futures execution
 -> portfolio/journal/evaluation/export
```

This is an implementation task, not another design exercise.

Do not stop at schemas, TODOs, mocks, or a pretty frontend.

Continue until the vertical slice is runnable and validated, unless blocked by a genuine external dependency such as missing API credentials.

## Core Principle

Use each component for what it is good at.

Deterministic code owns:
- point-in-time cutoffs
- numerical features
- position sizing
- risk limits
- leverage limits
- margin/liquidation
- fees/funding
- order simulation
- portfolio accounting
- scheduling/idempotency
- kill switch

Jev owns:
- fast atomic fuzzy judgments
- typed decisions
- probabilities/confidence

GPT-6 Luna + crypto skill owns:
- ambiguous/conflicting/high-impact deep reasoning
- unusual context
- catalyst/news interpretation when point-in-time safe
- nuanced thesis/invalidation/target reasoning

No AI component can bypass the risk engine.

## Jev Integration

Create an isolated TypeSafe provider adapter.

Support configuration:

- Base URL
- Model
- API key reference
- Timeout
- Enabled/disabled

Use jev-latest only as a configurable default, never as a hard-coded immutable model.

Implement Test Connection using a minimal low-cost call.

Validate:

- auth
- model accepted
- Noul
- Choice
- Score
- confidence/probabilities parsing
- response latency
- sanitized errors

Persist provider metadata, never raw API key.

## Jev Question Design

Do not ask:

"Should I trade SOL?"

Instead create atomic questions such as:

- market regime
- trend alignment
- momentum quality
- breakout validity
- pullback quality
- volume confirmation
- leverage stress
- funding concern
- OI confirmation
- liquidity risk
- evidence conflict
- setup quality
- escalation needed

Use multiple questions in one call where appropriate.

Normalize into a versioned JevDecisionVector.

Persist:

- state/snapshot hash
- question schema version
- model/model version
- answer values
- confidence/probabilities
- usage if returned
- latency
- timestamp

Never assume confidence is calibrated for our trading domain without measuring it.

## Escalation Router

Implement deterministic routing.

Escalate to Luna when configured conditions are met, including:

- low confidence on critical Jev dimensions
- evidence conflict
- unstable regime
- quant/Jev disagreement
- borderline/high-impact setup
- funding/OI/liquidity conflict
- open-position reassessment
- manual force
- experiment arm requires Luna

Thresholds are configurable and versioned.

Jev's own "escalation_needed" output is evidence, not sole authority.

## Fast Path

If Jev is confident and deterministic rules can build a complete safe intent, avoid a Luna call.

If safe entry/stop/target cannot be derived, escalate.

Never invent missing trade levels simply to remain on the fast path.

## Deep Path

Luna receives:

- frozen point-in-time snapshot
- quant features/signals
- Jev decision vector
- explicit uncertainty/conflict
- portfolio context
- crypto skill

Return structured output only.

Then pass through the same deterministic risk engine as every other arm.

## Settings UI

Implement/extend Settings with these sections:

### TypeSafe / Jev

Fields:
- Display Name
- Base URL
- Model
- API Key
- Timeout
- Enabled
- Test Connection
- Save
- Set Default

After save, API key must be shown only as credential-present / masked state.

### GPT Providers

Support:
- OpenAI
- Microsoft Foundry / Azure OpenAI Responses-compatible
- generic Responses-compatible endpoint after capability validation

Fields:
- Display Name
- Base URL
- Model/Deployment
- Reasoning effort
- API Key
- Timeout
- Test Connection
- Save
- Set Default

### Security

Raw secrets must never be persisted in:
- localStorage
- frontend persisted state
- SQLite/application DB
- logs
- prompts
- predictions
- exports

Prefer OS keychain/credential store.

If secure persistence cannot be implemented portably, implement environment-variable credential references rather than weak reversible encryption.

Browser GET endpoints must never return the raw secret.

## Experiment UI

Allow user to configure:

- PAPER mode
- symbols
- 15m decision TF
- 1h and 4h context
- starting balance
- risk/trade
- max positions
- primary leverage
- x1/x2/x3/x5/x10 shadow cohorts
- Jev enabled
- Jev provider/model
- GPT escalation enabled
- GPT provider/model
- escalation thresholds/policy preset
- signal gate
- schedule
- experiment arms

Default:
- $100
- 15m
- 1h/4h
- 3x primary
- 1% risk/trade
- 3 max positions
- closed candle + 60 seconds

## Evaluation Arms

Support aligned evaluation for:

A. Quant only
B. Jev only
C. Luna only
D. Luna + Skill
E. Quant + Jev
F. Quant + Jev + Luna + Skill

The primary hypothesis is F.

All compared arms use identical frozen market inputs and same execution/risk model.

Do not let each leverage cohort or arm independently fetch a different market snapshot.

## Usage / Cost / Latency

Persist:

- Jev call count
- Jev input usage if returned
- Jev latency
- GPT call count
- GPT input/output/reasoning usage where available
- GPT latency
- escalation rate
- total AI cost estimate
- full-cycle latency

Pricing must be configuration/version based.

Do not permanently bake today's vendor price into business logic.

The dashboard should answer:

- What percentage of eligible cases avoided Luna?
- Did Jev + Hybrid change decision quality?
- Did Hybrid change PnL/expectancy?
- What was AI cost per eligible case?
- What was AI cost per executed trade?
- What were p50/p95 decision latencies?

## Failure Behavior

Jev unavailable must not default to trade approval.

Experiment-configurable fallback:
- DEFER
- GPT_FALLBACK
- SKIP

Persist the fallback event.

GPT escalation failure must fail closed for new positions unless a predeclared validated fast-path fallback explicitly allows otherwise.

## Paper Trading

Preserve all earlier paper-futures requirements:

- real futures data
- long/short
- isolated margin
- market/limit
- stops/targets
- reduce-only
- fees
- slippage
- funding
- maintenance margin
- liquidation
- risk-based sizing
- 1m intrabar path when available
- conservative ordering when ambiguous
- persistent SQLite state
- scheduler/restart recovery
- export
- no real trading

## Runtime

The backend must continue running without an open browser.

Scheduler:
- 15m closed candle
- +60s default delay
- deterministic cycle ID
- duplicate-cycle lock
- restart-safe

Position monitoring is separate and should not invoke Jev/Luna unnecessarily.

## Export

Extend experiment export with:

- jev-decisions.jsonl
- escalation-events.jsonl
- ai-usage.csv
- ai-cost-by-provider.csv
- latency.csv

Keep existing:

- manifest.json
- config.json
- summary.md
- metrics.json
- trades.csv
- positions.csv
- equity.csv
- pnl-by-day.csv
- pnl-by-asset.csv
- pnl-by-leverage.csv
- decisions.jsonl
- signals.jsonl
- risk-events.csv

No secrets.

## Required Tests

Add tests for all old paper-trading requirements plus:

- TypeSafe request schema
- TypeSafe response parsing
- Choice
- Score
- Noul
- confidence/probability persistence
- auth error
- timeout
- rate limit
- malformed response
- secret redaction
- provider Test Connection
- Jev unavailable fallback
- escalation threshold boundaries
- confident fast path
- uncertain deep path
- Quant/Jev disagreement
- identical snapshot across arms
- no duplicate AI invocation for a cycle outside retry policy
- usage/cost accounting
- latency accounting
- export reconciliation

CI must mock Jev and GPT.

No paid AI calls in CI.

No live/testnet exchange orders in CI.

## Local PoC Verification

Once credentials are available, run a real local smoke:

1. configure TypeSafe credential through Settings;
2. Test Connection;
3. configure OpenAI/Foundry/compatible GPT provider;
4. Test Connection;
5. create PAPER EXP-001;
6. use real futures market data;
7. run a 15m cycle;
8. verify quant feature output;
9. verify Jev atomic decisions;
10. verify escalation routing;
11. verify Luna is skipped for simple case if allowed;
12. verify Luna runs for a forced/uncertain case;
13. verify risk gate;
14. verify virtual trade lifecycle;
15. verify persistence after restart;
16. verify dashboard;
17. verify export.

If API credentials are unavailable during development, build complete adapters/tests with mocks and stop only the live credential smoke, not the rest of implementation.

## Validation

Run at minimum:

python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
node --test frontend/tests/*.test.mjs

plus any lint/typecheck/build tests introduced.

Run the actual local app and perform browser verification.

## Git Discipline

Remain on:
feature/ai-paper-futures-trading

Use logical commits.

Do not force-push.

Do not merge main.

Push completed work to the branch.

## Completion Report

Return:

1. Implemented
2. Architecture flow
3. Jev setup
4. GPT provider setup
5. Local run commands
6. EXP-001 instructions
7. Test counts/results
8. Browser QA
9. Cost/latency instrumentation
10. Security confirmation
11. Remaining blockers
12. final commit SHA and push status

## Definition of Success

The PoC is successful when we can leave the app running locally and observe:

real market data
 -> Quant
 -> Jev fast decisions
 -> selective Luna + skill escalation
 -> deterministic risk
 -> PAPER futures execution
 -> persistent portfolio
 -> measurable decision quality, latency, AI cost, and PnL
 -> exportable experiment

while the system remains incapable of placing real-money orders.
