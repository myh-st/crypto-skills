# Crypto decision evaluation harness

## Validation is not accuracy

The repository validator checks that skill files, schemas, examples, and
deterministic implementation contracts are internally consistent. Passing it
does **not** show that the `crypto-market-trading-analysis` skill makes accurate
forecasts, improves decisions, or earns returns.

The harness measures frozen decision outputs against later, separate market
outcomes. It does not submit orders, manage accounts, simulate a funded
portfolio, or invoke a model by itself.

## Quick start

The harness uses only the Python standard library:

```bash
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
python3 -m crypto_eval demo --out-dir reports/crypto-eval-demo
```

The final command is a small, deterministic **synthetic** flow. It writes
`dataset.json`, `predictions.jsonl`, `scores.json`, `baselines.json`,
`report.json`, and `report.md` under a new output directory. Existing output
directories/files are never overwritten. Keep generated reports out of source
control; `reports/` is ignored.

The fixture report is labeled **DEMO / HARNESS VALIDATION — NOT MARKET
PERFORMANCE EVIDENCE**. Its decision outputs come from a simple deterministic
price rule, not the production skill or an LLM.

## Pipeline and commands

Inputs and outputs are versioned JSON/JSONL contracts under `schemas/`.
`eval/specs/crypto-market-v1.json` is the production target specification;
`crypto_eval/fixtures/` contains only compact synthetic fixtures.

1. **Build a dataset** from a normalized candidate bundle. Every case must
   explicitly name `as_of`, `data_cutoff`, `asset`, `instrument`, `venue`, and
   `horizon`, plus the closed point-in-time snapshot and its candle interval /
   evaluation horizon:

   ```bash
   python3 -m crypto_eval build-dataset \
     --source path/to/candidates.json \
     --spec eval/specs/crypto-market-v1.json \
     --out reports/my-run/dataset.json
   ```

   The spec fixes the universe (BTC, ETH, SOL, SUI, SEI, AVAX, PYTH), sampling
   policy, regime coverage, baselines, and expanding chronological
   train/validation/test fractions. `include_all_eligible` and
   `no_cherry_picking` must be true. The builder creates stable case IDs from
   the dataset/version and source case key, sorts by `as_of`, and assigns folds
   without shuffling. The source bundle's sampling manifest must reconcile
   scheduled, included, and excluded cases; each exclusion records its asset,
   scheduled timestamp, reason, and a rule ID declared by the versioned spec.
   For the production weekly spec, every asset/time slot in the declared
   cadence/window at the declared spot/swing horizon must appear exactly once
   as included or excluded. The audit is retained in the dataset and report.
   Production minimum coverage is intentionally stricter than the fixture spec.
   Snapshot, candle, observation, news, and availability objects use an
   explicit allowlist; unknown fields at any supported nesting level are
   rejected instead of being forwarded opaquely to a prediction runner.

2. **Freeze fixture predictions** (or implement the Python
   `PredictionRunner` protocol for an external model):

   ```bash
   python3 -m crypto_eval run-fixture \
     --dataset reports/my-run/dataset.json \
     --out reports/my-run/predictions.jsonl
   ```

   Each record preserves runner/model/configuration identifiers, prompt
   version, skill commit when applicable, dataset ID/version/hash, case ID,
   `frozen_at`, and a content hash. The default writer uses exclusive creation;
   optional `--append --case-id <id>` only appends selected new, non-duplicate
   run/case identities for a forward collection. Outcome fields are forbidden
   in predictions. Scoring writes a separate file and cannot mutate a frozen
   prediction.

3. **Score predictions** against an outcome bundle kept apart from the case
   and prediction records:

   ```bash
   python3 -m crypto_eval score \
     --dataset reports/my-run/dataset.json \
     --predictions reports/my-run/predictions.jsonl \
     --outcomes path/to/outcomes.json \
     --fold test \
     --out reports/my-run/scores.json
   ```

   The outcomes bundle must carry the exact dataset ID, version, and content
   hash. Scored files include an outcomes hash so paired reports can prove they
   used the same data. `--fold` accepts `train`, `validation`, `test`, or `all`
   (default `test`).
   The scorer rejects predictions frozen at or after the outcome's `known_at`.
   A result with incomplete candles or missing intervals keeps horizon returns
   unavailable; an event observed before a later gap may still be recorded.

