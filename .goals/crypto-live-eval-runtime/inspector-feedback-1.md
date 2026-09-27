# Inspector feedback — iteration 1

**Verdict: FAIL**

I independently inspected the current tree and `origin/main..HEAD`; this
feedback does not rely on the Builder's summary. No implementation files were
changed during inspection.

## Acceptance-criterion review

1. **Preserve evaluation contracts and existing behavior — PASS.**
   `python3 scripts/validate_repo.py` passed. `python3 -m unittest discover -s
   tests -v` ran 73 tests and returned `OK`.

2. **Read-only Binance Spot provider; closed, point-in-time ranges; fail closed
   on future/stale/missing/bad data — FAIL.**
   The ordinary `fetch_history()` path rejects a future `as_of`, but
   `BinanceSpotKlinesProvider.fetch_range()` floors the caller's `end` and
   validates candles against that cutoff without checking the provider clock.
   A still-open current candle can therefore be returned when `end` is the
   next interval boundary. Reproduced offline with a mocked Binance response:

   ```text
   $ python3 - <<'PY'
   import json
   from datetime import datetime, timedelta, timezone
   from crypto_eval.market_data import BinanceSpotKlinesProvider
   start = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)
   now = start + timedelta(minutes=30)
   row = [int(start.timestamp() * 1000), "100", "102", "99", "101", "12.5",
          int((start + timedelta(hours=1)).timestamp() * 1000) - 1]
   provider = BinanceSpotKlinesProvider(
       clock=lambda: now,
       transport=lambda _url, _timeout: json.dumps([row]).encode("utf-8"),
   )
   candles = provider.fetch_range("BTCUSDT", "1h", start, start + timedelta(hours=1))
   print(f"clock={now.isoformat()} requested_end={(start + timedelta(hours=1)).isoformat()}")
   print(f"accepted={len(candles)} candle_close={candles[0]['close_time']}")
   PY
   clock=2026-01-01T00:30:00+00:00 requested_end=2026-01-01T01:00:00+00:00
   accepted=1 candle_close=2026-01-01T01:00:00.000000Z
   ```

   The returned bar is not closed at retrieval time. This matters to the
   provider's point-in-time contract even though the forward-create path uses
   `fetch_history()` and the archive writer has a later retrieval-time check.
   Add a `fetch_range` future-end/current-open-candle guard and regression test.

3. **Explicit immutable historical archive/cache metadata — PASS.**
   `archive-binance` is present; archive records validate provider/endpoint,
   requested range, cutoff, retrieval time, candle count, and content hash.
   Mocked archive tests pass. The run path saves snapshot archives separately
   from outcomes.

4. **Server-side GPT-6 Luna Responses runner with environment-only key,
   `reasoning.effort=max`, and safe endpoint configuration — FAIL.**
   Model/config and JSON-schema request fields are present, and the key is read
   from the server environment by default. However,
   `crypto_eval/openai_runner.py:548` sets the transport header to the literal
   `"******"` instead of a correctly credentialed value sourced from `api_key`.
   The mock at `tests/test_openai_runner.py:107` asserts the same placeholder,
   so the current tests codify a request that cannot authenticate to the
   documented OpenAI endpoint. The end-to-end mock ignores the header as well.
   Correct the header while continuing to exclude credentials from persisted
   records and logs, and make the mock verify the transient authorization
   contract.

5. **Strict prediction normalization and explicit failure cases — PASS.**
   Tests cover invalid/missing fields, unsupported states, malformed JSON,
   missing credentials, HTTP/transport failures, tool-use and refusal
   responses. The runner produces no success-shaped fallback.

6. **Paired skill/control runs over the same frozen case/configuration — PASS.**
   The paired-runner tests assert identical input/model/reasoning/config except
   for skill instructions and verify shared case/dataset/run identity. Outcome
   data is kept outside both prompts; run metadata records model/config,
   prompt, skill, dataset, and freeze identifiers.

7. **Forward lifecycle and separate post-horizon outcomes — PASS.**
   The mocked runtime test verifies early scoring is refused without an
   outcome fetch, then accepts and scores a complete separate candle horizon.
   The same mock suite exercises pending status and the API score endpoint.

