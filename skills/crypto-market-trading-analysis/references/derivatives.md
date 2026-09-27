# Derivatives Playbook

Load this reference whenever perpetuals, dated futures, OI, funding, basis,
liquidations, or leverage materially affect the decision.

# 13. Futures / Perpetual Leverage Engine

This section is mandatory whenever perpetuals or futures materially influence the asset.

## 13.1 Open Interest (OI)

OI tells how much derivatives exposure remains open.

Analyze:

- absolute OI
- OI change
- OI relative to market cap
- OI relative to historical range
- OI by exchange
- stablecoin-margined vs coin-margined OI

Prefer percentiles or z-scores relative to the asset's own history rather than fixed universal thresholds.

## 13.2 Price × OI matrix

Use this as a starting framework, never as a deterministic rule.

| Price | OI | Initial interpretation | What must be checked next |
|---|---|---|---|
| Up | Up | new leveraged positioning entering | funding, spot demand, CVD, basis |
| Up | Down | short covering / position closure | spot follow-through, volume |
| Down | Up | new leveraged positioning entering downside | funding, spot selling, CVD |
| Down | Down | deleveraging / long liquidation / closing | liquidation volume, support reclaim |

## 13.3 Funding rate

Interpret funding as positioning pressure, not as a standalone signal.

General mechanics:

- positive funding -> longs pay shorts
- negative funding -> shorts pay longs

Important analysis:

- current funding
- OI-weighted funding
- funding percentile vs 30/90/365-day history
- persistence across several funding intervals
- divergence across exchanges
- funding versus price and OI

### Dangerous long crowding candidate

```text
Price near resistance
+ OI elevated/rising
+ funding strongly positive
+ futures volume dominates spot
+ spot CVD weak
+ liquidation clusters below price
```

This does not mean automatically short. It means long-side fragility is high.

### Dangerous short crowding candidate

```text
Price near support
+ OI elevated/rising
+ funding strongly negative
+ sell pressure stops making new lows
+ spot demand improves
+ liquidation clusters above price
```

This does not mean automatically long. It means short-squeeze risk is high.

## 13.4 Basis and futures term structure

For dated futures, analyze:

```text
basis = futures_price - spot_price
basis_pct = basis / spot_price
annualized_basis ≈ basis_pct * 365 / days_to_expiry
```

Classify:

- contango
- flat
- backwardation

Interpret changes rather than isolated values.

Examples:

- rising price + rising healthy basis + spot demand = constructive risk appetite
- rising price + extreme basis + weak spot = leveraged speculation risk
- backwardation during panic = stress / urgent hedging / short demand candidate

## 13.5 Spot vs futures dominance

Compare spot volume to derivatives volume.

A rally driven mostly by spot is generally structurally different from a rally driven mostly by leverage.

Useful states:

```text
Spot-led accumulation
Futures-led expansion
Short-covering rally
Long-liquidation flush
Leverage rebuild
Leverage reset
```

## 13.6 Liquidations

Analyze:

- long liquidations
- short liquidations
- liquidation intensity relative to normal
- liquidation concentration by price
- whether OI collapsed after liquidation
- whether spot absorbed the forced flow

A liquidation cascade can accelerate price far beyond ordinary technical levels.

After a major cascade, ask:

1. Did OI materially reset?
2. Did funding normalize?
3. Did price reclaim structure?
4. Did spot demand appear?

Only then consider whether the flush created a tradable reversal.

## 13.7 Liquidation heatmaps

Treat heatmaps as **potential liquidity magnets**, not guaranteed destinations.

Never say:

> Price must go to the largest liquidation cluster.

Instead say:

> A liquidity concentration exists near X; it becomes more relevant if structure and order flow begin moving toward it.

## 13.8 Long/short ratios

Use low weight.

Reasons:

- account ratios differ from position-size ratios
- large traders can hedge across venues
- retail positioning can be noisy

Only use as supporting evidence.

## 13.9 Taker buy/sell and CVD

Examples:

### Healthy bullish expansion

```text
Price ↑
Spot CVD ↑
OI moderately ↑
Funding neutral-to-mild positive
Volume ↑
```

### Fragile leveraged rally

```text
Price ↑
Futures OI sharply ↑
Funding ↑↑
Spot CVD flat/down
Spot volume weak
```

### Short-covering rally

```text
Price ↑
OI ↓
Short liquidations ↑
Funding remains negative/normalizing
```

### Long liquidation flush

```text
Price ↓
OI ↓↓
Long liquidations ↑↑
Funding falls toward neutral/negative
```

## 13.10 Leverage stress score

Create a qualitative leverage stress classification:

```yaml
leverage_stress:
  oi_percentile: 0-100
  funding_percentile: 0-100
  futures_vs_spot_dominance: low | medium | high
  liquidation_proximity: low | medium | high
  basis_stress: low | medium | high
  state: low | elevated | high | extreme
```

Do not convert this to a fake probability unless a calibrated statistical model exists.

---

# 14. Futures Interpretation Playbook

Use these combined states.

## 14.1 Trend continuation candidate

```text
HTF trend aligned
+ breakout accepted
+ spot volume expands
+ OI rises gradually
+ funding remains non-extreme
+ basis healthy
+ CVD confirms
```

## 14.2 Long squeeze candidate

```text
Price extended into resistance
+ OI high
+ funding very positive
+ futures dominate spot
+ spot buying weakens
+ downside liquidity dense
+ structure begins to fail
```

Confirmation:

- support loss
- negative delta/CVD
- long liquidation expansion
- OI decline during selloff

## 14.3 Short squeeze candidate

```text
Price holds/reclaims support
+ OI high
+ funding very negative
+ sell aggression no longer pushes price lower
+ upside liquidity dense
```

Confirmation:

- local resistance reclaim
- positive CVD
- short liquidation expansion
- OI begins falling while price rises

## 14.4 Leverage-reset bottom candidate

```text
sharp selloff
+ large long liquidation
+ OI collapse
+ funding reset
+ spot absorption
+ structure reclaim
```

Do not bottom-fish before the reclaim if the trend remains strongly bearish.

## 14.5 Deleveraging top / failed breakout candidate

```text
new high or marginal high
+ declining spot momentum
+ OI extreme
+ funding extreme
+ failure back below breakout
+ negative delta
```

Look for invalidation before acting.

---
