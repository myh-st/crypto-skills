# Experiment Promotion Gates

Phase 5 of the development train (`docs/development-train.md`). This is methodology and
governance, not strategy. It keeps the project from fooling itself: it freezes what an
experiment means, compares treatments on aligned samples with denominators, and requires
explicit evidence before any move toward live validation.

Code: `crypto_eval/promotion.py`, reached as `runtime.governance`.

**Nothing in this phase can enable real trading.** Every gate result carries
`live_execution_enabled: false`. A `PASS` only makes a separate, human-reviewed
`feature/live-execution-gateway` phase eligible for review. That phase stays
`PLANNING_ONLY_LIVE_DISABLED`.

## Promotion ladder

`IDEA → BACKTEST_REPLAY → WALK_FORWARD → SHADOW → PAPER → PAPER_CHECKPOINTS → LIVE_ELIGIBLE_REVIEW`

## Frozen experiment manifest (`experiment-manifest.v1`)

The manifest is frozen the first time the experiment starts. A running experiment from before
manifests existed is frozen at the next restart; its evaluation window starts then and earlier
history is not claimed.

The material fields, hashed together as `material_sha256`:

- experiment configuration except operational fields (`market_data_retention_days`,
  `ai_budget`, `cost_fx`, which are audited separately):
  - universe;
  - arms;
  - risk and leverage policy;
  - routing and escalation;
  - execution and fee assumptions;
- portfolio policy:
  - Portfolio Brain, crash/execution safety, Spot lifecycle, and AI Spot settings;
  - Spot risk and allocation settings;
  - the AI-management review switches;
  - the promotion gate criteria (so the goalposts cannot move inside one version);
- providers (id, kind, model, deployment, reasoning effort, enabled, role). There are never
  credentials;
- the skill directory hash (SKILL.md plus references);
- policy versions: escalation, budget, execution plan, market safety, lifecycle, gate;
- the AI price book (hashed);
- benchmark arm definitions.

**Changes:**

- A material Settings change requires confirmation (`CONFIRM_MATERIAL_CHANGE`) and records a
  new manifest version.
- Operational settings (attention thresholds, automation toggles) do not.
- Any other material change, such as a provider or model swap or an edited skill, shows as
  **drift**. A review is then `INVALID_EXPERIMENT` until the user records a new version with a
  reason (`CONFIRM_MANIFEST_VERSION`).
- The experiment configuration itself stays frozen once cycles exist, as in the runtime
  baseline.

## Checkpoint report (`experiment-checkpoint.v1`)

The report is reproducible: the same database and `as_of` give the same `report_sha256`. It
covers the current manifest version's window (from its `frozen_at`).

| Section | Contents |
|---|---|
| identity | experiment, manifest version/hash, drift, elapsed days, checkpoint (`PRE_DAY_7`, `DAY_7/30/60/90`), starting capital |
| safety | live execution disabled (constant), reconciliation, duplicate logical cycles, secret scan of the live DB, open critical incidents, DB integrity, kill switch, safety-event counts |
| reliability | scheduler slots (DONE / SKIPPED_GAP / ATTEMPTED), skipped-slot ratio, incidents by kind, restarts, provider outages, feed gaps |
| economics | completed trades (perp primary + Spot round trips) and denominator; net trading PnL; fees; funding; slippage; AI cost and unpriced calls; FX; net economic PnL (unavailable when AI cost has no FX policy or is unpriced); expectancy; win rate; profit factor; max drawdown on total equity; return distribution; top-trade share |
| AI incremental value | aligned tournament arms with denominators and incremental-vs-quant; labeled `WHOLE_EXPERIMENT_MIXED_VERSIONS` if the experiment has more than one manifest version |
| Spot lifecycle | lifecycle action/status counts, latest aligned benchmark summaries |
| coverage | trades by regime and asset, regimes observed |

## Gate (`promotion-gate-result`, policy `promotion-gate.v1`)

Exactly one status, in fixed precedence:

1. `INVALID_EXPERIMENT`: no frozen manifest, or unversioned material drift.
2. `FAIL_SAFETY`: live execution enabled, reconciliation failure, duplicate logical execution,
   credential-like values in the DB, an open critical incident, or DB integrity failure. This
   applies **regardless of PnL**.
3. `FAIL_RELIABILITY`: skipped-slot ratio above `max_skipped_slot_ratio`.
4. `CONTINUE_COLLECTING_DATA`: fewer than `min_days` or `min_completed_trades`, fewer than
   `min_regimes_observed` regimes, or the day-7 checkpoint (which judges correctness and
   reliability only, never profitability).
5. `FAIL_STRATEGY`: after a full sample, net economic PnL is not positive (or is
   unavailable), the profit factor is below the minimum, the drawdown is above the maximum,
   or one trade carries more than `max_top_trade_share` of net PnL.
6. `PASS`: next step `LIVE_ELIGIBLE_REVIEW` (human review; nothing is enabled).

Criteria are portfolio settings under `promotion`. They are part of the frozen manifest: changing them after Start requires `CONFIRM_MATERIAL_CHANGE` and records a new manifest version, and the gate then evaluates only that version's window.
The defaults:

| Criterion | Default |
|---|---|
| `min_days` | 90 |
| `min_completed_trades` | 200 |
| `min_profit_factor` | 1.15 |
| `max_drawdown` | 20% |
| `require_positive_net_economic_pnl` | on |
| `max_top_trade_share` | 50% |
| `max_skipped_slot_ratio` | 5% |
| `max_open_critical_incidents` | 0 |
| `min_regimes_observed` | 2 |

A fixed trade count is not statistical certainty; the report always shows denominators.

## Where to use it

- **Evaluations → "Experiment & promotion gate":** identity, frozen hash, elapsed/checkpoint,
  trades, net economic PnL, the gate decision with blockers and next step, drift with
  "Record new version", and review history.
- **API:**
  - `GET /api/experiment/manifest`
  - `POST /api/experiment/manifest/version` `{reason, confirm}`
  - `GET /api/promotion/report` (not persisted)
  - `POST /api/promotion/review` (persisted)
  - `GET /api/promotion/reviews`
- **CLI:** `python3 -m crypto_eval paper-checkpoint [--database P] [--out F] [--dry-run]`.
- **Export:** `experiment-manifests.jsonl`, `promotion-reviews.jsonl`.

## Tests

`tests/test_promotion_gates.py` rehearses every status and precedence rule, including day-7
behavior, single-trade dependence, unavailable economics, and safety beating PnL. It also
covers manifest freezing without secrets, material-change confirmation and versioning, drift
invalidation, the upgrade freeze, reproducible reports from real PAPER trades, and persisted
reviews. Schema conformance is in `tests/test_portfolio_schemas.py`.
