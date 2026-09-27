---
name: crypto-market-trading-analysis
description: >-
  Use this skill when analyzing cryptocurrency markets for spot investing, swing trading,
  position trading, perpetual futures, dated futures, or options. The skill prioritizes
  price action, multi-timeframe market structure, volume, order flow, derivatives positioning,
  leverage, funding, open interest, liquidations, volatility, and cross-market confirmation.
  It uses news and narratives as secondary context rather than the primary trading signal.
  The goal is to produce an evidence-based market view with explicit entry conditions,
  invalidation, risk, scenarios, and decision states such as ACCUMULATE, ENTER, WAIT,
  REDUCE, HEDGE, or AVOID CHASING. Internally, use an adversarial investment-committee
  workflow: neutral evidence collection, bull/bear challenge, execution planning, risk
  stress-testing, portfolio-aware synthesis, and outcome learning. Human-facing output
  remains concise by default.

---

# Crypto Market & Trading Analysis Skill

IMPORTANT: System and user instructions always take precedence over this skill.

## 1. Mission

Analyze crypto markets as a connected system rather than as a collection of isolated indicators.

The analysis must combine, when available:

1. Price action and market structure
2. Volume and volume-at-price
3. Momentum and volatility
4. Spot order flow
5. Perpetual futures and dated futures
6. Open interest, funding, basis, liquidations, and leverage
7. Options positioning and implied volatility
8. On-chain flows and holder behavior
9. Relative strength, breadth, and capital rotation
10. Macro liquidity and scheduled catalysts
11. Token-specific supply, unlock, and fundamental risks
12. News only as confirmation, explanation, or catalyst context

The central principle is:

> Read what price, volume, positioning, and leverage are already showing before relying on the narrative that appears afterward.

Technical analysis does **not** know future news. Do not claim that a chart can foresee an unknown event. Instead, identify when positioning, liquidity, volatility, or market structure suggests that participants are already repricing risk.

---

# 2. Core Operating Principles

## 2.1 Process over prediction

Never attempt to predict a single exact future price as if it were certain.

Always produce:

- current market regime
- directional bias
- evidence supporting that bias
- evidence against that bias
- important liquidity levels
- invalidation level
- bullish / base / bearish scenarios
- trigger required before entering
- risk/reward and position-risk considerations

## 2.2 Never trade from one indicator

No single signal is sufficient.

Do not make a directional decision solely from:

- RSI overbought/oversold
- MACD cross
- moving-average cross
- Fibonacci level
- funding rate
- liquidation heatmap
- long/short ratio
- one candlestick pattern
- one influencer or analyst opinion
- one news headline

Require confluence between independent evidence categories.

## 2.3 Price is primary; derivatives explain fragility

Use this default hierarchy for trading decisions:

1. Market structure / price acceptance
2. Spot volume and order flow
3. Derivatives positioning and leverage
4. Volatility regime
5. Relative strength / cross-market confirmation
6. On-chain or flow data
7. Macro and event calendar
8. News and narrative

For long-term investment analysis, increase the weights of fundamentals, valuation, token supply, adoption, and on-chain behavior.

## 2.4 Distinguish leading, coincident, and lagging evidence

Examples:

- Price structure: coincident / early confirmation
- Order flow: coincident and sometimes early
- Funding/OI crowding: positioning risk signal
- Options IV/skew: forward-looking expectations, not direction certainty
- Liquidation heatmaps: potential liquidity zones, not guaranteed targets
- Moving averages: lagging trend filters
- News: often explanatory and sometimes genuinely new information

Do not call a lagging indicator a leading indicator.

---

# 3. Required Context Before Analysis

Determine or infer these fields when possible:

```yaml
asset: BTC | ETH | SOL | SUI | SEI | AVAX | ...
quote_currency: USDT | USD | USDC | THB | BTC
venue: Binance | Coinbase | OKX | Bybit | Bitkub | CME | aggregated
instrument: spot | perpetual | futures | options
horizon: scalp | intraday | swing | position | long_term
portfolio_currency: USD | THB | other
risk_style: conservative | balanced | aggressive
current_position:
  status: none | long | short | spot_holder
  entry: optional
  size: optional
  leverage: optional
```

If some fields are unavailable, continue with reasonable assumptions and clearly state them.

Do not block useful analysis only because one optional field is missing.

---

# 4. Data Freshness and Source Discipline

## 4.1 Always timestamp market data

For current-market analysis, state:

- observation timestamp
- timezone
- exchange or index
- instrument type

Example:

```text
Data snapshot: 2026-09-27 06:20 Asia/Bangkok
Primary price: BTCUSDT Binance Spot
Derivatives: aggregated perpetual futures across major exchanges
```

## 4.2 Preferred source hierarchy

### Tier 1 — Primary / regulated / exchange data

Prefer raw or official data where possible:

- Binance / Coinbase / OKX / Bybit official market APIs
- Deribit for crypto options
- CME Group for regulated futures/options and volatility data
- CFTC Commitments of Traders for CME positioning
- official project/token documentation for supply and unlock mechanics

### Tier 2 — High-quality aggregated analytics

Use these to combine exchanges or derive metrics:

- CoinGlass — open interest, funding, liquidation, futures volume, long/short metrics
- Glassnode — derivatives, leverage, on-chain and holder metrics
- CryptoQuant — exchange flows, reserves, derivatives and on-chain metrics

### Tier 3 — Charting / visualization / secondary datasets

- TradingView
- CoinGecko / CoinMarketCap for broad market metadata
- DeFiLlama for DeFi TVL, stablecoins, protocol revenue where relevant
- Dune dashboards only when methodology is inspectable

### Tier 4 — News / social / narrative

Use only as context and catalyst validation:

- official company/project announcements
- regulator announcements
- established financial news providers
- verified exchange announcements

Social media and influencer posts have low evidentiary weight unless they point to verifiable primary data.

## 4.3 Cross-check important values

For high-impact decisions, cross-check at least two sources when practical for:

- current price
- open interest
- funding rate
- liquidation totals
- circulating supply / unlocks
- exchange flows

Explain material discrepancies instead of silently choosing one value.

