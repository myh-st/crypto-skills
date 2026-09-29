# Crash, Price, Liquidity, and Execution Safety (PAPER)

Phase 2 of the development train (`docs/development-train.md`). This layer sits between
every decision (AI, user, or system) and the PAPER fill path. It is deterministic code in
`crypto_eval/execution_safety.py`, wired through `paper_runtime.py` and `portfolio_os.py`.
AI never chooses execution mechanics, and nothing in this layer can place a real order:
the Gate write block (`DisabledLiveExecutionAdapter`) is unchanged.

## Authority hierarchy

```
LIQUIDATION / ACCOUNTING  >  RISK ENGINE  >  CRASH / PRICE / LIQUIDITY GUARDS  >  PORTFOLIO BRAIN  >  AI / HUMAN
```

A lower layer can shrink, defer, or block what a higher-priority layer allows. It can never
expand it. Deterministic SYSTEM risk-reducing actions (liquidation emergency reduce, stop
monitoring) are never blocked by the kill switch.

## Market safety state (`market-safety-state.v1`)

`classify_market` returns one of:

| State | Trigger (defaults, all configurable under `safety.*`) | New entries |
|---|---|---|
| `NORMAL` | none | allowed |
| `VOLATILITY_ALERT` | wide spread (25 bps), flash move 2%, or a price anomaly | reduced size (x0.5) |
| `CRASH_MODE` | 5 min drop 5% or 15 min drop 8% | blocked |
| `RECOVERY` | 15 min after crash mode ends | reduced size |
| `MARKET_DATA_UNTRUSTED` | stale quote (30 s), mark/index divergence, cross-venue divergence 2%, or a crossed book | blocked; protective reduce deferred unless emergency |

A `LIQUIDITY_VACUUM` reason (spread 100 bps or more) also blocks entries. Each assessment
reports price confidence (`HIGH`, `REDUCED`, `LOW`, `UNTRUSTED`), a trusted price, whether a
drawdown is structural (the low held) or a transient wick (60% recovery), and the explicit
list of what is allowed now.

## Suspect prints

A bar whose wick is extreme against recent typical range and recovers inside the bar is
flagged `SUSPECT_PRINT`. The bar monitor then does **not** trigger wick-based stops or PAPER
liquidation from that bar. Close-based logic still runs and the event is journaled.

## Execution planner (`execution-plan.v1`)

`plan_execution` turns economic intent (entry / increase / reduce / close / protect, with
urgency normal / protective / emergency) into mechanics: `EXECUTE`, `SLICE`, `DEFER`,
`REJECT`, or `RESIZE`, with an order style, a worst-acceptable-price envelope, max slippage
(entry 20, reduce 50, emergency 300 bps), up to 5 slices, and a 180 s decision TTL.

- **Sell velocity:** at most 50% of a position per 30 minutes for discretionary reductions.
  It applies to AI on Spot, to AI on perpetuals outside `NORMAL`, and to users in
  `CRASH_MODE`/`RECOVERY`. Users can override with an explicit confirmation
  (`CONFIRM_SAFETY_OVERRIDE`), which is journaled as `SAFETY_OVERRIDE`.
- **Spot Core/Tactical:** a holding can mark a Core fraction. In unsafe market states without
  a confirmed structural breakdown, discretionary sells stop at Core (`CORE_PROTECTED`).
  Plan stops need `spot_stop_confirm_closes` (default 2) consecutive closes and never sell Core.
- **Idempotency:** `client_request_id` retries return the stored result
  (`DUPLICATE_PREVENTED`); an expired preview (60 s) is rejected as `STALE_DECISION`; a
  reduce/close on an already-flat position is blocked as `WRONG_SIDE_BLOCK`, never reversed.

## Kill switch

Levels, lowest to highest: `NORMAL`, `NO_NEW_ENTRIES`, `AI_MANAGEMENT_PAUSED`,
`RISK_REDUCING_ONLY`, `FULL_AUTOMATION_HALT`. Raising is immediate. Lowering requires a USER,
a passing reconciliation, and confirmation (`CONFIRM_KILL_SWITCH_LOWER`). The scheduler skips
cycles at `FULL_AUTOMATION_HALT`; the bar monitor and liquidation safety keep running.

## Reconciliation and liquidation emergency

`reconcile_ledgers` recomputes the perpetual wallet, position quantities from fills, stop
integrity, Spot cash, reserved quote, and holdings from immutable fills. A failure is journaled
as `RECONCILIATION_FAILURE` and escalates the kill switch to `RISK_REDUCING_ONLY`.

When a perpetual's liquidation buffer falls under 2%, the SYSTEM reduces it by 50% with a 5
minute cooldown (`LIQUIDATION_BUFFER_CRITICAL`). This is the only SYSTEM-initiated reduction.

## API and UI

- `GET /api/safety`: kill switch, per-instrument states, event counts, reconciliation
- `POST /api/safety/assess`: `{instrument_id}` gives a fresh assessment
- `POST /api/safety/kill-switch`: `{level, reason, confirm}`
- `GET /api/execution-plans`
- `POST /api/positions/{ref}/core`: `{core_fraction}`

The Overview shows a safety strip and the kill-switch control. Trade shows the instrument's
market state and restrictions. The ticket preview shows the execution plan. The position
drawer shows safety and the Spot Core form. Settings exposes the main thresholds. Safety
events appear in Activity. Exports include `safety-events.csv`, `execution-plans.jsonl`,
`market-safety-states.csv`, and `kill-switch.json`.

## Torture tests

`tests/test_crash_execution_safety.py` replays: FLASH_CRASH_RECOVERY, STRUCTURAL_CRASH,
LIQUIDITY_VACUUM, BAD_PRINT, AI_HALLUCINATION, DUPLICATE_RETRY, DELAYED_ORDER,
WRONG_SIDE_RETRY, RECONCILIATION_BREAK, and LIQUIDATION_EMERGENCY, all on fixtures.
