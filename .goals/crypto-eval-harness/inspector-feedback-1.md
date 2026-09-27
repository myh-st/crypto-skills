# Inspector feedback — iteration 1

**Verdict: FAIL**

## Scope and independent review

I reviewed `.goals/crypto-eval-harness/goal.md`, the repository diff from
`initial_sha` `2e2c0df8d53a7c7dd1a09177006d3d6d85de981b`, and Builder commit
`90ef30b` (`feat(eval): [B] add point-in-time crypto evaluation harness`).
The commit changes 33 files, all within the evaluation harness, its schemas and
fixtures, tests, documentation, validator, `.gitignore`, and CI. I made no
implementation/product edits.

## Acceptance criteria

| # | Verdict | Evidence |
|---|---|---|
| 1. Inspect existing contracts and preserve architecture | **UNVERIFIED** | The final changes add a separate `crypto_eval` package and continue using the repository's canonical decision-state schema. `python3 scripts/validate_repo.py` passes the existing skill/schema/example checks. The commit does not prove the Builder's pre-implementation read order. |
| 2. Modular evaluation pipeline | **PASS** | Dataset construction, runner, providers, lifecycle, scoring, baselines, reporting, CLI, schemas, and fixtures are separate modules/contracts. |
| 3. Explicit point-in-time coordinates and fail-closed leakage checks | **FAIL** | Required coordinates, timezone-aware timestamps, cutoff checks, and named future-data checks exist. However, unrecognized snapshot keys are accepted and preserved: a runtime probe added `snapshot.future_label = {"value": "bullish", "future_close": 999.0}` and `build_dataset(...)` **accepted it**, retaining the field in the frozen case. See Finding F1. |
| 4. Freeze and preserve predictions separately from outcomes | **PASS** | Prediction records carry dataset/run metadata and a content hash; the default writer uses exclusive creation and append checks duplicate run/case identities. Outcome bundles are separate and bound to the dataset hash. |
| 5. Forward paper-evaluation lifecycle | **PASS** | Lifecycle projection implements pending, frozen, waiting, ready-to-score, and scored states; the lifecycle unit test passes. |
| 6. Trigger-aware decision/path scoring | **FAIL** | Waits, untriggered entries, same-candle ambiguity, gaps, and target ordering have implementation/tests. But delayed-trigger MFE/MAE include OHLC from bars before entry. A direct probe returned `(1.0, -0.5)` where post-trigger-only values are `(0.05, -0.01)`. See Finding F2. |
| 7. Metrics, denominators, intervals, and breakdowns | **FAIL** | The requested metric families, counts, Wilson intervals, qualitative confidence breakdowns, and grouping dimensions are present. MFE/MAE are nevertheless wrong for a delayed trigger because of F2, so this criterion is not met end-to-end. |
| 8. Deterministic fixed baselines | **PASS** | Six fixed baseline names are emitted; EMA/RSI return unavailable when history is insufficient, and the random comparator is case-ID/seed deterministic. The fixture suite and run passed. |
| 9. Chronological walk-forward and confidence handling | **PASS** | Spec enforces expanding chronological folds with shuffle disabled; cases are ordered and folds are recomputed/validated. Confidence is treated as qualitative observed hit-rate, not probability calibration. |
| 10. Versioned, reproducible target dataset specification | **PASS** | `eval/specs/crypto-market-v1.json` declares BTC, ETH, SOL, SUI, SEI, AVAX, and PYTH, fixed weekly sampling, coverage/regime requirements, exclusion rules, and chronological splits. Stable IDs and sampling reconciliation are implemented. |
| 11. Read-only provider abstraction and unavailable data | **PASS** | Provider protocol is read-only; only a local JSON archive provider ships. Unsupported lanes remain unavailable rather than fabricated. |
| 12. Decision quality separated from portfolio PnL | **PASS** | Documentation and generated reports explicitly disclaim portfolio simulation; no execution or trading path was added. |
| 13. CLI and deterministic workflow | **PASS** | Dataset build, fixture prediction, score, baseline comparison, report, and status commands are present. The full individual CLI chain completed successfully; its artifacts matched the documented `demo` flow byte-for-byte. |
| 14. Honest reports and evidence boundaries | **FAIL** | The actual synthetic report is clearly labeled `DEMO / HARNESS VALIDATION — NOT MARKET PERFORMANCE EVIDENCE`, identifies the fixture runner as not invoked, sets accuracy/PnL claims to `none`, and qualifies its n=1 metrics. But the report builder accepts mismatched score/baseline folds and then labels the combined report with only the score fold. See Finding F3. |
| 15. Automated regression coverage | **FAIL** | The suite covers most listed cases and passes, but does not cover the demonstrated unknown future-label field, pre-trigger excursion contamination, or cross-fold report merge. These gaps allow the F1–F3 defects to pass. |
| 16. CI integration | **PASS** | Workflow retains repository tests and adds the deterministic fixture demo; no live API or credentials are required. |
| 17. README and evaluation documentation | **PASS** | English/Thai READMEs reference the harness. `docs/evaluation.md` documents validation versus accuracy, commands, data boundaries, lifecycle, metrics, baselines, comparison limits, and the PnL boundary. |
| 18. Required verification, diff, and cleanup | **PASS** | Commands and results are recorded below. `git diff --check` passes. The Builder commit contains only evaluation-related paths; generated report directories from this inspection were removed. Untracked UI/screenshots and other goal artifacts visible at inspection start were not edited or staged. `frontend/favicon.svg` appeared in a later untracked-file listing and was left untouched; its origin is unknown. |

