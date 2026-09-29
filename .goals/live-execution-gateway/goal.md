# Goal: Live Execution Gateway

## HARD GATE — DO NOT IMPLEMENT YET

This branch is planning-only.

Do not implement real-money execution until all of the following are true:
- `feature/experiment-promotion-gates` is completed and merged;
- the canonical 500 USDT PAPER campaign has been run for the required evidence window;
- the promotion review explicitly marks the system live-eligible;
- there are no unresolved critical safety, accounting, reconciliation or resilience defects;
- current Gate official API/order semantics are re-verified at implementation time.

Passing these conditions does not guarantee profitability. It permits only a controlled tiny-live validation.

## Mission

When eventually authorized, add the smallest possible real-execution adapter on top of the already-proven decision, risk, crash-safety, execution-planning and reconciliation stack.

No trading intelligence is to be invented in this phase.

## Architecture rule

AI / User Intent -> existing Portfolio policy -> existing Risk -> existing Crash Safety -> existing Execution Planner -> LIVE GATEWAY -> Exchange -> reconciliation.

The live gateway must not bypass or duplicate strategy/risk logic.

## Default state

- live execution disabled;
- PAPER remains default;
- existing read-only Gate mirror remains safe;
- no trade-permission credential required while disabled.

## Future credential policy

When implementation is authorized:
- use a separate API credential intended only for trading;
- withdrawal/transfer permission must remain disabled;
- recommend IP allowlisting where practical;
- store credentials only through the existing secure secret mechanism;
- never return raw secrets to browser/log/export/SQLite;
- capability-test permissions before enabling the adapter.

## Tiny-live ladder

Initial live validation should be staged, not jump directly to full intended capital:

1. very small execution smoke (for example roughly 50-100 USDT total allocated capital);
2. verify acknowledgements, fills, fees, cancellations, reduce-only semantics, recovery and reconciliation;
3. only after clean evidence consider the planned approximately 500 USDT tiny-live portfolio;
4. any material defect returns the system to PAPER / disabled live mode.

Exact capital/risk limits remain user-configured and must be hard-capped by deterministic policy.

## Required controls when eventually implemented

- global live execution enable defaults false;
- exchange/account capability check;
- explicit LIVE visual state distinct from PAPER;
- maximum live capital;
- maximum per-trade risk;
- maximum portfolio heat/exposure;
- maximum order notional;
- max daily loss / drawdown halt;
- kill-switch integration;
- crash-mode integration;
- price/slippage/liquidity guards;
- idempotent client order identity;
- stale-decision/order TTL;
- futures reduce-only exit semantics;
- order/fill/account reconciliation;
- startup reconciliation before new orders;
- read-after-write verification;
- no automatic withdrawal/transfer capability;
- live incident journal/export.

## Failure behavior

Ambiguous exchange acknowledgement, reconciliation mismatch, stale state, permission mismatch, repeated order failure or critical market-data fault must fail closed.

The AI must never repair or guess live exchange state.

## Scope

Initial exchange: Gate only.
Do not add multi-exchange execution, arbitrage, HFT or smart-order routing across venues in this phase.

Spot and Perpetual support should reuse the proven PAPER domain and current official Gate semantics verified at implementation time.

## Required acceptance when eventually authorized

- isolated test/smoke proves real order lifecycle with tiny exposure;
- create/fill/cancel/replace/reduce/close reconcile exactly;
- retry does not duplicate exposure;
- restart does not lose exchange state;
- wrong-side guard cannot reverse a close;
- kill switch blocks new live mutations;
- critical crash/liquidity guard behavior works;
- fees/funding/slippage reconcile to exchange data;
- real account and internal portfolio reconcile;
- live mode cannot be enabled accidentally.

## Definition of Done

This goal is intentionally NOT currently implementable.
It becomes implementable only after an explicit promotion decision based on the completed PAPER campaign.
Until then, the correct state is PLANNING_ONLY_LIVE_DISABLED.