## 4.4 Normalize derivatives data

Before comparing exchanges:

- distinguish USD-margined vs coin-margined contracts
- distinguish perpetual vs delivery futures
- normalize OI to USD notional where possible
- account for different funding intervals
- distinguish last price, mark price, and index price
- identify exchange-specific leverage or margin-rule changes

---

# 5. Multi-Timeframe Analysis Framework

Analyze top-down.

## 5.1 Default timeframe map

| Horizon | Context | Setup | Execution |
|---|---|---|---|
| Scalping | 1H / 15m | 5m | 1m |
| Intraday | 4H / 1H | 15m | 5m |
| Swing | 1W / 1D | 4H | 1H |
| Position | 1M / 1W | 1D | 4H |
| Long-term | 1M / 1W | 1D | DCA zones |

Do not use a 5-minute bullish setup to override a weekly downtrend without explicitly labeling it as a counter-trend trade.

## 5.2 Determine the regime first

Classify each relevant timeframe as one of:

- strong uptrend
- weak uptrend
- range / balance
- distribution-like range
- accumulation-like range
- weak downtrend
- strong downtrend
- volatility expansion
- volatility compression
- transition / unclear

Do not force a directional trade when the regime is unclear.

---

# 6. Price Action and Market Structure

## 6.1 Swing structure

Identify:

- Higher High (HH)
- Higher Low (HL)
- Lower High (LH)
- Lower Low (LL)
- range high / low
- prior day/week/month high and low
- all-time high or major cycle levels when relevant

Trend interpretation:

```text
HH + HL = bullish structure
LH + LL = bearish structure
mixed swings = range or transition
```

## 6.2 Break of structure

Differentiate:

- confirmed breakout
- wick-only liquidity sweep
- false breakout
- reclaim
- failed reclaim

A breakout is stronger when supported by:

- close beyond level
- expansion in volume
- spot participation
- successful retest
- rising relative strength
- derivatives positioning that is not excessively crowded

## 6.3 BOS / CHoCH / liquidity concepts

BOS, CHoCH, liquidity sweeps, fair value gaps, order blocks, and similar market-structure concepts may be used as heuristics.

Rules:

- never treat them as guaranteed institutional footprints
- require confirmation from volume, structure, or order flow
- avoid retrofitting labels after price has already moved

## 6.4 Support and resistance hierarchy

Prioritize:

1. Major weekly/monthly structure
2. Prior swing highs/lows
3. High-volume nodes and POC
4. Prior value-area boundaries
5. Breakout/retest levels
6. VWAP / anchored VWAP
7. Dynamic moving averages
8. Fibonacci as secondary confluence only

Zones are preferred over exact single-price lines.

---

# 7. Volume and Volume-at-Price

## 7.1 Raw volume

Check whether price movement is supported by volume.

Examples:

- breakout + expanding volume = stronger acceptance
- breakout + declining volume = higher failure risk
- selloff + capitulation volume + OI collapse = possible leverage reset
- price advance + weak spot volume + strong futures volume = fragile rally risk

## 7.2 Volume Profile

When available, identify:

- POC — Point of Control
- VAH — Value Area High
- VAL — Value Area Low
- HVN — High Volume Node
- LVN — Low Volume Node

Interpretation:

- HVN = prior acceptance / balance
- LVN = low acceptance; price can traverse quickly
- POC = important fair-value reference
- movement outside value followed by acceptance may indicate new price discovery
- rejection back into value may indicate failed breakout

## 7.3 VWAP and Anchored VWAP

Useful anchors include:

- major cycle low/high
- breakout candle
- ETF-launch / listing / major event date
- monthly or yearly open

Interpret whether price is accepted above or below important VWAPs rather than simply touching them.

---

# 8. Order Flow and Execution Evidence

When reliable data exists, analyze:

- CVD (Cumulative Volume Delta)
- taker buy vs taker sell volume
- bid/ask imbalance
- footprint delta
- absorption
- aggressive buying/selling without price follow-through

Important divergences:

### Bullish absorption candidate

```text
Aggressive selling increases
but price stops making new lows
and spot bids absorb supply
```

### Bearish absorption candidate

```text
Aggressive buying increases
but price cannot make new highs
and offers absorb demand
```

Order-flow data is venue-specific. Do not assume one exchange represents the entire crypto market.

---

# 9. Trend Indicators

Use moving averages primarily as filters.

Default set:

- EMA 20 — short trend
- EMA 50 — medium trend
- EMA 200 — long trend
- SMA 200 — broad long-term reference when useful

Analyze:

- price relative to MA
- MA slope
- separation/compression
- reclaim/loss
- dynamic support/resistance behavior

Do not buy merely because of a golden cross or sell merely because of a death cross.

---

# 10. Momentum Indicators

## 10.1 RSI

Use RSI for:

- momentum regime
- divergence
- failure swings
- trend persistence

Important:

- overbought does not automatically mean short
- oversold does not automatically mean buy
- strong trends can remain extreme for long periods

## 10.2 MACD

Use for:

- momentum acceleration/deceleration
- trend confirmation
- divergence
- zero-line behavior

MACD crosses alone are low-weight evidence.

## 10.3 Divergence ranking

Highest-quality divergence occurs when it aligns with:

- major HTF level
- exhaustion volume
- volatility extreme
- liquidation event
- OI reset
- structural reclaim/rejection

Divergence without structural confirmation is insufficient.

---

# 11. Volatility Analysis

## 11.1 ATR

ATR measures volatility, not direction.

Use ATR for:

- stop-distance sanity check
- identifying abnormal expansion
- comparing current range to normal range
- avoiding entries after overextended moves

## 11.2 Bollinger Bands / realized volatility

Look for:

- volatility compression
- expansion after compression
- repeated band riding in trends
- mean reversion only when the market is actually ranging

## 11.3 Extension risk

Flag chasing risk when price is unusually extended from:

- EMA20/50
- VWAP
- anchored VWAP
- recent base
- ATR-normalized trend

Do not assign arbitrary universal thresholds. Compare against the asset's own recent distribution.

---

# 12. Pattern Analysis

Patterns may include:

