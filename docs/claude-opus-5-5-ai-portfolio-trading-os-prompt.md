# Claude Opus 5.5 — AI Portfolio Trading OS Implementation Prompt

You are the lead engineer responsible for implementing the next major product phase of this repository.

Repository:
https://github.com/myh-st/crypto-skills

Required branch:
feature/ai-portfolio-trading-os

Use your highest available reasoning effort.

## Canonical Documents

Read these completely before editing:

1. CLAUDE.md
2. .goals/ai-portfolio-trading-os/goal.md
3. docs/ai-portfolio-trading-os-plan.md
4. docs/paper-futures-runtime.md
5. skills/crypto-market-trading-analysis/SKILL.md and relevant references
6. current frontend modules/tests
7. current PAPER runtime/server/contracts/tests
8. current Gate, AI-cost, Jev, Foundry, and real-integration modules

The AI Portfolio Trading OS goal and plan are canonical for this branch.

Do not reconstruct or follow superseded phase plans from git history.

## Mission

Transform the existing real-AI/live-Gate PAPER research lab into a portfolio-first AI trading operating system.

The product must let AI and human jointly:

- discover opportunities;
- plan trades;
- execute PAPER trades;
- manage positions;
- manage a whole portfolio rather than isolated symbols;
- recommend and apply re-plans;
- support Spot and Perpetual;
- override each other safely according to explicit authority modes;
- measure trading result after fees/funding/slippage and AI cost;
- review every event;
- learn prospectively which strategy/AI stack actually adds economic value.

Do not merely make the UI prettier.

Build the product loop.

## Non-Negotiable Safety Boundary

This branch remains PAPER execution only.

Real Gate public market data is allowed.
Real Jev/GPT calls are allowed.
Real Gate read-only account sync is allowed.

Real Gate writes remain BLOCKED BY DESIGN.

Do not add any network path capable of:

- real order creation;
- real order amendment;
- real order cancellation;
- real leverage/margin changes;
- transfers;
- withdrawals.

Preserve the current pre-transport block.

## Preserve Existing Strengths

Do not rewrite working foundations:

- Gate perpetual REST/WebSocket
- backend SSE
- real Jev
- Azure AI Foundry GPT-6 Luna reasoning=max
- deterministic risk
- PAPER execution/accounting
- scheduler
- monitor
- AI cost ledger
- hard budget guard
- Keychain/secret handling
- export/evaluation
- real-integration acceptance
- point-in-time guarantees

Extend them.

## Product Principle

The first screen should answer:

- What is my portfolio worth?
- What changed?
- Am I profitable after AI cost?
- What positions are open?
- What needs my attention?
- What does AI recommend?
- Is automation healthy?
- What can I do now?

The UI is a trading cockpit, not an AI demo.

Low prose.
Charts/tables/actions first.
No marketing copy.
No decorative KPI wall.
No fake controls.

## Navigation Target

Refactor toward:

- Overview
- Portfolio
- Trade
- Activity
- Research
- Evaluations
- Settings

Consolidate current Runs/Decisions/Watchlist under Research where practical.

Paper Trading should evolve into the Trade/Portfolio workflow rather than remain an engineering-heavy page.

## Phase 0 — Baseline

Before editing:

