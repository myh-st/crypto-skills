# Inspector feedback — iteration 2

**Verdict: PASS**

## Scope and independent review

I independently reviewed the immutable goal and iteration-1 findings, then
examined the committed implementation and tests at Builder commit `30fd04e`
(`fix(eval): [B] correct trigger and fold scoring`). The relevant history is
`90ef30b` (harness implementation), `fbdfedf` (iteration-1 inspection), and
`30fd04e` (Builder corrections). The diff from initial SHA
`2e2c0df8d53a7c7dd1a09177006d3d6d85de981b` contains 36 evaluation-related
paths; iteration 2 changed the contracts, scoring/reporting, schemas,
documentation, validator, and regression tests. I made no product-code edits.

The pre-implementation order of the original repository inspection cannot be
proved from commit history. The current harness is separated from the
production skill, preserves the canonical decision-state contract, and passes
the repository validator.

## Earlier findings — independent reproductions

### F1 — Unknown and nested future snapshot data

The runtime path now fails closed at both dataset construction and prediction
execution. `validate_candidate_bundle` rejects unknown snapshot and nested
observation fields and recursively rejects label-bearing content;
`validate_dataset` reuses that validation, and `run_predictions` validates the
dataset before calling the runner. The candidate/dataset schemas also reject
additional snapshot and nested fields.

Reproduction results:

- Candidate `snapshot.future_metric = {"value": "up"}`: rejected as an
  unsupported snapshot field.
- Candidate `snapshot.vendor_payload = {"future": {"close": 999}}`: rejected
  as an unsupported snapshot field.
- Candidate `snapshot.funding[0].future_label`: rejected as an unsupported
  nested field.
- A content-hash-recomputed dataset containing either a nested unrecognized
  funding field or a nested `future_label` was rejected by `run_predictions`;
  the sentinel runner was not called.

Regression coverage is present in
`tests/test_eval_contracts.py::test_unknown_snapshot_fields_and_nested_payloads_fail_closed`
and `::test_candidate_schema_rejects_unknown_snapshot_fields`.

### F2 — Trigger-aware MFE/MAE

`crypto_eval/scoring.py::_excursions` skips every bar before the detected
trigger and uses only the trigger candle close for its excursion; later bars
provide the remaining OHLC range. I reran the prior extreme-pre-trigger probe
for both trigger types:

| Probe | Pre-trigger high/low | Trigger | MFE | MAE |
|---|---:|---:|---:|---:|
| Long breakout | 200 / 50 | 2nd bar | **5.00%** | **-1.00%** |
| Short pullback | 200 / 50 | 2nd bar | **5.00%** | **-1.00%** |

Both exactly match the expected post-trigger-only values. Regressions are in
`tests/test_eval_scoring.py::test_wait_excursions_exclude_bars_before_the_actual_trigger`
and `::test_pullback_wait_excursions_exclude_bars_before_the_actual_trigger`.
The documented conservative trigger-candle stop treatment remains explicit in
`docs/evaluation.md`.

### F3 — Fold-consistent reports

`crypto_eval/reporting.py::_validate_report_scope` now requires the score and
baseline `fold_scope` values to match, validates every row's fold, and requires
each baseline's per-case IDs to equal the selected score-fold IDs.

Reproduction results:

- `test` scores paired with `all` baselines: rejected with
  `scores fold_scope 'test' does not match baseline fold_scope 'all'`.
- Changing only the all-fold baseline label to `test`: rejected because the
  baseline case set does not match the test-fold score scope.
- Correct `test`/`test` inputs: report labels both sections `test`; counts are
  dataset=3, test-fold=1, predictions=1, scored=1, complete forward paths=1.
  Each of the six baseline summaries has one eligible test case.

The regression is in
`tests/test_eval_baselines_reports.py::test_report_requires_matching_fold_and_case_scopes`.
The report explicitly separates dataset-wide and selected-fold counts rather
than mislabeling an all-fold baseline as a test-fold comparison.