- ascending / descending triangle
- symmetrical triangle
- bull / bear flag
- falling / rising wedge
- double top / bottom
- head and shoulders / inverse H&S
- rounded base
- cup and handle
- volatility contraction
- range breakout

For every pattern, require:

```yaml
pattern:
  structure_quality: low | medium | high
  volume_confirmation: yes | no | mixed
  breakout_level: price_zone
  invalidation: price_zone
  measured_target: optional
  higher_timeframe_alignment: yes | no
  derivatives_confirmation: yes | no | mixed
```

Pattern targets are hypotheses, not guarantees.

---

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

# 15. Options and Forward-Looking Volatility

For BTC/ETH and liquid option markets, use options to understand expected volatility and positioning.

Analyze when available:

- ATM implied volatility (IV)
- realized volatility (RV)
- IV vs RV spread
- 25-delta put/call skew
- term structure
- put/call OI
- strike concentration
- expiry calendar
- dealer/gamma estimates only from transparent sources

## 15.1 Interpretation examples

### Rising IV before event

Market expects larger future movement, not necessarily a specific direction.

### Put skew steepening

Downside protection demand is rising.

### Call skew / call concentration rising

Upside demand may be increasing, but could also represent overwriting or structured positions.

Do not infer direction from options OI alone.

## 15.2 Expiry effects

Large expiries can alter hedging flows and intraday volatility.

Treat "max pain" as low-weight context, never as a primary target.

---

# 16. On-Chain Analysis

Use on-chain data primarily for BTC, ETH, and chains where metrics are meaningful and well-defined.

## 16.1 Exchange flows

Analyze:

- exchange inflow
- exchange outflow
- netflow
- exchange reserve

Potential interpretations:

- rising deposits may increase available sell-side inventory
- persistent withdrawals may indicate reduced exchange supply

But never assume every exchange transfer is a sale or purchase.

## 16.2 Holder profitability and valuation

When available:

- MVRV
- SOPR
- realized price
- short-term-holder realized price
- long-term-holder behavior
- realized profit/loss

Use historical percentiles rather than universal magical thresholds.

## 16.3 Stablecoin liquidity

Analyze:

- stablecoin supply growth/contraction
- stablecoin exchange balances/inflows
- stablecoin dominance shifts

This can help assess available crypto-native liquidity.

---

# 17. Relative Strength and Capital Rotation

Always compare an altcoin against relevant benchmarks.

Examples:

```text
ALT/USDT
ALT/BTC
ALT/ETH
BTC dominance
TOTAL / TOTAL2 / TOTAL3 where reliable
sector basket
```

An altcoin rising in USD but falling sharply against BTC may not represent true relative strength.

## Rotation clues

Look for sequences such as:

```text
BTC strength -> ETH confirmation -> large-cap alts -> mid/small caps
```

Do not assume the sequence will always occur.

---

# 18. Tokenomics and Fundamental Overlay

Mandatory for multi-week or long-term investment decisions.

Check:

- circulating supply
- total / max supply
- FDV
- scheduled unlocks
- emissions
- staking inflation
- insider / foundation allocation
- treasury runway
- protocol revenue / fees when meaningful
- active users / developers where measurable
- concentration of holders
- chain/security model
- bridge/custody risk
- regulatory/listing risk

A technically attractive chart can still be a poor long-term investment if supply expansion is severe.

---

# 19. Macro and Cross-Asset Context

Use macro to understand regime, not to replace the chart.

Relevant variables can include:

- USD liquidity
- DXY
- US Treasury yields / real yields
- equity risk appetite
- VIX or broader volatility
- central-bank policy
- inflation/employment releases
- global liquidity conditions
- major crypto ETF flows where relevant

For BTC in particular, institutional derivatives and ETF-related flows may matter more than for small-cap tokens.

---

# 20. Event and Catalyst Calendar

Before recommending a new position, check for near-term event risk:

- FOMC / central-bank decisions
- CPI / PCE / payrolls
- major options expiry
- token unlock
- network upgrade
- governance vote
- exchange listing/delisting
- ETF/regulatory deadlines
- court decisions
- protocol migration
- earnings of crypto-sensitive public companies if relevant

Do not predict event outcomes solely from the chart.

Instead distinguish:

```text
market positioning before event
actual event result
price reaction after event
```

The reaction can be more informative than the headline itself.

---

# 21. AI Evidence-Synthesis Engine

The AI must not merely list indicators. It must build competing hypotheses.

## 21.1 Build at least 3 scenarios

Example:

```yaml
scenario_A:
  name: trend_continuation
  evidence_for: []
  evidence_against: []
  confirmation: []
  invalidation: []

scenario_B:
  name: leveraged_squeeze
  evidence_for: []
  evidence_against: []
  confirmation: []
  invalidation: []

scenario_C:
  name: range_mean_reversion
  evidence_for: []
  evidence_against: []
  confirmation: []
  invalidation: []
```

## 21.2 Contradiction handling

When signals conflict, state the conflict explicitly.

Example:

```text
Daily structure remains bullish, but perpetual OI and funding are stretched while spot CVD is weakening.
Interpretation: trend is still intact, but chasing longs has poor asymmetry until leverage cools or spot confirms.
```

This is preferred to forcing all indicators into one narrative.

## 21.3 Evidence weighting

Default weights for swing trading:

| Evidence group | Weight |
|---|---:|
| HTF price structure | 25% |
| Volume / spot order flow | 20% |
| Futures positioning / leverage | 20% |
| Key levels / volume profile | 15% |
| Momentum / volatility | 10% |
| On-chain / macro / catalysts | 10% |

For intraday trading, increase order flow and derivatives weights.

For long-term investment, increase fundamentals/on-chain and decrease short-term order-flow weights.

Do not present the weighted score as a statistically calibrated win probability.

---


# 21A. Internal Investment Committee — TradingAgents-Inspired

Use a staged, adversarial reasoning workflow before the final answer. This is a **logical decomposition**, not a requirement to run separate LLMs. A single capable model may execute the roles as independent internal passes.

Do **not** expose the full committee transcript to the user unless explicitly requested. The default human answer remains concise and decision-first.

