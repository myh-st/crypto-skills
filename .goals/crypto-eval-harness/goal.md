# Goal: Build a trustworthy crypto evaluation harness

## User Request

The user asked: “ทำตามแผนนี้ให้สำเร็จจนได้ผลลัพธ์มาตอบ” (follow the attached plan through to completion and report the result). The plan requests a production-quality evaluation harness for the `crypto-market-trading-analysis` skill, implemented and validated in this repository.

## Refined Goal

Implement a modular, reproducible Python harness that evaluates frozen crypto-market decisions without leaking future information. It must score decisions according to their stated triggers, compare them with deterministic baselines, support fixture/mock model execution and a future same-model skill-vs-control experiment, and produce honest reports with no fabricated market-performance claims. Complete the related tests, CI, documentation, and end-to-end validation while preserving existing repository contracts.

## Acceptance Criteria

- [ ] Inspect the existing README files, skill and relevant references, schemas, examples, validator, tests, and workflows before implementation; preserve existing architecture and canonical contracts where reasonable.
- [ ] Implement a separable pipeline for point-in-time cases/snapshots, frozen predictions, model-runner interface, separate outcomes, scoring, baselines, metrics, and report generation.
- [ ] Require explicit `as_of`, `data_cutoff`, `asset`, `instrument`, `venue`, and `horizon` for each case. Fail closed when cutoff integrity is unclear; reject future candles and any future funding/OI, news, benchmark, label, or reflection data. Historical cases must not use current search results.
- [ ] Normalize and freeze prediction records, retain model/configuration, skill commit, prompt/config and dataset versions, never overwrite predictions after outcomes are known, and store outcome data separately.
- [ ] Support forward paper-evaluation lifecycle from pending case through frozen prediction, waiting for outcome, ready-to-score, and scored, without trading, order execution, or credentials.
- [ ] Score decision states and triggers correctly, including pullback and breakout waits, avoid-chasing and no-trade decisions. Track trigger timing, post-trigger return, MFE/MAE, targets and invalidation; do not penalize a valid wait if its trigger never occurs. Use conservative, documented handling for ambiguous intrabar ordering, gaps, multiple targets, and missing intervals.
- [ ] Define metric denominators and implement the requested decision-quality metrics: directional accuracy, target-before-invalidation rate, mean/median forward return, benchmark return and alpha, MFE, MAE, max drawdown, entry-trigger/invalidation/target-hit rates, and time-to-target/trigger. Provide useful breakdowns by decision state, asset, horizon, market/BTC regime, leverage stress, confidence, and liquidity; unavailable data must not silently become zero.
- [ ] Provide documented deterministic Buy & Hold, BTC benchmark, EMA20/EMA50, fixed-rule RSI, and seeded naive/random baselines; do not tune them to favor the skill.
- [ ] Support chronological walk-forward evaluation without random time-series shuffling or calibration/test leakage. Evaluate low/moderate/high confidence without inventing probabilities; include sample counts and intervals/paired comparisons when sample size permits and avoid overstating significance.
- [ ] Define a versioned, reproducible dataset specification targeting BTC, ETH, SOL, SUI, SEI, AVAX, and PYTH across varied regimes, with stable case IDs and no cherry-picking. Do not commit generated large datasets/results accidentally.
- [ ] Abstract read-only market data providers; start without credentials, do not fabricate derivatives/news/on-chain data, and mark unsupported data unavailable.
- [ ] Keep decision-quality evaluation separate from portfolio PnL simulation; do not claim portfolio backtesting without sizing, allocation, cash ledger, fees, slippage, fills, exits, and applicable funding.
- [ ] Provide usable CLI commands for dataset construction, fixture/mock runner, prediction scoring, baseline comparison, and report generation. Validate inputs, fail non-zero on errors, and emit deterministic machine-readable and concise human-readable output.
- [ ] Generate honest machine-readable and human-readable reports; fixture-only results must say `DEMO / HARNESS VALIDATION` and `NOT MARKET PERFORMANCE EVIDENCE`. Report the limits of programmatic model invocation rather than fabricating skill/control results.
- [ ] Add automated tests for schema validation, future-leakage rejection, dataset parsing, trigger-aware scoring, MFE/MAE, target/invalidation ordering, BTC alpha, deterministic baselines, confidence calibration, missing data, walk-forward integrity, prediction immutability, reports, and listed edge cases (same-candle target/stop, stop gaps, untriggered entries, multiple targets, missing intervals, invalid prices, timezone mismatch, partial data).
- [ ] Extend GitHub Actions with lightweight deterministic evaluation tests while preserving repository contract tests and avoiding credentials, paid APIs, or live crypto API dependencies.
- [ ] Update README documentation, including `Validation != Accuracy Evaluation`, eval commands, datasets, historical vs. forward evaluation, metrics, limitations, skill/control comparisons, and extension points. Keep it concise.
- [ ] Run `python3 scripts/validate_repo.py`, `python3 -m unittest discover -s tests -v`, and a complete deterministic dataset→prediction→outcome-scoring→baseline-comparison→report flow. Fix regressions, inspect `git diff`, and remove temporary/debug/generated junk.
- [ ] Final response reports implemented components, exact validation commands/results, fixture outcome with a clear evidence disclaimer, remaining requirements before any accuracy claim, the recommended same-model skill-vs-control experiment, and truthful branch/files/commit/push status.

## Scope Boundaries

**In scope:**
- The largest complete, coherent, auditable evaluation harness that satisfies the acceptance criteria.
- Necessary schemas/contracts, CLI scripts, fixtures, tests, docs, CI, and `.gitignore` updates.
- A mock/fixture runner when the current runtime cannot invoke both model variants programmatically; clearly document that limitation.

**Out of scope:**
- Claiming that the skill improves decisions before statistically meaningful real evaluation exists.
- Fabricated performance/benchmark results, hidden future data, secrets, paid/live API dependencies in CI, or trading/order execution.
- Claiming portfolio PnL/backtesting unless the complete required portfolio mechanics are implemented.
- Unrelated cleanup or breaking existing repository contracts without necessity.

## Applicable Project Conventions

**Quality gate command:**
- `python3 scripts/validate_repo.py`
- `python3 -m unittest discover -s tests -v`
- Run the new deterministic fixture evaluation end-to-end.

**Commit convention:**
- Discovery found no authoritative commit guideline. Use conventional commits with the goal workflow role markers: `type(scope): [B/I] description` (title ≤72 characters).
- Include the required `Assisted-by:` role trailer and `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` trailer on commits.

**Guidelines:**
- No `AGENTS.md`, `CONSTITUTION.md`, `.agents/guidelines/`, or `.github/guidelines/` found at discovery time.

**Rules:**
- No project-specific constitution/rules file found. Keep Python simple and auditable, minimize dependencies, preserve schemas/contracts, fail closed on unverifiable point-in-time integrity, and distinguish correctness, schema validity, trading performance, and forecast calibration.
