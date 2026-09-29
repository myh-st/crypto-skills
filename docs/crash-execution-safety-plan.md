# Crash & Execution Safety — Implementation Plan

## Phase 0 — Sync Portfolio OS
- Wait for feature/ai-portfolio-trading-os to finish and be pushed.
- Rebase or merge this branch onto that final head.
- Inspect new Spot, position, order, authority, re-plan and Portfolio Brain contracts.
- Run the full existing test gate before editing.

## Phase 1 — Safety contracts
- Add MarketSafetyState, ExecutionSafetyDecision, KillSwitchLevel, ExecutionPlan, PriceEnvelope and DecisionTTL contracts.
- Add canonical reason codes and schema validation.

## Phase 2 — Market anomaly / crash classifier
- Implement deterministic volatility, spread, freshness and price-divergence checks.
- Keep AI read-only with respect to safety state.
- Persist state transitions and recovery evidence.

## Phase 3 — Price / liquidity guards
- Validate reference price against bid/ask, recent candles and perpetual mark/index where available.
- Add optional secondary-venue sanity input.
- Add max-slippage envelopes.
- Add liquidity-vacuum detection and execution deferral.

## Phase 4 — Execution Planner
- Separate economic intent from execution mechanics.
- Add passive/marketable limit, IOC-style simulation, defer/reject and emergency profiles.
- Add execution slicing with revalidation between slices.
- Add sell-velocity limits.

## Phase 5 — Staleness / idempotency / wrong-side safety
- Snapshot, intent, quote and execution TTLs.
- Stable parent/child idempotency keys.
- Retry/restart protection.
- Prevent reduce/close from increasing or reversing exposure.
- Prevent Spot sells beyond available base balance.

## Phase 6 — Position integration
- Integrate Spot Core/Tactical lifecycle rules.
- Integrate Futures liquidation-buffer emergency rules.
- Integrate AUTO_PAPER, RECOMMEND_ONLY, MANUAL_OVERRIDE and PAUSED.
- Integrate AI re-plan without allowing it to bypass execution guards.

## Phase 7 — Reconciliation / kill switch
- Reconcile intent -> order -> fills -> position/holding -> wallet -> portfolio.
- Escalate on invariant mismatch.
- Implement five kill-switch levels and audited recovery.

## Phase 8 — UX
- Add compact safety strip on Overview/Trade/Position views.
- Show crash state, price confidence, execution restriction, kill-switch level and reasons.
- Show allowed vs blocked actions.
- Add confirmation for materially risk-increasing recovery actions.

## Phase 9 — Torture and chaos testing
- Flash crash recovery.
- Persistent structural crash.
- Bad print.
- Liquidity vacuum.
- AI hallucinated quantity/mechanics.
- Duplicate retry.
- Delayed execution.
- Wrong-side retry.
- Reconciliation break.
- Futures liquidation emergency.
- Stale/out-of-order/duplicate feed events.
- Restart mid-sliced execution.

## Phase 10 — Metrics / export / docs
- Export safety events, state transitions, execution plans and guard outcomes.
- Add execution-shortfall and prevention metrics.
- Update runtime docs and goal status.

## Validation
- python3 scripts/validate_repo.py
- python3 -m unittest discover -s tests -v
- node --test frontend/tests/*.test.mjs
- find frontend -name '*.js' -exec node --check {} \;
- local browser crash-scenario QA

## Constraint
Do not implement real Gate write execution in this branch.