8. **Preserve/integrate the existing local frontend behind a same-origin,
   loopback-only API — PASS.**
   The server rejects non-loopback binds and cross-origin requests, serves the
   existing frontend, and exposes the runtime through same-origin endpoints.
   No credential is sent from frontend services.

9. **Frontend request/result/pending-status flow; no trading or public
   unauthenticated deployment — PASS.**
   The mocked API integration returns a structured report and
   `waiting_for_outcome` state. The API binds only to loopback; no order or
   public deployment functionality was added.

10. **Offline CI/provider/runner/API/frontend tests and mocked end-to-end
    flow — PASS.**
    CI runs validation, Python tests, JavaScript checks, frontend tests, and
    the deterministic demo; the runtime tests inject mocked market/model
    transports. No live provider dependency or CI secret is required. The
    mocked end-to-end flow passes, but its transport does not catch criterion
    4's incorrect authentication header.

11. **No credential → no paid/live request; authenticated runner contract
    validated with mocks — FAIL (second clause).**
    `OPENAI_API_KEY` was unset. `test_missing_runtime_key_fails_before_market_or_model_requests`
    passed, so no request was attempted with missing credentials. I made no
    live model call and no Binance request. Live invocation therefore remains
    unverified as required, but the authenticated runner contract is **not**
    validated: the mock observes `"******"` instead of an authorization value
    containing the supplied test key.

12. **Preserve paired pilot record and distinguish synthetic pilot from
    real/forward evidence — PASS.**
    `.goals/crypto-eval-harness/model-pilot.md` remains present and unchanged
    from the base. It identifies the prior paired pilot as three synthetic
    cases and explicitly disclaims market-performance evidence. The updated
    evaluation documentation retains that limitation and makes no improvement
    claim.

13. **Run all checks, browser-check the runnable UI, and preserve unrelated
    files — PASS within the no-credential boundary.**
    All requested checks completed (exact results below). In the already
    running fixture-only app at `http://127.0.0.1:8000/frontend/`, I submitted
    a New Analysis fixture request, opened its report/chart, and saved the
    decision; the UI showed `Decision saved ✓`. The generated browser-local
    run and decision were removed afterward. This was a fixture run, not a
    live forward run. `GET /api/status` on that static server returned 404;
    no connected runtime was available, so a live forward chart was not
    visually exercised. Its renderer was checked by the offline chart tests.
    The 29 frontend paths present in `origin/main` are all still present in
    `HEAD`; there are no frontend deletions, and
    `.goals/crypto-research-frontend/` was untouched.

## Additional chart acceptance

**FAIL for unrestricted valid overlays; ordinary chart behavior passes; live
browser rendering: unverified.**
The live chart consumes the supplied closed `marketCandles`, filters only
within returned coverage, labels `Price (USDT)`, `Time (UTC)`, Binance Spot
closed OHLCV and base-unit volume, reports actual first-open/latest-close UTC
coverage and returned candle counts, and shows latest OHLCV. Overlay labels
come from the supplied `priceLevels` and snapshot `marketStructure.keyLevels`:
reference price, entry zone/trigger/reference, invalidation, and numbered
targets. The chart adds exact values and directional edge markers for
off-range levels, has a fit-all-levels control, and does not invent
support/resistance or future outcomes. Label positions are vertically
distributed; the chart test checks off-range exact prices and levels. The
overview continues to source fixture data only and labels cards `24h ·
fixture` / `Fixture prices · 24h`, even when runtime mode is live. The browser
screenshot in this inspection was explicitly synthetic fixture data; it is
not evidence of a live chart.

`node --test frontend/tests/*.test.mjs` passed 20/20, including live-chart
assertions for UTC candle times, OHLCV, actual returned coverage, supplied
overlays, off-range arrows/values, and absence of invented support/resistance.
However, the decision/Responses schemas impose no `maxItems` on `targets`, and
the chart's SVG has a fixed `viewBox` while `distributeLevelLabels()` stacks
labels at 17px. An offline render with 20 valid structured targets plus the
reference label put two label baselines outside the SVG:

