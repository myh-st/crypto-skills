# Goal: AI Portfolio Trading OS

## Branch

`feature/ai-portfolio-trading-os`

Base commit:

`3271426cc509f0932c649e7575afd3096c4a4ed4`

## Product North Star

Build a local-first AI-assisted portfolio and trading operating system where the AI and the human jointly discover, plan, execute, manage, evaluate, and improve trading decisions.

The system must be able to:

- continuously observe real exchange markets;
- discover and rank actionable opportunities;
- trade autonomously in PAPER mode when the configured policy allows;
- recommend actions before execution when the policy requires approval;
- summarize portfolio state and what needs attention;
- manage open positions over their full lifecycle;
- support both Spot and Perpetual markets;
- let the human override, edit, pause, re-plan, reduce, close, or take control at any time;
- measure trading economics after fees, funding, slippage, and AI inference cost;
- learn from every completed trade and compare competing strategy/AI approaches prospectively.

Success is **not** prediction accuracy, trade frequency, or sophisticated-looking AI output.

Success is sustainable improvement in **risk-adjusted net economic expectancy after all modeled trading costs and AI costs**, demonstrated prospectively with auditable data.

No claim of guaranteed profit is permitted. The platform must be designed to discover, measure, reject, and improve strategies rather than assume an edge exists.

## Product Principle

The product should feel like a portfolio/trading cockpit, not an AI demo and not a clone of a crypto exchange.

A user opening the app after leaving the scheduler running should understand within roughly 5-10 seconds:

1. What is my portfolio worth?
2. Am I up or down?
3. What is the result after AI cost?
4. Which positions are open?
5. Which positions need attention?
6. What is the AI recommending?
7. Is the automation healthy?
8. How much risk and AI budget remain?
9. What can I safely do next?

## Existing System To Preserve

The base repository already has:

- real Gate USDT perpetual REST/WebSocket market data;
- real TypeSafe Jev integration;
- real Azure AI Foundry GPT-6 Luna integration with reasoning=max;
- deterministic signal/risk/paper-execution pipeline;
- persistent SQLite PAPER runtime;
- scheduler and independent position monitor;
- live Lightweight Charts integration;
- AI cost ledger and hard budget guard;
- read-only Gate account mirror capability;
- export/evaluation harness;
- deterministic CI and local real-integration acceptance;
- real Gate write operations blocked by design.

Do not rebuild these foundations.

Refactor and extend them into a user-centered portfolio/trading product.

## Hard Safety Boundary

This goal remains PAPER execution only.

Allowed:

- real public Gate Spot and Perpetual market data;
- real Jev and GPT calls;
- real read-only Gate account synchronization;
- autonomous PAPER trades;
- manual PAPER trades;
- AI-managed PAPER positions;
- user-managed PAPER positions;
- simulated order amendments/cancellations;
- read-only display of real Gate account positions where credentials permit.

Forbidden:

- real Gate order creation;
- real order amendment;
- real order cancellation;
- real leverage/margin mutation;
- transfer;
- withdrawal;
- custody/private-key handling;
- any path that can mutate the real Gate account.

The existing Gate live-write block must remain enforced before network transport.

## Operating Modes

The user must be able to choose per experiment and per position how much authority the AI has.

### RECOMMEND_ONLY

AI may:

- discover opportunities;
- produce plans;
- propose entry/exit/stop/target/size changes;
- summarize portfolio risk.

AI may not mutate PAPER positions until the user applies the proposal.

### AUTO_PAPER

AI may autonomously:

- create PAPER orders;
- manage PAPER stops and targets;
- reduce/close PAPER positions;
- skip trades;
- re-plan PAPER positions;

subject to deterministic risk, budget, data-freshness, and experiment policies.

Every AI action must be auditable and reversible where the domain permits.

### MANUAL_OVERRIDE

The user can take control of an existing position.

When active:

- AI continues to observe and may recommend;
- AI may not mutate that position;
- the user can edit protection, reduce, close, or return control to AI.

A visible state is required:

- AI MANAGED
- RECOMMEND ONLY
- MANUAL OVERRIDE
- PAUSED

No hidden authority transitions.

## Canonical Portfolio Loop

