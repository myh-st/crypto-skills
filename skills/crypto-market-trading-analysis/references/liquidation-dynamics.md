# Liquidation Dynamics

Load this reference for liquidation maps, squeeze/flush analysis, leverage resets, crowded
positioning, or questions such as "how far would price need to move to liquidate longs?"

## Observable vs not observable

Observable or estimable: venue price/mark/index, OI and OI change, funding, basis, reported
liquidations, published ratios, taker flow/CVD, modeled heatmaps, transparent-venue public
positions, spot depth and spread.

Not globally observable: every trader's entry, leverage, cross-margin collateral, private OTC
exposure, hidden hedges and the exact global liquidation-price distribution.

Therefore never state that all positions at a leverage liquidate at one price or that the largest
heatmap cluster must be hit.

## Price x OI first-pass matrix

| Price | OI | Plausible first interpretation | Required next checks |
|---|---|---|---|
| Up | Up | new leverage entering | funding, spot CVD, basis, depth |
| Up | Down | short covering / position closure | short liquidations, spot follow-through |
| Down | Up | fresh downside positioning and/or hedging | funding, spot selling, CVD, depth |
| Down | Down | deleveraging / long liquidation / closures | long liquidations, funding reset, reclaim |

This is a hypothesis generator, not a trading signal.

## Recurring squeeze lifecycle

~~~text
short/neutral crowding
→ spot-supported breakout
→ short liquidation
→ forced buying / arb / momentum
→ long FOMO
→ funding and OI overheat
→ pullback
→ long liquidation
→ OI reset
→ rebuild
~~~

Do not retrofit this narrative after every oscillation. Require contemporaneous evidence.

## Long-flush candidate

~~~text
sharp price decline
+ material OI contraction
+ long liquidations at an extreme percentile
+ funding moves toward neutral/negative
+ spot selling begins to be absorbed
~~~

A preferred accumulation candidate requires flush + absorption + reclaim.

## Bad breakdown / do-not-DCA state

~~~text
price down
+ OI flat/up
+ spot CVD strongly negative
+ bid depth deteriorating
+ no reclaim
~~~

This is more consistent with fresh bearish positioning and/or real spot distribution than a
completed leverage reset.

## Short-squeeze candidate

Setup: support/reclaim holds, OI is elevated/rebuilding, funding is cheap relative to history,
aggressive selling stops making new lows, and upside forced-flow liquidity exists.

Confirmation: structural resistance closes/reclaims, spot volume expands, spot CVD is positive,
short liquidations expand, and OI behavior remains compatible with healthy demand.

## Heatmaps

Heatmaps answer where forced flow may become important if price approaches. They do not tell us
where price must go. Store actual reported liquidations separately from modeled heatmap exposure.

## Healthy reset checklist

Before calling a selloff a leverage-reset accumulation opportunity, prefer material OI
contraction, unusually large long liquidations, funding normalization, stabilizing spot CVD,
rebuilding bid depth, prompt structural reclaim and intact higher-timeframe structure.

Missing items lower confidence; they do not become neutral evidence.
