# Goal: Real AI + Live Gate Market Data + Paper Execution

## Branch

`feature/real-ai-live-paper-trading`

Base:

`main@63bfdaacbc6c8cab9e0a2b652b8616ceda9377fb`

## Why This Goal Exists

The previous paper-futures goal successfully built the local runtime, hybrid Jev/GPT routing, risk engine, paper execution, persistence, evaluation, and frontend. However, its acceptance process deliberately deferred all real external-provider validation and allowed fixture-backed market/browser QA.

That limitation is removed for this feature.

This goal is an **integration and realism phase**:

- AI calls must be real during local acceptance.
- Gate futures public market data must be real during local acceptance.
- The live chart must stream real Gate futures data.
- Gate account/wallet synchronization may use real authenticated **read-only** API calls.
- Real order placement, leverage mutation, transfers, withdrawals, or any other money-moving Gate API call remain disabled and must be mocked/blocked.

CI remains deterministic and may mock external providers. Local acceptance is different: the feature cannot be marked complete until the real integration checklist passes with user-supplied credentials.

## Canonical Runtime

```text
Gate Futures REST warm-up
        +
Gate Futures WebSocket live stream
        |
        v
Normalized Event Store
        |
        +---------------------> Live Chart / Market Tape
        |
        v
15m Closed-Candle Snapshot
        |
        v
Deterministic Quant Features
        |
        v
Real TypeSafe Jev API
        |
        v
Escalation Policy
        |
        +---- no escalation ----> typed decision
        |
        v
Real Azure AI Foundry
GPT-6 Luna / reasoning=max
+ crypto-market-trading-analysis skill
        |
        v
Validated TradingIntent
        |
        v
Deterministic Risk Engine
        |
        v
PAPER Execution Engine
        |
        v
Virtual Futures Wallet / Positions
        |
        v
Journal / Eval / Export
```

Optional Gate account connection:

```text
Gate API credentials
        |
        v
Secure local secret store
        |
        v
Authenticated READ-ONLY Gate client
        |
        +---- account/futures balance
        +---- real open positions
        +---- real open/closed orders
        +---- personal trade history
        |
        v
Gate Account Mirror (read-only)
```

The Gate Account Mirror is context/reconciliation only. It must never route a paper order into the real account.

## Current Official API References

Implementation must re-check current vendor docs while coding.

Microsoft Foundry / Azure OpenAI:

- https://learn.microsoft.com/en-us/rest/api/aifoundry/azureopenai/responses
- https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/reasoning

TypeSafe / Jev:

- https://docs.typesafe.ai/introduction
- https://docs.typesafe.ai/introduction/quickstart

Gate API v4:

- https://www.gate.com/docs/developers/apiv4/en/
- https://www.gate.com/docs/developers/apiv4/en/futures/
- https://www.gate.com/docs/developers/futures/ws/en/

## Critical Acceptance Rule: Real vs Mocked

### Real calls are REQUIRED in local acceptance

The developer must perform real local integration tests for:

- Azure AI Foundry Responses API
- deployed GPT-6 Luna
- reasoning effort `max`
- structured response parsing
- TypeSafe Jev `/v1/systemone`
- Jev Choice
- Jev Score
- Jev Noul
- Gate public futures REST
- Gate public futures WebSocket
- live Gate futures candlesticks
- live ticker/mark/index/funding values
- best bid/ask
- live chart update path

When Gate API credentials are supplied:

- real authenticated read-only Gate account test
- real futures account/balance sync
- real position list sync
- real order/history queries where permitted by read-only scope

A live integration test that was not actually executed must be reported as BLOCKED/NOT VERIFIED, never PASS.

### Mocks remain REQUIRED in CI/unit tests

External APIs must still be mocked in:

- GitHub Actions
- deterministic regression tests
- failure injection tests
- rate-limit tests
- malformed-response tests
- real-money Gate write APIs

### Gate real-money write APIs are FORBIDDEN in this goal

Never call live Gate endpoints that:

- place an order
- amend an order
- cancel an order as part of the paper execution path
- change real leverage
- change real margin mode
- transfer funds
- withdraw
- otherwise mutate the real account

The paper engine remains the only order execution engine.

If code contains a future live adapter interface, its transport must be disabled by construction and covered by a test proving no write request can leave the process.

## Azure AI Foundry Acceptance

