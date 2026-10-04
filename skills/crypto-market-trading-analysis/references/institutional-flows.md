# Institutional, ETF and ETP Flows

Load this reference when ETF/ETP/treasury holdings, staking products, authorized participants or
institutional demand are used in a crypto thesis.

## AUM is not flow

Never treat AUM change as equal to fresh underlying buying. AUM changes with token price,
creations/redemptions, staking rewards, fees, cash balances and operational adjustments.

Prefer daily shares outstanding/creations-redemptions, token entitlement or disclosed token
quantity, then price-adjusted net assets. Use raw AUM only when better data is unavailable.

## Spot-demand transmission

A physically backed product can create underlying demand when net creations require additional
token exposure. Effect size depends on creation/redemption mechanics, AP hedging, execution
schedule, spot depth, staking reserves and redemptions.

Do not assume product inflow moves price one-for-one.

## Staking and liquid float

Staked ETP holdings may be less immediately available for ordinary spot selling, but they are not
permanently removed. Treat staking as a potential liquid-float modifier, not a guaranteed
catalyst.

## Materiality

Compare verified net creation flow with normal spot turnover:

~~~text
creation_materiality =
verified_net_creation_usd / median_daily_spot_volume_usd
~~~

Prefer rolling asset-specific distributions over universal thresholds.

## Institutional + futures feedback

Constructive candidate:

~~~text
verified underlying creations
+ spot absorption
+ moderate OI build
+ non-extreme funding
+ breakout acceptance
→ short liquidations / arb hedging
→ additional momentum
~~~

Fragile candidate:

~~~text
price up
+ OI/funding overheat
+ weak spot confirmation
+ no verified creation impulse
→ leveraged-long reservoir grows
→ downside move can trigger long-liquidation cascade
~~~

## Source discipline

Highest weight: issuer holdings pages, issuer share/entitlement data, SEC/regulatory filings and
official disclosures. If a product exists but current AUM/holdings cannot be verified, record
NOT_VERIFIED.

Analyze institutional demand alongside circulating supply, unlocks,
unlocked-but-not-circulating supply, staking, treasury holdings and exchange deposits. Never mix
incompatible supply definitions silently.
