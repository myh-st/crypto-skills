# Crypto Research frontend summary

## Implemented

- Built a research-first console with Overview, New Analysis, Runs, Decisions, Watchlist, Evaluations, Data Sources, and Settings.
- Added market snapshot cards with synthetic sparklines and an interactive, labeled relative-performance chart with Price / Market Cap / Volume, time ranges, and asset toggles.
- Replaced the report's simple line schematic with synthetic candlesticks, volume bars, labeled price/time axes, entry zones, invalidation, current price, and targets.
- Added a structured composer for asset search, analysis type, horizon, question, capital, risk, and collapsed request metadata.
- Added fixture-only Analysis, Market Data, Run, Decision, and Evaluation service boundaries. No live provider, model, trading, or portfolio-PnL integration is claimed.
- Kept generated runs and decisions aligned with the repository's analysis, evidence, and decision record contracts; corrected leverage notes and evidence to use consistent funding/open-interest values.

## Verification

- `python3 scripts/validate_repo.py` — passed.
- `python3 -m unittest discover -s tests -v` — 56 tests passed.
- `find frontend -name '*.js' -exec node --check {} \;` — passed.
- `node --test frontend/tests/generator.test.mjs frontend/tests/charts.test.mjs frontend/tests/services.test.mjs` — 13 tests passed.
- Browser QA covered all eight primary routes at desktop width, all routes at 390px without horizontal page overflow, and the New Analysis → report → chart-range → evidence-filter → save-decision flow.
- Browser console had no errors after adding a local favicon.

## Review limitation

The independent GPT-6 Luna Inspector dispatch failed with a provider 502 error (`Cannot have more than 128 tools per request`). The result is marked self-verified rather than represented as an independent Inspector verdict. The GPT-6 Luna request used maximum reasoning and long-context settings; the already-running GPT-5.6 Luna Builder could not be switched mid-run.

## Remaining gaps

- All market cards, chart paths, evidence, and analysis results are fixture data. No live market provider, model runtime, backend API, or exchange execution is connected.
- Replace the fixture service implementations in `frontend/modules/services.js` with authenticated application APIs before presenting live analysis or performance results.
- A production deployment pipeline/build is not present because this dependency-free frontend has no bundler or package manifest.