4. **Compare deterministic baselines** on the same cases and fold:

   ```bash
   python3 -m crypto_eval compare-baselines \
     --dataset reports/my-run/dataset.json \
     --predictions reports/my-run/predictions.jsonl \
     --outcomes path/to/outcomes.json \
     --fold test \
     --out reports/my-run/baselines.json
   ```

   Baselines are fixed, not fit or tuned: Buy & Hold, the provided BTC
   benchmark, EMA20/EMA50 trend, simple RSI14 (long below 30 / short above 70),
   previous-candle direction, and a SHA-256-seeded random side (default seed
   1729). A baseline without enough history or a benchmark series reports
   unavailable/no signal, never a fabricated zero return.

5. **Render reports**:

   ```bash
   python3 -m crypto_eval report \
     --scores reports/my-run/scores.json \
     --baselines reports/my-run/baselines.json \
     --out-json reports/my-run/report.json \
     --out-md reports/my-run/report.md
   ```

   The report includes fold/sample counts, data origin, runner invocation
   status, unavailable-data notes, baseline results, and limitations. It
   distinguishes fixture mechanics from any archived real evaluation. Score
   and baseline fold scopes and case sets must match; report generation rejects
   mismatches. Counts distinguish the full dataset from cases, predictions,
   scored outcomes, and complete returns in the selected fold.

6. **Project the forward paper-evaluation lifecycle**:

   ```bash
   python3 -m crypto_eval status \
     --dataset reports/my-run/dataset.json \
     --predictions reports/my-run/predictions.jsonl \
     --outcomes path/to/outcomes.json \
     --scores reports/my-run/scores.json
   ```

   Cases progress through `pending`, `prediction_frozen`,
   `waiting_for_outcome`, `ready_to_score`, and `scored`. The projection is
   derived from immutable inputs; it does not rewrite them.

## Point-in-time and data boundaries

- Case snapshots are input-only. All timestamps must include a timezone, and
  all observation/publication/availability times and candle closes must be no
  later than `data_cutoff`; `data_cutoff` cannot exceed `as_of`.
- Snapshot objects accept only the documented candle, benchmark,
  timestamped-observation, archived-news, and data-availability fields.
  Unknown snapshot or nested fields—including unlabeled, timestamped, or
  vendor-specific fields—are rejected before a case can reach a runner.
- Candle prices must be finite, positive, internally consistent OHLC values.
  Funding, OI, derivative, options, macro, and benchmark observation arrays
  require observation timestamps.
- Missing intervals in the pre-cutoff asset or benchmark candles mark that lane
  partial; history-dependent EMA/RSI baselines are unavailable rather than
  calculated as if the series were contiguous.
- Historical news additionally requires `published_at`, `available_at`, and an
  archive ID. A current search result with only a historical publication date
  is not admissible.
- Outcome candles must open at or after the cutoff and are stored in a
  separate outcome bundle with a later `known_at`. Labels, reflections, and
  realized returns cannot be inserted into frozen cases.
- Data providers implement a read-only interface. No credentials, live
  exchange adapter, paid data dependency, or fabricated derivatives/news/
  on-chain data is included. Unsupported lanes are marked unavailable.
- Historical model outputs generated after their outcome is known cannot be
  presented as point-in-time predictions. Prefer predictions archived before
  the outcome window closes, or collect new cases prospectively via forward
  paper evaluation.

## Decision states, triggers, and conservative path rules

Predictions use the canonical states in
`schemas/decision-state.schema.json`. Pullback waits require an explicit
direction, zone, and confirmation; breakout waits require a level and close
confirmation. Immediate entries require an explicit reference. `NO_TRADE`,
`AVOID_CHASING`, and other non-entry states do not acquire an invented fill.

A wait that does not trigger within a complete evaluation window is recorded as
`not_triggered`. It contributes to the trigger-rate denominator but has no
entry return, MFE, MAE, target rate, or invalidation rate. If a missing interval
prevents determining whether the trigger happened, it is `unknown` and excluded
from that rate's denominator.

