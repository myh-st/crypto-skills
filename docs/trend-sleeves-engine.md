# Trend sleeves engine (`sleeves_v1`, EXP-002)

A second PAPER strategy engine that runs next to the 15m breakout engine (`breakout_15m`, EXP-001).
It trades three long/short futures strategies ("sleeves"), each in its own PAPER sub-account.
Selecting it is an experiment setting (`strategy_engine: "sleeves_v1"`), so it is part of the
frozen manifest. Everything is PAPER. Nothing in this engine can place a real order.

## Why this engine

The 15m breakout with a 1.5 ATR stop and a 2 ATR target lost money in replay on real Gate data
(profit factor about 0.4–0.9). A study on about four years of Gate 4h USDT-perp history
(Aug 2022 to Sep 2026) found three strategies that each made money on their own and had almost
uncorrelated daily returns. Rules for that study:

- signals use closed bars only;
- fills happen at the next bar;
- the stop wins when a bar touches both stop and target;
- fees are 0.05% per side, plus 2 bps slippage, plus real funding;
- results were checked walk-forward by year and at double cost.

| Sleeve | Rule | Research Sharpe (7 coins) |
|---|---|---|
| `donchian` | 4h close above/below the prior 30-bar channel. Stop 2 ATR. Trailing stop 6 ATR, fixed at entry and ratcheted on 4h closes. No fixed target. | ~1.0 |
| `tsmom` | Daily. Sign of the 60-day return per coin, sized to a volatility target. | ~1.0 |
| `xsmom` | Weekly. Long the 2 strongest and short the 2 weakest 60-day performers, volatility-sized. | ~0.6 |

Blended at equal risk, the three sleeves reached Sharpe ~1.4 with a ~13% maximum drawdown.
The default universe is the user's watchlist: BTC, ETH, NEAR, SEI, SUI, AVAX, ENA.

## Runtime replay: the production code reproduces the research

`reports/paper-500/replay_sleeves.py` (git-ignored) drives this module's real `PaperScheduler`,
`SleeveEngine`, `RiskEngine`, PAPER fills, funding and liquidation over the same 4h history. It
starts with 500 USDT and runs from 2022-12-30 to 2026-09-29.

| Replay | Sharpe | t-stat | Annual | Max DD | End equity | Sharpe by year (2023 / 24 / 25 / 26) |
|---|---|---|---|---|---|---|
| Costs as configured | 1.32 | 2.55 | +37% | 18.5% | 1,648 | 2.11 / 0.77 / 0.24 / 2.24 |
| Double fees and slippage | 1.18 | 2.28 | +32% | 19.5% | 1,418 | 1.93 / 0.64 / 0.08 / 2.16 |

Each sleeve matches its research counterpart on its own. This comparison uses a run without the
monthly transfers, because a transfer day distorts a single sleeve's daily return:

| Sleeve | Sharpe (research / engine) | Daily-return correlation (research vs engine) |
|---|---|---|
| Donchian | 1.05 / 1.07 | 0.81 |
| TSMOM | 1.00 / 0.97 | 0.83 |
| XSMOM | 0.58 / 0.55 | 0.73 |

Reconciliation, which covers every sleeve wallet and every transfer, passes at the end of the
replay.

The first engine version did not match the research. The replay exposed three defects, and each
fix moved the engine to the research result:

1. **Catastrophe stops at 25% plus 3× isolated margin cut momentum positions on ordinary weekly
   swings.** Stops and liquidations cost the momentum sleeves more than they earned, and XSMOM's
   Sharpe was −0.1. The research holds these positions through such swings. The momentum sleeves
   now use 2× leverage, so liquidation sits near 50%, and a catastrophe stop of
   `min(40%, 3 × daily vol × √10)`.
2. **The Donchian trail was recomputed from the current ATR each bar.** The research fixes it at
   entry. It is now stored at entry (`sleeve_trails.trail_dist`) and never recomputed.
3. **Sub-accounts were never rebalanced.** Over time the best sleeve came to own the book and the
   diversification faded. Blending the engine's own sleeve returns: never rebalanced gives Sharpe
   1.17, monthly gives 1.32, daily gives 1.33. The engine now moves free cash between the sleeve
   wallets back to equal equity on the 1st of each month (`capital_rebalance: "monthly"`).

Read these numbers as evidence, not a promise. They are one history of seven coins, including
coins that survived to be picked (survivorship bias), and 2025 was weak. The forward PAPER
campaign is the real test.

## How it runs