## 21A.1 Stage 1 — Neutral evidence collectors

Build the factual evidence first, before adopting a bullish or bearish conclusion. Keep observations separate from interpretation.

Use these specialist lenses when relevant:

| Internal role | Primary responsibility | Must not do |
|---|---|---|
| Market Structure Analyst | M/W/D/4H/1H structure, support/resistance, volume profile, VWAP, ATR | decide from RSI/MACD alone |
| Spot & Order-Flow Analyst | spot volume, CVD, taker flow, absorption, liquidity | infer sustainable demand from futures volume alone |
| Derivatives Analyst | OI, funding, basis, liquidation, leverage, futures/spot dominance | treat positive funding as automatically bearish |
| Volatility / Options Analyst | realized vol, IV, skew, term structure, expiry risk | treat IV as a directional forecast |
| On-Chain / Flow Analyst | exchange flows, holder behavior, stablecoin liquidity | overrule clean price structure with one metric |
| Token / Fundamental Analyst | supply, unlocks, FDV, adoption, revenue/fees/TVL where applicable | use marketing claims as facts |
| Macro / Catalyst Analyst | liquidity, rates, ETF/regulatory/event risk, scheduled catalysts | let headlines replace market evidence |

Every specialist pass should produce only:

```yaml
observations: []
implications: []
contradictions: []
data_quality: high | medium | low
important_levels: []
```

If a data layer is unavailable, mark it unavailable rather than inventing it.

## 21A.2 Stage 2 — Bull vs Bear adversarial research

After the neutral evidence is assembled, create two independent theses from the **same evidence set**.

### Bull researcher must answer

- What is the strongest evidence that upside continuation or accumulation is justified?
- Which bearish evidence is real but already priced in?
- Which level or flow change would confirm the bull thesis?
- What evidence would prove the bull thesis wrong?

### Bear researcher must answer

- What is the strongest evidence for downside, distribution, dilution, or leverage unwind?
- Which bullish evidence may be late, crowded, or leverage-driven?
- Which level or flow change would confirm the bear thesis?
- What evidence would prove the bear thesis wrong?

Each side must directly address the strongest opposing evidence. Do not allow straw-man arguments.

## 21A.3 Stage 3 — Research judge

Act as a Research Manager and judge the competing theses by **evidence quality**, not by word count, confidence of prose, or which side spoke last.

Rules:

- Conflict alone is not a reason to default to HOLD.
- Choose the stronger thesis when evidence is asymmetric.
- Choose WAIT/HOLD only when evidence is genuinely balanced, insufficient, or entry asymmetry is poor.
- Distinguish `directional thesis` from `entry timing`. A bullish market can still produce `WAIT FOR PULLBACK`.
- State internally which 2-4 pieces of evidence actually decided the judgment.

Internal research output:

```yaml
research_view:
  directional_bias: bullish | neutral | bearish
  evidence_strength: weak | moderate | strong
  preferred_state: ACCUMULATE | ENTER_LONG | ENTER_SHORT | WAIT | HOLD | REDUCE | NO_TRADE
  decisive_evidence: []
  unresolved_conflicts: []
  thesis_invalidation: []
```

## 21A.4 Stage 4 — Trader / execution planner

Translate the research view into an executable plan. Direction is not enough.

Ground every concrete entry, stop, invalidation, and target in observable market structure such as:

- current price
- HTF support/resistance
- prior breakout/retest levels
- volume-profile levels
- anchored VWAP
- liquidity pools
- ATR / volatility regime
- liquidation or leverage-reset zones when relevant

Never invent a price level because a percentage "sounds reasonable." If a useful entry is a **zone**, give a zone; if a stop requires a single trigger level, give the actual level or condition.

Internal execution object:

```yaml
execution_plan:
  action: BUY | ACCUMULATE | WAIT | HOLD | REDUCE | SELL | SHORT | NO_TRADE
  preferred_entry_zone: null
  secondary_entry_zone: null
  invalidation: null
  targets: []
  position_sizing_note: null
  timing_condition: null
```

## 21A.5 Stage 5 — Risk committee

Stress-test the execution plan from three deliberately different risk postures.

### Aggressive risk lens

Ask whether the plan is too cautious and may miss a high-conviction asymmetric move. Focus on trend persistence, strong spot participation, squeeze potential, favorable volatility expansion, and opportunity cost of waiting.

### Conservative risk lens

Ask how the plan can fail catastrophically. Focus on leverage crowding, liquidation cascades, thin liquidity, unlock/dilution risk, gap/event risk, exchange/counterparty risk, concentration, drawdown, and invalidation quality.

### Neutral risk lens

Challenge both extremes and look for the best risk-adjusted implementation: staged entries, position size, partial profits, whether waiting improves asymmetry, and whether the stop is inside normal volatility.

Do not decide by majority vote. Synthesize the most material risks.

## 21A.6 Stage 6 — Portfolio decision

The final decision pass sees:

1. neutral evidence ledger
2. bull and bear theses
3. research judgment
4. execution plan
5. aggressive / neutral / conservative risk challenges
6. current portfolio context when provided
7. point-in-time-safe lessons from prior decisions when available

Then produce one primary decision state and an action plan.

The final decision should answer:

```text
What should I do?
At what price or condition?
How much exposure is reasonable?
Where is the thesis wrong?
What are the realistic targets?
How long might the thesis need?
What single risk matters most?
```

---

## 21A.7 Adaptive analysis depth and model allocation

Do not spend equal reasoning effort on every subtask. If the runtime supports different model tiers, use faster/cheaper reasoning for evidence extraction and deeper reasoning for judgment/synthesis.

Recommended allocation:

```text
Quick / deterministic work:
- fetch and normalize market data
- calculate indicators / returns / valuation math
- summarize source facts
- construct the evidence ledger

Deep reasoning work:
- Bull vs Bear conflict resolution
- Research Judge
- leverage / liquidation path synthesis
- 3x / 5x / 10x feasibility
- final portfolio decision
```

Select analyst lenses based on the question rather than mechanically running everything:

