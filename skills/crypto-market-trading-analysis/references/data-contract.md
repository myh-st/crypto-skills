# Market Data Contract

Use this reference when wiring the skill to CoinMarketCap, exchange APIs, options venues, on-chain providers, or other runtime sources.

## Required context

```yaml
asset: SEI
quote_currency: USDT
venue: Binance
instrument: spot
horizon: swing
portfolio_currency: THB
risk_style: balanced
analysis_time: "2026-09-27T00:00:00Z"
```

Optional fields may be omitted; the analyst must state reasonable assumptions rather than blocking a useful answer.

## Normalization rules

- Distinguish spot, perpetual, and dated futures.
- Normalize OI to USD notional where possible.
- Label last, mark, and index price separately.
- Record each venue's funding interval before comparing rates.
- Separate USD-margined and coin-margined contracts.
- Keep exchange-specific margin or leverage-rule changes visible.
- Cross-check high-impact values when practical and explain discrepancies.

## Missing-data semantics

`unavailable`, `stale`, `partial`, and `not covered` are valid states. Never convert missing OI, liquidation, holder, or unlock data into zero, neutrality, or a directional conclusion.

## Source priority

1. Official exchange, regulated venue, protocol, contract, or governance data.
2. Inspectable high-quality aggregators for cross-venue context.
3. Charting and secondary datasets.
4. News and social sources only as context or catalyst validation.