## Findings

### F1 — Unknown snapshot data bypasses the point-in-time boundary

`crypto_eval/contracts.py:637-649` rejects a finite set of exact label keys and
checks recognized timestamps, but does not allowlist snapshot fields. The
candidate schema also sets `snapshot.additionalProperties` to `true`
(`schemas/eval-candidates.schema.json:81-96`). Consequently, an input field
such as `future_label` with no timestamp is accepted by the actual dataset
builder and included in the case snapshot. A model runner consuming that case
can therefore receive future labels despite the documented fail-closed
guarantee. Add a regression for this exact class of input and make runtime and
schema validation reject unrecognized outcome/label-bearing snapshot content.

### F2 — Pre-trigger candles contaminate MFE/MAE

`crypto_eval/scoring.py:384-407` loops over every outcome bar. It substitutes
the close for the trigger bar, but does not skip bars with `index <
trigger_index`; their full high/low excursions are measured against the later
entry reference. The probe's pre-trigger bar had high 200 and low 50, producing
MFE 100% and MAE -50%, although the trigger-and-later path had MFE 5% and MAE
-1%. This violates the required post-trigger excursion definition and can
materially distort both case results and aggregate/breakdown metrics.

### F3 — Report accepts baseline metrics from a different fold

`crypto_eval/reporting.py:24-32` checks dataset/version/hash and outcome hash,
but not `fold_scope`. It then places baseline metrics in the report while
copying only the score fold into `report.dataset.fold_scope` (`:51-57`,
`:70-74`). Reproduction: a `test` score had one test case; `compare_baselines`
with `fold="all"` had three returns; `build_report` accepted both and emitted
`fold=test` with the all-fold baseline (`n=3`). This breaks the documented
same-fold comparison and can mislead report readers. The report's
`sample_counts` also mixes all rows in `scores.cases` with the selected-fold
complete-path metric (`:76-84`); label these scopes or make them consistent.

## Verification and report review

| Command/check | Result |
|---|---|
| `python3 scripts/validate_repo.py` | **PASS**, exit 0. Existing skill validation passed; 10 schemas, 3/3 existing examples, and 4/4 evaluation JSON examples validated; 12 canonical decision states found. |
| `python3 -m unittest discover -s tests -v` | **PASS**, exit 0: 50 tests ran, `OK`. |
| `python3 -m crypto_eval demo --out-dir reports/crypto-eval-demo` | **PASS**, exit 0: 3 synthetic cases; fixture runner not invoked; output explicitly said `NOT MARKET PERFORMANCE EVIDENCE`. |
| Individual CLI chain: `build-dataset` → `run-fixture` → `score --fold test` → `compare-baselines --fold test` → `report` using `crypto_eval/fixtures/{candidates,fixture-spec,outcomes}.json` | **PASS**, exit 0 at every step. Dataset hash was `d96e59558e70c6fcf3d94c218eb8be05f9eb7420391c6f8b95683ed322eeb98b`; 3 predictions, 1 complete test path, 6 fixed baselines; report printed the required evidence disclaimer. |
| Compare `demo` and individual-CLI artifacts | **PASS**, all six output files were byte-identical. |
| `git diff --check 2e2c0df8d53a7c7dd1a09177006d3d6d85de981b...HEAD` | **PASS**, no whitespace errors. |

The generated Markdown/JSON report is truthful about the run actually
performed: synthetic fixture data, no model invocation, one test-fold metric
sample, explicit small-sample/no-significance language, and no accuracy or
portfolio-PnL claim. Fixture predictions were frozen at each case's `as_of`;
fixture outcomes begin at the cutoff and their candles precede `known_at`.
That successful fixture does not cure F1: the separate mutation probe shows
that arbitrary future-labeled snapshot data can cross the same runtime
validation boundary.

## Summary

**FAIL.** The repository validator, all 50 unit tests, and both deterministic
end-to-end paths pass, and the produced fixture report is appropriately
disclaimed. Acceptance is blocked by F1 (future-label snapshot bypass), F2
(pre-trigger MFE/MAE contamination), and F3 (cross-fold report composition).
No product files were changed by the Inspector. Only the requested process
artifacts are to be committed.
