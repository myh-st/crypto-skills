# Valuation Multiples and Holding-Horizon Playbook

Load this reference when the user asks about 2x/3x/5x/10x outcomes, required
price/market cap, or a scenario-based holding horizon.

# 31B. Investment-Multiple and Holding-Horizon Questions

When the user asks questions such as:

- "If I invest 100,000 THB, can it become 5x?"
- "How long would I need to hold?"
- "What price must SEI reach for 5x?"

perform both trading analysis and investment feasibility analysis.

## Required calculations

Calculate:

```text
units_acquired = investment_amount / assumed_entry_price
portfolio_value_at_target = units_acquired * target_price
required_price_for_Nx_value = assumed_entry_price * N
profit = portfolio_value_at_target - investment_amount
ROI_pct = (portfolio_value_at_target / investment_amount - 1) * 100
```

If "5x profit" is ambiguous, distinguish concisely:

```text
5x portfolio value = final value is 5 times principal = +400% profit
5x profit = profit alone is 5 times principal = final value is 6 times principal = +500% profit
```

Do not ask a clarification question if both can be shown in one short line.

## Feasibility test for large upside targets

Do not judge a 3x, 5x, or 10x target from chart geometry alone. Estimate whether the required price is plausible by checking:

1. Required market cap at target price
2. Circulating supply and expected supply at the target horizon
3. FDV and scheduled unlock dilution
4. Previous ATH and prior cycle valuation
5. BTC / ETH / total-alt market regime
6. Relative strength and capital rotation
7. Spot liquidity and sustainable volume required
8. Protocol adoption, revenue/fees, TVL, users, or other relevant fundamentals
9. Comparable assets and sector valuation where appropriate
10. Macro liquidity and major regulatory/event risk

A mathematically possible target is not automatically economically plausible.

For a large target such as 3x/5x/10x, run the internal Bull/Bear challenge explicitly:

- Bull case: what market regime, adoption, liquidity, and valuation expansion could make the target reachable?
- Bear case: what supply dilution, unlocks, competition, leverage, liquidity, or macro constraints make the target unrealistic?
- Judge: compare the required future market cap / FDV with plausible sector and cycle conditions before stating the feasibility label.

## Holding-horizon estimation

Never state a precise date as guaranteed. Provide a scenario-based time window:

```text
Fast bull case:     approximate window + required conditions
Base case:          approximate window + required conditions
Slow / invalidated: what would delay or invalidate the target
```

For the concise default response, usually report only the base horizon and one sentence describing the faster/slower cases.

The horizon must be derived from market structure, volatility, historical cycle behavior, supply changes, and required valuation expansion—not from an arbitrary guess.

---
