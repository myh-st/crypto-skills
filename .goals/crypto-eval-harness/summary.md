# Goal summary: Crypto Skill Evaluation Harness

## Outcome

**Completed and independently verified after 2 iterations.** The repository now has a deterministic, fixture-capable evaluation harness for `crypto-market-trading-analysis`. A separate GPT-6 Luna sub-agent pilot was also run after the implementation; the harness still does not establish that the skill improves market predictions.

## Acceptance criteria

| # | Result | Delivered |
|---|---|---|
| 1 | PASS* | Existing repository contracts and canonical decision states are preserved; the evaluation package is separate. The independent review could not establish the exact pre-implementation read order from Git history. |
| 2 | PASS | Point-in-time dataset, provider, runner, lifecycle, scoring, baselines, reporting, CLI, schemas, and fixtures are separate components. |
| 3 | PASS | Cases carry required point-in-time coordinates. Unknown/nested fields, future timestamps, and label-bearing data fail closed before runner invocation. |
| 4 | PASS | Frozen prediction records bind case/dataset/runner metadata and content hashes; outcome data is separate and predictions are append-only/immutable. |
| 5 | PASS | Forward paper-evaluation lifecycle is supported without trading or order execution. |
| 6 | PASS | Wait-state trigger scoring, target/invalidation ordering, gaps, missing intervals, and post-trigger MFE/MAE are covered. Both breakout and pullback regression probes return 5.00% MFE / -1.00% MAE without pre-trigger leakage. |
| 7 | PASS | Metrics have documented denominators and breakdowns; unavailable values remain unavailable. Confidence remains qualitative, with statistical intervals used only where appropriate. |
| 8 | PASS | Six fixed comparators are provided: Buy & Hold, BTC, EMA20/50, RSI14, previous-candle naive, and seeded random. |
| 9 | PASS | Walk-forward folds are chronological and validated; shuffling is disabled and confidence is not represented as invented probabilities. |
| 10 | PASS | A versioned dataset specification covers BTC, ETH, SOL, SUI, SEI, AVAX, and PYTH with reproducible stable case IDs and fixed exclusions. |
| 11 | PASS | A read-only provider interface and local archive adapter are included; unsupported data is unavailable rather than fabricated. |
| 12 | PASS | Decision scoring is separate from portfolio PnL; no portfolio-backtesting claim is made. |
| 13 | PASS | The dataset, fixture runner, scoring, baseline comparison, and report CLI flow completes successfully. |
| 14 | PASS | Fixture reports say `DEMO / HARNESS VALIDATION — NOT MARKET PERFORMANCE EVIDENCE` and disclose the lack of model invocation. |
| 15 | PASS | Automated regression coverage includes schema/leakage, trigger scoring, excursions, fold scope, immutability, lifecycle, missing data, baselines, and reporting. |
| 16 | PASS | CI retains repository validation/unit tests and adds an offline deterministic evaluation demo. |
| 17 | PASS | English/Thai README material and `docs/evaluation.md` explain validation vs. accuracy, operation, data boundaries, metrics, baselines, and limitations. |
| 18 | PASS* | Required validation and CLI flow pass; generated report output was cleaned up. Unrelated shared-worktree artifacts were not staged or edited. |

## Iteration history

| Iteration | Verdict | Inspector findings and resolution |
|---|---|---|
| 1 | FAIL | Found an unrecognized nested future-label data bypass, pre-trigger bars contaminating wait-decision MFE/MAE, and reports accepting mismatched test/all-fold comparisons. |
| 2 | PASS | Builder closed the unknown-field path, restricted excursions to the actual trigger onward, and enforced matching fold/case scopes. Inspector independently reproduced all three fixes. |

## Validation

- `python3 scripts/validate_repo.py` — **PASS**; 10 schemas, 3/3 existing examples, 4/4 evaluation examples, and 12 canonical decision states validated.
- `python3 -m unittest discover -s tests -v` — **PASS**, 56 tests.
- Deterministic CLI dataset → frozen fixture predictions → outcome scoring → baseline comparison → report — **PASS**. The report contains 3 synthetic cases and 3 fixture predictions; the selected test fold contains 1 complete path and all 6 baselines run on that one eligible test case.
- `git diff --check 2e2c0df8d53a7c7dd1a09177006d3d6d85de981b..HEAD` and worktree `git diff --check` — **PASS**.

The example is harness validation only. Its tiny synthetic fixture is not market-performance evidence, not a skill/control A/B test, and not a statistically meaningful result.

## Remaining gap and recommended experiment

The harness's checked-in/demo CLI runner is still deterministic fixture mode; a separate paired GPT-6 Luna sub-agent pilot has now run on the same three synthetic cases, as recorded in [model-pilot.md](./model-pilot.md). It is not a real historical or prospective market cohort, and the result is far too small and artificial to establish that the skill improves prediction or decision quality. Confidence remains qualitative, and portfolio PnL is not simulated.

Next, run the **same model with the skill vs. without the skill** on a sufficiently large, predeclared set of identical frozen point-in-time cases, with only skill instructions changed. Prefer a prospective forward-paper cohort to mitigate model-training contamination; balance asset/regime coverage, preserve immutable prompts/predictions/outcomes in the harness, and report paired uncertainty and sample counts before making any performance claim.

## Git and shared-worktree status

- Branch: `main`
- Evaluation implementation and inspection: 4 commits since initial SHA `2e2c0df8d53a7c7dd1a09177006d3d6d85de981b`; current HEAD at summary creation: `7b20e9cbf1496d4e19e628a0e3021c55b2c65570`.
- Push status: **not pushed**; local `main` was 4 commits ahead of `origin/main`.
- The independent review counted 36 evaluation-related changed paths.
- Unrelated untracked paths left untouched: `.goals/crypto-research-frontend/`, `frontend/`, and `tests/test_frontend_smoke.py`.
- The Inspector also observed `.playwright-mcp/` and PNG screenshots present at an earlier shared-worktree check but absent later; the disappearance could not be attributed. They were not recreated, staged, or committed.
