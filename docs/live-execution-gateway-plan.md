# Live Execution Gateway — Future Conditional Plan

## Status
PLANNING ONLY. Do not implement before PAPER promotion approval.

## Phase 0 — Re-verify before coding
- Review final main after all prior phases.
- Review PAPER promotion evidence.
- Re-check current official Gate API/order/authentication/permission documentation.
- Confirm tiny-live capital and risk caps.

## Phase 1 — isolated live adapter
- Implement Gate adapter behind a default-off capability flag.
- No strategy logic in adapter.

## Phase 2 — live order lifecycle
- create, acknowledge, fill, cancel/replace and reduce/close with idempotency and TTL.

## Phase 3 — reconciliation
- exchange orders/fills/balances/positions vs internal state.
- startup reconciliation and fail-closed ambiguity.

## Phase 4 — safety integration
- Crash Safety, kill switch, slippage/liquidity guard, portfolio risk caps.

## Phase 5 — tiny execution smoke
- very small allocated capital first;
- prove mechanics and operational recovery;
- return to PAPER on any material defect.

## Phase 6 — controlled 500 USDT tiny-live consideration
- only after clean smoke evidence and explicit human approval.

## Non-goals
- new strategy features;
- multi-exchange;
- HFT/arbitrage;
- withdrawals/transfers;
- automatic capital scaling.