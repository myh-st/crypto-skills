# Portfolio and Execution Risk Playbook

Load this reference when the answer needs entry construction, invalidation,
position sizing, profit-taking, DCA, or evidence/confidence risk controls.

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
