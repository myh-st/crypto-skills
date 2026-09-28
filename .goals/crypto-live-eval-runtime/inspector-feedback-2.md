# Inspector feedback — iteration 2

**Verdict: PASS**

I independently inspected `HEAD=eabd67127ff7572a6defa67254ac1afb7e31cad5`
and `.goals/crypto-live-eval-runtime/inspector-feedback-1.md`; I did not rely
on a Builder summary. `origin/main` remains `887ced89866fb53748519170d19b3080cadab230`
(the requested `887ced8` baseline). No product files were changed and nothing
was pushed.

## Acceptance-criterion review

| # | Verdict | Evidence |
|---|---|---|
| 1. Preserve evaluation contracts and existing behavior | **PASS** | `python3 scripts/validate_repo.py` passed; the full Python suite passed all 77 tests. The decision-state vocabulary remains shared with the schema. |
| 2. Read-only Binance Spot provider with point-in-time closed candles | **PASS** | `fetch_range` rejects an end after its injected retrieval clock before transport, and `_normalize_kline` rejects source closes after both cutoff and retrieval time. The exact 00:30/01:00 probe below returned no provider transport calls. |
| 3. Immutable historical archive/cache metadata | **PASS** | Archives retain provider, endpoint, range, cutoff, retrieval time, count, and content hash; builder and validator reject ranges after `retrieved_at`. The 00:30/01:00 archive probe failed closed. The market-data unit tests verify immutable content-addressed writes and secret-free records. |
| 4. Server-side GPT-6 Luna Responses runner and secret handling | **PASS (mocked contract)** | The injected transport observed the actual `/v1/responses` request; its assertions verified the standard bearer authorization scheme and exact suffix match to the injected test sentinel. The request used `model=gpt-6-luna`, `reasoning.effort=max`, and `store=false`. The injected key was not in the request body, returned prediction/run response, stdout/stderr, saved run files, or archive. |
| 5. Strict prediction normalization and explicit failures | **PASS** | The runner/parser explicitly rejects targets over five, as well as malformed/unsupported model output and API errors; missing credentials fail explicitly with no success-shaped result. Target overflow regression test passed. |
| 6. Paired skill/control execution and point-in-time prompt parity | **PASS** | The two mocked requests share the same input, model, reasoning, schema, run/case/dataset identities and config hash; only the skill instruction text differs. Prompt construction selects the frozen case fields and closed candles, not outcome records/future candles. |
| 7. Separate forward outcome lifecycle | **PASS** | Mocked lifecycle tests show early scoring makes no provider request and writes no outcomes. After the injected horizon close, a separate complete outcome archive/file is fetched, validated, and scored; repeated scoring reuses the frozen outcome. |
| 8. Existing frontend and local same-origin API integration | **PASS** | The current frontend remains present (no frontend deletions versus `origin/main`); server tests enforce loopback binding, same-origin requests, and disallow client-supplied outcome bodies. |
| 9. Structured Analyze result and pending evaluation UI; no trading | **PASS** | In an isolated local mocked-runtime browser session, Analyze returned a structured report and frozen run with `waiting_for_outcome`; no order capability is present. The report identifies the frozen Spot cutoff and pending horizon. |
| 10. Offline CI and deterministic mocked end-to-end coverage | **PASS** | CI runs repository validation, Python tests, frontend syntax/tests, and the deterministic synthetic demo only. The mocked runtime E2E suite passed; tests use injected Binance/OpenAI transports and made no external API requests. |
| 11. Missing credential behavior and live invocation boundary | **PASS with required limitation** | `OPENAI_API_KEY` was unset. The missing-key test asserts both market and model transports receive zero calls. No live model request was made; the real credentialed invocation remains unverified, as required. |
| 12. Preserve pilot record and evidence disclaimers | **PASS** | `.goals/crypto-eval-harness/model-pilot.md` is unchanged from `origin/main`; it labels its three shared cases synthetic and disclaims skill/performance evidence. `docs/evaluation.md` repeats that limitation and makes no improvement claim. The generated demo report also carries `DEMO / HARNESS VALIDATION — NOT MARKET PERFORMANCE EVIDENCE`. |
| 13. Required checks, browser flow, and unrelated-file preservation | **PASS** | All gates below passed. A connected UI flow was exercised using a separate loopback server and mocked transports. The pre-existing listeners on ports 8000 and 8766 were not touched; the isolated server and its exact generated record directory were removed. `.goals/crypto-research-frontend/` was not changed. |

## Iteration-1 blockers reproduced and rechecked

### Responses Authorization and credential isolation

`tests.test_openai_runner.OpenAIResponsesRunnerTests.test_responses_api_shape_and_frozen_prediction_contract`
captures the request passed to the injected Responses transport and asserts
the injected test key is passed in the `Bearer` authorization scheme.
It also checks that the body has no key, `store` is false, and the expected
model/reasoning/schema are sent.

