# Spot Cycle Lifecycle Manager — Implementation Plan

## Phase 0 — Sync predecessor
- Sync from final merged Crash Safety main.
- Run full baseline and inspect final Spot/Portfolio/Crash contracts.

## Phase 1 — Lifecycle contracts
- Add lifecycle state, Core/Tactical allocation, lifecycle plan and reason codes.
- Add deterministic transition validation and audit events.

## Phase 2 — Regime / breadth context
- Add point-in-time market-regime context from reliable existing/new public data.
- Explicitly represent unavailable evidence rather than fabricate it.

## Phase 3 — Core/Tactical policy
- Core/Tactical split and reconciliation.
- Limits for add/reduce/distribute actions.
- Integrate crash safety and portfolio concentration.

## Phase 4 — Bull-cycle manager
- Hold strong trends, protect gains, progressively distribute, rotate to cash, exit on confirmed failure.
- No all-or-nothing AI panic action.

## Phase 5 — Re-plan / scheduler integration
- Trigger lifecycle reviews on configured candle/regime/portfolio events rather than each tick.
- Structured before/after proposals and AUTO_PAPER policy support.

## Phase 6 — Benchmark tournament
- Buy & Hold, fixed TP, rebalance, grid, trailing, AI lifecycle, AI lifecycle + Portfolio Brain.
- Aligned data, cost and execution assumptions.

## Phase 7 — UX / metrics / export
- Holding lifecycle UI, Core/Tactical visualization, peak capture, giveback, upside/downside capture.
- Extend activity/evidence/export.

## Phase 8 — deterministic tests
- early bull, strong trend, range, late bull, flash crash recovery, structural breakdown, concentration shock, false distribution signal.

## Validation
- full Python/frontend/schema suite;
- local real Gate public-data browser acceptance;
- no real Gate writes.