# Goal: Crash & Execution Safety Layer

## Dependency gate

This branch is intentionally planning-only until feature/ai-portfolio-trading-os is completed and pushed.
Before implementation, sync this branch onto the final Portfolio OS head and rerun the full baseline.

## Mission

Make catastrophic execution difficult even when AI is wrong, panics, hallucinates, sees a bad tick, or reacts to a flash crash.
AI owns trading intent only. Deterministic code owns execution mechanics and final safety approval.

Safety hierarchy:

LIQUIDATION / ACCOUNTING SAFETY > RISK ENGINE > CRASH / PRICE / LIQUIDITY GUARDS > PORTFOLIO BRAIN > AI / HUMAN INTENT

## Required safety layers

1. Market safety state machine: NORMAL, VOLATILITY_ALERT, CRASH_MODE, RECOVERY, MARKET_DATA_UNTRUSTED.
2. Price sanity guard using last, bid/ask, recent candles and, for perpetuals where available, mark/index plus optional secondary-venue reference.
3. Structural-breakdown logic that distinguishes transient wick/tick moves from confirmed multi-timeframe failure.
4. Crash-mode policy: block new entries, averaging down, leverage increase, pyramiding and discretionary full-position dumps.
5. Spot Core/Tactical protection so a flash crash cannot liquidate a long-cycle core holding from one anomalous signal.
6. Sell-velocity guard limiting discretionary reduction over short windows.
7. Execution Planner owns order style; AI cannot demand an unsafe market order.
8. Max-slippage and acceptable-price envelope for every execution.
9. Execution slicing with revalidation between slices.
10. Stale snapshot / AI decision / quote / order TTL protection.
11. Idempotency and duplicate-retry prevention.
12. Wrong-side and fat-finger guards: reduce cannot increase exposure; Spot sell cannot exceed holdings; close cannot reverse a futures position.
13. Futures liquidation safety can override slow confirmation when liquidation buffer becomes critical.
14. Stop/target integrity checks and risk-increasing amendment policy.
15. Reconciliation after every PAPER execution; mismatch escalates safety state and blocks new autonomous mutation.
16. Explicit kill-switch levels: NORMAL, NO_NEW_ENTRIES, AI_MANAGEMENT_PAUSED, RISK_REDUCING_ONLY, FULL_AUTOMATION_HALT.
17. Human-visible safety state, reason codes, restrictions and allowed actions.

## Execution principle

AI may say HOLD, REDUCE, CLOSE, PROTECT_PROFIT, TIGHTEN_STOP or EXIT_THESIS_BROKEN.
AI must not directly control final quantity, order type, slippage tolerance, slicing, TTL, liquidation override or kill-switch state.

## Spot crash behavior

Spot is allowed to wait for stronger confirmation when there is no liquidation risk.
A transient wick or bad print must not automatically sell a Core holding.
Confirmed regime/thesis failure may progressively reduce or exit.

## Futures crash behavior

Futures must not wait for a slow higher-timeframe confirmation when liquidation safety is threatened.
Emergency deterministic reduction is allowed even when AI says HOLD.

## Required reason codes

FLASH_MOVE_DETECTED
PRICE_ANOMALY
MARK_INDEX_DIVERGENCE
CROSS_VENUE_DIVERGENCE
SPREAD_EXPANSION
LIQUIDITY_VACUUM
FEED_STALE
EXECUTION_DEFERRED
SLIPPAGE_LIMIT
SELL_VELOCITY_LIMIT
STALE_DECISION
DUPLICATE_PREVENTED
WRONG_SIDE_BLOCK
POSITION_SIZE_CLAMP
LIQUIDATION_BUFFER_CRITICAL
RECONCILIATION_FAILURE
KILL_SWITCH_CHANGED
CRASH_MODE_ENTERED
CRASH_MODE_RECOVERED

## Torture scenarios

- FLASH_CRASH_RECOVERY: 100, 99, 98, 70, 96, 99. Do not dump protected Spot Core at the anomalous low.
- STRUCTURAL_CRASH: persistent 100, 95, 90, 84, 78, 72. Risk reduction must remain possible.
- LIQUIDITY_VACUUM: spread expands sharply. Unsafe discretionary execution must defer/slice/reject.
- BAD_PRINT: one source is a large outlier while trusted references remain normal. No execution from the bad print.
- AI_HALLUCINATION: impossible quantity or unsafe mechanics must be rejected/resized.
- DUPLICATE_RETRY: timeout then retry creates exactly one logical order/fill.
- DELAYED_ORDER: expired decision cannot execute.
- WRONG_SIDE_RETRY: repeated close after flat cannot reverse the position.
- RECONCILIATION_BREAK: ledger mismatch escalates kill switch and blocks autonomous mutation.
- LIQUIDATION_EMERGENCY: futures emergency reduction can override normal delay.

## Metrics

Track crash-mode entries, safety-state duration, execution deferrals, slippage blocks, bad-print blocks, stale-decision blocks, duplicate prevention, wrong-side blocks, reconciliation failures, emergency reductions, execution shortfall, Core/Tactical reductions, peak giveback and PAPER liquidation-avoidance scenarios.

## Scope boundary

This branch remains PAPER-only.
Real Gate public data and read-only account context are allowed.
Real Gate money-moving writes remain BLOCKED BY DESIGN.

## Definition of Done

- AI cannot bypass execution safety.
- Bad tick / flash crash cannot cause an unbounded panic liquidation.
- Persistent structural failure can still de-risk.
- Spot Core and Tactical behavior is explicit.
- Futures liquidation safety is explicit.
- Slippage, liquidity, TTL, slicing, sell velocity and wrong-side guards work.
- Duplicate retry cannot double-fill.
- Reconciliation failure stops unsafe automation.
- Kill-switch levels work and are visible.
- Deterministic crash/fault-injection tests pass.
- Portfolio OS regression remains green.
- Real Gate writes remain BLOCKED BY DESIGN.