The Settings UI must support a real Azure AI Foundry / Azure OpenAI provider.

Required fields:

- provider display name
- endpoint
- deployment/model name
- API key or credential reference
- reasoning effort
- timeout
- enabled/default state

Initial target:

- model/deployment: user-selected GPT-6 Luna deployment
- reasoning effort: `max`
- API: Responses API

The current Foundry v1 Responses server shape is expected to use:

`{endpoint}/openai/v1/responses`

with supported API-key authentication.

The implementation must treat Azure's `model` value as a deployment/model configuration supplied by the user, not assume a hard-coded deployment name.

### Test Connection must make a real request

A successful Test Connection is not HTTP-only.

It must verify:

- endpoint reachable
- API key accepted
- deployment/model accepted
- Responses API works
- structured output works
- requested `reasoning.effort=max` is accepted
- response content can be normalized by the runtime
- latency measured
- request/response IDs recorded where safe
- credential value never logged

The provider card must show:

- Connected / Failed
- provider kind
- model/deployment
- reasoning effort
- last validation time
- last real-call latency
- credential stored/missing
- last sanitized error

## TypeSafe Jev Acceptance

Settings must support:

- API base URL
- model, default `jev-latest`
- API key
- timeout
- Test Connection
- enabled/default state

The real connection test must call:

`POST https://api.typesafe.ai/v1/systemone`

and exercise Choice + Score + Noul in the same request where practical.

Persist:

- returned concrete model version
- usage tokens
- latency
- typed outputs
- confidence/probabilities for supported primitive types

The local acceptance run must route at least one real market snapshot through real Jev.

## Secret Storage

The user must be able to enter credentials in the UI and save them locally.

Secrets include:

- Azure AI Foundry / Azure OpenAI API key
- TypeSafe API key
- Gate API key
- Gate API secret
- future provider credentials

Rules:

- never persist raw secrets in browser localStorage/sessionStorage/IndexedDB
- never persist raw secrets in SQLite
- never put secrets in experiment config
- never put secrets in logs
- never put secrets in exports
- never put secrets in model prompts
- never echo secrets back through API responses

Preferred implementation:

- OS credential store through a maintained keyring abstraction
- on macOS: Keychain-backed storage
- on Windows/Linux: platform-supported credential store where available

Fallback:

- environment-variable reference or session-memory secret
- no plaintext file fallback

The frontend may POST a new secret once over loopback TLS-less localhost HTTP for this local-only app, but the backend must immediately store it in the OS credential store and return only a masked status/reference.

The UI never receives the full secret after save.

## Gate Market Data

Add Gate USDT perpetual futures as a first-class market-data source.

### REST warm-up / gap fill

Use official public futures REST endpoints for:

- contract metadata
- historical candlesticks
- funding history/current funding where required
- other public futures context needed by the existing MarketSnapshot

Normalize Gate contract names:

- UI symbol: `BTCUSDT`
- Gate contract: `BTC_USDT`

Do not leak Gate-specific naming into core trading contracts.

### WebSocket live stream

Use the official USDT futures WebSocket endpoint:

`wss://fx-ws.gateio.ws/v4/ws/usdt`

At minimum subscribe to:

- `futures.candlesticks`
- `futures.tickers`
- `futures.book_ticker`

Optional where useful:

- `futures.trades`
- public liquidation stream

The live stream is authoritative for the UI's current market view.

Persist enough normalized events for:

- chart continuity
- spread/slippage modeling
- mark/index price
- funding context
- connection diagnostics

### Connection resilience

Implement:

- ping/pong/heartbeat
- reconnect with bounded exponential backoff
- resubscribe
- stale-feed detection
- sequence/update sanity where applicable
- REST backfill after reconnect
- connection-state UI
- event timestamps
- dropped-message/error counters

A stale Gate stream must block new paper entries until freshness recovers.

## Real Live Chart

The current static SVG chart is not sufficient for this goal.

Implement a real interactive trading chart using a maintained chart library suitable for financial time-series data, preferably TradingView Lightweight Charts or an equivalent small pinned dependency.

Do not use a remote unpinned CDN in production/local runtime.

The chart must use real Gate futures data and support:

