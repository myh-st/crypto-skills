# Goal summary: Real market data, GPT-6 Luna, evaluation, and frontend

## Outcome

**Completed and independently verified after 2 iterations.** The evaluation harness now connects to a public read-only Binance Spot kline provider, a server-side GPT-6 Luna Responses API runner, forward evaluation, and the existing frontend behind a loopback-only same-origin API. The app can display a structured analysis and pending outcome state. No trade execution is provided.

The per-run chart also incorporates the user's clarification: it is a trading-system-style candlestick chart with real UTC coverage, closed OHLCV and volume, axes, and supplied entry/trigger, invalidation, target, reference, and snapshot levels. It does not invent technical levels; off-range labels remain reachable through the chart's fit/scroll behavior. Fixture overview cards remain explicitly identified as synthetic.

## Acceptance criteria

| # | Result | Delivered |
|---|---|---|
| 1 | PASS | Existing evaluation contracts and canonical decision states are preserved; repository validation and all existing/new Python tests pass. |
| 2 | PASS | Binance Spot public klines use explicit UTC millisecond bounds and UTC interval semantics. Candle timestamps are normalized; future/open candles beyond `as_of`, cutoff, or retrieval time fail closed. |
| 3 | PASS | Historical snapshots are archived separately with provider/range/cutoff/retrieval metadata and a content hash; archives are immutable and contain no API credentials. |
| 4 | PASS* | Server-side GPT-6 Luna runner uses the official Responses API shape, model ID `gpt-6-luna`, `reasoning.effort=max`, and an environment-supplied key. The authenticated request and secret isolation are verified with mocks. Live API invocation remains unverified because `OPENAI_API_KEY` was absent; no paid request was made. |
| 5 | PASS | Model outputs are normalized/validated against frozen-prediction contracts; missing credentials, malformed/unsupported output, API errors, and excess targets fail explicitly. The shared target maximum is five across API output, records, and frontend chart. |
| 6 | PASS | Skill/control paired runner uses identical frozen input/model configuration; only skill instructions differ. Metadata records model/config, skill, prompt, dataset, run, and freeze identity; future outcomes are excluded from prompts. |
| 7 | PASS | Forward case/prediction/outcome flow creates pending runs and refuses early scoring; outcomes are fetched, stored, and scored separately only once the horizon is complete. |
| 8 | PASS | Existing frontend is integrated with a same-origin loopback-only runtime API. Credentials stay server-side; the separate `.goals/crypto-research-frontend/` process-artifact directory was not changed. |
| 9 | PASS | Browser verification exercised Analyze → structured report → frozen prediction → `waiting_for_outcome`. Per-run TradingView-style chart displays the actual closed candle range and supplied levels; overview fixture data remains labeled synthetic. |
| 10 | PASS | CI/tests use injected market/model transports and deterministic fixtures only; no live API, credential, or paid request is required. |
| 11 | PASS* | Missing `OPENAI_API_KEY` causes an explicit failure before provider/model transports are called. Mocked authenticated invocation passes. A real model API call is not verified. |
| 12 | PASS | Existing GPT-6 Luna paired-pilot record and `NOT MARKET PERFORMANCE EVIDENCE` disclaimers remain intact; no improvement/accuracy claim was introduced. |
| 13 | PASS | Repository/Python/frontend checks, offline demo, mocked end-to-end flow, public read-only data smoke test, and isolated browser flow passed. |

## Iteration history

| Iteration | Verdict | Inspector findings and resolution |
|---|---|---|
| 1 | FAIL | Found (1) a literal masked placeholder instead of a transient authenticated bearer header, (2) acceptance of a kline not yet closed at provider retrieval time, and (3) chart labels clipped for an unbounded target list. |
| 2 | PASS | Builder added credentialed mock coverage and secret-leak checks; provider and archive reject future cutoffs/open candles; target arrays are consistently capped at five and maximum valid chart overlays fit. Inspector independently reproduced each fix. |

## Validation

- `python3 scripts/validate_repo.py` — **PASS**; 10 schemas, 3/3 repository examples, 4/4 evaluation examples.
- `python3 -m unittest discover -s tests -v` — **PASS: 77 tests**.
- `node --test frontend/tests/*.test.mjs` — **PASS: 21 tests**.
- `find frontend -name '*.js' -exec node --check {} \;` — **PASS**.
- `python3 -m crypto_eval demo --out-dir .goal-inspector-iteration-2-eabd671` — **PASS**; deterministic 3-case synthetic demo and required disclaimer; generated output removed.
- `python3 -m unittest tests.test_forward_runtime -v` — **PASS: 4 mocked runtime/API/lifecycle tests**.
- OpenAI request auth/schema and target-bound tests — **PASS**; provider/archive cutoff probes rejected an end/candle at 01:00 when retrieval clock was 00:30 with zero transport calls.
- Inspector's isolated browser flow — **PASS** using loopback-only server and mocked model/data transports; chart SVG labels were inspected and fit within its viewBox. A separate public, read-only Binance smoke request used no API key.
- No paid/live GPT-6 Luna API request was made. It remains unverified without an authorized API credential.

## Remaining gap and recommended next experiment

The app and API are ready for a credentialed deployment, but a real OpenAI Responses API invocation has not been performed. Provide/configure `OPENAI_API_KEY` in the server runtime and authorize a single small live request before treating the model connection as live-verified. Keep the key out of browser code, logs, fixtures, and generated run records.

After live connectivity is verified, run a preregistered forward cohort of identical frozen point-in-time cases through skill and no-skill arms, then score separate outcomes. The 3-case synthetic pilot is only a harness smoke test and cannot establish skill improvement. Public Binance data is read-only; the system does not execute trades or simulate portfolio PnL.

## Git and shared-worktree status

- Branch: `main`
- Initial SHA: `c821bd889dc8e8578a391ef8357f6793bf994b60`
- Builder implementation commit: `eabd67127ff7572a6defa67254ac1afb7e31cad5`
- Inspector PASS commit: `d92cc861afd8f5703142ea01ed4e3c177d0e6a0c`
- Push status: **not pushed**; `main` is 4 commits ahead of `origin/main` (`887ced8`).
- Inspector reports a clean tracked worktree before this final summary/status update. This `.goals/crypto-live-eval-runtime/summary.md` and `status.json` completion update are process artifacts to include in the user's eventual squash/commit.
