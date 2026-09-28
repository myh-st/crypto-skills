# GPT-6 Luna VS Code Prompt — Real AI + Gate Live Data + PAPER Execution

You are the lead engineer implementing the next integration phase of this repository.

Repository: https://github.com/myh-st/crypto-skills
Required branch: feature/real-ai-live-paper-trading
Use your highest available reasoning effort.

## Read first

Read these files completely before editing:
1. .goals/real-ai-live-paper-trading/goal.md
2. docs/real-ai-live-paper-trading-plan.md
3. docs/paper-futures-runtime.md
4. crypto_eval/paper_ai.py
5. crypto_eval/paper_market.py
6. crypto_eval/paper_runtime.py
7. crypto_eval/paper_server.py
8. crypto_eval/paper_contracts.py
9. frontend/modules/views/paperTrading.js
10. frontend/modules/paperApi.js
11. frontend/modules/components/priceChart.js
12. current schemas/tests/workflows
13. skills/crypto-market-trading-analysis/SKILL.md and relevant references

Do not search for or follow superseded goals/plans. They were intentionally removed from this branch. The new goal is canonical.

## Mission

Turn the current fixture-validated PAPER platform into a real-integration validated system:

Gate Futures REAL market data
→ Quant features
→ REAL TypeSafe Jev
→ deterministic escalation
→ REAL Azure AI Foundry GPT-6 Luna with reasoning=max
→ crypto skill
→ validated TradingIntent
→ deterministic risk
→ PAPER futures execution
→ virtual wallet
→ AI cost/budget ledger
→ economic PnL
→ evaluation/export

Real Gate order execution must remain impossible.

## Local credentials

The user explicitly authorizes real local integration testing with credentials already present on the machine.

Use local .env, environment variables, or secure OS credential storage.

Expected bootstrap variables:
- TYPESAFE_API_KEY
- AZURE_OPENAI_API_KEY

Optional:
- OPENAI_API_KEY
- COMPATIBLE_AI_API_KEY

Never print secret values.
Never commit .env.
Never copy secrets into fixtures, logs, prompts, SQLite, exports, screenshots, or completion reports.

If .env exists, load it locally without exposing values.

Implement secure Settings persistence using the OS credential store when practical.

## Real calls are required for local acceptance

Do not use fixture transports for:
- Azure AI Foundry Responses API
- GPT-6 Luna deployment
- reasoning effort=max
- TypeSafe Jev /v1/systemone
- Gate public futures REST
- Gate public futures WebSocket
- live chart data

If Gate credentials are later supplied, perform authenticated Gate read-only account sync using safe GET calls only.

If a credential is absent or invalid, mark that capability NOT VERIFIED. Do not substitute a fixture and call it PASS.

CI and deterministic tests remain network-free and mocked.

## Azure AI Foundry

Implement and verify:
- endpoint
- deployment/model
- API key credential
- reasoning effort
- timeout
- enabled/default

Target GPT-6 Luna with reasoning effort=max using the Responses API.

Test Connection must make a real request and verify:
- authentication
- deployment/model
- Responses endpoint
- structured output
- reasoning=max accepted
- response normalization
- usage metadata if available
- latency
- safe provider request/response ID

Do not silently downgrade reasoning effort.

## TypeSafe Jev

Use real POST https://api.typesafe.ai/v1/systemone

Test:
- Choice
- Score
- Noul

Record:
- returned concrete model
- confidence/probabilities
- usage if returned
- latency

At least one real Gate market snapshot must be evaluated by real Jev.

No automatic fixture fallback for failed real provider calls.

## Gate Futures public market source

Implement Gate USDT perpetuals as a first-class provider using current official API docs.

Normalize:
- contract metadata
- OHLCV
- ticker
- last
- mark
- index
- funding
- OI when available
- bid/ask spread
- instrument limits

Keep core symbols exchange-neutral. Gate-specific BTC_USDT naming belongs only inside the adapter.

Use REST for warm-up/metadata/gap recovery and WebSocket for live state.

## Gate WebSocket

Use official USDT futures WebSocket.

At minimum:
- futures.candlesticks
- futures.tickers
- futures.book_ticker

