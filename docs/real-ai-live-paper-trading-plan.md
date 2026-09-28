# Real AI + Live Gate Paper Trading Development Plan

## Objective

Move the current hybrid paper-futures platform from fixture-validated to **real-integration validated** while keeping exchange execution simulated.

Target user flow:

```text
Settings
  -> configure Azure AI Foundry
  -> configure TypeSafe Jev
  -> configure Gate read-only account (optional)
  -> Test Connection using REAL providers
  -> choose Gate futures live market source
  -> open LIVE trading chart
  -> configure PAPER experiment + AI budget
  -> Start scheduler
  -> real market + real AI
  -> deterministic PAPER trades
  -> cost/PnL monitoring
  -> evaluate / export
```

Only Gate money-mutating APIs remain mocked/blocked in local acceptance.

---

# 1. Test Tiers

## Tier A — deterministic CI

Mocks/fixtures required.

Covers:
- contract correctness
- accounting/risk
- provider error handling
- secret redaction
- scheduler/idempotency
- cost guard logic
- no network dependency

## Tier B — real integration acceptance

Real external calls required:
- Azure AI Foundry Responses API
- GPT-6 Luna deployment
- reasoning effort=max
- TypeSafe Jev System One
- Gate public futures REST
- Gate public futures WebSocket
- optional Gate authenticated read-only account sync

This tier runs locally, never as mandatory CI.

## Tier C — continuous PAPER runtime

Real market + real AI + deterministic PAPER execution.

---

# 2. Secret Store

Implement a shared OS-backed secret store.

Preferred:
- Python keyring or maintained equivalent
- macOS Keychain
- Windows Credential Manager
- Linux Secret Service

No plaintext file fallback.

Never store raw credentials in:
- browser persistence
- SQLite
- experiment config
- logs
- prompts
- predictions
- exports

Frontend may submit a new secret once to the loopback backend. The backend stores it immediately and returns only masked status.

---

# 3. Azure AI Foundry / GPT-6 Luna

Settings must support:
- endpoint
- deployment/model
- credential reference
- reasoning effort
- timeout
- enabled/default

Default target:
- GPT-6 Luna
- reasoning effort=max
- Responses API

Real Test Connection must verify:
- endpoint
- authentication
- deployment
- Responses API
- structured output
- reasoning=max accepted
- usage metadata when supplied
- latency
- safe request/response ID

Do not silently downgrade reasoning effort.

Local acceptance must execute at least one real market-snapshot decision with the real Foundry deployment.

---

# 4. TypeSafe Jev

Reuse the existing typed Jev architecture.

Real Test Connection must call:
- POST /v1/systemone

and exercise:
- Choice
- Score
- Noul

Persist:
- concrete model returned
- typed answers
- confidence/probabilities
- token usage when returned
- latency

Do not silently replace failed Jev calls with fixture results.

---

# 5. Gate Futures Public Market Data

Add Gate USDT perpetual futures as a first-class source.

Normalize:
- contracts
- OHLCV
- ticker
- last
- mark
- index
- funding
- open interest where available
- best bid/ask
- instrument limits

Canonical internal symbols stay exchange-neutral:
- BTCUSDT
- ETHUSDT

Gate adapter handles:
- BTC_USDT
- ETH_USDT

Use REST for:
- history warm-up
- metadata
- gap fill

Use official API docs as implementation source.

---

# 6. Gate Futures WebSocket

Use official USDT futures WebSocket.

At minimum subscribe to:
- futures.candlesticks
- futures.tickers
- futures.book_ticker

Optional:
- futures.trades
- public liquidation stream

Backend owns the stream.

Frontend receives normalized local events through SSE or a local WebSocket.

Implement:
- heartbeat
- reconnect
- bounded exponential backoff
- resubscribe
- stale detection
- per-symbol freshness
- REST gap fill
- duplicate-event de-dup
- current-vs-closed candle distinction
- exchange timestamp / local receipt latency

A stale feed blocks new PAPER entries.

---

# 7. Real Live Chart

Replace/supplement static SVG with a maintained interactive financial chart.

Preferred:
- TradingView Lightweight Charts

Alternative:
- ECharts candlestick

Pin the dependency/version. No floating CDN.

Required:
- real Gate futures candles
- volume
- 1m / 5m / 15m / 1h / 4h
- realtime current candle update
- closed-candle distinction
- last price
- mark price
- index price
- bid/ask spread
- funding
- 24h change/volume
- paper entry
- stop
- targets
- estimated paper liquidation
- optional real Gate read-only position overlay clearly labeled

Status:
- LIVE
- RECONNECTING
- STALE
- OFFLINE