```text
Real Market Data
      |
      v
Exchange Instrument Catalog
      |
      v
Quant Features / Opportunity Scanner
      |
      v
Jev Fast Judgment
      |
      v
Cost- and Risk-aware Escalation
      |
      v
GPT-6 Luna + Crypto Skill
      |
      v
Portfolio Brain
      |
      v
Structured Trade / Re-plan Proposal
      |
      v
Deterministic Risk + Budget + Freshness Gates
      |
      +--------------------+
      |                    |
      v                    v
AUTO_PAPER            RECOMMEND_ONLY
      |                    |
      v                    v
Paper Execution       Human Review / Edit / Apply
      |                    |
      +----------+---------+
                 |
                 v
          Position Manager
                 |
                 v
         Outcome / Journal
                 |
                 v
       Evaluation / Learning
```

## 1. Information Architecture

Replace the current research-heavy navigation with a simpler daily-user hierarchy.

Target top-level navigation:

- Overview
- Portfolio
- Trade
- Activity
- Research
- Evaluations
- Settings

Avoid separate top-level destinations for concepts that are naturally part of the trading workflow.

### Overview

Purpose: "What is happening and what needs my decision?"

Show:

- equity;
- today/experiment trading PnL;
- AI cost;
- net economic PnL;
- current/max drawdown;
- open risk;
- open positions count;
- pending orders count;
- scheduler state;
- market-data health;
- AI budget remaining;
- attention queue;
- changes since last visit;
- latest meaningful AI actions/recommendations.

Do not show a wall of engineering telemetry.

### Portfolio

Unified portfolio view with explicit account boundaries:

- PAPER Spot wallet/holdings;
- PAPER Perpetual wallet/positions;
- optional REAL Gate Account mirror, clearly read-only and visually separated.

Support:

- allocation;
- cash/quote balances;
- spot holdings;
- futures margin/exposure;
- realized/unrealized PnL;
- concentration;
- equity curve;
- drawdown;
- AI cost;
- economic PnL;
- portfolio risk.

Never combine real Gate equity with PAPER equity.

### Trade

The main market workspace:

- Spot / Perpetual selector;
- live exchange instrument selector;
- live chart;
- compact market stats;
- current AI plan;
- current position/order for selected symbol;
- PAPER trade ticket;
- AI analysis/re-plan entry point;
- evidence drawer.

### Activity

Unified human-readable journal:

- opportunity detected;
- quant gate;
- Jev decision;
- Luna escalation;
- AI proposal;
- risk resize/reject;
- user approval/rejection/edit;
- PAPER order;
- fill;
- stop/TP change;
- manual override;
- funding/fee;
- position close;
- outcome.

Filter by:

- symbol;
- position;
- event;
- source: AI / USER / SYSTEM;
- Spot / Perpetual;
- severity.

### Research

Keep deeper:

- Runs;
- Decisions;
- Watchlist;
- evidence;
- scenario analysis;
- saved research.

Research supports trading; it does not dominate daily operations.

### Evaluations

Keep:

- strategy tournament;
- arms;
- leverage cohorts;
- regime/asset breakdowns;
- decision quality;
- portfolio PnL;
- AI incremental value;
- AI cost efficiency.

### Settings

Move engineering/configuration detail here:

- AI providers;
- Exchange accounts;
- AI cost/price book;
- budgets;
- global risk policy;
- scheduler defaults;
- data retention/export;
- advanced experiment configuration.

## 2. Overview / Attention Queue

The Overview must prioritize decisions over metrics.

Suggested hierarchy:

```text
Portfolio $108.42    +8.42%
Economic PnL +$6.84
Drawdown -4.8%
AI spend $1.13 / $2.00 today
Agent RUNNING | Gate LIVE | Next scan 06:46

Needs attention
------------------------------------------------
SOL +6.4%   AI suggests protect profit        Review
SUI         0.8% from stop                    Review
AI budget   81% used                          Review
------------------------------------------------

Open positions
...
```

Attention severity:

- CRITICAL
- ACTION
- WATCH
- INFO

Only actionable or important changes should surface.

Do not generate alert spam for every model call.

## 3. Exchange-backed Instrument Catalog

Production trading views must not use hard-coded coin lists as truth.

