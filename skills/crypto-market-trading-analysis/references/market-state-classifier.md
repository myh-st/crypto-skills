# Derivatives-Aware Market State Classifier

Load this reference when synthesizing spot, OI, funding, liquidations, depth and institutional
flow into a compact market state. These labels are analytical states, not calibrated
probabilities.

## States

- SPOT_LED_ACCUMULATION: support/balance holds, spot absorption improves, OI is stable/moderate,
  funding is not extreme.
- FUTURES_LED_EXPANSION: price expands, OI rises rapidly, futures turnover dominates and spot
  confirmation lags. Momentum can continue, but fragility is elevated.
- SHORT_SQUEEZE: price rises through structure with covering/liquidations; spot follow-through
  determines durability.
- LONG_SQUEEZE: price falls rapidly, long liquidations expand, OI contracts and funding resets.
- LEVERAGE_RESET: forced deleveraging + material OI contraction + funding normalization; stronger
  confirmation requires spot absorption and reclaim.
- LIQUIDITY_SWEEP: price briefly trades beyond structure, acceptance is weak and price reclaims
  with supporting flow/depth evidence. Never infer intentional stop hunting.
- BREAKOUT_ACCEPTANCE: close/acceptance beyond structure + spot confirmation + continued
  acceptance/retest without immediate leverage extremity.
- FAILED_BREAKOUT: price trades beyond structure but returns inside prior value/range while spot
  follow-through weakens or reverses.
- STRUCTURAL_BREAKDOWN: support fails and remains accepted below, spot selling confirms and no
  prompt reclaim occurs.
- DISTRIBUTION: candidate only when major supply, repeated failed advances and weakening real
  demand align. Do not label from chart shape alone.
- VOLATILITY_COMPRESSION / VOLATILITY_EXPANSION: volatility state only; direction comes from
  price/flow.
- ROTATION_IN / ROTATION_OUT: require relative-strength and breadth evidence. Do not encode a
  universal BTC→ETH→alt sequence.
- MARKET_DATA_UNCERTAIN: material sources are stale, contradictory or unavailable.

## Evidence contract

~~~yaml
state:
  name: LEVERAGE_RESET
  confidence: low | medium | high
  observed: []
  inferred: []
  unavailable: []
  invalidation: []
  source_quality: low | medium | high
~~~

## Fallback research heuristics

Prefer rolling asset-specific percentiles. If history is insufficient, use these only as v1
research alerts:

- leverage-crowding candidate: OI/mcap >15% and futures/spot >4x;
- crowded-long warning: funding >=p90, OI +10%/24h, futures/spot >=5x;
- long-flush candidate: 4H <=-4% or <=-1.5 ATR, OI <=-8%, long liquidations >=p95;
- depth-vacuum candidate: 1% depth <=p10_30d or falls >30% in 1h;
- unlock-risk candidate: unlock value >=1% of market cap within 14 days.

Calibrate or replace these thresholds when sufficient history exists.

Good wording: "Evidence is consistent with a leverage reset, but reclaim is not yet confirmed."
Bad wording: "Whales flushed longs and are now accumulating."