Never synthesize continuation candles in live mode.

---

# 8. Gate Exchange Accounts

Add Settings > Exchange Accounts > Gate.io.

Fields:
- account display name
- Live / Testnet
- API key
- API secret
- settle currency
- enabled
- account sync toggle

Credentials go to secure OS secret store.

Recommended Gate permission:
- perpetual/futures read-only
- withdrawal disabled
- wallet writes disabled
- IP allowlist where practical

## Read-only Test Connection

Authenticated live acceptance may use safe signed GET calls for:
- account detail
- futures account/balance
- futures positions
- order history/read state
- personal trade history

No mutating HTTP call is allowed.

Return a capability matrix including:
- authenticated
- futures_read
- balance_sync
- positions_sync
- orders_read
- trades_read
- write_execution=false

---

# 9. Gate Account Mirror

Keep separate:

## Paper Wallet
Authoritative for PAPER experiment.

## Gate Account Mirror
Read-only real/testnet account context.

Never sum or merge balances.

Never close/modify real Gate positions from PAPER actions.

Allow an explicit one-time helper:
"Copy current Gate equity as starting PAPER balance"

This copies a numeric value only into a NEW experiment.

---

# 10. Hard Gate Write Block

Build an explicit allowlisted authenticated Gate client.

For this branch:
- GET endpoints only
- approved path allowlist only

Any:
- POST
- PUT
- PATCH
- DELETE
- leverage mutation
- order create/amend/cancel
- transfer
- withdrawal

must fail BEFORE network transport.

Add recording-transport tests proving no write request can leave the process.

A future ExchangeExecutionAdapter interface may exist, but implementation must be DisabledLiveExecutionAdapter.

---

# 11. PAPER Execution Using Real Gate Context

Keep deterministic PAPER execution.

Improve realism with live Gate data:

Market orders:
- base fill on live best bid/ask
- apply spread/slippage model
- optionally depth-aware slippage when reliable depth is available

Limit orders:
- use real path data
- conservative fill assumptions
- no look-ahead

Stops/targets:
- monitor real data
- version trigger source: mark/last

Funding:
- apply real observed funding at correct timestamps

Fees:
- use versioned fee configuration
- optionally use authenticated read-only account-specific fee information only if safely available

Liquidation:
- deterministic paper calculation using current contract metadata where available

---

# 12. AI Cost Ledger — REQUIRED

AI cost is a first-class trading metric.

The system must answer:

> Is the strategy still economically profitable after AI inference cost?

Do not track only tokens. Track cost per call, per cycle, per trade, per day, per experiment, per provider, and per strategy arm.

## AIUsageEvent

Persist a record for every attempted provider call.

Minimum fields:
- usage_event_id
- experiment_id
- cycle_id
- provider_id
- provider_kind
- model/deployment
- reasoning_effort
- call_type: test_connection / jev_decision / gpt_escalation / manual_analysis
- started_at
- completed_at
- latency_ms
- status
- input_tokens
- output_tokens
- reasoning_tokens when exposed
- cached_input_tokens when exposed
- provider_request_id when safe
- price_book_version
- estimated_cost_usd
- billed_cost_usd when externally reconciled
- cost_status: exact / estimated / unavailable
- error_code sanitized

Do not fabricate missing usage.

## Price Book

Create a versioned pricing configuration.

Example logical structure:

```json
{
  "provider": "azure_foundry",
  "model": "gpt-6-luna",
  "effective_from": "...",
  "currency": "USD",
  "input_per_million": null,
  "cached_input_per_million": null,
  "output_per_million": null,
  "reasoning_billing_rule": "provider-defined",
  "source": "manual-or-provider-pricing-reference"
}
```

Rules:
- never rewrite historical prices
- every cost estimate references a price-book version
- user can edit/update current pricing in Settings
- show when pricing is unknown
- do not silently assume public OpenAI pricing equals Azure contract pricing
- Jev pricing must have its own provider/model price configuration

If actual invoice/billing data is later imported, preserve both:
- estimated cost
- billed cost

---

# 13. AI Budget Guard — REQUIRED

Scheduled execution must check AI budget BEFORE every paid call.

Support configurable limits:

## Per call
- max estimated USD per GPT call
- max input tokens
- max output/reasoning tokens

## Per cycle
- max AI USD per 15m cycle
- max GPT escalations per cycle

## Per hour/day
- max GPT calls/hour
- max GPT calls/day
- max Jev calls/day
- max AI spend/day

## Per experiment
- max total AI budget USD
- optional experiment end date
- optional max number of paid calls