Add an exchange-neutral normalized instrument catalog.

Initial source: Gate.

Support:

### Spot

Gate Spot currency-pair metadata.

Fields include at least:

- instrument_id
- exchange
- market_type=spot
- exchange_symbol
- display_symbol
- base
- quote
- status/tradable
- price precision/tick
- amount precision/step
- min/max order constraints
- 24h quote volume when available
- spread/liquidity state when available
- refreshed_at

### Perpetual

Gate USDT perpetual contract metadata.

Include:

- settle currency
- min/max leverage
- maintenance rate
- funding state
- mark/index/last
- contract constraints
- status/delist state.

### Selector UX

Support:

- Spot / Perpetual toggle;
- search;
- favorites;
- recent instruments;
- base/quote;
- 24h change;
- volume/liquidity;
- spread;
- tradable status;
- strategy compatibility.

Cache server-side with explicit freshness.

Delisted/untradable instruments must fail closed.

## 4. Spot PAPER Trading

Add Spot as a first-class domain, not as futures with leverage=1.

Spot supports:

- buy/sell;
- base and quote balances;
- average cost;
- realized PnL;
- unrealized PnL;
- allocation;
- market orders;
- limit orders;
- partial fills;
- fees;
- PAPER stop/target plan where strategy requires;
- rebalancing;
- portfolio concentration checks.

Spot does not have:

- leverage;
- liquidation;
- maintenance margin;
- funding;
- short exposure by default.

Keep spot accounting/contracts isolated from futures-only mechanics while sharing common order/journal interfaces.

## 5. Portfolio Brain

Add a portfolio-level decision layer above per-symbol analysis.

The Portfolio Brain must consider:

- total equity;
- cash/quote balance;
- Spot allocations;
- Perpetual exposure;
- long/short net and gross exposure;
- concentration;
- correlated positions;
- BTC/market regime;
- drawdown;
- existing open risk;
- pending orders;
- AI budget;
- current strategy/regime performance;
- asset-level opportunity ranking.

Example:

A SOL long setup may be good in isolation but rejected because total alt-long exposure is already too high.

The Portfolio Brain produces typed portfolio actions/reason codes, not arbitrary account mutations.

Examples:

- ALLOW_NEW_POSITION
- REDUCE_NEW_POSITION_SIZE
- BLOCK_CONCENTRATION
- BLOCK_CORRELATED_EXPOSURE
- PREFER_SPOT
- PREFER_PERPETUAL
- HOLD_CASH
- DE_RISK_PORTFOLIO
- PROTECT_PROFIT
- REBALANCE

Deterministic risk remains authoritative.

## 6. Position Manager

Every position has a lifecycle:

```text
DISCOVER
  -> PLAN
  -> ENTER
  -> MANAGE
  -> PROTECT
  -> SCALE / REDUCE
  -> EXIT
  -> REVIEW
```

### Futures position detail

Show:

- symbol;
- side;
- leverage;
- quantity;
- notional;
- entry;
- mark;
- unrealized PnL;
- realized PnL for partial exits;
- margin;
- liquidation estimate/buffer;
- stop;
- target(s);
- funding;
- fees/slippage;
- AI management state;
- thesis status;
- last AI review;
- next scheduled review;
- risk state;
- decision/evidence link.

Actions:

- Edit Stop
- Edit Take Profit
- Reduce 25/50/75/custom
- Close
- Pause AI management
- Resume AI management
- Take manual control
- Return control to AI
- Ask AI to Re-plan
- View evidence/journal

### Spot position/holding detail

Show:

- quantity;
- average cost;
- current value;
- unrealized/realized PnL;
- allocation;
- concentration;
- current AI plan;
- protection/rebalance plan;
- last/next AI review.

Actions:

- Buy more PAPER
- Sell/reduce PAPER
- Close holding PAPER
- Edit plan/protection
- Pause/resume AI management
- Ask AI to Re-plan

## 7. Orders

Create a dedicated unified order model/view for PAPER Spot and Perpetual orders.

Show:

- market type;
- symbol;
- side/action;
- order type;
- requested quantity/notional;
- filled/remaining;
- limit/trigger;
- status;
- source: AI / USER;
- related position;
- created/updated timestamps.

