# Decision Memory

Store structured decisions and outcomes when a journal or persistent memory is available. The objective is to learn when a decision state works, not to turn old calls into authority.

## Minimum record

```yaml
analysis_time: "2026-09-27T00:00:00Z"
asset: SEI
horizon: swing
market_regime: alt_bull
decision_state: WAIT_FOR_PULLBACK
entry_zone: [0.062, 0.067]
invalidation: 0.048
targets: [0.084, 0.096, 0.109]
decisive_evidence: []
leverage_state: elevated
btc_regime: bullish
trigger:
  triggered: false
  triggered_at: null
  entry_reference: null
  time_to_trigger: null
outcome:
  target_hit_first: null
  invalidation_hit_first: null
  post_trigger_return: null
  post_trigger_mfe: null
  post_trigger_mae: null
outcome_window: 90d
raw_return: null
benchmark_return: null
alpha: null
max_favorable_excursion: null
max_adverse_excursion: null
thesis_result: pending
reflection: null
outcome_known_at: null
```

## Evaluation discipline

`WAIT_FOR_PULLBACK` is a conditional timing decision, not a market-order BUY.
Evaluate it in two stages:

1. **Trigger quality:** did price enter the declared zone and satisfy the
   confirmation condition? Record `trigger.triggered`, the timestamp, and the
   entry reference. A rally that never reaches the zone is not a failed WAIT.
2. **Post-trigger outcome:** once triggered, compare target and invalidation
   paths from the entry reference. Record which event happened first and the
   post-trigger return/MFE/MAE separately from the raw return since analysis.

This keeps direction quality separate from timing quality and prevents a
correctly avoided chase from being scored as a missed trade.

- Compare altcoin decisions with BTC, or with an appropriate sector benchmark when available.
- Keep direction error separate from timing error.
- Review leverage, spot confirmation, invalidation quality, valuation, and unlock assumptions.
- Segment results by decision state, horizon, market regime, BTC regime, leverage stress, confidence, and liquidity tier.
- Use walk-forward or out-of-sample evaluation; do not tune rules to one bull market.
- Do not call a text-decision review a portfolio-PnL backtest without fills, fees, slippage, funding, sizing, and cash accounting.

The machine-readable contract lives at `schemas/decision-record.schema.json` in the repository root.
