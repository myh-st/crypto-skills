# Spot Cycle Lifecycle Manager (PAPER)

Phase 3 of the development train (`docs/development-train.md`). It turns Spot PAPER holdings
into long-cycle portfolio management: hold strong winners, protect and distribute
progressively when evidence changes, and never sell a Core holding because of a transient
crash. Code: `crypto_eval/spot_lifecycle.py` (policy), `crypto_eval/spot_benchmarks.py`
(benchmark arms), and the lifecycle section of `crypto_eval/portfolio_os.py`.

All execution goes through the existing risk engine, crash/execution safety, kill switch, and
reconciliation. Real Gate writes remain blocked by design.

## States and actions

States (`spot-lifecycle-state`): `ACCUMULATE`, `HOLD_CORE`, `ADD_ON_PULLBACK`,
`TREND_EXPANSION`, `PROTECT_PROFIT`, `DISTRIBUTE`, `REDUCE`, `EXIT`, `CASH_WAIT`.
Transitions are typed (`TRANSITIONS`); an invalid transition becomes `HOLD` with an
`INVALID_TRANSITION_*` reason. Every review is stored in `lifecycle_events` with the evidence
and plan, and the row in `spot_lifecycle` carries a state version.

Actions: `HOLD`, `ADD`, `TAKE_PARTIAL_PROFIT`, `PROTECT_PROFIT`, `DISTRIBUTE`, `REDUCE`,
`ROTATE_TO_CASH`, `EXIT`. `HOLD` is a valid, common answer.

## Regime evidence (`spot-regime-evidence.v1`)

Built from closed 4h Spot candles (120 bars, point-in-time: nothing after the review time):

| Input | Source | When unavailable |
|---|---|---|
| Asset trend (EMA 12/48, slope, cycle return, efficiency, drawdown, volatility) | asset bars | regime `UNKNOWN`, action `HOLD` |
| Relative strength vs BTC | BTC bars | excluded |
| BTC regime | BTC bars | excluded |
| ETH/BTC trend | aligned ETH and BTC bars | excluded |
| Breadth (share of tracked assets above slow EMA) | open holdings plus majors | excluded below 3 assets |
| Volume trend | asset volume | excluded |
| Funding / crowding | not sourced for Spot | always explicitly unavailable |
| Crash safety | `assess()` | excluded |

Unavailable evidence is reported with a reason and never zero-filled. Regimes:
`EARLY_BULL`, `STRONG_TREND`, `RANGE`, `LATE_BULL` (extended plus at least 2 warning
signs), `BREAKDOWN`, `UNKNOWN`. There is no built-in assumption that an alt season happens;
breadth is measured.

## Policy (`spot-lifecycle-policy.v1`)

In priority order:

1. **Crash safety is authoritative.** In `CRASH_MODE` or `MARKET_DATA_UNTRUSTED` without a
   structural breakdown, the answer is `HOLD`: Core is not sold and nothing is added.
2. An exit already decided continues until flat (sell velocity may pace it).
3. **Unknown evidence:** `HOLD`.
4. **Breakdown:** shed Tactical first (`REDUCE`). Core is sold (`EXIT`) only after
   `exit_confirm_reviews` consecutive breakdown reviews or a structural breakdown.
5. **Profit giveback:** a winner that gave back `protect_giveback_pct` of its peak gain trims
   a Tactical step (`PROTECT_PROFIT`).
6. **Late bull:** progressive `DISTRIBUTE` of `distribute_step_fraction` of Tactical per step,
   with a cooldown between actions.
7. **Strong trend:** `HOLD` in `TREND_EXPANSION`. A winner may stay overweight up to
   `overweight_tolerance_x` times the allocation limit; above that, only Tactical is trimmed.
8. **Early bull:** `ADD` only on a qualified pullback near the slow EMA, in a `NORMAL` market,
   with allocation room.
9. **Range:** `HOLD`. The lifecycle manager does not run grid-style churn.

Anti-overtrading rules:

- AI is not called per price move.
- Core is never churned back to a target allocation.
- Laggards are never bought because a winner became overweight.
- Nothing mutates the experiment strategy.

## Authority and execution

- The scheduler (`after_monitor` → `lifecycle_due`) reviews each open Spot holding every
  `review_interval_minutes` (default 240).
- **`AUTO_PAPER` holdings** execute as `AI` through `reduce_position` / `create_order`, so
  the kill switch, authority modes, sell velocity, Core protection, slippage envelope, and
  reconciliation all apply. AI-owned holdings get the default Core split (50%) once.
- **Other modes** get a pending proposal. The user can Apply (confirmation required) or
  Dismiss it. A proposal older than one review interval is refused as `STALE_DECISION`.
- **Recommendations** (`merge_recommendation`) from AI or a user can only choose a less
  aggressive action or a smaller sell than the deterministic plan. Anything else is
  `RECOMMENDATION_CLAMPED` or `RECOMMENDATION_INVALID_TRANSITION`.

## Benchmark arms (`spot-benchmark-report.v1`)

The arms run on the same closed bars, fee, slippage, and fill rule (close of the decision bar):

- Buy & Hold
- fixed TP ladder
- threshold rebalance
- simple grid (only when the opening window is range-bound; otherwise `NOT_APPLICABLE`)
- 15% trailing stop
- lifecycle manager
- lifecycle + Portfolio Brain allocation cap

Reported metrics: total and net economic return, max drawdown, Sharpe/Sortino (only with at
least 30 returns), turnover, fees, upside/downside capture, peak-capture ratio, profit giveback,
time in cash, premature exits, and avoided drawdowns (count and value).

AI cost for lifecycle arms is reported as unavailable, because the offline benchmark does
not simulate paid AI calls. Every report carries `NOT_EVIDENCE_OF_SUPERIORITY`: prospective
PAPER results are required before any claim.

## API and UI

- `GET /api/lifecycle`, `GET /api/lifecycle/{ref}`
- `POST /api/lifecycle/{ref}/review` `{recommendation?}`: always propose-only from the API
- `POST /api/lifecycle/{ref}/apply` `{confirm}`, `POST /api/lifecycle/{ref}/dismiss`
- `POST /api/lifecycle/benchmark` `{instrument_id, bars}`, `GET /api/lifecycle/benchmarks`

The position drawer shows lifecycle state, regime, the Core/Tactical split, the pending
proposal (Apply/Dismiss), a Review button, and recent lifecycle events. Evaluations has a
benchmark panel. Settings exposes the main lifecycle parameters. Exports include
`lifecycle-states.csv`, `lifecycle-events.jsonl`, and `lifecycle-benchmarks.jsonl`.

## Tests

`tests/test_spot_lifecycle.py` covers:

- scenarios: early bull, strong trend, range, late bull, flash crash recovery, structural
  breakdown, concentration shock, false distribution signal;
- point-in-time exclusion and explicit unavailability;
- recommendation bounds;
- benchmark honesty;
- PortfolioOS authority, kill switch, crash hold, scheduler cadence, stale proposals, and
  export.