Implement reconnect, bounded backoff, resubscribe, heartbeat, stale detection, REST gap fill, de-duplication, current/closed candle distinction, and per-symbol freshness.

Backend owns market truth. Browser consumes normalized local events through SSE or local WebSocket.

A stale required feed blocks new PAPER entries.

## Live chart

Upgrade the paper trading chart to an interactive financial chart.

Preferred: TradingView Lightweight Charts.
Pin the dependency. Do not use an unpinned remote CDN.

Support:
- live Gate futures candlesticks
- volume
- 1m / 5m / 15m / 1h / 4h
- realtime current candle
- completed candle distinction
- last / mark / index
- bid/ask spread
- funding
- 24h change/volume
- PAPER entry / stop / targets
- PAPER liquidation estimate
- PAPER average entry
- optional REAL ACCOUNT read-only position overlay

Show LIVE / RECONNECTING / STALE / OFFLINE.

Never synthesize continuation candles in live mode.

## Gate Exchange Accounts

Add Settings → Exchange Accounts → Gate.io.

Fields:
- account display name
- Live/Testnet
- settle currency
- API key
- API secret
- enabled
- account sync

Store secrets in OS credential storage.

Real account Test Connection may use signed GET requests only.

Support read-only sync for:
- account detail
- futures balance/equity
- futures positions
- order history where read permission allows
- personal futures trade history where read permission allows

Build an authenticated client with an HTTP method allowlist and endpoint allowlist.

Any POST/PUT/PATCH/DELETE must fail BEFORE network transport.

Implement DisabledLiveExecutionAdapter or equivalent.

Keep REAL Gate Account Mirror separate from PAPER Wallet.

## PAPER execution realism

Use real Gate market context:
- market order base = bid/ask
- spread/slippage model
- conservative limit fills
- real market path
- real funding observations
- versioned fees
- deterministic margin/liquidation

No future information.

## AI Cost Ledger — mandatory

Every AI call is a financial cost event.

Persist:
- experiment/cycle
- provider
- model/deployment
- reasoning effort
- call type
- timestamp
- latency
- input tokens
- cached input tokens if available
- output tokens
- reasoning tokens if available
- price-book version
- estimated cost USD
- billed cost if later known
- cost status exact/estimated/unavailable
- safe request ID
- sanitized error

Do not fabricate missing usage.

## Versioned Price Book

Support separate pricing for Azure Foundry/GPT-6 Luna, TypeSafe Jev, and compatible providers.

Pricing has:
- provider
- model/deployment
- effective date
- currency
- input price
- cached input price
- output price
- reasoning billing rule
- source/reference

Do not assume public OpenAI pricing equals the user's Azure contract.
Unknown price is NOT zero.
Never rewrite historical price versions.

## AI Budget Guard — mandatory

Before every paid provider call:
1. estimate worst-case cost
2. atomically reserve budget
3. call provider
4. reconcile with actual usage
5. release unused reservation

Support:
- max estimated cost/call
- max input tokens
- max output/reasoning tokens
- max AI spend/cycle
- max GPT calls/hour
- max GPT calls/day
- max Jev calls/day
- max AI spend/day
- max experiment AI budget
- max paid calls

Warnings: 50%, 80%, 95%, 100%.

At 100%, no new paid call starts.

Concurrent scheduler workers must not overspend the same remaining budget.

Unknown pricing with a monetary hard limit must fail closed or require explicit fallback pricing.

## Budget-limit policies

Implement explicit versioned policies:
- PAUSE_NEW_ENTRIES
- FALLBACK_QUANT
- JEV_ONLY
- BLOCK_PAID_AI

Open PAPER positions continue deterministic monitoring after budget exhaustion.

Persist every budget block/fallback event.

## Cost Settings UI

Add Settings → Cost & Budgets.

Show:
- provider/model price book
- daily AI budget
- experiment AI budget
- max cost/call
- call/token limits
- warning thresholds
- action at limit
- current price-book version
- USD/USDT cost FX policy
- projected spend labeled ESTIMATE

## Economic PnL