## Warning thresholds
Default configurable:
- 50%
- 80%
- 95%
- 100%

At 100%, hard block additional paid calls.

No scheduler retry may bypass the same budget guard.

## Preflight reservation

Before a paid call:
1. estimate worst-case call cost using configured max tokens/current price book
2. atomically reserve that budget
3. make provider call
4. replace reservation with actual usage estimate
5. release unused reservation

This prevents simultaneous scheduled symbols from overspending the limit.

If price is unknown and a hard monetary budget is enabled:
- fail closed OR require an explicit user-configured fallback price
- do not treat unknown price as zero

---

# 14. Budget Limit Actions

User selects policy:

### BLOCK_PAID_AI
When budget reached:
- no new Jev/GPT call
- continue market collection
- continue open-position deterministic monitoring
- no fake AI decision

### FALLBACK_QUANT
When paid AI budget reached:
- continue only Quant deterministic arm
- clearly label AI_BUDGET_FALLBACK

### JEV_ONLY
When GPT-specific budget reached:
- allow Jev if Jev budget remains
- prohibit GPT escalation
- clearly label GPT_BUDGET_BLOCK

### PAUSE_NEW_ENTRIES
When total AI budget reached:
- monitor existing paper positions
- stop generating new paper entries

Default should be conservative:
- PAUSE_NEW_ENTRIES or FALLBACK_QUANT
- never silently change policy

Persist every budget-block event.

---

# 15. AI Cost Settings UI

Add Settings > Cost & Budgets.

Show current price book:

```text
Azure Foundry / GPT-6 Luna
Input            $... / 1M
Cached input     $... / 1M
Output           $... / 1M
Reasoning rule   ...
Effective        ...
Source           ...

TypeSafe / Jev
...
```

Budget controls:

```text
Daily AI budget            $5.00
Experiment AI budget      $30.00
Max GPT call               $0.10
Max GPT calls / hour          8
Max GPT calls / day          50
Input token limit          ...
Output/reasoning limit     ...

At budget limit:
[ Pause new entries ▼ ]
```

Display projected spend based on recent run rate:
- today projected
- experiment projected
- estimated days until budget exhaustion

Projection must be labeled ESTIMATE.

---

# 16. Trading Economics — REQUIRED

Trading PnL alone is not sufficient.

Dashboard must show at least:

```text
Trading PnL after fees/funding/slippage     +$8.40
AI cost                                     -$2.15
--------------------------------------------------
Net experiment economics                    +$6.25
```

Track separately:
- gross trading PnL
- exchange/paper fees
- funding
- slippage
- net trading PnL
- Jev cost
- GPT cost
- total AI cost
- optional infrastructure/data cost later
- net experiment economics

## Currency normalization

Do not silently equate USD and USDT.

Support an explicit cost FX policy:
- USD -> USDT conversion rate
- source
- timestamp
- manual fixed rate or provider
- version recorded in experiment

If no FX conversion exists:
- show Trading PnL in USDT
- AI cost in USD
- net economic PnL = unavailable

For initial experiments a user may explicitly configure:
- USD/USDT = 1.0

but this assumption must be visible and recorded.

---

# 17. Economic KPIs

Add:

- AI cost / analysis
- AI cost / eligible case
- AI cost / trade
- AI cost / winning trade
- AI cost / realized trading profit
- AI cost as % of gross trading profit
- AI cost as % of net trading PnL
- net economic expectancy/trade
- net economic PnL
- net economic return on starting paper capital
- model escalation cost
- cost by asset
- cost by arm
- cost by provider
- cost by hour/day
- cost wasted on NO_TRADE decisions

Important metric:

`AI Cost Efficiency = incremental PnL attributable to AI arm / incremental AI cost`

Only calculate this on aligned experiment arms and sufficient samples.

Do not claim causal value from unmatched runs.

---

# 18. Cost-aware Escalation

Add optional cost-aware decision policy.

Example:
- Quant sees no eligible setup -> no Jev/Luna
- Jev high-confidence simple case -> no Luna
- Jev uncertain/high-impact -> Luna escalation
- GPT budget low -> escalation may be blocked according to configured policy

Persist:
- why GPT was invoked
- why GPT was not invoked
- estimated pre-call cost
- budget remaining
- actual cost after call

The LLM must never decide its own budget.

Budgeting is deterministic code.

---

# 19. Experiment Builder

Add:

- market source = Gate live
- Jev provider/model
- GPT provider/deployment
- reasoning=max
- starting paper balance
- 15m decision TF
- 1h/4h context
- leverage
- shadow leverage
- risk/trade
- max positions
- schedule
- AI budget profile
- budget-limit action
- price-book version
- USD/USDT cost FX policy