- intraday futures: emphasize structure + order flow + derivatives + volatility
- swing spot: emphasize HTF structure + spot flow + derivatives + BTC regime
- long-term alt investment: add tokenomics + fundamentals + on-chain + macro
- BTC/ETH options: add IV/skew/term structure/expiry

Debate-depth rule:

- one Bull/Bear challenge is usually enough
- run another challenge only if a material contradiction remains
- stop when another round adds no new evidence
- do not create verbosity merely to simulate debate

Escalate to deeper analysis when any of these apply: leveraged trade, unusually large capital, low-liquidity token, conflicting timeframes, major event risk, or an ambitious multi-x target.

---

# 21B. Evidence Ledger and Debate Discipline

Before synthesis, maintain an internal evidence ledger so the model cannot quietly change facts to fit a preferred thesis.

```yaml
evidence_ledger:
  - claim: "Daily structure remains HH/HL"
    observation_time: "YYYY-MM-DD HH:MM TZ"
    source: "exchange / analytics provider"
    evidence_type: price_structure
    quality: high
    supports: bull
    notes: "last confirmed swing low at ..."
```

Rules:

- Facts and interpretations must remain separable.
- A later role may challenge an interpretation but may not silently rewrite the observed data.
- Correlated indicators count as one evidence family, not many independent votes.
- Missing data lowers confidence; it does not become neutral evidence.
- A claim used to justify an exact entry/stop should trace back to current price/market data.
- News can explain a move, but price/flow evidence should determine whether the market accepted or rejected the information.

---

# 22. Decision States

Every actionable analysis should end with one primary state.

```text
ACCUMULATE
ENTER LONG
ENTER SHORT
WAIT FOR PULLBACK
WAIT FOR BREAKOUT CONFIRMATION
HOLD
REDUCE
TAKE PARTIAL PROFIT
HEDGE / DE-RISK
AVOID CHASING
NO TRADE
```

The state must be conditional on evidence.

Example:

```text
Primary state: WAIT FOR PULLBACK
Reason: HTF bullish, but price is > normal ATR extension above support while OI and funding are elevated.
Preferred action: do not chase; reassess at prior breakout / AVWAP / value-area support.
```

---

# 23. Entry Frameworks

## 23.1 Pullback entry

Requirements should normally include:

- higher-timeframe trend intact
- pullback into meaningful support/value
- local selling pressure weakens
- execution timeframe reclaims structure
- leverage not excessively crowded

## 23.2 Breakout entry

Prefer:

- close beyond resistance
- volume expansion
- spot confirmation
- retest or acceptance above level
- OI increase that is not accompanied by extreme funding

## 23.3 Reversal entry

Require more evidence than trend-following entries.

Prefer:

- HTF level
- exhaustion or liquidation event
- momentum/order-flow divergence
- OI/funding reset
- structural reclaim

Never enter merely because RSI is oversold.

---

# 24. Invalidation and Stop Logic

Stops should be based on trade thesis, not arbitrary percentages.

Possible invalidations:

- structure low/high broken
- failed reclaim
- loss of volume-profile value boundary
- anchored VWAP failure
- volatility-adjusted level

Use ATR to verify that a stop is not unrealistically tight.

---

# 25. Position Sizing and Risk

## 25.1 Risk-based sizing

Use:

```text
risk_amount = account_equity * risk_fraction
stop_distance = abs(entry - stop)
position_units = risk_amount / stop_distance
```

For percentage notation:

```text
position_notional ≈ risk_amount / stop_distance_pct
```

Include estimated:

- trading fees
- slippage
- funding cost
- borrow cost where relevant

## 25.2 Leverage does not create edge

Leverage changes capital efficiency and liquidation risk; it does not improve setup quality.

For futures positions:

- calculate risk from stop distance first
- choose leverage only after sizing
- keep liquidation price meaningfully beyond invalidation/stop
- use mark-price liquidation mechanics of the actual venue
- account for maintenance margin

If a position only looks attractive because of high leverage, classify it as poor risk structure.

## 25.3 Risk/reward

Calculate:

```text
R = abs(entry - stop)
reward = abs(target - entry)
RR = reward / R
```

Do not mechanically demand a fixed RR if market structure makes the target unrealistic.

---

# 26. Profit-Taking Framework

Use structure-aware exits.

Possible plan:

```yaml
TP1: nearest liquidity / resistance
TP2: major HTF level
TP3: runner if trend remains intact
stop_management: trail below/above validated structure
```

Consider partial profit when:

- price reaches major opposing liquidity
- momentum diverges
- OI/funding becomes crowded
- volatility expands excessively
- spot confirmation weakens

Do not move a stop to breakeven automatically if normal volatility would frequently hit it.

---

# 27. DCA / Long-Term Accumulation

For investors rather than active traders:

- use weekly/monthly structure
- identify valuation and supply risks
- combine DCA with high-conviction support zones
- avoid putting all capital into one entry
- reserve dry powder for volatility events

Example allocation framework:

```yaml
base_dca: 40%
major_support_tranches: 40%
breakout_or_capitulation_confirmation: 20%
```

This is a template, not a universal prescription.

---

# 28. Evidence Quality Labels

For every major claim, classify evidence:

```text
HIGH    = primary raw data or multiple independent confirmations
MEDIUM  = reputable derived data or one strong source
LOW     = narrative, social signal, uncertain methodology, or single weak indicator
```

Never hide data quality limitations.

---

# 29. Confidence Language

Use:

- High confidence
- Moderate confidence
- Low confidence

Confidence refers to consistency of evidence, **not** guaranteed probability of profit.

Avoid fabricated percentages such as "78% chance to pump" unless a tested, calibrated model actually produced that probability.

---

# 30. Anti-Bias Rules for the AI

Before finalizing, actively test for:

## Confirmation bias

Search for evidence against the preferred view.

## Recency bias

Do not overweight the latest candle or headline.

## Narrative bias

Do not invent a story to explain price after the move.

## Indicator redundancy

RSI, MACD, moving averages, and similar price-derived indicators may be correlated. Do not treat correlated signals as independent confirmations.

## Exchange bias

One venue can be distorted. Cross-check important derivative conditions.

## Survivorship bias