Dashboard must separately show:
- gross trading PnL
- fees
- funding
- slippage
- net trading PnL
- Jev cost
- GPT cost
- total AI cost
- net experiment economics after AI cost

Do not silently equate USD AI cost and USDT PnL.

Require explicit USD/USDT conversion policy. If none, currencies stay separate and net economic PnL is unavailable.

Track:
- AI cost/analysis
- AI cost/eligible case
- AI cost/trade
- AI cost/winning trade
- AI cost as % of trading profit
- net economic expectancy/trade
- net economic PnL
- cost by provider/model/asset/arm
- cost spent on NO_TRADE
- GPT escalation cost

For aligned arms only, evaluate incremental PnL versus incremental AI cost.

## Scheduler

Preserve 15m candle-close schedule:
- closed candle
- +60 seconds
- signal-gated AI invocation

Do not call GPT every minute.

Market/position monitor may run more frequently without AI.

Before each Jev/GPT call enforce budget, data freshness, and experiment state.

## Real integration acceptance command

Add a local command such as:

python3 -m crypto_eval real-integration-check

It must never silently switch to fixtures.

Required checks:
1. secret store available
2. Gate public REST real
3. Gate public WS real
4. real Gate events observed
5. Jev real Test Connection
6. Azure GPT real Test Connection
7. reasoning=max verified
8. real Gate snapshot
9. real Jev decision
10. real GPT escalation
11. structured TradingIntent
12. deterministic risk
13. PAPER execution only
14. cost ledger persisted
15. budget reservation/reconciliation
16. live chart feed
17. export smoke
18. optional Gate read-only sync
19. Gate live write = BLOCKED BY DESIGN

Return non-zero if required real checks fail.

## Frontend

Preserve the existing design.

Add/upgrade:
- AI Providers
- Exchange Accounts
- Cost & Budgets
- Runtime health
- Gate LIVE/STALE status
- scheduler
- PAPER mode
- Jev/GPT health
- AI budget remaining
- live chart
- paper portfolio
- real read-only Gate mirror
- latest Quant/Jev/GPT/risk/execution activity
- AI spend and economic PnL

No fake controls, metrics, or unlabeled fixtures.

## Export

Extend bundle with:
- ai-usage.csv
- ai-cost-ledger.csv
- ai-budget-events.csv
- provider-price-book.json
- economic-pnl.csv
- market-stream-health.csv
- provider-validation.json
- real-integration-summary.json
- gate-account-sync-summary.json when used

No secrets.

## Testing

CI stays offline.

Add deterministic tests for:
- Azure/Jev request and response contracts
- reasoning=max validation
- Gate REST/WS normalization
- reconnect/stale/gap fill
- Gate GET allowlist and write-block
- usage/token parsing
- versioned pricing
- cost calculations
- atomic budget reservation
- concurrent reservations
- fallback policies
- USD/USDT conversion
- economic PnL
- secret sentinel scans
- frontend provider/Gate/cost/chart behavior

Run at minimum:
- python3 scripts/validate_repo.py
- python3 -m unittest discover -s tests -v
- node --test frontend/tests/*.test.mjs

Then run the REAL local integration check using actual local credentials.

## Working style

Do not stop at planning.
Do not stop at schemas.
Do not stop at mocked acceptance.
Do not stop when UI merely renders.

Implement the real local vertical slice.

Use logical commits and continue after each commit.
Do not merge to main.

## Completion report

Report:
1. Implemented
2. Startup commands
3. Azure real validation
4. Jev real validation
5. Gate REST/WS real validation
6. Gate read-only sync
7. Live chart
8. AI cost/budget
9. PAPER trade smoke
10. Validation/test counts
11. Security confirmation
12. Remaining gaps
13. Final commit SHA
14. Push status

Gate live write execution must be reported as BLOCKED BY DESIGN.

## Final success criterion

The local app runs continuously with REAL Gate futures data, REAL TypeSafe Jev, REAL Azure AI Foundry GPT-6 Luna reasoning=max, a live chart, deterministic PAPER execution, optional read-only Gate account mirroring, persistent AI cost accounting, hard budget controls, and net economic PnL after AI cost — while remaining technically unable to place a real Gate trade.
