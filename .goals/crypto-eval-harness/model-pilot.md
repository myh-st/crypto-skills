# GPT-6 Luna paired skill/control pilot

## Purpose and limits

This is a direct paired sub-agent model invocation, requested after the offline harness implementation. It is **not** an integrated live-model run through `crypto_eval.PredictionRunner` and is **not** evidence that the skill improves trading or market decisions.

- Model request: `openrouter/OpenRouter/openai/gpt-6-luna`
- Reasoning effort: `max`
- Arms: two separate sub-agent calls; skill arm was instructed to read the skill and point-in-time/data-contract/technical-analysis references, while the control arm was instructed not to read or apply skill files.
- Shared input: the same three `synthetic_fixture` candidate snapshots from `crypto_eval/fixtures/candidates.json`.
- Both arms received only closed candles and metadata available at each `as_of`. Neither received outcome records/future candles; neither was instructed to browse or use live data.
- Each arm returned one concise decision per case. Responses were normalized JSON text, not content-hashed/immutably frozen prediction records produced by the harness. The configured provider/model were supplied in the sub-agent invocation; inference parameters beyond `reasoning_effort=max` and a stable API run ID were not available for audit.
- The general-purpose and task sub-agent tool routes rejected requests because their tool catalogs exceeded the gateway's 128-tool limit. The same requested model/config succeeded using the restricted explore sub-agent route.
- Cases contain only two hourly candles each, and all market values are synthetic. Any relationship between decisions and their synthetic outcomes is only harness behavior validation.

## Decisions returned

| Case | Skill arm | No-skill control |
|---|---|---|
| BTC, `btc-20260101-0200` | `WAIT_FOR_BREAKOUT_CONFIRMATION`, bullish, low; long close-confirmation at 101; invalidation 98; no target supplied. Rationale: rising closes/volume and higher high; require close above the observed high. | `NO_TRADE`, bullish, low; no entry, invalidation, or targets. Rationale: two rising candles are insufficient to establish a reliable setup. |
| ETH, `eth-20260102-0200` | `WAIT_FOR_PULLBACK`, bearish, low; short pullback zone 49.5–50.5; invalidation 52; target 48.5. Rationale: lower close/high/low and relative weakness vs. the supplied BTC candles; 48.5 described as observed support, not a forecast. | `NO_TRADE`, bearish, low; no entry, invalidation, or targets. Rationale: ETH fell while the supplied BTC benchmark rose slightly, but evidence was too sparse for a trade. |
| SOL, `sol-20260103-0200` | `WAIT_FOR_BREAKOUT_CONFIRMATION`, bullish, low; long close-confirmation at 25.5; invalidation 24; no target supplied. Rationale: rising closes/volume and outperformance vs. BTC; require close above the observed high. | `NO_TRADE`, bullish, low; no entry, invalidation, or targets. Rationale: SOL and BTC rose, but two synthetic candles were not enough to support an actionable setup. |

## Post-prediction fixture check

Only after both model outputs were returned, the separate synthetic outcome fixture was inspected to check trigger reachability:

- BTC close-confirmation level 101 was reached (the first future candle closed at 101; the evaluator's breakout rule treats a close at the level as confirmation).
- ETH short pullback zone 49.5–50.5 was **not** reached; the first future candle high was 49.3 and the subsequent highs were lower.
- SOL close-confirmation level 25.5 was reached on the second future candle, which closed at 25.8.

Thus **2/3 skill-arm triggers were reached** in this toy fixture; the ETH wait remained untriggered. BTC and SOL supplied no target, and ETH's target belongs to an untriggered setup. This does not produce a meaningful target-hit rate or skill-vs-control performance estimate. The all-`NO_TRADE` control has no triggered entries; opportunity cost/avoided downside must be scored separately from triggered setup outcomes.

## Interpretation

The skill-arm outputs differed from the control by providing conditional setups for all three cases, while both arms assigned low confidence and the same broad directional biases. The two reached triggers show that the outputs are consumable by the harness's trigger-aware concept; they do not show that the decisions are profitable, calibrated, robust, or better than no trade. No fee/slippage/fill model was applied, and this is not portfolio PnL simulation.

**Conclusion:** the requested GPT-6 Luna model invocation succeeded and demonstrates a real-model paired prompt smoke test. It does **not** support a claim that `crypto-market-trading-analysis` improves prediction or decision quality. A larger, preregistered forward-paper cohort with immutable model predictions and matched outcomes is still required.