Freeze all values into experiment version.

---

# 20. Real Integration Check

Create a local command such as:

```bash
python3 -m crypto_eval real-integration-check
```

It must NOT silently use fixtures.

Required checks:
1. secret store available
2. Gate public REST real
3. Gate public WebSocket real
4. real ticker/candle/book data observed
5. real Jev Test Connection
6. real Azure GPT-6 Luna Test Connection with reasoning=max
7. real Gate snapshot -> features
8. real snapshot -> Jev
9. forced/eligible real GPT escalation
10. structured TradingIntent
11. deterministic risk
12. PAPER execution only
13. AI usage/cost ledger written
14. budget reservation/reconciliation written
15. dashboard receives live market/cost data
16. live chart receives Gate data
17. export smoke succeeds
18. optional Gate read-only account sync
19. Gate write transport reports BLOCKED BY DESIGN

Exit non-zero if required real integrations are not verified.

---

# 21. Frontend Dashboard

Show compact operational economics.

## Header

- Gate LIVE / STALE
- scheduler
- paper mode
- Jev health
- GPT health
- AI budget remaining

## Paper portfolio

- starting balance
- equity
- realized/unrealized
- fees
- funding
- slippage
- drawdown

## Cost panel

- AI spend today
- AI spend experiment
- remaining daily budget
- remaining experiment budget
- Jev cost
- GPT cost
- projected daily spend

## Economics

- net trading PnL
- AI cost
- net economic PnL
- economic expectancy/trade

## Provider usage

- calls
- tokens
- reasoning tokens
- p50/p95 latency
- estimated/billed cost
- budget blocks

No fake numbers without DEMO/FIXTURE label.

---

# 22. Export

Add:
- ai-usage.csv
- ai-cost-ledger.csv
- ai-budget-events.csv
- provider-price-book.json
- economic-pnl.csv
- market-stream-health.csv
- provider-validation.json
- real-integration-summary.json
- gate-account-sync-summary.json when applicable

Manifest records:
- exact provider/model
- reasoning effort
- price-book version
- cost FX policy
- budgets
- budget-limit action
- actual returned model version
- real integration status

No credentials.

---

# 23. Tests

## Cost ledger
- token usage parsing
- reasoning token parsing
- unknown usage
- price lookup
- historical price version
- cost calculation
- estimated vs billed
- USD/USDT conversion

## Budget guard
- per-call limit
- hourly call limit
- daily call limit
- daily USD limit
- experiment USD limit
- atomic reservation
- concurrent cycle reservation
- release unused reservation
- unknown-price fail closed
- block events persisted
- fallback policy behavior

## Economics
- net trading PnL
- AI-cost subtraction
- unavailable FX behavior
- per-trade economics
- aligned-arm incremental value

## Gate
- REST normalization
- WS normalization
- reconnect
- stale feed
- gap recovery
- GET allowlist
- POST/DELETE blocked pre-network

## Secrets
- sentinel scan of DB/export/log/API response

## Frontend
- budget settings
- cost display
- budget warning/breach
- live chart status
- Gate account mirror separation

CI remains network-free.

---

# 24. Local Acceptance Evidence

The developer may use the user's real local credentials.

Do not commit them.

Do not print them.

The final completion report must include sanitized evidence:

- Azure real test: PASS/FAIL, model/deployment, reasoning=max, latency
- Jev real test: PASS/FAIL, returned model, latency
- Gate REST: PASS/FAIL
- Gate WS: PASS/FAIL
- Gate account read-only: PASS/FAIL/SKIPPED
- AI cost ledger: PASS/FAIL
- Budget guard: PASS/FAIL
- Live chart: PASS/FAIL
- PAPER trade smoke: PASS/FAIL
- Gate real write: BLOCKED BY DESIGN

If a real check did not happen, call it NOT VERIFIED.

---

# 25. Definition of Done

Complete only when:

1. Gate futures data is actually live.
2. Live chart updates from real Gate WS.
3. Jev is actually called.
4. Azure Foundry GPT-6 Luna reasoning=max is actually called.
5. Real market snapshot routes through hybrid decision flow.
6. PAPER execution uses real market context.
7. Gate read-only account sync works when credentials are supplied.
8. Gate real writes are technically blocked.
9. AI cost is visible and auditable.
10. Scheduler cannot exceed configured AI budgets.
11. Net trading PnL and net economic PnL are separately visible.
12. Export includes complete AI-cost evidence.
13. CI remains deterministic/offline.
14. Local real-integration acceptance passes.