- candlesticks
- volume
- selected symbol
- selected timeframe
- 1m
- 5m
- 15m
- 1h
- 4h where supported
- real-time current candle updates
- completed-candle distinction
- last price
- mark price
- index price
- best bid/ask or spread summary
- funding display
- 24h change/volume where available
- entry line
- stop line
- take-profit line(s)
- paper position average entry
- paper liquidation estimate
- optional real Gate read-only position overlay clearly labeled REAL ACCOUNT if synced

Chart status must visibly show:

- LIVE
- RECONNECTING
- STALE
- OFFLINE

No generated/synthetic candle may appear without a FIXTURE/DEMO label.

## Gate Exchange Account Settings

Add Settings > Exchange Accounts > Gate.io.

Fields:

- account display name
- environment: Live / Testnet
- API key
- API secret
- settle currency, default USDT
- optional expected IP whitelist
- enabled
- sync toggle

### Credential policy for this phase

Recommend a Gate API key with:

- perpetual contract: read-only
- no withdrawal permission
- no wallet write permission
- IP whitelist where practical

Gate supports separate read-only/read-write permission groups, and Futures TestNet uses separate credentials from live.

### Test Connection

For Live/Read-only Gate account:

- authenticate a signed GET request
- query account detail
- query futures account/balance
- query futures positions
- query open/finished futures orders if enabled by read-only permissions
- optionally query personal futures trades

Do not make any non-GET authenticated request.

Return a capability matrix, for example:

```json
{
  "authenticated": true,
  "environment": "live",
  "futures_read": true,
  "account_sync": true,
  "positions_sync": true,
  "orders_read": true,
  "trades_read": true,
  "write_execution": false
}
```

If a write-enabled credential is entered, this branch must still expose `write_execution=false`.

## Gate Wallet / Account Mirror

Implement a read-only real-account mirror separate from the paper wallet.

### Paper Wallet

Authoritative for EXP paper execution:

- initial balance or user-set virtual capital
- virtual margin
- virtual positions
- virtual PnL
- paper funding
- paper fees/slippage

### Gate Account Mirror

Read-only context:

- Gate futures balance/equity
- real Gate futures positions
- real Gate open orders
- recent real Gate fills/trades where user enables sync
- last sync time
- sync error
- account environment
- source = GATE_LIVE_READONLY or GATE_TESTNET_READONLY

Never merge paper balances with real balances.

Never allow the paper engine to close/amend a real Gate position.

The UI must make PAPER vs REAL ACCOUNT visually unambiguous.

## Future Live-Ready Architecture

The user wants credentials configured now so the application can evolve into live execution later.

Prepare, but do not enable, these interfaces:

- ExchangeAccountProvider
- ExchangeMarketDataProvider
- ExchangeAccountSync
- ExchangeExecutionAdapter
- ExchangeCapability

The Gate execution adapter interface may describe:

- place order
- cancel order
- amend order
- set leverage

but the implementation in this branch must use a `DisabledLiveExecutionAdapter` or equivalent that refuses every mutating operation before network transport.

Future live trading must be a separate explicitly reviewed goal/branch.

## Runtime Experiment Defaults

Keep the existing research defaults unless explicitly changed:

- PAPER execution
- real Gate futures market data
- starting virtual balance: 100 USDT
- decision timeframe: 15m
- context: 1h + 4h
- live monitor/chart: 1m/WebSocket
- closed 15m candle + 60s decision delay
- primary paper leverage: 3x
- shadow leverage: 1x / 2x / 3x / 5x / 10x
- risk/trade: 1%
- max concurrent positions: 3
- BTCUSDT, ETHUSDT, SOLUSDT, SUIUSDT, SEIUSDT where Gate supports the contract
- TypeSafe Jev enabled
- Azure GPT-6 Luna escalation enabled
- GPT reasoning effort: max

## No-AI-Mock Acceptance Run

Add a dedicated local acceptance command, for example:

```bash
python3 -m crypto_eval real-integration-check
```

or equivalent.

It must not silently substitute fixture providers.

It should:

1. validate secret references
2. call real Gate public REST
3. connect real Gate WebSocket and observe messages
4. call real Jev test
5. call real Azure Foundry GPT-6 Luna test with reasoning=max
6. fetch a real market snapshot
7. compute quant features
8. call real Jev on that snapshot
9. force or naturally trigger one GPT escalation
10. call real GPT-6 Luna on the frozen snapshot
11. validate the resulting intent
12. pass it through deterministic risk
13. simulate PAPER execution only
14. persist the cycle
15. verify dashboard data
16. verify live chart feed
17. export a smoke bundle