Actions:

- Cancel pending order
- Amend pending limit/conditional order where safe
- Inspect risk/audit trail

Semantic rule:

- Cancel applies to pending orders.
- Close applies to positions.
- Protection changes apply to open positions.
- Amend may be implemented internally as cancel/replace when required by the simulator contract.

Every change is audited.

## 8. AI Re-plan

Re-plan is a structured workflow, not a chatbot.

Inputs:

- current position/holding;
- original thesis;
- latest market state;
- portfolio context;
- risk context;
- user instruction, optional.

Quick user intents may include:

- Tighten risk
- Protect profit
- Give it more room
- Reduce exposure
- Exit if thesis weakened
- Reassess from scratch

Output must be a typed proposal.

UI presents:

```text
CURRENT                       PROPOSED

Stop       148.0       ->     150.5
TP1        156.0       ->     156.0
TP2        163.0       ->     160.0
Size       100%        ->      75%
Risk       $0.86       ->      $0.41
Control    AI managed  ->     AI managed

Why
- momentum weakened
- BTC risk increased
- position already in profit

[Apply] [Edit] [Reject]
```

Requirements:

- show before/after diff;
- show estimated risk impact;
- show portfolio exposure impact;
- show economic impact when estimable;
- no chain-of-thought;
- concise reason codes and evidence;
- user can inspect evidence separately;
- AI never silently applies a change under RECOMMEND_ONLY;
- AUTO_PAPER may apply only within declared policy and must journal the action.

## 9. Human Override and Control

Human controls are first-class.

Per position:

- AI-managed;
- recommend-only;
- manual override;
- paused.

Global:

- Pause automation;
- Resume automation;
- Pause new entries but continue monitoring;
- Close no positions automatically;
- Emergency stop for new PAPER actions.

Manual action always produces an immutable audit event.

User actions never bypass deterministic risk/accounting invariants.

## 10. Market / Trade Workspace

