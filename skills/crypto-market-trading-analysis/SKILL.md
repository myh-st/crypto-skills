---
name: crypto-market-trading-analysis
description: >-
  Use this skill when analyzing cryptocurrency markets for spot investing, swing trading,
  position trading, perpetual futures, dated futures, or options. The skill prioritizes
  price action, multi-timeframe market structure, volume, order flow, derivatives positioning,
  leverage, funding, open interest, liquidations, volatility, and cross-market confirmation.
  It uses news and narratives as secondary context rather than the primary trading signal.
  The goal is to produce an evidence-based market view with explicit entry conditions,
  invalidation, risk, scenarios, and canonical decision states such as ACCUMULATE,
  ENTER_LONG, ENTER_SHORT, WAIT_FOR_PULLBACK, WAIT_FOR_BREAKOUT_CONFIRMATION,
  AVOID_CHASING, NO_TRADE, HOLD, REDUCE, TAKE_PARTIAL_PROFIT, HEDGE_DE_RISK, or EXIT.
  Internally, use an adversarial investment-committee
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

# 5. Progressive Analysis References

Keep this file as the core reasoning contract. Load only the lane-specific
reference needed for the request:

| Request lane | Reference |
|---|---|
| multi-timeframe chart, volume, momentum, volatility, patterns | `references/technical-analysis.md` |
| CVD, taker flow, footprint, imbalance, absorption, execution quality | `references/order-flow.md` |
| futures, OI, funding, basis, liquidations, leverage | `references/derivatives.md` |
| options IV, skew, term structure, expiry | `references/options.md` |
| on-chain flows, holders, stablecoin liquidity | `references/on-chain.md` |
| relative strength, macro, event calendar | `references/market-context.md` |
| supply, unlocks, adoption, fees, valuation fundamentals | `references/tokenomics.md` |
| entries, stops, sizing, exits, DCA, confidence | `references/portfolio-risk.md` |
| historical cutoffs, journals, trigger-aware outcomes, calibration | `references/backtesting.md` |
| 2x/3x/5x/10x and holding-horizon feasibility | `references/valuation-multiples.md` |

The core workflow, canonical decision states, evidence contract, safety rules,
and concise response contract remain in this file. A missing lane reference is
unavailable data, not permission to invent a signal.

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
- Choose `HOLD` or `NO_TRADE` only when evidence is genuinely balanced,
  insufficient, or entry asymmetry is poor. Distinguish `directional thesis`
  from `entry timing`. A bullish market can still produce `WAIT_FOR_PULLBACK`.
- State internally which 2-4 pieces of evidence actually decided the judgment.

Internal research output:

```yaml
research_view:
  directional_bias: bullish | neutral | bearish
  evidence_strength: weak | moderate | strong
  preferred_state: ACCUMULATE | ENTER_LONG | ENTER_SHORT | WAIT_FOR_PULLBACK | WAIT_FOR_BREAKOUT_CONFIRMATION | AVOID_CHASING | NO_TRADE | HOLD | REDUCE | TAKE_PARTIAL_PROFIT | HEDGE_DE_RISK | EXIT
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
  decision_state: ACCUMULATE | ENTER_LONG | ENTER_SHORT | WAIT_FOR_PULLBACK | WAIT_FOR_BREAKOUT_CONFIRMATION | AVOID_CHASING | NO_TRADE | HOLD | REDUCE | TAKE_PARTIAL_PROFIT | HEDGE_DE_RISK | EXIT
  execution_action: BUY | ACCUMULATE | WAIT | HOLD | REDUCE | SELL | SHORT | HEDGE | TAKE_PROFIT
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
    metric: daily_structure
    value: "HH/HL"
    unit: null
    venue: Binance
    instrument: spot
    observed_at: "YYYY-MM-DDTHH:MM:SSZ"
    retrieved_at: "YYYY-MM-DDTHH:MM:SSZ"
    freshness_seconds: 0
    source:
      provider: "exchange / analytics provider"
      type: primary | aggregator | charting | derived | news | other
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
ENTER_LONG
ENTER_SHORT
WAIT_FOR_PULLBACK
WAIT_FOR_BREAKOUT_CONFIRMATION
AVOID_CHASING
NO_TRADE
HOLD
REDUCE
TAKE_PARTIAL_PROFIT
HEDGE_DE_RISK
EXIT
```

These are the canonical final states defined in
`schemas/decision-state.schema.json`. Human-readable labels, internal planner
verbs, and adapter-specific states must map to this list before the decision is
persisted or returned. Use `ENTER_LONG`/`ENTER_SHORT` only when the confirmation
condition is already satisfied; `WAIT_FOR_PULLBACK` and
`WAIT_FOR_BREAKOUT_CONFIRMATION` are timing decisions, not market orders.

Internal execution labels map as follows:

```text
BUY + confirmed         -> ENTER_LONG
SELL/SHORT + confirmed  -> ENTER_SHORT
BUY without confirmation -> ACCUMULATE or WAIT_FOR_PULLBACK
WAIT + breakout trigger  -> WAIT_FOR_BREAKOUT_CONFIRMATION
HEDGE                    -> HEDGE_DE_RISK
TAKE_PROFIT              -> TAKE_PARTIAL_PROFIT or EXIT
```

The state must be conditional on evidence.

Example:

```text
Primary state: WAIT_FOR_PULLBACK
Reason: HTF bullish, but price is > normal ATR extension above support while OI and funding are elevated.
Preferred action: do not chase; reassess at prior breakout / AVWAP / value-area support.
```

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

# 30A. Point-in-Time Integrity

For historical analysis, backtests, and any `as_of` request, use only data
that was public and observable by the cutoff. Freeze the cutoff first, record
source timestamps, reject later outcomes or revisions, and label conclusions
approximate when publication timing cannot be established. See
`references/point-in-time.md` and `references/backtesting.md`.

# 30B. Decision Memory and Outcome Learning

When a journal or persistent memory is available, store structured decisions
and outcomes rather than prose alone. Keep direction quality separate from
timing quality: `WAIT_FOR_PULLBACK` is evaluated first for zone/confirmation
trigger quality, then for post-trigger target, invalidation, return, MFE, and
MAE. Compare against an appropriate benchmark and treat prior outcomes as
context, not authority. See `references/decision-memory.md`.

# 30C. Backtest and Calibration Discipline

Evaluate the decision process across assets, dates, and regimes. Segment by
canonical state, horizon, market regime, leverage stress, BTC regime,
confidence, and liquidity tier. Report hit rate, benchmark alpha, MFE/MAE,
invalidation/target rates, time-to-target, and drawdown when data supports it.
Do not call a text review a portfolio-PnL backtest without fills, fees,
slippage, funding, sizing, and cash accounting. Prefer walk-forward or
out-of-sample checks. See `references/backtesting.md`.

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
1. Decision: ACCUMULATE / ENTER_LONG / ENTER_SHORT / WAIT_FOR_PULLBACK / WAIT_FOR_BREAKOUT_CONFIRMATION / AVOID_CHASING / HOLD / REDUCE / TAKE_PARTIAL_PROFIT / HEDGE_DE_RISK / EXIT / NO_TRADE
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
SEI/USDT: WAIT_FOR_PULLBACK rather than chase; preferred accumulation is $X-$Y, invalidation below $Z, with $A/$B as the next major upside zones.
```

Then provide only the evidence needed to support that decision.

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
State: ACCUMULATE / ENTER_LONG / ENTER_SHORT / WAIT_FOR_PULLBACK / WAIT_FOR_BREAKOUT_CONFIRMATION / AVOID_CHASING / NO_TRADE / HOLD / REDUCE / TAKE_PARTIAL_PROFIT / HEDGE_DE_RISK / EXIT
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
state: ACCUMULATE | ENTER_LONG | ENTER_SHORT | WAIT_FOR_PULLBACK | WAIT_FOR_BREAKOUT_CONFIRMATION | AVOID_CHASING | NO_TRADE | HOLD | REDUCE | TAKE_PARTIAL_PROFIT | HEDGE_DE_RISK | EXIT
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

Therefore the trend bias remains bullish, but the trade-quality state is WAIT_FOR_PULLBACK rather than chase. Preferred conditions are either:
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
SEI/USDT — Decision: WAIT_FOR_PULLBACK

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

Before outputting `ENTER_LONG` or `ENTER_SHORT`, confirm most of these are true:

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

If these conditions are not met, prefer `WAIT_FOR_PULLBACK`, `WAIT_FOR_BREAKOUT_CONFIRMATION`, or `NO_TRADE`.

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
- [Technical Analysis](references/technical-analysis.md) for structure, volume, momentum, volatility, or chart patterns.
- [Order Flow](references/order-flow.md) for CVD, taker flow, footprint, imbalance, absorption, or execution quality.
- [Derivatives](references/derivatives.md) for futures, OI, funding, basis, liquidations, or leverage.
- [Options](references/options.md) for IV, skew, term structure, or expiry risk.
- [On-Chain](references/on-chain.md) for holder, exchange-flow, stablecoin, or chain-specific metrics.
- [Market Context](references/market-context.md) for relative strength, macro, rotation, or events.
- [Tokenomics](references/tokenomics.md) for supply, unlocks, adoption, fees, or valuation fundamentals.
- [Portfolio and Execution Risk](references/portfolio-risk.md) for entries, invalidation, sizing, exits, DCA, or confidence.
- [Backtesting](references/backtesting.md) for historical cutoffs, trigger-aware outcomes, calibration, or journals.
- [Valuation Multiples](references/valuation-multiples.md) for multi-x feasibility and holding-horizon questions.
- [Concise Response Example](examples/concise-response.md) when checking the human-facing response shape.

The canonical final state is defined by `schemas/decision-state.schema.json` and
the remaining machine-readable contracts live under `schemas/`, with fixtures
under the repository root `examples/`. These resources refine the workflow; they
do not override system instructions, user scope, or current source data.