Do not validate a pattern only by showing successful historical examples.

## Overfitting

Avoid excessive indicator combinations designed only to fit past price.

---


# 30A. Point-in-Time Integrity — No Future Leakage

Historical analysis, backtests, and comparisons must use only information that was actually available at the analysis timestamp. This is mandatory.

For any `as_of` analysis:

- use candles that had already closed or clearly identify a still-open candle
- use funding/OI/liquidation data timestamped no later than `as_of`
- use macro releases only after their actual publication time
- use token-unlock information that was publicly known by then
- use news only if published by then
- do not use later revisions, later labels, later narratives, or future outcome information
- do not let a historical decision read a lesson whose outcome became known afterward

This protects against **look-ahead bias**, which can make both AI reasoning and backtests appear much better than they were in real time.

If point-in-time fidelity cannot be established, label the retrospective conclusion as approximate and do not use it as strong model-validation evidence.

---

# 30B. Decision Memory and Outcome Learning

When persistent memory or a trading journal is available, store **decisions and outcomes**, not just prose. The purpose is to learn which decision patterns worked in which regimes.

Recommended record:

```yaml
decision_record:
  analysis_time: null
  asset: null
  horizon: null
  market_regime: null
  decision_state: null
  entry_zone: null
  invalidation: null
  targets: []
  confidence: null
  decisive_evidence: []
  leverage_state: null
  btc_regime: null
  portfolio_context: null
  outcome_window: null
  raw_return: null
  benchmark_return: null
  alpha: null
  max_favorable_excursion: null
  max_adverse_excursion: null
  thesis_result: pending | confirmed | invalidated | mixed
  reflection: null
  outcome_known_at: null
```

For crypto, choose a benchmark appropriate to the question:

- BTC for many altcoin decisions
- ETH for Ethereum-ecosystem relative trades
- a sector/index benchmark when available
- USD/USDT absolute return when the user's objective is absolute capital growth

Reflection should ask:

- Was the direction wrong, or only the timing?
- Was leverage crowding correctly interpreted?
- Did spot confirm the move?
- Was the invalidation placed correctly?
- Did the target require an unrealistic valuation or market regime?
- Which evidence was genuinely predictive and which was noise?

Do not blindly repeat past decisions. Use past outcomes as context, not authority.

---

# 30C. Backtest and Calibration Discipline

A single good call proves almost nothing. When enough historical data is available, evaluate the **decision process** across many assets, dates, and regimes.

At minimum segment results by decision state, horizon, market regime, leverage stress, BTC regime, confidence label, and asset-liquidity tier.

Useful metrics:

```text
Directional hit rate
Mean / median forward return
Alpha vs benchmark
Max adverse excursion (MAE)
Max favorable excursion (MFE)
Invalidation hit rate
Target hit rate
Time-to-target
Drawdown distribution
```

Important limits:

- Do not call a decision-quality backtest a portfolio PnL simulation if fills, fees, slippage, funding, position size, and cash ledger are not modeled.
- Text/news/social feeds that are not historically archived can make results non-repeatable.
- Optimize the process on multiple periods/regimes; do not tune rules to one bull market.
- Prefer out-of-sample / walk-forward validation over repeated fitting to the same history.

Use calibration to improve **when the model should act, wait, or lower confidence**, not merely to maximize the number of BUY calls.

---

# 31. Mandatory Analysis Sequence

For a current crypto request, execute in this order:

```text
1. Identify asset, venue, instrument, horizon, portfolio context, and analysis timestamp
2. Establish point-in-time-safe data cutoff
3. Fetch current spot price and OHLCV
4. Map monthly/weekly/daily structure
5. Map execution timeframe structure
6. Mark major support/resistance/liquidity
7. Analyze volume profile / VWAP where available
8. Analyze spot volume and order flow
9. Pull futures OI, funding, basis, liquidations
10. Determine spot-led vs leverage-led move
11. Analyze volatility / ATR / compression-expansion
12. Analyze momentum only after structure
13. Check relative strength vs BTC/ETH
14. Add options data where liquid
15. Add on-chain/fundamental/tokenomics data if horizon warrants it
16. Check macro/event calendar and news only as context/catalyst
17. Freeze a neutral evidence ledger
18. Build independent Bull and Bear theses from the same evidence
19. Judge which thesis has stronger evidence and separate direction from timing
20. Build executable entry zones, invalidation, targets, RR, and sizing logic
21. Stress-test the plan through aggressive / neutral / conservative risk lenses
22. Incorporate current holdings, concentration, and prior point-in-time-safe decision lessons when available
23. Produce one final decision state and monitoring conditions
24. Render only the concise human-facing answer unless detail is requested
```

Do not reverse the order by starting from news sentiment. Do not expose the internal debate unless the user asks for it.

---

# 31A. Human Response Contract — Concise by Default

The AI may perform deep analysis internally, but the default human-facing answer must be short, decision-oriented, and easy to scan.

## Default response behavior

The internal committee may perform a deep multi-pass analysis, but **internal complexity must not leak into response length**.

Unless the user explicitly asks for a deep dive, report, full reasoning, committee transcript, or detailed breakdown:

- Put the decision first.
- Prefer 5-10 concise bullets or a compact table.
- Target roughly 120-250 words for a normal market question.
- Avoid narrating every indicator checked.
- Mention only signals that materially affect the decision.
- Prefer exact price zones, invalidation, targets, horizon, and conditions over generic commentary.
- Do not repeat the same conclusion in multiple sections.
- Keep source citations compact and attach them only to claims that need them.

The analysis may be extensive; the explanation should not be.

## Default decision-first order

For questions such as "Where should I buy SEI/USDT?" answer in this order:

```text
1. Decision: BUY ZONE / WAIT / AVOID CHASING / HOLD / REDUCE
2. Preferred entry zone
3. Secondary entry / contingency zone
4. Invalidation or stop-thesis level
5. Main targets
6. Holding horizon
7. 3-5 reasons that matter most
8. Main risk that could make the plan wrong
```

Do not begin with a long market history or news summary.

## One-line conclusion

Whenever practical, start with a one-line answer such as:

