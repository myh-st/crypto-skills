# Crypto Market Microstructure

Load this reference when the question involves spot/perpetual interaction, market makers,
arbitrage, liquidity, basis transmission, squeeze mechanics, or claims that a large actor is
"pushing" price.

Research basis: the 2026-10-04 derivatives-driven squeeze dossier. Snapshot values in the
case-study reference are historical observations, not live constants.

## Core principle

Derivatives are an accelerator, not independent proof of sustainable demand.

Analyze the market as interacting flows:

~~~text
spot demand/supply
+ futures positioning
+ arbitrage and hedging
+ forced liquidations
+ executable order-book depth
+ institutional creations/redemptions
+ token supply changes
= marginal clearing pressure
~~~

Market-cap change is not cash inflow. Price is set at the margin by the liquidity available
between the current price and the next clearing level.

## Futures-to-spot transmission

A futures move can transmit to spot through cash-and-carry arbitrage, market-maker inventory
hedging, basis convergence, forced liquidations, momentum, cross-venue arbitrage and
collateral/margin de-risking.

Never assume one dollar of futures notional creates one dollar of spot demand. Any
futures-to-spot transmission coefficient is asset-, venue- and regime-dependent and must be
empirically calibrated.

## Liquidity is a cost function

For a large lawful allocator, ask how much executable depth exists before the desired price
level. Track multi-venue cumulative bid/ask depth near 0.5%, 1%, 2% and 5%, with rolling median
and stress percentiles. Cascades are most dangerous when depth falls toward its own low
percentile while forced flow accelerates.

## Spot-led vs leverage-led moves

Healthier bullish expansion:

~~~text
price up
+ spot volume/CVD up
+ OI flat to moderately up
+ funding neutral to mildly positive
+ depth remains healthy
~~~

Fragile leveraged expansion:

~~~text
price up
+ OI rises much faster than price
+ funding becomes expensive
+ futures turnover dominates
+ spot participation fails to expand
~~~

The second state can continue higher, but it accumulates future liquidation fuel.

## Rational large-participant lens

Use this only as an adversarial interpretation framework, not as an assertion of manipulation.

Ask where executable liquidity is concentrated, which side appears expensive/crowded, whether
spot accepts the move, whether leverage is being added or removed, where inventory can be
acquired/reduced with lower impact, and what evidence invalidates the interpretation.

Preferred wording: "The liquidity configuration makes an upside squeeze plausible if spot
confirms."

Avoid: "Market makers will pump price to liquidate shorts."

Private OTC books, internal market-maker inventory and undisclosed fund hedges are not observable.

## Flash move vs structural move

A transient liquidity event is more plausible when price moves sharply, OI contracts,
liquidations spike, funding normalizes, spot selling stops worsening, depth rebuilds and price
reclaims structure quickly.

A structural breakdown is more plausible when price falls, OI is flat/rising, spot CVD remains
strongly negative, bid depth deteriorates and no timely reclaim occurs.

Never call a wick accumulation without absorption/reclaim evidence.

## Analytical prohibitions

Never infer without evidence that a specific actor intentionally hunted stops, a heatmap cluster
must be visited, OI growth means more longs, an account ratio equals notional positioning, ETF
AUM change equals fresh buying, a global liquidation price is known, or a futures-led rally is
sustainable without spot confirmation.