- **Schedule.** `PaperScheduler.cycle_tick` routes to `SleeveEngine.tick(now)` when the experiment
  uses `sleeves_v1`. A tick runs once per 4h boundary, after `schedule_delay_seconds`, and is
  idempotent: the boundary is recorded in `sleeve_ticks`, and every decision is recorded once in
  `sleeve_decisions`. Donchian runs every 4h. TSMOM runs at 00:00 UTC. XSMOM runs at 00:00 UTC on
  Fridays, using Thursday's close (UTC day number % 7 == 0), as in the research. A sleeve that
  has never rebalanced starts on the first tick from the latest closed day. It does not wait
  for its slot.
- **Sub-accounts.** Each sleeve has a cohort and wallet: `sleeve-don`, `sleeve-ts`, `sleeve-xs`.
  Each starts with a third of `starting_balance_usdt`. Sleeves never share a position, so one
  sleeve can be long a coin while another is short it. Portfolio, promotion and campaign views
  sum the three wallets through `capital_cohorts(config)`.
- **Orders.** Every entry is a `TradingIntent` sized by the same `RiskEngine`:
  - For Donchian, the risk per trade is the sleeve's `risk`.
  - For the momentum sleeves, the engine converts the target notional into a risk fraction at the
    catastrophe stop.
  - Rebalances inside the band (±25%) are skipped. Flips close first.
  - Exits use `execute_reduce` limited to the sleeve's cohort. Stops, liquidation and funding come
    from the normal monitor loop.
- **Stop distance.** The RiskEngine still rejects stops wider than 25% for strategy entries. Only
  an engine's internal risk config can widen the cap, to the sleeve's `catastrophe_stop_max_pct`.
  That is never more than 45%, and the liquidation-before-stop check still applies.
- **Capital rebalance.** Only free cash (equity minus locked margin) moves between wallets. Each
  transfer is written once to `wallet_transfers`, and reconciliation includes it.

## Safety gates

| Gate | Effect |
|---|---|
| Kill switch ≥ `NO_NEW_ENTRIES`, automation paused, emergency stop | no new entries; trails still ratchet |
| Stale market feed (when `stale_feed_blocks_entries`) | no new entries for that symbol |
| Combined daily loss ≥ `max_daily_loss` | `RISK_PAUSE`; no entries or rebalances that day |
| Combined drawdown ≥ `max_drawdown_stop` | `RISK_HALT`; momentum books are flattened, Donchian keeps only its trailing stops |
| Kill switch `FULL_AUTOMATION_HALT` | tick does nothing |

Daily loss and drawdown are measured on the sum of the three sleeve wallets, not per sleeve.
Real-money execution stays disabled; this engine only writes PAPER orders and fills.

## Configuration (`sleeves`)

```json
{
  "universe": ["BTCUSDT", "ETHUSDT", "NEARUSDT", "SEIUSDT", "SUIUSDT", "AVAXUSDT", "ENAUSDT"],
  "gross_cap": 3.0,
  "donchian": {"enabled": true, "leverage": 3, "n": 30, "stop_atr": 2.0, "trail_atr": 6.0, "risk": 0.009, "atr_floor_pct": 0.004},
  "tsmom": {"enabled": true, "leverage": 2, "look_days": 60, "vol_target": 0.028, "vol_days": 30, "rebalance_band": 0.25},
  "xsmom": {"enabled": true, "leverage": 2, "look_days": 60, "k": 2, "vol_target": 0.0285, "vol_days": 30, "rebalance_band": 0.25},
  "catastrophe_stop_vol_mult": 3.0,
  "catastrophe_stop_max_pct": 0.4,
  "history_bars": 450,
  "capital_rebalance": "monthly"
}
```

The per-sleeve `risk` and `vol_target` values scale each sleeve to about 2.1% daily volatility,
so the blend lands near 1.2–1.4% per day. `validate_sleeves` rejects unknown fields and
out-of-range values.

## UI

The Overview campaign panel shows a sleeves table for a `sleeves_v1` experiment. For each sleeve
it lists equity, P&L net of transfers, the coins held long and short, and leverage. It also shows
the last 4h tick, the combined drawdown, and any blocked entries. The data comes from
`/api/campaign` → `sleeves`.

## Research backlog (not built)

Each item must pass the same walk-forward, double-cost and replay checks before it becomes a
sleeve or a filter:

- **Deep-drawdown reversal:** trend-confirmed recoveries from very deep drawdowns, as in NEAR's
  run from under $1 to about $5.
- **Squeeze and range-compression filters for Donchian:** used as filters, not signals.
- **Funding and open-interest crowding filter.**
- **Regime-based sleeve weights.**
- **A radar for the watchlist:** trend state, crowding and squeeze per coin.