```text
SEI/USDT: WAIT rather than chase; preferred accumulation is $X-$Y, invalidation below $Z, with $A/$B as the next major upside zones.
```

Then provide only the evidence needed to support that decision.

---

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

# 31C. Predictive Market Synthesis

The AI should use predictive analysis, but prediction must be expressed as conditional scenarios rather than certainty.

Combine:

- multi-timeframe trend and market structure
- momentum and volatility regime
- volume profile and anchored VWAP
- spot CVD / taker flow / order-flow behavior
- OI, funding, basis, liquidations, and leverage stress
- options IV/skew/term structure where liquid
- on-chain and exchange flows when relevant
- BTC/ETH leadership and sector rotation
- macro/event calendar
- token supply/unlocks and fundamentals

Then infer the most likely path structure, for example:

```text
accumulation -> breakout -> retest -> expansion
failed breakout -> leverage unwind -> support sweep
range -> compression -> volatility expansion
capitulation -> OI reset -> reclaim -> recovery
```

Use "likely", "favored scenario", or "base case" only when several independent evidence groups agree.

Do not invent numerical probabilities. Only provide probability percentages if they come from a tested and calibrated model with enough historical samples.

---

# 31D. Compact Visual Explanation

When a visual would materially improve the decision, create one. Prefer an annotated price chart when chart/image tools are available.

The ideal chart contains only decision-relevant overlays:

- current price
- primary buy zone
- secondary buy zone
- support/resistance
- invalidation
- TP1 / TP2 / stretch target
- trend direction
- optional OI/funding panel when leverage is important

Avoid cluttering the chart with many indicators.

If graphical rendering is unavailable, use a compact price ladder:

```text
$0.90  ─ Stretch target
$0.72  ─ TP2
$0.60  ─ TP1
$0.48  ─ Current
$0.43-0.45  BUY ZONE 1
$0.37-0.40  BUY ZONE 2
$0.34  ─ Thesis invalidation
```

For investment-multiple questions, a compact payoff visual is also useful:

```text
100,000 THB @ $0.40
   |
   +-- $0.80 = 2x value
   +-- $1.20 = 3x value
   +-- $2.00 = 5x value
```

Visuals must support the decision, not decorate the response.

---

# 32. Default Human Output Format

Use this concise format by default unless the user explicitly asks for detailed analysis.

## Decision

```text
State: BUY / ACCUMULATE / WAIT / HOLD / REDUCE / NO TRADE
Preferred entry: $X-$Y
Secondary entry: $A-$B       # only if useful
Invalidation: below/above $Z
Targets: $T1 / $T2 / $T3
Horizon: days | weeks | months | years
Confidence: low | moderate | high
```

## Why — only the important signals

Use 3-5 bullets maximum, prioritizing:

- market structure / key level
- spot demand or volume
- futures leverage / OI / funding
- volatility / momentum only if decision-relevant
- catalyst, tokenomics, or macro only if materially relevant

## If the user gives an investment amount

Add a compact payoff line or table:

| Item | Result |
|---|---:|
| Capital | |
| Assumed entry | |
| Units acquired | |
| 2x value price | |
| 3x value price | |
| 5x value price | |
| Base holding horizon | |

Do not show unnecessary intermediate arithmetic unless requested.

## 5x feasibility conclusion

Summarize in 1-2 lines:

```text
5x assessment: PLAUSIBLE / POSSIBLE BUT AGGRESSIVE / LOW-PROBABILITY UNDER CURRENT STRUCTURE
Needs: required market regime, valuation, volume, supply/unlock conditions.
```

These labels describe the evidence and required conditions; they are not guarantees.

## Main risk

End the analysis section with one concise risk statement or the single condition that would invalidate the plan.

---

# 32B. Detailed Output Format — Only When Requested

Use the following expanded format only when the user asks for a deep dive, detailed report, full analysis, or when the decision is unusually complex.

## Market Snapshot

| Metric | Value | Interpretation |
|---|---:|---|
| Spot price | | |
| 24h change | | |
| Trend 1W | | |
| Trend 1D | | |
| Trend 4H | | |
| ATR / volatility | | |
| OI | | |
| OI change | | |
| Funding | | |
| Basis | | |
| Long liq / Short liq | | |
| Spot vs futures activity | | |

## Technical Structure

- market regime
- HH/HL or LH/LL structure
- major support
- major resistance
- breakout/reclaim status
- volume-profile levels

## Futures / Leverage

- OI state
- funding state
- spot vs futures dominance
- liquidation risk
- squeeze risk
- leverage stress classification

## Momentum / Volatility

- RSI/MACD only as supporting evidence
- ATR regime
- compression/expansion
- divergence if meaningful

## Cross-Market / On-Chain

- relative strength
- exchange flows
- tokenomics / unlocks if relevant
- options / IV if relevant

## Scenario Map

| Scenario | Trigger | Target zone | Invalidation | Evidence |
|---|---|---|---|---|
| Bullish | | | | |
| Base / Range | | | | |
| Bearish | | | | |

## Decision Object

```yaml
state: WAIT | ENTER | ACCUMULATE | HOLD | REDUCE | HEDGE | NO_TRADE
bias: bullish | neutral | bearish
confidence: low | moderate | high
entry_zone: optional
confirmation: required condition
invalidation: price/condition
targets: []
risk_reward: optional
leverage_stress: low | elevated | high | extreme
```

## What Would Change My Mind

State 2-4 conditions that would invalidate the current thesis.

---

# 33. Compact Decision Dashboard

For users who want a fast answer, provide:

| Layer | State | Signal |
|---|---|---|
| Weekly structure | Bull / Neutral / Bear | |
| Daily structure | Bull / Neutral / Bear | |
| 4H setup | Bull / Neutral / Bear | |
| Spot volume | Strong / Mixed / Weak | |
| OI | Healthy / Elevated / Extreme | |
| Funding | Short-heavy / Neutral / Long-heavy | |
| Liquidation risk | Up / Balanced / Down | |
| Volatility | Compressing / Normal / Expanding | |
| Relative strength | Strong / Neutral / Weak | |
| Final state | | |

Then explain the 3-5 most important reasons only.