Path scoring rules:

- A stop gap executes at the opening price when that is worse than the stop.
- A target gap records the opening price when it is more favorable than the
  target.
- If target and invalidation are both touched in one candle, invalidation is
  assumed first and the candle is marked ambiguous.
- When one candle reaches multiple targets, only the nearest target in the
  declared ordered list is treated as first.
- A breakout/pullback trigger candle's target-only excursion is not credited;
  candle-close OHLC cannot prove whether the target traded after entry. A stop
  touched in that candle is treated conservatively.
- For a triggered wait, all bars before the trigger are excluded from MFE/MAE
  and path-event scoring. The trigger candle contributes only its close to
  MFE/MAE; its ambiguous earlier intrabar extremes are not attributed to the
  position.
- Trigger/target times are candle-resolution observations, not exact intrabar
  timestamps.

## Metrics and denominators

Every rate includes `n`, numerator, and a Wilson 95% interval. Means include
sample counts and an approximate normal 95% interval only for `n >= 2`; small
samples are descriptive, not significance tests.

| Metric | Definition |
|---|---|
| Directional accuracy | Correct sign of forward asset return for predictions with a bullish/bearish bias; neutral and exactly flat outcomes are excluded. This is directional diagnosis, not a trade return. |
| Forward return | Asset close-to-close return from the last closed snapshot candle to the declared horizon close. Incomplete horizons are omitted, not zero-filled. |
| BTC benchmark / alpha | BTC return from the explicit point-in-time BTC series; alpha is raw asset return minus BTC return. Both are unavailable when either complete series is missing. |
| Trigger rate | Triggered waits / waits with a complete, observable trigger window. |
| Target / invalidation rates | First-target or invalidation events / triggered (or immediate) entries with the relevant level and an observable path. Target-before-invalidation requires both levels. No-event complete paths remain in the denominator. |
| MFE / MAE | Best favorable and worst adverse side-adjusted excursion from the entry reference, on complete paths only. For a trigger candle, the close is used instead of ambiguous earlier intrabar extremes. |
| Time to trigger / target | Elapsed hours between the analysis/trigger bar close and the detected event bar close; intrabar precision is not inferred. |
| Max drawdown proxy | Peak-to-trough decline in a cumulative, equal-weighted sequence of triggered/immediate decision returns. This is not an equity curve, portfolio simulation, or PnL backtest. |
| Confidence breakdown | Observed directional hit rates by the qualitative `low` / `moderate` / `high` label. These labels are not probabilities; no probability calibration claim is made. |

Breakdowns include canonical decision state, asset, horizon, market regime, BTC
regime, leverage stress, confidence, and liquidity tier. Missing grouping data
appears as unavailable rather than being coerced to neutral or zero.

## Skill/control experiments and extension points

`compare-runs` accepts two frozen JSONL runs marked `variant: "skill"` and
`variant: "control"`. It requires identical provider, model ID, inference
configuration hash, dataset, and case IDs, then reports paired return and
directional-accuracy differences with sample counts/intervals. Prompt version
and skill commit are recorded separately so the intended treatment can differ.
The comparison is descriptive and refuses mismatched model setups; an honest
claim still requires an experiment protocol decided in advance, enough
chronological out-of-sample cases, and review of failures and confidence
intervals.

`PredictionRunner` is the model extension point; its metadata must identify the
provider/model, inference configuration hash, prompt version, skill commit,
variant, run ID, and whether invocation actually occurred. An actually invoked
runner must return its timezone-aware `frozen_at` in the prediction response,
which the harness removes from the decision body and preserves as record
metadata. A plugin must freeze its output before the outcome is available. The
harness intentionally does not load arbitrary Python plugins or store model
credentials. Read-only data providers should yield the normalized candidate
contract and mark unsupported features unavailable.

Decision-quality scores are not portfolio PnL. A portfolio backtest would also
need a cash ledger, capital allocation, position sizing, fills, exits, fees,
slippage, liquidity constraints, and applicable funding/borrow costs. No such
simulation or trading/order execution is implemented here.
