# Crypto Data Source Quality

Load this reference when selecting sources, reconciling conflicting values, or deciding whether a
metric is suitable for live analysis.

## Source tiers

Tier A: Gate, Binance, Bybit and OKX official APIs; transparent on-chain venue data where
applicable; ETF/ETP issuer pages; SEC/regulatory filings; official project supply schedules.

Tier B: CoinGlass, Coinalyze, Kaiko where licensed, Glassnode and CryptoQuant for appropriate
aggregated/derived metrics.

Tier C: TradingView, CoinGecko/CoinMarketCap, DeFiLlama and inspectable Dune dashboards.

Tier D: news/social/analyst commentary. Use to explain catalysts, not replace price/flow evidence.

## Metric matrix

| Metric | Preferred | Important limitation |
|---|---|---|
| price/candles | official exchange | venue-specific |
| spot volume | official exchange, then aggregate | venue mix differs |
| futures volume | official exchange, then aggregate | not underlying demand |
| OI | official venue + aggregate | direction is not encoded |
| funding | official venue + OI-weighted aggregate | intervals/methods differ |
| basis | official spot + futures | normalize expiry |
| actual liquidations | official/aggregated | incomplete global visibility |
| liquidation heatmap | modeled aggregator | potential zone, not ground truth |
| account L/S ratio | official venue | account count may not equal notional |
| top-trader position ratio | official venue | hedges invisible |
| taker flow/CVD | raw trades / reputable derived | venue coverage matters |
| depth/spread | official L2 across venues | highly time-sensitive |
| ETF/ETP | issuer + SEC | AUM is not flow |
| unlocks/supply | official project/on-chain | definitions can conflict |

## Freshness and conflicts

Use seconds/minutes for price/book/trades, current/completed intervals for OI/funding, closed 4H
candles unless explicitly labeling the live candle, latest issuer publication for ETP holdings
and a fresh authoritative check before unlock events.

If sources disagree, identify venue/timestamp/definition differences, prefer the primary source
for that metric, record the conflict and lower confidence if unresolved.

Every derivative contract has counterparties. A 60% long-account ratio does not mean 60% of
notional is net long. No source observes every trader's leverage, entry and collateral across all
venues, so global liquidation maps remain models.

Use NOT_VERIFIED rather than filling missing values from memory, screenshots, stale pages or
narrative sources.
