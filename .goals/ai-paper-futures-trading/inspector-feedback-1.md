# Inspector Feedback — Iteration 1

**Verdict: FAIL**

## Blocking findings

1. **Non-fixture provider authentication is not wired to the configured credential.** `crypto_eval/paper_ai.py:83-86` returns the literal `******` for TypeSafe's default `bearer` scheme; the resolved token is only returned for `raw`. `crypto_eval/paper_ai.py:972` and `:1019` likewise send `Authorization: ******` for OpenAI and compatible Responses providers, although `_credentials` has already resolved the environment value. Only Foundry uses the resolved value in its `api-key` header. The default provider scheme is `bearer` (`crypto_eval/paper_contracts.py:384`). The loopback mock test asserts the literal mask (`tests/test_paper_futures.py:1376`), so it can pass without authenticating. Real provider Test Connection/inference will not use the configured key in these default paths. Fix the adapter headers and make mock assertions verify correct use of a synthetic test token without logging or returning it.

2. **GPT Test Connection skips configured reasoning-mode validation.** `ResponsesAdapter.generate_intent` adds `reasoning.effort` from provider settings (`crypto_eval/paper_ai.py:961-963`), but `ResponsesAdapter.test_connection` builds a separate payload without that field (`:1013-1057`). A provider can therefore pass Test Connection even when the configured reasoning mode is unsupported.

## Verification evidence

- `python3 scripts/validate_repo.py` passed (15 schemas, 3 examples, 4 evaluation examples); Python suite passed **105 tests**; Node suite passed **19 tests**; Python compile and frontend syntax checks passed; the deterministic evaluation fixture passed without a model invocation.
- Deterministic tests cover typed Jev parsing, mocked auth/error handling, configured Jev fallback, escalation boundaries, shared frozen inputs, duplicate-cycle suppression, risk rejection, isolated-margin/liquidation and bounded limit fills, fee/funding/slippage/closed-PnL reconciliation, persistence, and export reconciliation.
- Started the app with its `.env` loader disabled and used only the disposable SQLite database, fixture providers, and fixture market data. Browser smoke passed provider add/test for fixture Jev and GPT, experiment save/market-source test, five cycles with zero duplicates, start/pause/resume/stop, dashboard/portfolio/positions/evaluation/risk/activity views, and the export UI. It showed two open positions, six arm rows, five leverage cohorts, and fixture-labeled usage; observed browser resource hosts were only `127.0.0.1:8765`.
- No real provider, exchange, or paid model requests were made. `.env` remains ignored and untracked; no secrets or product files were staged. Source exposes no live-money order route; market-data requests are read-only GETs.

Live credential-backed provider validation remains deferred as requested. The authentication defect above is visible in the local adapter and mocked request construction, so it is not waived by that deferral.