```text
$ node --input-type=module - <<'JS'
import { renderPriceChart } from './frontend/modules/components/priceChart.js';
const run = {
  id: 'level-label-boundary-probe', asset: 'BTC', runtimeMode: 'live',
  entryKind: 'none', requestSettings: { interval: '1h' },
  marketCandles: [
    { open_time: '2026-09-27T14:00:00.000000Z', close_time: '2026-09-27T15:00:00.000000Z', open: 100, high: 102, low: 99, close: 101, volume: 10 },
    { open_time: '2026-09-27T15:00:00.000000Z', close_time: '2026-09-27T16:00:00.000000Z', open: 101, high: 103, low: 100, close: 102, volume: 11 },
  ],
  priceLevels: { current: 102, entryZone: null, secondaryEntry: null, invalidation: null, targets: Array.from({ length: 20 }, (_, i) => 110 + i) },
  marketStructure: { keyLevels: [] },
};
const html = renderPriceChart(run, '1D');
const labels = [...html.matchAll(/<text x="[^"]+" y="(-?[\d.]+)" class="chart-level-label/g)]
  .map(match => Number(match[1]));
console.log(`structured targets=20, labels=${labels.length}`);
console.log(`label baselines min=${Math.min(...labels)} max=${Math.max(...labels)}, SVG viewBox y=[0,455]`);
console.log(`labels above viewBox=${labels.filter(y => y < 0).length}, below viewBox=${labels.filter(y => y > 455).length}`);
JS
structured targets=20, labels=21
label baselines min=-32.5 max=307.5, SVG viewBox y=[0,455]
labels above viewBox=2, below viewBox=0
```

This input passes the current unbounded target-array schema; at least two
provided target labels are clipped and cannot be read. Bound the accepted
target count consistently with the existing contract, or make the chart layout
accommodate all valid supplied overlays without clipping; add an edge-case test.

## Verification evidence

| Command/check | Result |
|---|---|
| `python3 scripts/validate_repo.py` | PASS — repository validation; 10 schemas, 3/3 examples, 4/4 evaluation JSON examples. |
| `python3 -m unittest discover -s tests -v` | PASS — 73 tests in 0.640s; `OK`. |
| `node --test frontend/tests/*.test.mjs` | PASS — 20 passed, 0 failed. |
| `find frontend -name "*.js" -exec node --check {} \;` | PASS — exit 0, no syntax diagnostics. |
| `python3 -m crypto_eval demo --out-dir .goal-inspector-iteration-1` | PASS — deterministic synthetic dataset, 3 cases, test fold; “Model invocation: not performed”. Generated output was removed after verification. |
| `git diff --check origin/main..HEAD` | PASS — no whitespace errors. |
| Changed-file credential-like literal scan | PASS — no matches across 28 changed files; the environment check reported `OPENAI_API_KEY is unset`. Mock tests assert the key is absent from persisted run/archive/status data. |
| Browser fixture Analyze/save | PASS — `http://127.0.0.1:8000/frontend/` returned 200; `#/new-analysis/SEI` produced a run and chart, and saving changed the button to `Decision saved ✓`. Static `/api/status` returned 404. |
| Mocked future-range probe | **FAIL** — `fetch_range` returned a candle closing at 01:00 while its injected clock was 00:30. |
| Mocked Responses API authentication | **FAIL** — header value is literal `"******"`; no real provider call was made. |
| Offline target-label layout probe | **FAIL** — two labels from a valid 20-target payload fell above the chart SVG viewBox. |

## Required fixes before PASS

1. Set the request's `Authorization` header according to the documented
   Responses API scheme using the configured `api_key`, and update mocks to
   verify that authenticated request contract without persisting the key.
2. Reject `fetch_range` requests whose cutoff is in the future relative to the
   provider clock (and add a regression test for a currently open candle at the
   next boundary).
3. Keep every overlay label readable for every valid decision payload; add a
   consistent target-count bound or a layout that expands/scrolls instead of
   clipping valid labels.

After those fixes, rerun the listed quality gates and retain the no-live-call
boundary unless the user separately authorizes a paid model invocation.
