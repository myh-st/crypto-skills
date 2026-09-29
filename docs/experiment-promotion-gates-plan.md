# Experiment Promotion Gates — Implementation Plan

## Phase 0 — Sync predecessor
- Sync from final merged Resilience main and run full baseline.

## Phase 1 — Experiment manifest
- Create canonical immutable experiment manifest/version/hash.
- Classify material vs operational configuration changes.

## Phase 2 — Checkpoint reports
- Day 7 / 30 / 60 / 90 and trade-count-aware summaries.
- correctness, economics, AI value, Spot lifecycle, regime and resilience sections.

## Phase 3 — Promotion engine
- PASS / CONTINUE_COLLECTING_DATA / FAIL_STRATEGY / FAIL_SAFETY / FAIL_RELIABILITY / INVALID_EXPERIMENT.
- deterministic blockers for unresolved critical safety/reconciliation defects.

## Phase 4 — Comparison integrity
- aligned samples, denominators, missing/unavailable handling, no retrospective sample cherry-picking.

## Phase 5 — UI / export
- compact gate status, blockers, evidence drill-down and signed/hashed export manifest where existing architecture supports it.

## Phase 6 — campaign rehearsal
- run deterministic fixture/replay examples of pass/continue/fail/invalid outcomes.

## Non-goals
- inventing new trading strategies;
- automatically tuning active experiments;
- automatically enabling live trading;
- pretending a fixed trade count guarantees statistical certainty.