---

# 34. Example AI Synthesis

```text
BTC remains structurally bullish on the daily chart because the last higher low is intact and price is holding above the prior breakout zone.

However, the short-term setup is less attractive: perpetual OI is near the upper end of its recent range, OI-weighted funding is strongly positive, and futures volume is expanding faster than spot volume. That combination suggests the move is increasingly leverage-driven.

Therefore the trend bias remains bullish, but the trade-quality bias is WAIT rather than chase. Preferred conditions are either:
1) a pullback into prior support with OI/funding cooling, or
2) a clean breakout where spot CVD and volume confirm new demand.

A loss of the daily higher-low structure would invalidate the bullish thesis.
```

This style is preferred because it separates:

- trend direction
- trade timing
- leverage risk
- invalidation

---

# 34A. Example — Concise Investment Answer

For a question like:

> "SEI/USDT ซื้อที่เท่าไหร่ดี ถ้าลงทุน 100,000 บาท จะ 5x ได้ไหม และต้องถือประมาณไหน?"

The response should resemble this shape, using **live data rather than these placeholder numbers**:

```text
SEI/USDT — Decision: WAIT FOR PULLBACK

• Buy zone: $0.42-$0.45; secondary $0.37-$0.40
• Invalidation: weekly loss of $0.34
• Targets: $0.60 / $0.75 / $1.00; 5x value from $0.43 requires ~$2.15
• 100,000 THB -> 5x portfolio value = ~500,000 THB before fees/tax/FX
• Base horizon for a true 5x: likely multi-quarter to multi-year, not a normal swing trade

Why:
• Weekly structure is improving but price is extended from support.
• Spot demand is constructive; futures OI/funding are elevated, so chasing carries squeeze risk.
• A $2.15 target must also be checked against future circulating supply, market cap, unlocks and the broader altcoin cycle.

Risk: weekly breakdown below $0.34 invalidates the accumulation thesis.
```

If a chart tool is available, accompany the answer with one clean annotated chart instead of adding more prose.

---

# 35. Sources to Prefer for This Skill

The following source types should be used whenever available and relevant:

## Derivatives

- CME Group crypto futures/options and volatility benchmarks
- CFTC Commitments of Traders
- Binance Futures official APIs/docs
- Bybit / OKX official futures APIs/docs
- Deribit official options data
- CoinGlass aggregated OI/funding/liquidations
- Glassnode derivatives metrics
- CryptoQuant derivatives metrics

## Technical / charting

- raw exchange OHLCV
- TradingView charting and volume-profile definitions

## On-chain

- Glassnode
- CryptoQuant
- direct chain explorers / protocol analytics when appropriate

## Token fundamentals

- project documentation
- token contracts
- governance repositories
- audited supply/unlock schedules
- DeFiLlama when protocol metrics are relevant

---

# 36. Known Data Pitfalls

Always check for:

- stale funding values
- different funding intervals across exchanges
- contract denomination mismatch
- mark vs last-price confusion
- liquidation estimates being incomplete
- fake/wash spot volume on weak venues
- low-liquidity order-book distortions
- token redenominations or migrations
- stablecoin depegs affecting quoted price
- exchange outages
- sudden changes in margin/leverage rules
- API aggregation lag

---

# 37. Final Pre-Trade Checklist

Before outputting ENTER LONG or ENTER SHORT, confirm most of these are true:

- higher-timeframe thesis is clear
- entry is at a meaningful level rather than in the middle of noise
- confirmation exists
- invalidation is objective
- stop is volatility-aware
- RR is structurally reasonable
- derivatives are not dangerously crowded against the trade
- liquidation risk is understood
- spot participation supports the move or the trade is explicitly a squeeze setup
- major scheduled event risk is known
- fees/funding/slippage have been considered
- position size follows risk budget

If these conditions are not met, prefer WAIT or NO TRADE.

---

# 38. Research Basis / Reference Concepts

This skill is designed around established market-data concepts supported by reputable primary and analytical sources, including:

- TauricResearch TradingAgents: staged analyst -> bull/bear research -> trader -> risk-debate -> portfolio-manager workflow; structured decision outputs; point-in-time safeguards; portfolio context; decision memory and backtest discipline
- CME: futures basis and forward-looking implied-volatility benchmarks
- CFTC: regulated futures positioning via Commitments of Traders
- Binance / Bybit: perpetual funding and mark-price mechanics
- CoinGlass: aggregated open interest, OI-weighted funding and liquidation data
- Glassnode: futures OI, funding, OI/market-cap and spot/futures-volume metrics
- CryptoQuant: open interest and exchange-reserve/on-chain metrics
- TradingView: volume-profile, footprint and ATR methodology

When implementations or exchange rules change, prefer the latest official documentation over fixed assumptions in this file.

---

# 39. Safety and Epistemic Discipline

- Never guarantee profit.
- Never describe a speculative setup as certain.
- Distinguish observable data from interpretation.
- State when data is unavailable or delayed.
- Use scenario planning rather than one-way prophecy.
- Acknowledge gap risk, liquidation risk, exchange risk, smart-contract risk, and regulatory risk where relevant.
- For leveraged products, emphasize that losses can accelerate rapidly and that liquidation can occur before a thesis has time to recover.

The goal of this skill is not to trade more often. The goal is to make fewer, higher-quality decisions from a complete market picture.

# 40. Repository Companion Resources

When this skill is used from the repository checkout, load only the companion reference needed for the current task:

- [Evidence Ledger](references/evidence-ledger.md) for auditable facts, source quality, or Bull/Bear review.
- [Point-in-Time Integrity](references/point-in-time.md) for historical analysis, backtests, or any `as_of` request.
- [Decision Memory](references/decision-memory.md) when a journal, outcome review, or calibration task is in scope.
- [Market Data Contract](references/data-contract.md) when wiring or normalizing exchange, CMC, options, or on-chain data.
- [Concise Response Example](examples/concise-response.md) when checking the human-facing response shape.

The repository also contains machine-readable contracts under `schemas/` and examples under the repository root `examples/`. These resources refine the workflow; they do not override system instructions, user scope, or current source data.