The full mocked forward pair test uses the same sentinel and verifies that it
appears only in the transient auth headers—not either prompt, stdout/stderr,
the HTTP response, prediction metadata, files in the persisted run directory,
or the archive. The local browser E2E server used only these injected
transports. No real credential was available and no paid/live request was made.

`test_missing_runtime_key_fails_before_market_or_model_requests` passed with
both transport call lists empty after `create()` raised
`MissingOpenAICredentialsError`.

### Retrieval-time cutoff

I reran the case with an injected clock at 00:30 UTC, request end/candle close
boundary at 01:00 UTC, and a mocked 00:00–01:00 kline. The offline probe
reported:

```text
provider future-end rejection: requested end is after the provider retrieval time
provider retrieval-time candle rejection: probe source close timestamp is after the provider retrieval time
archive retrieval-time rejection: archive requested range extends after retrieved_at
provider transport calls=0; no external request made
```

This independently exercises the future requested end guard, the source-close
retrieval-time check, and archive rejection when `retrieved_at` precedes the
requested end/cutoff.

### Target limit and chart layout

The shared `MAX_TARGET_LEVELS` is 5. I checked all target-bearing contracts:

```text
Responses request: maxItems=5
eval-prediction frozen run decision: maxItems=5
analysis response: maxItems=5
decision record: maxItems=5
parser and contract constant: MAX_TARGET_LEVELS=5
```

The forward UI run derives `report.targets` and `priceLevels.targets` from the
validated frozen decision; the run manifest itself has no independent target
array. A six-target mocked model result is rejected with the explicit
`OpenAIResponsesError` “model output targets may contain at most 5 levels”.

I rendered a maximum overlay set including a primary and secondary entry,
invalidation, five targets, reference price, and both runtime snapshot key
levels. All 11 expected labels were present and non-overlapping; their
baselines were within the `0..1050 × 0..455` SVG viewBox (y range `43.4..307.5`).
The frontend maximum-target regression test also confirms five off-range
target lines and in-viewBox labels.

For the connected browser check, the narrow report pane has a horizontally
scrollable 900px chart in a 550px viewport. Its right-side annotations require
horizontal scrolling at that width; after scrolling, the browser-measured
labels were all inside the scroll viewport and the screenshot showed them
readably. They are in the SVG viewBox and remain reachable rather than being
permanently clipped or dropped.

## Browser verification

I confirmed port 8767 was free, then started a loopback-only runtime there with
`MockedKlineTransport` and `MockedResponsesTransport` plus the test-only
`MOCK_KEY`; the real Binance/OpenAI transports were never used. `GET
/frontend/` returned 200 and `GET /api/status` returned the expected local
runtime metadata. I opened `/frontend/#/new-analysis/BTC`, entered a question,
and clicked **Analyze with Luna**. The browser navigated to the generated run
and displayed the structured report, frozen-prediction state, pending
forward-evaluation status, UTC candle chart, and provided price-level
annotations. The chart screenshot and DOM bounds were inspected locally.

The mock-generated browser run was removed from that isolated origin's
localStorage, its persisted sentinel scan found no key in the archive, dataset,
run manifest, or prediction files, and the exact generated data directory and
server process were cleaned up. No production or paid service was called.

## Verification commands

| Command/check | Result |
|---|---|
| `python3 scripts/validate_repo.py` | **PASS** — 10 valid schemas, 3/3 examples, 4/4 evaluation JSON examples. |
| `python3 -m unittest discover -s tests -v` | **PASS** — 77 tests, `OK`. |
| `node --test frontend/tests/*.test.mjs` | **PASS** — 21 passed, 0 failed. |
| `find frontend -name '*.js' -exec node --check {} \;` | **PASS** — exit 0, no syntax diagnostics. |
| `python3 -m crypto_eval demo --out-dir .goal-inspector-iteration-2-eabd671` | **PASS** — deterministic three-case synthetic fixture; model invocation not performed; disclaimer verified. Only this generated directory was removed afterward. |
| `python3 -m unittest tests.test_forward_runtime -v` | **PASS** — 4 mocked API/pair/lifecycle/missing-key E2E tests. |
| `python3 -m unittest tests.test_openai_runner.OpenAIResponsesRunnerTests.test_responses_api_shape_and_frozen_prediction_contract tests.test_openai_runner.OpenAIResponsesRunnerTests.test_model_schema_and_parser_reject_more_than_five_targets -v` | **PASS** — authenticated mock request contract and explicit six-target rejection. |
| Injected 00:30/01:00 provider/archive probe | **PASS** — provider and archive rejected; provider transport calls 0. |
| Maximum overlay SVG probe and local mock browser flow | **PASS** — all max labels fit the SVG; browser labels remained visible after horizontal scroll. |
| `git diff --check origin/main..HEAD` | **PASS** — no whitespace errors. |

**Final note:** the API credential is absent and the real GPT-6 Luna invocation
has not been verified; confirming it requires a separately authorized,
credentialed live request. That limitation is explicitly retained rather
than making a paid call during inspection.
