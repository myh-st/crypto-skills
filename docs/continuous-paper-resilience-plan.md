# Continuous PAPER Resilience — Implementation Plan

## Phase 0 — Sync predecessor
- Sync from final merged Spot Cycle main and run full baseline.

## Phase 1 — Recovery model
- Inventory restart-sensitive runtime state.
- Add startup reconciliation and explicit recovery states.
- Prove idempotent cycle/order/review recovery.

## Phase 2 — Scheduler / sleep recovery
- Persist/derive safe next-cycle state.
- Detect overdue/missed cycles and machine sleep.
- Backfill market data without inventing signals.

## Phase 3 — Market stream resilience
- Reconnect/backoff/resubscribe, duplicate/out-of-order handling, bounded gap fill, unrecoverable-gap state.

## Phase 4 — SQLite / storage hardening
- schema versioning, migrations, WAL/transaction checks, size/retention, disk error behavior.

## Phase 5 — Provider resilience
- timeout/backoff/circuit behavior and retry-budget correctness.
- no duplicate paid calls from logical retries.

## Phase 6 — Health / watchdog / graceful shutdown
- compact health endpoint/UI.
- watchdog that detects, never duplicates.
- graceful stop and startup recovery.

## Phase 7 — Backup / restore
- consistent local snapshot and restore drill.
- exclude secrets.

## Phase 8 — soak / fault testing
- restart, sleep, feed outage, provider outage, DB contention, slow client, browser disconnect.
- run a multi-day accelerated/local soak where practical.

## Phase 9 — docs/export
- incident/recovery evidence and campaign readiness checklist.

## Non-goals
- Kubernetes, Kafka, Redis cluster, PostgreSQL migration, microservice split, multi-node HA.