## Criterion-level assessment

| # | Status | Assessment |
|---|---|---|
| 1. Preserve existing contracts and architecture | **PASS*** | The new `crypto_eval` package is separate; canonical decision states are still sourced from the existing schema. Repository contracts validate. The Builder's pre-implementation read order is not inferable from commits. |
| 2. Modular point-in-time evaluation pipeline | **PASS** | Dataset, provider, runner, lifecycle, scoring, baselines, reporting, CLI, schemas, and fixtures remain separately implemented. |
| 3. Explicit case coordinates and fail-closed leakage checks | **PASS** | Required coordinates, timezone/cutoff checks, archived-news availability, field allowlists, future timestamp checks, and outcome/label rejection are present. F1 probes and schema tests passed. |
| 4. Frozen predictions separate from outcomes | **PASS** | Prediction records bind dataset/version/hash, case, runner metadata, freeze time, and content hash. Writes are exclusive or append-only with duplicate checks; outcome records are separately bound and validated. Mutation/outcome-contamination tests pass. |
| 5. Forward paper-evaluation lifecycle | **PASS** | Lifecycle tests project pending, waiting, ready-to-score, and scored states; no trading or order execution is introduced. |
| 6. Trigger-aware scoring and path rules | **PASS** | Pullback/breakout trigger detection, untriggered waits, target/invalidation ordering, gaps, missing intervals, and post-trigger MFE/MAE are covered. F2 reproduced at 5%/-1% for both trigger types. |
| 7. Metrics, denominators, intervals, and breakdowns | **PASS** | Scoring and docs define counts/denominators, Wilson binary intervals, descriptive mean intervals, forward/benchmark/alpha and path metrics, qualitative confidence rates, drawdown proxy, and the requested breakdown dimensions. Missing values remain unavailable. |
| 8. Fixed deterministic baselines | **PASS** | Six fixed comparators are implemented: Buy & Hold, BTC, EMA20/50, RSI14, previous-candle naive, and seeded random. Parameters/seed are fixed by the versioned spec; insufficient history is unavailable. |
| 9. Walk-forward integrity and confidence handling | **PASS** | The spec requires expanding chronological folds and `shuffle: false`; case folds are recomputed and validated. Confidence remains qualitative and is not represented as calibrated probability. |
| 10. Reproducible target dataset specification | **PASS** | `eval/specs/crypto-market-v1.json` declares BTC/ETH/SOL/SUI/SEI/AVAX/PYTH, a fixed weekly schedule, regime/asset coverage, fixed exclusions, and stable source-derived case IDs. Sampling reconciliation is validated. |
| 11. Read-only data-provider abstraction | **PASS** | A read-only provider protocol and local JSON archive adapter are present; no credentialed/live provider or fabricated feature lane is used. Unsupported data is marked unavailable. |
| 12. Decision quality separated from portfolio PnL | **PASS** | Reports claim neither accuracy nor portfolio PnL. Documentation defines drawdown as a decision-sequence proxy and lists unmodeled sizing, cash, fills, fees, slippage, and funding. |
| 13. Deterministic CLI workflow | **PASS** | Dataset build, fixture prediction, scoring, baseline comparison, and report commands all completed successfully in the end-to-end run below. |
| 14. Honest reports and evidence boundaries | **PASS** | Generated output says `DEMO / HARNESS VALIDATION — NOT MARKET PERFORMANCE EVIDENCE`, records no accuracy/PnL claim, and reports fixture invocation limits. Report schema validation passed. |
| 15. Automated regression coverage | **PASS** | The complete unit suite passes (56 tests); it includes schema/leakage, excursion, fold-scope, lifecycle, immutability, scoring edge cases, and baseline/report coverage. |
| 16. CI integration | **PASS** | `.github/workflows/validate.yml` retains repository validation and unit tests and adds the offline deterministic demo; no live API or credentials are required. |
| 17. Documentation | **PASS** | English and Thai READMEs distinguish validation from accuracy evidence; `docs/evaluation.md` documents individual CLI steps, data boundaries, metrics/denominators, lifecycle, baselines, and experiment limits. |
| 18. Required verification, diff hygiene, and cleanup | **PASS*** | Required validator/tests and the complete individual CLI chain passed; generated report artifacts were removed and `git diff --check` passed. See the shared-worktree note below. |