The acceptance report must state which calls were truly external and include sanitized request IDs/timestamps/latencies.

## CI vs Local Acceptance Matrix

| Capability | CI | Local acceptance |
|---|---|---|
| Azure GPT call | mock | REAL required |
| Jev call | mock | REAL required |
| Gate public REST | mock | REAL required |
| Gate public WS | mock | REAL required |
| Gate read-only private API | mock | REAL if credentials configured |
| Gate order/write API | mock/blocked | BLOCKED |
| Paper order engine | real deterministic code | real deterministic code |
| Live chart | fixture component test | REAL Gate feed required |

## Acceptance Checklist

### Provider realism

- [ ] Azure Foundry real provider saves securely.
- [ ] Azure Test Connection makes real Responses call.
- [ ] GPT-6 Luna real deployment accepted.
- [ ] reasoning effort max accepted and recorded.
- [ ] structured output round-trip verified.
- [ ] real Jev credential saves securely.
- [ ] Jev Test Connection makes real systemone call.
- [ ] Choice/Score/Noul verified from real response.
- [ ] real snapshot is evaluated by Jev.
- [ ] at least one real snapshot is escalated to real GPT.

### Gate market realism

- [ ] Gate public REST works.
- [ ] Gate contract support is validated from live metadata.
- [ ] Gate public WebSocket connects.
- [ ] candlestick stream works.
- [ ] ticker/mark/index/funding stream works.
- [ ] book ticker/spread stream works.
- [ ] reconnect works.
- [ ] stale detection blocks new paper entries.
- [ ] REST gap fill restores chart after reconnect.

### Live chart

- [ ] chart is interactive.
- [ ] chart uses Gate futures, not generated candles.
- [ ] chart streams live updates.
- [ ] volume visible.
- [ ] timeframe switching works.
- [ ] position overlays update.
- [ ] mark/index/last and spread displayed.
- [ ] chart connection status visible.
- [ ] no fake values when feed unavailable.

### Gate account sync

- [ ] Gate credentials can be entered in Settings.
- [ ] credentials are securely stored/masked.
- [ ] signed GET auth works.
- [ ] account/balance sync works.
- [ ] position sync works.
- [ ] order/history read works where permission permits.
- [ ] account mirror is separate from paper wallet.
- [ ] write execution capability remains false.
- [ ] no non-GET live authenticated Gate request is emitted.

### Paper trading

- [ ] existing deterministic risk remains authoritative.
- [ ] existing margin/fee/funding/liquidation logic still passes.
- [ ] paper position uses real market prices.
- [ ] execution simulation uses real spread/market context where available.
- [ ] virtual wallet remains independent from Gate real balance.
- [ ] scheduling still processes closed candles once.

### Export/evaluation

- [ ] export records market provider = Gate.
- [ ] export records actual Jev model.
- [ ] export records actual GPT provider/deployment/reasoning config.
- [ ] export records real-call latencies/usage.
- [ ] no credentials in export.
- [ ] exported chart/market timestamps reconcile with persisted data.
- [ ] comparison metrics keep sample denominators.

### Security

- [ ] no secret in browser persistence.
- [ ] no secret in SQLite.
- [ ] no secret in logs.
- [ ] no secret in export.
- [ ] no Gate real order endpoint reachable from paper flow.
- [ ] no withdrawal endpoint exists in app.
- [ ] local server binds loopback by default.
- [ ] provider errors sanitized.

## Definition of Done

This feature is complete only when, on the developer's local machine with valid credentials:

1. Gate futures market data is genuinely live.
2. The chart genuinely moves from Gate WebSocket data.
3. Jev is genuinely called and returns typed decisions.
4. Azure AI Foundry genuinely calls GPT-6 Luna with reasoning effort max.
5. The hybrid route produces a real-model TradingIntent from a real frozen market snapshot.
6. The deterministic risk engine evaluates it.
7. The system executes only a simulated PAPER trade.
8. The virtual wallet/position/PnL updates from real market prices.
9. Optional Gate read-only account sync works when credentials are supplied.
10. All credentials are securely stored and masked.
11. The experiment can run continuously on the local machine.
12. Export contains the complete evidence trail.
13. CI remains deterministic.
14. Gate live trading remains impossible.