Desktop target:

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Gate LIVE · PAPER EXECUTION · EXP-004 RUNNING · AI $1.18/$2.00      │
├──────────────────────────────────────────────────────────────────────┤
│ [Spot|Perp] [BTC/USDT ▼] [1m 5m 15m 1h 4h]                         │
├────────────────────────────────────────────┬─────────────────────────┤
│                                            │ Current AI Plan         │
│              LIVE CHART                    │ LONG / WAIT / NO TRADE  │
│                                            │ Entry / SL / TP / Risk  │
│ entry / stop / targets / fills / liq       │ [Re-plan] [Evidence]    │
│                                            ├─────────────────────────┤
│                                            │ PAPER Trade             │
│                                            │ risk-first ticket       │
├────────────────────────────────────────────┴─────────────────────────┤
│ Position / Holding     Orders     Activity                           │
└──────────────────────────────────────────────────────────────────────┘
```

Do not replicate a full exchange terminal with unnecessary order-book widgets or indicator clutter.

Always visible:

- market type;
- symbol;
- live/stale state;
- price/chart;
- current position/order;
- current AI plan;
- risk/action controls.

Progressively disclosed:

- raw evidence;
- detailed model routing;
- low-level telemetry;
- advanced indicators.

## 11. Live Chart

Continue using the current pinned Lightweight Charts implementation.

Support Spot and Perpetual.

Required overlays:

- average entry;
- individual fills;
- current stop;
- TP1/TP2/TP3;
- liquidation for Perpetual;
- pending limit/trigger orders;
- AI entry/plan zones;
- user/AI action markers.

Optional interaction:

Allow drag-to-adjust stop/target only if:

1. drag creates a draft, not an immediate mutation;
2. risk preview is recalculated server-side;
3. the user confirms if risk increases materially;
4. AUTO_PAPER policy does not make an unreviewed manual drag authoritative;
5. all changes are audited.

## 12. PAPER Trade Ticket

Manual PAPER trading is required because the human and AI jointly manage the portfolio.

Use the same deterministic risk/execution/accounting path as AI trades.

### Perpetual

Inputs:

- Long / Short
- Market / Limit
- risk percentage or risk amount
- stop
- target(s)
- leverage
- optional advanced quantity/notional

Derived server-side:

- authoritative quantity;
- notional;
- margin;
- max loss;
- estimated fee;
- estimated slippage;
- liquidation buffer.

Primary action:

- Simulate Long
- Simulate Short

### Spot

Inputs:

- Buy / Sell
- Market / Limit
- amount / quote spend
- optional portfolio-risk/allocation target
- optional stop/plan.

Primary action:

- Simulate Buy
- Simulate Sell

Never use "Place live order" language.

## 13. Strategy Tournament

Treat strategy evaluation as a continuous experiment.

Prospective arms may include:

- Quant
- Jev
- Luna
- Luna + Skill
- Hybrid
- Hybrid + Portfolio Brain

Preserve leverage cohorts where relevant.

Use:

- identical frozen inputs;
- aligned market windows;
- identical execution assumptions;
- separate wallets/state;
- explicit sample denominators.

Evaluate:

- net trading PnL;
- economic PnL after AI cost;
- expectancy;
- profit factor;
- Sharpe/Sortino when valid;
- max drawdown;
- MFE/MAE;
- fee/funding/slippage drag;
- AI cost;
- AI cost per eligible case/trade;
- incremental PnL vs incremental AI cost;
- performance by regime/asset/session.

No treatment may be promoted solely on headline PnL.

## 14. Learning Loop

Every completed position/trade must support post-trade diagnosis.

Capture:

- original signal;
- quant state;
- Jev state;
- Luna state;
- Portfolio Brain state;
- risk decision;
- entry;
- management actions;
- user overrides;
- exit;
- costs;
- MFE/MAE;
- outcome;
- AI cost.

Use a typed failure/learning taxonomy.

Initial examples:

- BAD_DIRECTION
- LATE_ENTRY
- FALSE_BREAKOUT
- STOP_TOO_TIGHT
- STOP_TOO_WIDE
- TARGET_TOO_AMBITIOUS
- PREMATURE_EXIT
- REGIME_MISCLASSIFIED
- CORRELATION_OVEREXPOSURE
- BTC_SHOCK
- FUNDING_DRAG
- SLIPPAGE_DRAG
- AI_COST_TOO_HIGH
- AI_FILTER_HELPED
- AI_OVERRIDE_HURT
- HUMAN_OVERRIDE_HELPED
- HUMAN_OVERRIDE_HURT

This taxonomy supports evaluation and later experiment design.

Do not automatically rewrite production strategy/prompts from a few losing trades.

## 15. AI Cost and Economic PnL

Preserve the current AI cost ledger and hard budget guard.

Daily workflow should show only:

- AI spend today;
- experiment AI spend;
- budget remaining;
- net economic PnL after AI cost.

Advanced details live in Settings/Evaluations:

- price book;
- token usage;
- provider/model costs;
- cost per call;
- latency;
- cost per trade;
- NO_TRADE cost;
- escalation cost.

Portfolio decisions should be cost-aware.

Examples:

- Quant finds no setup -> no Jev/Luna.
- Jev confidently resolves a simple setup -> no Luna.
- Complex/high-impact conflict -> Luna.
- Budget exhausted -> apply configured deterministic fallback.

The model never decides its own budget.

## 16. Scheduler / Automation UX

Daily control must be simple.

Show:

- RUNNING / PAUSED / STOPPED;
- universe;
- Spot / Perpetual coverage;
- decision timeframe;
- next scan;
- last scan;
- AI budget remaining;
- risk profile.

Actions:

- Start
- Pause
- Resume
- Stop new entries
- Stop experiment

Advanced cron/timing internals remain in Settings.

Open positions continue deterministic monitoring when new scans/AI calls are paused according to policy.

## 17. Alerts

Add an attention system with actionable priorities.

Examples:

CRITICAL:
- market feed stale while position is open;
- liquidation buffer critically low;
- accounting/reconciliation failure;
- scheduler unexpectedly stopped.

ACTION:
- AI recommends re-plan;
- position near stop;
- protect-profit opportunity;
- pending order needs review;
- AI budget near hard limit.

WATCH:
- thesis weakening;
- concentration increasing;
- funding worsening.

INFO:
- order filled;
- position closed;
- experiment export ready.

Deduplicate/reduce notification spam.

## 18. Decision / Evidence UX

Progressive disclosure:

```text
Decision
  -> Key Reasons
  -> Risk / Portfolio Impact
  -> Scenarios
  -> Evidence
  -> Provider / Routing Metadata