## Required verification

| Command/check | Result |
|---|---|
| `python3 scripts/validate_repo.py` | **PASS**, exit 0: 10 schemas valid; 3/3 existing YAML examples and 4/4 evaluation JSON examples validated; 12 canonical decision states found. |
| `python3 -m unittest discover -s tests -v` | **PASS**, exit 0. The same complete suite was counted with `python3 -m unittest discover -s tests -q`: **56 tests, OK**. |
| `git diff --check 2e2c0df8d53a7c7dd1a09177006d3d6d85de981b..HEAD` and worktree `git diff --check` | **PASS**, no whitespace errors. |

The complete individual CLI flow used
`crypto_eval/fixtures/{candidates,fixture-spec,outcomes}.json` and
`--fold test`:

```text
python3 -m crypto_eval build-dataset --source crypto_eval/fixtures/candidates.json --spec crypto_eval/fixtures/fixture-spec.json --out reports/crypto-eval-inspector-iteration-2/dataset.json
python3 -m crypto_eval run-fixture --dataset reports/crypto-eval-inspector-iteration-2/dataset.json --out reports/crypto-eval-inspector-iteration-2/predictions.jsonl
python3 -m crypto_eval score --dataset reports/crypto-eval-inspector-iteration-2/dataset.json --predictions reports/crypto-eval-inspector-iteration-2/predictions.jsonl --outcomes crypto_eval/fixtures/outcomes.json --fold test --out reports/crypto-eval-inspector-iteration-2/scores.json
python3 -m crypto_eval compare-baselines --dataset reports/crypto-eval-inspector-iteration-2/dataset.json --outcomes crypto_eval/fixtures/outcomes.json --predictions reports/crypto-eval-inspector-iteration-2/predictions.jsonl --fold test --out reports/crypto-eval-inspector-iteration-2/baselines.json
python3 -m crypto_eval report --scores reports/crypto-eval-inspector-iteration-2/scores.json --baselines reports/crypto-eval-inspector-iteration-2/baselines.json --out-json reports/crypto-eval-inspector-iteration-2/report.json --out-md reports/crypto-eval-inspector-iteration-2/report.md
```

All five commands exited 0. The dataset had 3 cases; 3 fixture predictions
were frozen; the score artifact held 3 case rows and one complete test-fold
path; all 6 baselines ran on the one selected test case. The report's test
scope/counts matched, and its generated JSON passed `eval-report.schema.json`.
It made no market-performance claim. The generated `reports/` directory was
removed after verification.

## Worktree safety note

At the first status check, the only tracked worktree modification was the
requested `status.json` iteration change. Untracked frontend work was also
present. Later, `.playwright-mcp/` and the initially listed PNG screenshots
were absent; the disappearance was not attributable from this shared
worktree. The evaluation CLI cleanup targeted only the newly generated
`reports/crypto-eval-inspector-iteration-2/` directory, and the inspected
validator/tests contain no deletion logic for those paths. I did not recreate,
stage, or commit them. The remaining unrelated untracked paths
`.goals/crypto-research-frontend/`, `frontend/`, and
`tests/test_frontend_smoke.py` were not edited or staged.

## Summary

**PASS.** All three iteration-1 defects are fixed and independently reproduced
as rejected/5%-and--1%/fold-mismatch outcomes. Required repository validation,
all 56 unit tests, and the full deterministic test-fold CLI chain pass. No
product implementation files were changed. The only Inspector deliverables
are this report and the requested iteration-2 status-history entry.
