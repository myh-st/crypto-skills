# Inspector Feedback — Iteration 3

**Verdict: FAIL**

## Independent review

Reviewed branch `feature/ai-paper-futures-trading`, `HEAD` `5311881911582ed547ecb814671b21d6b22e2e37`, and the full delta from base `887ced89866fb53748519170d19b3080cadab230` (39 files, 17,656 insertions, 49 deletions). Commits `cc88f9b7d8c5047f2d25829013e01890ed3b33dc` and `5311881911582ed547ecb814671b21d6b22e2e37` are siblings on `09cf6084c577d6191c47c6f47c4f519a2b3882c1`; the shared runtime, docs, schema, and Python test files are identical. The checked-out commit adds the frontend routing view and tests. The working tree had a pre-existing `status.json` iteration value of 3 (committed value 2); it was preserved during inspection, then advanced to 4 as required for this FAIL. No product files were changed.

## Rechecked findings

- **Provider authentication and redaction: PASS.** `crypto_eval/paper_ai.py:75-85,967-975,1016-1028` resolves credential references in the server process; TypeSafe uses its configured scheme, OpenAI/generic Responses use the resolved bearer value, and Foundry uses `api-key`. Mock transports verify the resolved synthetic credential is confined to the expected header. Tests also cover sanitized provider failures and omission of credential references/values from API responses, logs, and exports. No real credential or provider request was used.
- **GPT reasoning validation: PASS.** `ResponsesAdapter.test_connection` includes the configured `reasoning.effort` and validates the structured response (`crypto_eval/paper_ai.py:1016-1093`). Tests cover low/medium/high/max, unsupported effort with no request, and provider rejection.
- **Completed-cycle routing and explicit escalation rate: PARTIAL.** The dashboard displays Quant, Jev, escalation, Luna, risk, and persisted order/fill counts for completed cycles; the evaluation exposes an explicit rate and denominators. The incomplete-cycle case below remains incorrect.
- **Grouped PnL: PARTIAL.** Asset/regime counts, PnL, expectancy, origin labels, and reconciliation to the primary closed-trade ledger are implemented. The requested grouped drawdown is not.

## Blocking acceptance gaps

1. **Unknown routing is reported as a decision.** In `crypto_eval/paper_runtime.py:5051-5053`, an absent/invalid persisted escalation route becomes `False`; `schemas/paper-dashboard.schema.json:100` requires a boolean, so an unknown cannot be represented. Missing `primary_arm` similarly yields `luna_result: null` (`paper_runtime.py:5070-5072`), which the UI renders as “Not invoked” (`frontend/modules/views/paperTrading.js:317-350`). An in-memory recovery check of a persisted interrupted cycle with no primary decision returned `escalation_required: false`, `luna_result: null`, and risk `approved: false` / `NOT_EVALUATED`. The user-facing “No” and “Not invoked” therefore imply facts not present in persisted state. Make these fields represent unknown/not-run and render them distinctly; avoid presenting `NOT_EVALUATED` as “Not approved.”

2. **Per-regime/per-asset drawdown is always missing.** `PaperRuntimeReports._bucket_trade_metrics` sets `max_drawdown` to `None` for every group (`crypto_eval/paper_runtime.py:4370-4395`); the dashboard consequently renders `—` despite having closed trades (`frontend/modules/views/paperTrading.js:206-219,458-467`). The integration test explicitly asserts `None` for both grouped drawdowns after a closed trade (`tests/test_paper_futures.py:1163,1183`), and the runtime documentation confirms it is unavailable (`docs/paper-futures-runtime.md:223-227`). This does not satisfy the requested actual drawdown breakdown.

3. **The Export/Data Retention settings section is incomplete.** The goal lists Export/Data Retention under Settings (`.goals/ai-paper-futures-trading/goal.md:269-281`). The UI has an Export button inside Runtime controls (`frontend/modules/views/paperTrading.js:723-735`), but no data-retention section or retention control exists in the current frontend/runtime. Export works; retention settings remain absent.

## Verification

- `python3 scripts/validate_repo.py` — passed (16 schemas, 3 examples, 4 evaluation examples).
- `python3 -m unittest discover -s tests -v` — passed; quiet rerun confirmed 109 tests. Focused `test_market_to_escalation_fill_funding_exit_metrics_and_export` — passed.
- `node --test frontend/tests/*.test.mjs` — passed, 22 tests.
- In-memory Python compilation — passed for 30 files; `node --check` — passed for 4 frontend modules; branch/worktree `git diff --check` — passed.
- Browser QA at `http://127.0.0.1:8765/frontend/#/paper-trading` used an in-memory SQLite database, fixture market/provider adapters, and an overridden environment-file loader. Verified provider and fixture-market tests, provider save/enable/disable/default flows, EXP-001 defaults, Start/Pause/Resume/Stop, five fixture cycles plus five duplicate-slot blocks, positions, risk/activity, equity/evaluation charts, and export. The exported ZIP had 25 files including all required evaluation files; its manifest reported no credential references or values, and a synthetic missing environment-variable reference was absent. The app server was stopped and port 8765 verified closed.
- `.env` was never opened, read, modified, or staged. It remains ignored and untracked. No external AI, market-data, exchange, or paid-model call was made.

Live provider tests and public market-data network tests remain deferred as explicitly instructed; this is not a failure.