```

Default output should be concise.

No hidden chain-of-thought.

Show:

- decision;
- confidence;
- state;
- entry/stop/targets;
- portfolio effect;
- invalidation;
- 3-5 decisive signals;
- reason codes.

## 19. Activity / Journal

Activity should read like a timeline a human can understand.

Example:

```text
06:31  BTCUSDT  Signal detected         SYSTEM
06:31  BTCUSDT  Jev: breakout valid     AI
06:31  BTCUSDT  Luna escalated           AI
06:32  BTCUSDT  LONG plan proposed       AI
06:32  BTCUSDT  Risk resized 0.42→0.31   SYSTEM
06:32  BTCUSDT  PAPER order filled       SYSTEM
07:16  BTCUSDT  Stop moved to 67,100     AI
08:03  BTCUSDT  Reduced 25%              USER
...
```

Provide drill-down without exposing implementation noise by default.

## 20. Mobile

Desktop is primary.

Mobile must support the critical control loop:

- portfolio glance;
- attention queue;
- position risk;
- position detail;
- adjust stop;
- reduce/close;
- pause/resume automation;
- alerts.

Do not reproduce the entire desktop trading terminal on mobile.

Use compact cards/tabs rather than wide tables.

## 21. UX / Visual Direction

Maintain:

- professional quantitative/research tool;
- neutral/light surfaces;
- restrained blue accent;
- green/red only for financial/state meaning;
- amber for caution/wait;
- compact data tables;
- charts as primary visual information;
- 6-10px radius;
- minimal shadow;
- low prose.

Avoid:

- gradients;
- glow;
- mascots;
- marketing hero copy;
- excessive cards;
- huge empty spacing;
- fake AI branding;
- decorative KPIs;
- chat-first UX.

Every visible control must work.

## 22. Current UX Debt To Fix

Known debt from current main:

- Overview still contains fixture market-snapshot remnants.
- `paperTrading.js` is monolithic and mixes daily-user workflow with engineering/experiment controls.
- Production instrument selection still relies on hard-coded catalog assumptions in some paths.
- Open positions do not yet provide a complete management workflow.
- No first-class Spot PAPER runtime/accounting/trading UX.
- No structured AI Re-plan position workflow.
- No explicit per-position AI/manual authority state.
- Research/evaluation telemetry is too close to daily trading controls.
- Portfolio/positions/orders are not yet the primary information architecture.
- Some Settings functionality belongs outside the daily workspace.
- Current UI does not yet express "what changed since last visit" or a prioritized attention queue.

## 23. Backend / API Additions

Reuse existing endpoints where possible.

Add or extend exchange-neutral contracts/endpoints for:

- `GET /api/markets`
- `GET /api/markets/{instrument_id}`
- `GET /api/portfolio`
- `GET /api/positions`
- `GET /api/positions/{id}`
- `PATCH /api/positions/{id}/protection`
- `POST /api/positions/{id}/reduce`
- `POST /api/positions/{id}/close`
- `POST /api/positions/{id}/replan`
- `POST /api/positions/{id}/management-mode`
- `GET /api/orders`
- `POST /api/orders/preview`
- `POST /api/orders`
- `PATCH /api/orders/{id}`
- `DELETE /api/orders/{id}`
- `POST /api/portfolio/review`
- `GET /api/attention`
- `GET /api/activity`
- `GET /api/runtime/stream`
- `POST /api/experiment/validate`

Exact route names may adapt to current server conventions.

All mutations are PAPER-local.

## 24. Domain Contracts To Add

Suggested schemas:

- market-instrument
- spot-wallet
- spot-holding
- unified-position-view
- paper-order-request
- paper-order-preview
- position-protection-update
- position-management-mode
- position-replan-request
- position-replan-proposal
- portfolio-state
- portfolio-brain-decision
- attention-event
- activity-event
- post-trade-review
- learning-tag

Execution-critical values remain typed fields.

## 25. Frontend Refactor

The current large Paper Trading view should become composition.

Suggested components:

- TradingStatusBar
- PortfolioKpiStrip
- AttentionQueue
- MarketSelector
- LiveTradingChart
- AiPlanPanel
- PaperTradeTicket
- PositionsTable
- PositionDetailDrawer
- OrdersPanel
- PortfolioAllocation
- ExperimentToolbar
- AiCostSummary
- ActivityTimeline
- ReplanPanel
- StrategyTournamentSummary

Keep current vanilla ES-module architecture unless a repository-level decision explicitly changes it.

Do not introduce a framework migration as part of this goal.

## 26. Development Phases

### Phase 0 — Baseline and refactor safety

- inspect current main;
- run baseline tests;
- split the current Paper Trading monolith;
- preserve all existing functionality and real integration.

Exit:
- no regression;
- daily-user and advanced-setting concerns can be developed independently.

### Phase 1 — Portfolio-first shell

Implement:

- new navigation;
- Overview cockpit;
- Portfolio page;
- unified Positions/Orders summary;
- attention queue;
- experiment status bar.

Exit:
- user understands portfolio/attention state without opening engineering views.

### Phase 2 — Exchange catalog + unified Market selector

Implement:

- Gate Spot catalog;
- Gate Perpetual catalog normalization;
- searchable Spot/Perpetual selector;
- favorites/recent;
- tradable/liquidity metadata;
- remove hard-coded production asset catalog as authority.

Exit:
- every tradeable selection comes from exchange metadata.

### Phase 3 — Spot PAPER domain

Implement:

- Spot market data;
- Spot wallet/holding accounting;
- Spot order simulator;
- fees/partial fills;
- average cost/realized PnL;
- Spot risk/allocation rules;
- Spot UI.

Exit:
- end-to-end Spot PAPER buy/sell/hold/reduce/close works.

### Phase 4 — Trading workspace + manual PAPER ticket

Implement:

- unified Trade workspace;
- risk-first futures ticket;
- Spot ticket;
- live chart overlays;
- server risk preview;
- manual PAPER trades.

Exit:
- human can trade through the same deterministic engine as AI.

### Phase 5 — Position Manager

Implement:

- Position Detail Drawer;
- stop/target updates;
- reduce/close;
- order cancel/amend;
- management modes;
- audit events.

Exit:
- all active PAPER positions/orders are controllable from UI.

### Phase 6 — AI Re-plan + Portfolio Brain

Implement:

- structured re-plan;
- before/after diff;
- portfolio-aware opportunity gate;
- portfolio review;
- AI recommendation/apply policy;
- AUTO_PAPER vs RECOMMEND_ONLY behavior.

Exit:
- AI can manage portfolio/positions while human authority stays explicit.

### Phase 7 — Strategy Tournament + Learning

Implement:

- Portfolio Brain arm;
- aligned strategy comparison;
- post-trade diagnosis taxonomy;
- AI incremental cost/benefit views.

Exit:
- system can identify which decision stack helps/hurts under which regimes.

### Phase 8 — Activity / alerts / responsive hardening

Implement:

- unified journal;
- attention dedupe/severity;
- mobile control flows;
- accessibility;
- browser/E2E acceptance.

## 27. Tests

### Market catalog

- Spot metadata normalization
- Perpetual metadata normalization
- tradable/delist handling
- precision/minimums
- caching/freshness

### Spot accounting

- buy/sell
- average cost
- realized/unrealized PnL
- fees
- partial fills
- insufficient quote/base
- allocation/concentration

### Position/order management

- stop update
- target update
- risk-increasing update
- reduce
- close
- cancel
- amend/cancel-replace
- duplicate request/idempotency
- manual override
- return to AI

### Re-plan

- structured proposal validation
- diff
- no silent mutation in recommend-only
- auto-paper applies only permitted changes
- risk rejection
- stale data rejection
- budget rejection

### Portfolio Brain

- concentration block
- correlated exposure
- cash reserve
- drawdown/de-risk
- no mutation bypassing risk

### UX

- Overview state
- attention queue
- Market selector
- Trade ticket
- Position drawer
- Orders
- Activity
- Spot/perp differences
- mobile
- keyboard/focus
- non-color-only state

### Safety

- Gate writes blocked before transport
- secrets never browser/SQLite/log/export
- real mirror never merges with PAPER
- stale market blocks new entries
- closed-candle decision boundary
- budget guard remains authoritative

## 28. Acceptance Checklist

### Overview
- [ ] Portfolio state understood within one screen.
- [ ] Trading PnL and economic PnL both visible.
- [ ] AI cost/budget visible.
- [ ] Open positions and attention items visible.
- [ ] Scheduler/market health visible.
- [ ] No fixture cards presented as live data.

### Portfolio
- [ ] PAPER Spot and Perpetual shown together but correctly categorized.
- [ ] REAL Gate mirror is visually and logically separate.
- [ ] Allocation/exposure/concentration visible.
- [ ] Equity/drawdown reconcile to runtime.

### Market / Trade
- [ ] Spot/Perpetual toggle.
- [ ] Exchange-backed symbol selector.
- [ ] Live chart.
- [ ] Current AI plan.
- [ ] Manual PAPER ticket.
- [ ] Risk preview is server-authoritative.

### Position Management
- [ ] Full detail.
- [ ] Edit stop.
- [ ] Edit targets.
- [ ] Reduce.
- [ ] Close.
- [ ] Pause/resume AI.
- [ ] Manual override.
- [ ] Re-plan.
- [ ] Audit trail.

### Orders
- [ ] Pending orders visible.
- [ ] Filled/remaining.
- [ ] Source AI/User.
- [ ] Cancel.
- [ ] Amend where supported.
- [ ] Cancel is not confused with Close.

### Spot
- [ ] Live Gate Spot data.
- [ ] Spot instrument catalog.
- [ ] Wallet/holdings.
- [ ] Buy/sell simulator.
- [ ] Average cost/PnL.
- [ ] No futures-only fields.

### Futures
- [ ] Long/short.
- [ ] Leverage/margin/liquidation.
- [ ] Funding.
- [ ] Stops/targets.
- [ ] Risk controls unchanged.

### Re-plan
- [ ] Structured proposal.
- [ ] Before/after diff.
- [ ] Risk/exposure impact.
- [ ] Apply/Edit/Reject.
- [ ] Authority mode respected.

### Portfolio Brain
- [ ] Portfolio context affects opportunity decisions.
- [ ] Concentration/exposure controls.
- [ ] Portfolio review endpoint/UI.
- [ ] No direct bypass of deterministic risk.

### Strategy Tournament
- [ ] Aligned arms.
- [ ] Portfolio Brain arm.
- [ ] AI cost included.
- [ ] Sample counts visible.

### Activity / Alerts
- [ ] Human-readable timeline.
- [ ] Actionable severity.
- [ ] Filter by source/symbol/type.
- [ ] No spam from every price tick/model token.

### Responsive / Accessibility
- [ ] Critical mobile flows work.
- [ ] Keyboard navigable.
- [ ] Focus management correct.
- [ ] State not conveyed by color alone.
- [ ] Charts have text/table fallback where necessary.

### Safety
- [ ] PAPER only.
- [ ] Gate real writes blocked by design.
- [ ] Real account mirror read-only.
- [ ] Secrets remain backend-only.
- [ ] Risk/budget/freshness gates authoritative.

## 29. Definition of Done

This branch is complete when a user can:

1. open the app and immediately understand portfolio health and attention items;
2. browse actual Gate Spot and Perpetual instruments;
3. watch real market data;
4. allow AI to discover and autonomously PAPER trade under AUTO_PAPER;
5. run RECOMMEND_ONLY and approve/edit/reject AI proposals;
6. manually create PAPER Spot or Perpetual trades;
7. inspect every open holding/position and pending order;
8. edit protection, reduce, close, cancel, or amend safely;
9. move a position between AI-managed and manual-control modes;
10. ask AI to re-plan and see a structured before/after proposal;
11. receive portfolio-aware AI decisions rather than isolated per-symbol decisions;
12. see trading PnL, AI cost, and net economic result;
13. review the complete human/AI/system journal;
14. compare strategy/AI arms prospectively;
15. export a complete audit/evaluation bundle;
16. operate all of this while Gate real-money writes remain technically impossible.
