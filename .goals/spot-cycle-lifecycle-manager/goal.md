# Goal: Spot Cycle Lifecycle Manager

## Dependency gate

Do not implement until `feature/crash-execution-safety` is complete, reviewed, merged to main, and this branch is synchronized onto that final main.

## Mission

Turn Spot PAPER trading into a long-cycle portfolio-management system rather than a grid/rebalance bot.
The system should help hold strong winners through a bull regime, progressively protect/distribute when evidence changes, and avoid selling a Core holding from a transient crash.

## Lifecycle states

- ACCUMULATE
- HOLD_CORE
- ADD_ON_PULLBACK
- TREND_EXPANSION
- PROTECT_PROFIT
- DISTRIBUTE
- REDUCE
- EXIT
- CASH_WAIT

State transitions are typed, versioned and auditable. AI may recommend transitions, but deterministic risk/safety remains authoritative.

## Core / Tactical model

Each eligible Spot holding may define a logical Core and Tactical allocation.

- Core: intended to retain exposure through the broader cycle and requires stronger evidence for large reduction.
- Tactical: may take partial profit, rotate, rebalance, add on qualified pullbacks or reduce concentration.

Core/Tactical must reconcile to the actual holding; it is not a second wallet.

## Regime / cycle evidence

Use only data that can be sourced reliably and point-in-time safely. Candidate inputs include:

- asset multi-timeframe trend and volatility;
- relative strength versus BTC and market basket;
- BTC trend/regime;
- ETH/BTC relationship when available;
- breadth of tracked alt assets;
- volume expansion/contraction;
- funding/OI/leverage crowding where relevant to market context;
- portfolio concentration/correlation;
- drawdown from local/portfolio peak;
- thesis state and invalidation;
- crash-safety state.

Do not hard-code an assumption that an alt season must occur. The system must detect evidence for or against it.

## Bull-cycle exit behavior

Prefer progressive distribution over all-or-nothing discretionary exits.
Allow a strong winner to remain overweight when portfolio/risk policy allows and trend evidence remains strong.
Reduce when concentration, regime deterioration, thesis failure, late-cycle evidence or profit-protection policy warrants it.

## Required actions

- HOLD
- ADD
- TAKE_PARTIAL_PROFIT
- PROTECT_PROFIT
- DISTRIBUTE
- REDUCE
- ROTATE_TO_CASH
- EXIT

All PAPER actions pass through the existing risk, crash, execution and reconciliation layers.

## Benchmark arms

Evaluate aligned PAPER baselines:

- Buy & Hold
- Fixed take-profit ladder
- Periodic/threshold rebalancing
- Simple Spot Grid where the market regime makes it meaningful
- Trailing-stop policy
- AI Lifecycle Manager
- AI Lifecycle + Portfolio Brain

Do not claim the AI approach is superior until prospective results support it.

## Metrics

- total return;
- net economic return after trading + AI cost;
- max drawdown;
- Sortino/Sharpe when sample validity allows;
- turnover;
- fees;
- upside capture;
- downside capture;
- peak capture ratio;
- profit giveback;
- time in cash;
- concentration risk;
- number/value of premature exits;
- number/value of avoided drawdowns;
- AI cost per lifecycle action.

## UX

Spot holding detail should show lifecycle state, Core/Tactical split, thesis status, next review and concise AI plan.
Re-plan presents before/after allocation and expected risk impact.
Default UI stays concise and portfolio-first.

## Anti-overtrading rules

- No action is a valid action.
- Do not call Luna on every price move.
- Do not churn Core merely to restore a target allocation.
- Do not automatically buy laggards just because a winner became overweight.
- Do not mutate the active experiment strategy from a few outcomes.

## Scope boundary

Spot PAPER only for execution in this phase. Perpetual logic remains supported by the platform but is not redesigned here.
Real Gate writes remain BLOCKED BY DESIGN.

## Definition of Done

- Lifecycle state machine works end to end.
- Core/Tactical accounting reconciles.
- Bull/late-bull/distribution logic is auditable.
- Spot re-plan can hold, protect, distribute and exit progressively.
- Crash Safety remains authoritative.
- Benchmark arms run on aligned data/execution assumptions.
- Peak capture/giveback/upside-capture metrics are produced.
- Existing Portfolio OS + Crash Safety tests remain green.