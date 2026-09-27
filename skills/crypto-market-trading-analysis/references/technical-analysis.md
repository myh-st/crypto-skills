# Technical Analysis Playbook

Load this reference for swing, position, intraday, or chart-structure questions.
It contains the detailed multi-timeframe, price-action, volume, momentum,
volatility, and pattern lanes used by the core skill.

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