1. confirm branch;
2. inspect git status;
3. inspect current main-derived implementation;
4. run baseline:
   - python3 scripts/validate_repo.py
   - python3 -m unittest discover -s tests -v
   - node --test frontend/tests/*.test.mjs
   - find frontend -name '*.js' -exec node --check {} \;
5. record pre-existing failures.

Do not attribute existing failures to your work.

## Phase 1 — Refactor Current Paper Trading UI Safely

The current Paper Trading view is too monolithic.

Split daily product concerns into reusable ES modules while preserving behavior.

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
- ExperimentToolbar
- AiCostSummary
- ActivityTimeline
- ReplanPanel
- StrategyTournamentSummary

Do not introduce React/Vue or a build system merely to perform this refactor.

Keep current vanilla ES-module style.

Exit gate:
- existing behavior works;
- existing tests pass;
- new components are independently testable.

## Phase 2 — Portfolio-First UX

Implement:

### Overview

Show compactly:

- Equity
- Trading PnL
- Net economic PnL after AI cost
- Drawdown
- Open risk
- AI spend/budget
- automation health
- attention queue
- open positions

Remove/demote fixture market cards when real runtime state exists.

### Portfolio

Support explicit sections for:

- PAPER Spot
- PAPER Perpetual
- REAL Gate Account Mirror — READ ONLY

Never merge real and paper balances.

Show:

- allocation
- cash/quote balances
- Spot holdings
- futures exposure
- realized/unrealized PnL
- concentration
- equity
- drawdown
- AI cost
- economic result

## Phase 3 — Gate Spot + Unified Instrument Catalog

Production symbol selection must come from real exchange metadata.

Implement exchange-neutral normalized catalog.

Gate initial support:

### Spot

Use current official Gate Spot API documentation.

Normalize:
- pair
- base
- quote
- trade status
- precision
- min/max constraints
- ticker/volume/change/spread where available

### Perpetual

Reuse/extend current Gate futures contract metadata.

Frontend selector:

- Spot / Perpetual
- search
- favorites
- recent
- base/quote
- 24h change
- volume/liquidity
- spread
- tradable state

Do not keep hard-coded ASSET_CATALOG as production trading truth.

Unsupported/delisted instruments fail closed.

## Phase 4 — Spot PAPER Domain

Do not model Spot as Futures with leverage=1.

Implement separate Spot accounting:

- base balance
- quote balance
- average cost
- realized PnL
- unrealized PnL
- allocation
- fees

Orders:

- market buy
- market sell
- limit buy
- limit sell
- partial-fill representation
- cancel pending
- amend/cancel-replace pending

No:

- leverage
- liquidation
- maintenance margin
- funding

Add Spot-specific risk:

- max allocation
- concentration
- minimum cash reserve
- available base/quote
- stale data
- exchange min order constraints

Exit gate:
- live Gate Spot data → PAPER Buy → holding → partial sell → close → correct accounting/export.

## Phase 5 — Unified PAPER Trade Workspace

Build:

- Spot/Perpetual selector
- instrument selector
- live chart
- compact live market stats
- current AI plan
- current position/orders
- risk-first manual PAPER ticket
- evidence drill-down

Perpetual ticket:

- Long / Short
- Market / Limit
- risk
- stop
- targets
- leverage

Server derives authoritative:

- quantity
- notional
- margin
- max loss
- fee
- slippage
- liquidation buffer

Spot ticket:

- Buy / Sell
- Market / Limit
- quote/base amount or allocation target
- optional protection plan

Buttons must say:

- Simulate Long
- Simulate Short
- Simulate Buy
- Simulate Sell

Never "Place live order".

Manual PAPER trades must use the same deterministic execution/risk/accounting path as AI trades.

## Phase 6 — Position / Order Manager

Implement a real Position Detail Drawer.

Perpetual fields:

- side
- leverage
- quantity/notional
- entry
- mark
- PnL
- margin
- liquidation
- stop
- targets
- funding
- fees/slippage
- AI thesis
- management mode
- last/next AI review
- risk state

Spot fields:

- quantity
- average cost
- current value
- PnL
- allocation
- AI plan
- concentration

Actions:

- Edit Stop
- Edit Target(s)
- Reduce 25/50/75/custom
- Close
- Pause AI
- Resume AI
- Manual Override
- Return Control to AI
- Ask AI to Re-plan
- View Evidence
- View Journal

Orders:

- list pending/filled/canceled
- AI/User source
- filled/remaining
- amend pending
- cancel pending

Semantic rule:
Cancel = pending order.
Close = position/holding.

All mutations go through backend validation and audit.

## Phase 7 — Explicit Management Authority

Persist per position:

- AUTO_PAPER
- RECOMMEND_ONLY
- MANUAL_OVERRIDE
- PAUSED

AUTO_PAPER:
AI may manage PAPER positions under policy.

RECOMMEND_ONLY:
AI proposes; user applies.

MANUAL_OVERRIDE:
AI may advise but cannot mutate.

PAUSED:
No AI position-management mutation.

No hidden mode changes.

UI must show current authority clearly.

## Phase 8 — AI Re-plan

This is not a chat-first workflow.

Input:

- current position/holding
- current plan/thesis
- latest market
- portfolio state
- risk state
- optional user quick intent

Quick intents:

- Tighten risk
- Protect profit
- Give it more room
- Reduce exposure
- Exit if thesis weakened
- Reassess from scratch

Return a structured proposal with before/after diff.

Show:

- Stop before → after
- Targets before → after
- Remaining size before → after
- Risk before → after
- Portfolio exposure before → after
- concise reason codes
- concise evidence summary

Actions:

- Apply
- Edit
- Reject

RECOMMEND_ONLY must never auto-apply.

AUTO_PAPER may apply only if deterministic policy allows and every mutation is journaled.

No chain-of-thought exposure.

## Phase 9 — Portfolio Brain

Add a portfolio-level decision layer.

Inputs:

- total equity
- cash
- Spot allocation
- futures net/gross exposure
- risk-at-stop
- concentration
- correlated exposure
- direction concentration
- BTC/market regime
- drawdown
- pending orders
- AI budget
- current strategy/regime performance

Typed outputs/reason codes:

- ALLOW
- RESIZE
- BLOCK_CONCENTRATION
- BLOCK_CORRELATED_EXPOSURE
- HOLD_CASH
- PREFER_SPOT
- PREFER_PERPETUAL
- DE_RISK
- PROTECT_PROFIT
- REBALANCE

Example:
A good AVAX long may be blocked because the portfolio already has excessive correlated alt-long exposure.

Portfolio Brain must never override RiskEngine.

## Phase 10 — Autonomous Position Review

Add a management-review queue.

Trigger review on:

- configured management candle;
- stop/target proximity;
- material volatility/regime change;
- thesis state change;
- portfolio exposure change;
- explicit user Re-plan request.

Do not call Luna on every price tick.

Use:

deterministic trigger → Jev → optional Luna → structured plan/action.

Budget guard remains authoritative.

## Phase 11 — Strategy Tournament

Prospectively compare aligned arms:

- Quant
- Jev
- Luna
- Luna + Skill
- Hybrid
- Hybrid + Portfolio Brain

Preserve separate state/wallets and aligned inputs.

Evaluate:

- net trading PnL
- net economic PnL
- expectancy
- profit factor
- drawdown
- MFE/MAE
- fees/funding/slippage
- AI cost
- incremental PnL
- incremental AI cost
- regime/asset/session performance

Never promote a treatment solely on headline PnL.

## Phase 12 — Learning Loop

After each closed trade/position create a typed review.

Capture:

- signal
- Jev
- Luna
- Portfolio Brain
- RiskEngine
- entry
- management actions
- user overrides
- exit
- fees/funding/slippage
- AI cost
- MFE/MAE
- outcome

Use typed tags such as:

- BAD_DIRECTION
- LATE_ENTRY
- FALSE_BREAKOUT
- STOP_TOO_TIGHT
- STOP_TOO_WIDE
- PREMATURE_EXIT
- REGIME_MISCLASSIFIED
- CORRELATION_OVEREXPOSURE
- FUNDING_DRAG
- AI_COST_TOO_HIGH
- AI_FILTER_HELPED
- AI_OVERRIDE_HURT
- HUMAN_OVERRIDE_HELPED
- HUMAN_OVERRIDE_HURT

Do not automatically rewrite the active strategy based on a few outcomes.

Store improvement hypotheses for the next experiment version.

## Phase 13 — Attention + Activity

Add unified human-readable Activity events:

source:
- AI
- USER
- SYSTEM

category:
- SIGNAL
- DECISION
- RISK
- ORDER
- FILL
- MANAGEMENT
- COST
- ALERT
- OUTCOME

severity:
- INFO
- WATCH
- ACTION
- CRITICAL

Create Attention Queue from actionable runtime conditions.

Deduplicate alerts.

Do not surface every low-level provider call as a user alert.

## AI Cost / Economics

Preserve existing hard budget controls.

Daily UI:
- AI spend today
- experiment spend
- budget remaining
- trading PnL
- economic PnL after AI

Advanced:
- provider/model
- tokens
- cost/call
- cost/trade
- NO_TRADE cost
- escalation cost
- latency

AI invocation itself is a capital-allocation decision.

Quant → Jev → Luna only when additional reasoning is justified.

The model never owns its budget.

## Live Chart

Keep pinned Lightweight Charts.

Support Spot and Perpetual.

Overlays:

- entry
- fills
- stop
- TP1/2/3
- liquidation for Perpetual
- pending orders
- AI plan zones
- management action markers

If adding drag-to-adjust:
- drag only creates draft;
- backend recalculates risk;
- confirm risk-increasing changes;
- persist audit.

## Realtime Architecture

Keep exchange streams in Python backend.

Browser consumes normalized same-origin SSE/REST.

Do not put Gate credentials in browser.

Add runtime/activity event stream if useful.

## New/Extended API Direction

Implement equivalent repository-conformant endpoints for:

- market catalog
- portfolio
- positions
- position protection
- position reduce/close
- management mode
- re-plan
- orders preview/create/amend/cancel
- attention
- activity
- portfolio review
- experiment validate
- runtime event stream

Exact route names may follow current conventions.

All state-changing trading endpoints remain PAPER-only.

## Contracts

Add versioned schemas/contracts for:

- MarketInstrument
- SpotWallet
- SpotHolding
- UnifiedPositionView
- PaperOrderRequest
- PaperOrderPreview
- PositionProtectionUpdate
- PositionManagementMode
- PositionPlan
- PositionReplanRequest
- PositionReplanProposal
- PortfolioState
- PortfolioBrainDecision
- AttentionEvent
- ActivityEvent
- PostTradeReview
- LearningTag

Do not parse prose for execution-critical values.

## UI Design

Keep current professional quantitative direction.

Use:
- neutral/light surfaces
- restrained blue
- semantic green/red/amber
- compact tables
- charts
- small radius
- minimal shadow

Avoid:
- gradients
- glow
- mascots
- large hero text
- AI-marketing language
- excessive rounded cards
- helper-text walls
- fake metrics
- dead buttons

Progressive disclosure:
Decision → Key reasons → Risk/portfolio impact → Scenarios → Evidence → technical metadata.

## Mobile / Accessibility

Desktop first.

Mobile critical flows:

- portfolio glance
- attention
- position risk
- stop edit
- reduce/close
- re-plan review
- pause/resume automation

Do not cram desktop terminal into mobile.

Accessibility:
- no red/green-only meaning
- visible focus
- correct drawer/dialog focus
- descriptive actions
- no aria-live price-tick spam
- chart summary/table fallback

## Confirmation Behavior

Avoid confirmation fatigue.

Confirm:
- full close
- large reduce
- materially increased risk
- return from manual control to autonomous AI
- global automation changes that alter management behavior

Do not require heavy confirmation for low-risk navigation/review.

## Tests

Preserve all current tests.

Add deterministic tests for:

### Catalog
- Spot/perp normalization
- tradable/delist
- constraints
- freshness

### Spot
- buy/sell
- average cost
- realized/unrealized
- fees
- partial fills
- balance errors
- allocation risk

### Orders/positions
- preview
- open
- amend
- cancel
- protection update
- reduce
- close
- idempotency
- audit

### Authority
- AUTO_PAPER
- RECOMMEND_ONLY
- MANUAL_OVERRIDE
- PAUSED
- forbidden transitions/actions

### Re-plan
- schema
- diff
- apply/edit/reject
- stale market
- risk rejection
- budget rejection
- recommend-only no mutation

### Portfolio Brain
- concentration
- correlation
- drawdown
- cash reserve
- no risk bypass

### Activity/attention
- event generation
- severity
- dedupe
- resolution

### Safety
- Gate write transport blocked
- real mirror separate from PAPER
- secrets absent from browser/SQLite/log/export
- stale feed blocks new entries
- budget guard authoritative
- historical point-in-time boundaries preserved

### Frontend
- navigation
- Overview
- Portfolio
- Market selector
- Trade ticket
- Position drawer
- Orders
- Re-plan
- Attention
- Activity
- Spot/perp differences
- responsive
- accessibility-critical behavior

CI remains offline.

## Browser Verification

Run the local app and exercise real UI behavior.

Verify at least:

1. Overview communicates current portfolio/attention state quickly.
2. Gate Spot and Perpetual catalogs populate.
3. Market selector searches real exchange-supported pairs.
4. Live chart changes with selection.
5. Manual Spot PAPER trade works.
6. Manual Perpetual PAPER trade works.
7. AI-generated position appears consistently.
8. Position management works.
9. Pending order management works.
10. Management modes work.
11. Re-plan produces structured diff.
12. Apply/Edit/Reject works.
13. AI cost/economic PnL is correct.
14. Activity is understandable.
15. Attention items resolve.
16. Mobile critical actions remain usable.
17. Real Gate mirror is unmistakably read-only.
18. No live Gate write exists.

Use real public Gate market data for local acceptance where available.

Use real Jev/Foundry only where the test is explicitly local and credentials are already configured.

Never add paid external calls to CI.

## Development Style

Work continuously.

Do not stop after:
- plan
- schemas
- scaffolding
- component extraction
- one vertical slice
- mocked UI

Continue through the canonical goal until the achievable branch scope is complete.

Use logical commits but continue after each commit.

Do not ask for confirmation between normal phases.

Do not rewrite unrelated user work.

Do not merge to main.

## Validation Before Completion

Run:

python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
node --test frontend/tests/*.test.mjs
find frontend -name '*.js' -exec node --check {} \;

Run any additional checks introduced.

Run the local app and complete the end-to-end acceptance workflow in the plan.

## Required Completion Report

Keep the final report concise:

### Implemented
Major completed features.

### User Flow
What the user can now do.

### Spot
Status and accounting validation.

### Perpetual
Status and position-management validation.

### AI / Portfolio Brain
Authority modes, re-plan, autonomous PAPER management.

### UX
Pages/workflows verified.

### Evaluation
Strategy tournament and learning loop status.

### Economics
AI cost + net economic PnL behavior.

### Validation
Exact test commands and pass counts.

### Safety
Confirm Gate live write remains BLOCKED BY DESIGN.

### Remaining Gaps
Only genuine deferred/blocking work.

### Git
Final SHA and push status.

## Final Success Criterion

The branch is successful when the product behaves like an AI-assisted portfolio/trading operating system rather than a research dashboard:

- AI can discover, recommend, and autonomously manage PAPER trades;
- the human can inspect, edit, override, pause, re-plan, reduce, close, and resume AI control;
- Spot and Perpetual are both first-class;
- selection uses real exchange instruments;
- decisions are portfolio-aware;
- every action is auditable;
- trading economics include AI cost;
- competing approaches can be evaluated prospectively;
- the platform can learn what helps and what hurts;
- and real Gate money-moving operations remain technically impossible.
