# Goal: day-trade futures first (PAPER)

**Priority: FIRST.** Set by the user on 2026-09-29. It supersedes "feature freeze, wait for the
90-day campaign" as the main focus. EXP-001 and EXP-002 keep running untouched in the background
as long-horizon evidence.

## Objective

Find, build and PAPER-prove a futures **day-trading** engine that makes a **net profit per day**
after taker fees, slippage and funding. The engine must:

- hold every position for at most 24h;
- trade both long and short;
- trade the user's watchlist (BTC, ETH, NEAR, SEI, SUI, AVAX, ENA);
- trade often enough that a forward PAPER test gives a verdict in **2–3 weeks instead of 90 days**.

## Why day trading shortens the test

Confidence comes from the number of independent trades, not calendar days. The trend sleeves
close about one trade a day, so they need about 90 days. A day-trading engine that closes 10+
trades a day reaches 150–200 trades in about 14–21 days.

## Stages and gates (each must pass before the next)

1. **Research (history).** Use Binance USDT-M 5m data from 2023-01 onward as a price proxy
   (Gate keeps only 10,000 recent candles), and cross-check on Gate's latest ~35 days of 5m data.
   - Fix a small set of strategy families before looking at results.
   - In-sample is 2023–2024. Out-of-sample is 2025–2026. Also report walk-forward by year.
   - Pass criteria, out-of-sample:
     - net Sharpe of daily P&L ≥ 1.5;
     - profit factor ≥ 1.2;
     - still profitable at 2× costs;
     - ≥ 3 trades per day across the universe;
     - max drawdown ≤ 10% at 0.5% risk per trade;
     - ≥ 60% of days non-negative, or ≥ 70% of weeks positive;
     - t-stat ≥ 2.
   - Report the multiple-testing count and survivorship caveats.
2. **Engine.** Build it as a new `strategy_engine` next to `breakout_15m` and `sleeves_v1`. It must
   meet all of the following:
   - every order goes through the RiskEngine, kill switch, feed checks and PAPER fills;
   - a daily loss limit and forced flat after the maximum hold time;
   - no real-order path.
3. **Runtime replay.** Replay the engine through the real runtime on the same history. The
   results must match the research within reason, and reconciliation must be OK.
4. **Short forward PAPER test (EXP-003).**
   - Minimum 14 days and ≥ 150 closed trades.
   - Frozen manifest; promotion criteria set to `min_days 14` and `min_completed_trades 150`.
   - Checked daily by the scheduled health task. The verdict comes from `paper-checkpoint`.
5. **Review.** Continue, iterate as a new version, or stop. A PASS never enables real money.

## Non-negotiables

- PAPER only. Real Gate order, leverage, transfer and withdrawal writes stay blocked. Live
  execution stays `PLANNING_ONLY_LIVE_DISABLED`.
- No look-ahead. A signal uses closed bars only, and fills happen at the next bar.
- The stop wins when a bar touches both stop and target.
- Costs are never zero-filled.
- CI and unit tests stay offline and fake-only.
- Secrets never reach SQLite, logs, prompts or exports.
- Do not mutate a running experiment. A material change becomes a new version.

## Research outcome (2026-09-29): gate NOT passed, EXP-003 not built

Rounds 1–2 fail the stage-1 gate in every view. The rounds and their results:

| Round | Scope | Out-of-sample result |
|---|---|---|
| 1 | 199,020 backtests: 8 families × 5m–4h × 30 coins; walk-forward K20 | Sharpe 1.01 (holdout 0.36). 6/9 gate criteria fail: PF 1.08, loses at 2× costs, DD 23% at 0.5% risk |
| 2 | Honest maker fills (trade-through), 4h robust ensembles, Gate-liquidity universe, per-coin slippage | Best candidates Sharpe 0.72–0.74, t ≈ 0.9; pass 1–2 of 8 |

Overfitting diagnostics:
- PBO is about 0.44–0.50 in the comparable 4h space.
- The Deflated Sharpe of the round-1 procedure is 0.22 with all 82 trials counted (bar: 0.95).
- The best in-sample stream loses out of sample in 68% of CSCV splits.

Why:
- Sub-4h edges exist before costs but are smaller than taker fees.
- Maker fills are adversely selected: filled trades lose and missed trades win.
- Much of round 1's profit came from coins that are illiquid on Gate. Only 9 of 30 pass a Gate liquidity filter, and on those the walk-forward drops to Sharpe −0.06.

A 2–3 week forward PAPER test cannot prove an edge. The minimum track record for Sharpe > 0 at 95% is about 440 days even at a true Sharpe of 1.5. Short forward tests are for engine fidelity, meaning PAPER matching a replay of the same bars, not for proving an edge.

Evidence:
- `reports/day-trade/report.md`
- `reports/day-trade/round2/report.md` (with `PLAN.md` and `results.json`)
- `reports/company/scout-report.md`

All three are git-ignored research outputs.

### What happens instead

- **The aggressive track for fast growth on small capital is the evidence-backed trend engine at 2× risk.** EXP-002x runs the trend sleeves at 2× with `margin_scaling`, a 40% drawdown halt and a 15% daily loss pause. Its 4-year runtime replay gives Sharpe 1.33, +67.9%/yr and max DD 36.2%. EXP-002 (1×: Sharpe 1.44, +42.1%/yr, DD 18.4%) is its control.
- **Any new day-trade hypothesis needs different information,** not new bar patterns. Candidates are funding/basis extremes, OI or liquidation reversals, cross-sectional ranking across the Gate-liquid coins, and Gate–Binance lead-lag.
- Each hypothesis is pre-declared in a plan with a tiny trial count. It is judged on clean post-2026-07 data plus the forward PAPER stream, with the Deflated Sharpe and PBO reported.
