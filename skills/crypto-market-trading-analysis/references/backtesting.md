# Backtesting and Decision-Memory Playbook

Load this reference for historical `as_of` work, journals, outcome review,
trigger-aware timing evaluation, calibration, or backtests.

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
trigger:
  triggered: false
  triggered_at: null
  entry_reference: null
  time_to_trigger: null
outcome:
  target_hit_first: null
  invalidation_hit_first: null
  post_trigger_return: null
  post_trigger_mfe: null
  post_trigger_mae: null
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
