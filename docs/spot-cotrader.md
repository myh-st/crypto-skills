# Spot AI Co-Trader (`spot_cotrader.py`)

Decision support for the user's manual spot trades on Gate. A deterministic daily trend rule gives
each coin a state; a position ladder says how much to hold; AI adds context when something changes.
Everything is PAPER. Nothing here places, amends or cancels an order: the co-trader has no exchange
write path. Market data is public GET only (Gate spot daily candlesticks and tickers).

## Why this product

Short-horizon futures edges fail after costs. A simple, low-frequency spot trend rule on the user's
7 coins does not. On 2023-01 to 2026-09 daily data, with Gate spot fees of 0.1% plus 5 bps:

| Approach | Sharpe | CAGR | Max DD | 2025 |
|---|---|---|---|---|
| Buy & hold, equal weight | 0.78 | +36% | 79% | −63% |
| Hold while the 60-day return > 0 | 1.29 | +58% | 41% | −28% |
| Same, plus close > 100-day average | 1.35 | +57% | 41% | −34% |

The survivorship caveat applies. Decisions are rare (a few per week at most), so real AI analysis is
affordable. The co-trader:

- tells the user the state of each coin, not just a chart;
- calls AI only when something changes, plus a daily briefing and on demand;
- turns everything into one decision card;
- journals the user's own decisions, so discretion can be compared with the rule.

## Hosting

It runs in its own paper-server instance and database, so its AI spend never touches the running
experiments' economics:

```bash
python3 -m crypto_eval paper-server --database ~/paper-cotrader.sqlite3 --port 8771 --cotrader
```

- The experiment in that database stays **stopped**; the co-trader never trades.
- `--cotrader` mounts the `/api/cotrader*` routes and starts a `CoTraderScheduler` thread. Servers
  without the flag behave exactly as before (the routes return 404).
- Providers are configured in that database with the same `provider_id`s as EXP-001
  (`azure-gpt6-luna`, `typesafe-jev`), so the OS credential store resolves the same secrets. No
  secret is copied, logged or returned.
- Co-trader AI spend is recorded in the cost ledger under its own scope (`experiment_id =
  "cotrader"`), so even on a shared database it never mixes with an experiment's economics.
- A fixture provider (`fixture_gpt`, `fixture_jev`) is refused in the server: the co-trader shows
  `AI unavailable: fixture_provider…` instead of presenting fixture text as AI output.

## Universe and data

- Universe: BTC, ETH, NEAR, SEI, SUI, AVAX, ENA (Gate spot `*_USDT`). Configurable with
  `POST /api/cotrader/settings {"universe": [...]}` (2–20 coins), or one coin at a time with the
  Watchlist page (`POST /api/cotrader/watchlist {"add": "SOL"}` / `{"remove": "SEI"}`). An added coin
  must be listed on Gate spot as `<BASE>_USDT` (public tickers; stablecoins are refused), its daily
  history is fetched immediately, and the reply flags a thin coin (24h quote volume below 5M USDT)
  or less than 365 daily bars (no evidence verdict yet). At most 20 coins; at least one stays.
  No exchange account or API key is involved.
- `GateSpotDailySource` reuses `GateSpotMarketDataProvider` (approved-host allowlist, GET only,
  fail-closed errors) for `/spot/candlesticks?interval=1d` and `/spot/tickers`.
- Closed candles are cached in `cotrader_candles`. The first load fetches up to 1,000 days; later
  refreshes refetch only the last 3 days.
- Point-in-time: signals use CLOSED daily bars only. A bar closes at 00:00 UTC. A row that Gate marks
  as still open (`window_closed=false`) or that closes after "now" is never stored. The live price is
  shown separately and never used by a signal.
- A statistic whose window is incomplete or crosses a data gap is `null` with an
  `unavailable_reason`, never zero-filled.

## Signals (deterministic, pre-declared; never tuned from outcomes)

For each coin, at each daily close d:

- `ret_60d = close_d / close_{d-60} - 1`
- `sma100 = mean(close_{d-99..d})`
- `dist_sma100 = close_d / sma100 - 1`
- `vol_20d` = sample standard deviation of the last 20 daily returns
- State: **HOLD** if `ret_60d > 0` and `close_d > sma100`; **CASH** if both are false; otherwise
  **WATCH** (the conditions disagree; the rule holds nothing).
- `state_since` is the date of the close where the current state began; `days_in_state` is derived.
- An event `{coin, from, to, at, close}` is recorded in `cotrader_events` when the state changes. The
  key is (coin, close), so recording is idempotent.

### Trend line (Addendum B)

Tomorrow's close c is HOLD iff `c > close_{d-59}` and `c > sma100_{d+1}`. The SMA includes c, so
`c > (sum(close_{d-98..d}) + c) / 100` is exactly `c > sum(close_{d-98..d}) / 99`. Therefore:

```
trend_line_next = max(close_{d-59}, sum(close_{d-98..d}) / 99)
```

A daily close above it means HOLD tomorrow; at or below it means WATCH or CASH.
`distance_to_trend_line = price / trend_line_next - 1` uses the live price, for display only.
Each `trend_line_series` point is plotted at the open time of the bar it applies to (the close time
of the bar it was computed on).

### Rule backtest

Per coin, on the cached history: hold 100% of the coin while HOLD, otherwise cash. A decision at a
close earns the next bar. Each switch costs 0.1% + 5 bps; buy & hold pays one entry; neither pays a
final exit. It reports `cagr`, `max_dd`, `time_in_market`, `switches`, `bh_cagr`, `bh_max_dd`
(`null` below 60 days), the equity curves, and the rule's historical `trades`. Trade returns are net
of both switch costs.

### Market regime

`breadth` = coins in HOLD / coins with a known state. `RISK_ON` if breadth ≥ 0.6, `RISK_OFF` if
≤ 0.2, otherwise `MIXED`. The BTC state is reported next to it.

## Actions and sizing (Addendum B)

The action comes from the DETERMINISTIC rule and the user's holding (`held`: qty > 0, or `null` when
unknown). AI never changes it.

| State | held | `type` | Text |
|---|---|---|---|
| HOLD | false | `BUY` | In trend. The rule holds this coin. |
| HOLD | true | `HOLD` | Keep. Exit if a daily close is below {trend_line_next}. |
| HOLD | null | `IN` | Rule: hold. |
| not HOLD | true | `SELL` | Out of trend. The rule holds cash. |
| not HOLD | false | `WAIT` | Stay out. Buy if a daily close is above {trend_line_next}. |
| not HOLD | null | `OUT` | Rule: cash. |

`trigger = {kind: close_above|close_below, price: trend_line_next, distance_pct}` and `fresh` is true
when the state changed at the latest close.

Sizing uses `cotrader_capital_usdt` (set by the user, else the Holdings total, value + cash, when the
Gate wallet is synced, else `null`) and `sizing_method`: `equal_weight` (1/N) or `inverse_vol`
(∝ 1/`vol_20d`, normalised). `target_usdt` = weight × capital if HOLD, else 0;
`delta_usdt = target_usdt − current_usdt`; `risk_to_trend_line_usdt = target × max(0, price −
trend_line_next) / price`. Anything that needs an unknown capital or holding is `null`.

Holdings come from `crypto_eval/holdings.py` (manual entries + READ-ONLY Gate spot sync; see its
own doc). `attach_cotrader(runtime)` (used by `paper-server --cotrader`) wires both directions:
`Holdings(rule_state_provider=service.rule_state)` so the holdings view shows `rule_state` and
`alignment`, and the co-trader reads `held(base)`, the holdings payload (`current_usdt`, the capital
default) and `holding_for_ai(base)`. The scheduler calls `holdings.sync_if_due()` on every tick (a
15-minute sync when a Gate account is configured). `held` is `null` while no holdings are known.
Holdings reach an AI context only when `share_holdings_with_ai` is on: Luna gets
`{held, qty, avg_price, unrealized_pct}`, Jev gets `{held, unrealized_pct}`. Tests can inject
`holdings_provider` / `holdings_total_provider` callables instead.

Execution notes (static, rule-derived; also in the response as `execution`): decide on the daily
close (00:00 UTC = 07:00 Bangkok), not intraday wicks; use limit orders near the price and optionally
scale in over 2–3 days; the exit is a daily close below the trend line, not an intraday stop; costs
are 0.1% + ~5 bps; SEI and ENA are thin on Gate.

## Position ladder and CDC reference (Addendum D)

Replay evidence (7 coins, Gate daily 2022-08 to 2026-09, 0.25% per side): the ladder reached Sharpe
1.37 (+62% CAGR, 35.5% max DD) against 1.22 for the plain rule; all 12 lookback/EMA combinations
scored 1.22–1.43. About 20 variants were examined, so expect mild selection bias; the standard 20/20
parameters are used, not the sweep's best.

- `OUT`: the rule is off. `STARTER`: the rule is on (0.5 slot). `FULL`: the rule is on and the close
  is a 20-day closing high; it stays FULL until a close below EMA20 (→ STARTER) or the rule turns off
  (→ OUT). FULL is 2 slots. `slot = capital / N`; if the targets exceed capital they are all scaled
  down proportionally. Spot never uses leverage.
- Transitions: `BUY_STARTER` (OUT → STARTER), `ADD` (→ FULL), `TRIM` (FULL → STARTER), `SELL_ALL`
  (→ OUT), otherwise `HOLD`. The coin carries `ladder_state` and
  `ladder = {state, since, action: {type, text, fresh, target_weight, capital_fraction, target_usdt,
  delta_usdt, delta_qty, add_above, trim_below, exit_below}}`. The Addendum B `action` keeps its own
  shape. `target_weight` is in **slots** (OUT 0, STARTER 0.5, FULL 2, scaled down when the total
  exceeds N slots); `capital_fraction` is the same target as a fraction of capital (≤ 1 in total).
  `add_above`: a close at or above it is a 20-day closing high; `trim_below`: the current EMA20,
  exactly the next close's threshold; `exit_below`: `trend_line_next`. Levels that cannot fire from
  the current state are `null`. `delta_*` uses the holdings when known, otherwise `null`.
- Past transitions are served as `ladder_transitions` (`{at, action, t, from, to, code, close}`,
  marker codes B, A, T, S).
- CDC Action Zone (reference only): `src = ohlc4`, `AP = EMA(src, 2)`, `Fast = EMA(AP, 12)`,
  `Slow = EMA(AP, 26)`; green (`Fast>Slow`, `AP>Fast`), blue (`Fast>Slow`, `AP≤Fast`), yellow
  (`Fast≤Slow`, `AP>Fast`), red (otherwise). EMAs are seeded with the first value; zones start at
  bar 26. The coin gets `cdc: {zone, since}`, the detail gets `cdc_series` (`[[t, zone]]`).
- Per-coin evidence card: `backtest_coin(daily_bars, strategy)` is a pure function for `ladder`,
  `rule`, `cdc_1d` and `buy_hold` on up to 1,000 stored bars, 0.25% per side. Exposure is a fraction
  of the coin's capital: the rule and CDC hold 100%, the ladder 25% as STARTER and 100% as FULL (its
  0.5 : 2 slot ratio without leverage). The card is `{bars, from, to, insufficient_history, ladder,
  rule, cdc_1d, buy_hold, fee_per_side, label, verdict}`; each strategy reports `trades`, `win_rate`,
  `avg_win_pct`, `avg_loss_pct`, `total_return_pct`, `max_dd_pct`, `sharpe`, `time_in_market_pct`
  (fractions). Below 365 bars it returns `insufficient_history: true`, `null` strategies and no
  verdict. UI label: "What would have happened on this
  coin (past, not a promise)".

## AI (cost-controlled)

| Call | Provider | When | Cache key |
|---|---|---|---|
| Jev spot screen (`jev_spot`) | `typesafe-jev` | ~00:05 UTC, every coin | one per coin per close |
| Luna review (`gpt_spot_review`) | `azure-gpt6-luna` | a state change | one per event |
| Luna review | same | Jev answers `rule_agreement=disagree` | one per coin per day |
| Luna review | same | the user clicks Ask AI | ≤ 3 per coin per UTC day, then 409 unless `confirm=true` |
| Luna briefing (`gpt_spot_briefing`) | same | ~00:10 UTC, all coins, with the Jev table | one per UTC day |

- An automatic review of the same coin with the same rule state on the same UTC day reuses the stored
  review and is never re-billed. A manual Ask AI is an explicit, cost-confirmed request for a fresh
  review; the per-coin daily cap limits it instead.
- Jev uses a new question set (`build_spot_jev_questions()`, `jev-spot-questions.v1`):
  `trend_regime`, `trend_strength`, `reversal_risk`, `rule_agreement`, `entry_timing`, `key_risk`.
  `JevAdapter.evaluate_spot(state)` / `evaluate_questions(state, questions, …)` send it; scores are
  mapped to 0..1 as `score / 4`. The state holds the rule state, statistics, the trend line, the last
  60 closes and volumes, and the regime (well under 2k tokens). No account data.
- `ResponsesAdapter.generate_spot_review(context)` and `generate_spot_briefing(context)` return strict
  JSON via `json_schema` (`SPOT_REVIEW_RESPONSE_SCHEMA`, `spot_briefing_response_schema(coins)`).
  They use a compact extract of the crypto-market-trading-analysis skill (discipline sections plus
  the technical-analysis and point-in-time references, ~13 KB instead of ~60 KB). The model writes
  `summary_th` in plain Thai for a non-expert; the parser rejects a summary without Thai text.
- Review context: coin, state, `ret_60d`, `dist_sma100`, `vol_20d`, `trend_line_next`, the last 120
  closed daily candles, the rule stats, the regime and the latest Jev scores. No account data and no
  secrets.
- Budget: every call goes through `PaperRuntime._paid_call` with this database's `ai_budget`, overlaid
  with the co-trader defaults: `daily_usd` 0.50, `experiment_usd` 15.00, `limit_action`
  `BLOCK_PAID_AI`, `unknown_price_policy` `FAIL_CLOSED`, `gpt_max_output_tokens` 4000,
  `jev_max_output_tokens` 1000. `POST /api/cotrader/settings {"ai_budget": {…}}` can override
  `daily_usd`, `experiment_usd`, `gpt_max_output_tokens` and `jev_max_output_tokens`.
- Cost reality at the fallback price book (Luna $15/$60 per M tokens, Jev $5/$20): a Luna review
  reserves ≈ $0.33 worst case (≈ 6k input + 4k output tokens) and costs roughly $0.10–0.20; a Jev
  screen costs about a cent. With `daily_usd` 0.50 that allows the briefing plus one or two Luna
  reviews per day. Raise `daily_usd` if more are wanted.
- A blocked or failed call is stored with its reason (`status` `blocked` or `failed`) and shown as
  "AI unavailable: <reason>". It is never faked or zero-filled.
- Storage: `cotrader_analyses` (coin or `__market__`, `source` `jev`|`luna`, trigger, rule state,
  status, reason, stance, conviction, payload JSON, model, cost, latency, prompt hash, call id, and
  the reference close for the scorecard). Prompts contain no secrets.
- `pricing_is_fallback` is true when the current price-book entry for the Luna or Jev model says it is
  a fallback (not contract pricing). Show that note wherever cost is displayed.

## AI scorecard (Addendum C)

Every stored stance keeps the close it was based on. Once the closed daily bars exist, the scorecard
computes the forward +7d and +30d returns and whether the rule kept its state. Samples are one per
source, coin and close (repeated manual asks are not new evidence). Rows are grouped by source and
stance: Jev `agree`/`caution`/`disagree` and `reversal_risk_low|mid|high` (score ≤ 0.25, between,
≥ 0.75), and Luna `agree`/`caution`/`disagree`. `spreads` gives "agree minus disagree". Every bucket
reports `n`; below `min_samples` (20) the UI must say "too few samples", not a verdict.

## Scheduler

`CoTraderScheduler` polls every 30 s. After each 00:00 UTC close it refreshes candles (from 00:01,
retrying every 2 minutes for up to 30 attempts), records events and runs the state-change reviews;
then Jev at ≥ 00:05 and the Luna briefing at ≥ 00:10. A server started later in the day catches up
once. Every step is idempotent, so a restart never re-bills. It stops with the server.

## Journal

`POST /api/cotrader/journal` records the user's decision, timestamped server-side, with the rule
state and the last Luna stance at that moment. Entries return the live `price_now`, `change_since`
(only when the entry has a price; no P&L is invented) and `rule_agreed` (buy/hold agree with HOLD;
sell/skip agree with CASH or WATCH).

## API contract (JSON; all GET are read-only)

The response schema is `schemas/spot-cotrader.schema.json`; `$defs.coin_detail` and
`$defs.scorecard` cover the other GETs. Ratios are fractions (0.012 = 1.2%), including every
`*_pct` field.

### `GET /api/cotrader`

```json
{
  "schema_version": "spot-cotrader.v1",
  "as_of": "2026-09-29T13:30:00Z",
  "last_close": "2026-09-29T00:00:00Z",
  "next_close": "2026-09-30T00:00:00Z",
  "regime": {"label": "MIXED", "breadth": 0.57, "hold": 4, "total": 7, "btc_state": "HOLD"},
  "briefing": {"as_of": "…", "stance_market": "mixed", "summary_th": "…", "highlights": [{"coin": "NEAR", "note": "…"}], "model": "gpt-6-luna", "cost_usd": 0.02},
  "ai": {"enabled": true, "spent_today_usd": 0.03, "daily_cap_usd": 0.5, "spent_total_usd": 0.41, "total_cap_usd": 15.0, "blocked_reason": null,
         "jev_calls_today": 7, "luna_calls_today": 1, "jev_spent_usd": 0.01, "luna_spent_usd": 0.02, "pricing_is_fallback": true},
  "settings": {"cotrader_capital_usdt": 1000.0, "capital_source": "user", "sizing_method": "equal_weight"},
  "coins": [
    {"symbol": "BTC_USDT", "base": "BTC", "price": 84210.5, "change_24h": 0.012,
     "state": "HOLD", "state_since": "2026-09-12", "days_in_state": 17, "changed_today": false,
     "ret_60d": 0.084, "dist_sma100": 0.031, "vol_20d": 0.021,
     "rule": {"cagr": 0.41, "max_dd": 0.33, "time_in_market": 0.56, "switches": 18, "bh_cagr": 0.29, "bh_max_dd": 0.52},
     "spark": [[1780012800, 83110.2]],
     "last_ai": {"as_of": "…", "trigger": "state_change", "stance": "agree", "conviction": "medium", "summary_th": "…"},
     "event": {"from": "WATCH", "to": "HOLD", "at": "2026-09-12T00:00:00Z"},
     "trend_line_next": 80120.4, "distance_to_trend_line": 0.051,
     "action": {"type": "IN", "text": "Rule: hold.", "fresh": false,
                "trigger": {"kind": "close_below", "price": 80120.4, "distance_pct": 0.051}},
     "ladder": {"state": "FULL", "since": "2026-09-20",
                "action": {"type": "HOLD", "text": "Hold (FULL).", "fresh": false, "target_weight": 2.0,
                           "capital_fraction": 0.2857, "target_usdt": 285.71, "delta_usdt": null, "delta_qty": null,
                           "add_above": null, "trim_below": 82950.1, "exit_below": 80120.4}},
     "sizing": {"method": "equal_weight", "weight": 0.1429, "target_usdt": 142.86, "current_usdt": null, "delta_usdt": null,
                "delta_qty": null, "risk_to_trend_line_usdt": 7.3, "scale_in": null},
     "held": null, "ladder_state": "FULL", "cdc": {"zone": "green", "since": "2026-09-20"},
     "jev": {"as_of": "…", "trend_regime": "uptrend", "trend_strength": 0.75, "reversal_risk": 0.25, "rule_agreement": "agree",
             "entry_timing": "wait_pullback", "key_risk": "none"},
     "data_through": "2026-09-29T00:00:00Z", "unavailable_reason": null}
  ],
  "execution": {"decide_at": "…", "order_type": "…", "exit": "…", "fee_rate": 0.001, "slippage_bps": 5.0, "thin_liquidity": ["SEI", "ENA"]},
  "disclaimer": "…"
}
```

`spark` holds the last 90 closes as `[unix_seconds, close]` (t = bar open time). `briefing`,
`last_ai`, `event`, `jev`, `cdc`, `action`, `ladder` and `sizing` may be `null`.

### `GET /api/cotrader/<BASE>` (e.g. `/api/cotrader/BTC`)

The coin object above plus `candles` (≈400 closed bars `[t, o, h, l, c, v]`), `sma100`, `periods`
(`{from, to, state}` over the same span), `equity` (`rule`, `buy_hold`), `analyses` (newest first;
Luna entries carry the Thai text, Jev entries carry `answers`), `journal`, `trend_line_series`,
`trades` (`{entry_at, entry_t, entry_price, exit_at, exit_t, exit_price, return_pct, days}`, open
trades have null exits), `ladder_transitions`, `cdc_series`, `evidence` and `events`.

### `POST /api/cotrader/<BASE>/analyze`

Body `{"confirm": false}` (optional). Returns `{"analysis": {...}}`; HTTP 409
`{"confirmation_required": true, "reason": "manual_cap"}` after 3 manual reviews of that coin today;
or HTTP 200 `{"analysis": null, "blocked_reason": "..."}` when the budget or provider blocks the call.

### `POST /api/cotrader/journal`

Body `{coin, action: "buy"|"sell"|"hold"|"skip", price: number|null, amount_usdt: number|null,
note: str ≤ 500}`. Returns `{"entry": {...}}`. `GET /api/cotrader/journal` returns
`{"journal": [...]}` for all coins.

### `GET /api/cotrader/scorecard`

`{as_of, horizons: [7, 30], min_samples, rows: [{source, stance, n_7d, avg_ret_7d, hit_7d,
rule_same_7d, n_30d, avg_ret_30d, hit_30d, rule_same_30d}], spreads, cost: {jev_usd, luna_usd,
calls: {jev, luna}}, note_pricing_fallback}`.

### `GET|POST /api/cotrader/settings`

POST body: any of `{cotrader_capital_usdt: number|null, sizing_method: "equal_weight"|"inverse_vol",
universe: [bases], jev_scoring: bool, ai_budget: {daily_usd, experiment_usd, gpt_max_output_tokens,
jev_max_output_tokens}}`. Returns `{settings}`. POST routes keep the server's origin validation.

## Non-negotiables

- Nothing here places, amends or cancels orders. The co-trader has no exchange write path.
- CI and unit tests are offline: fixture adapters and faked HTTP, no network, no paid calls
  (`tests/test_spot_cotrader.py`).
- Secrets never reach SQLite, prompts, logs or responses. Loopback only.
- Closed bars only for signals. Missing data is `null` with a reason, never zero-filled.
- AI output is advisory text. It never changes a rule state, a ladder state or any position.


## Web app (co-trader mode)

`/api/health` reports `"cotrader": true` on a `--cotrader` server. The frontend then becomes the
Spot Co-Trader app: the navigation shows only **Signals**, **Watchlist** and **AI · Settings**, the
old futures lab menus sit under a collapsed "Futures lab (old)" group, `#/cotrader` is the default
route and the futures runtime stream is not opened. Servers without the flag keep the lab navigation.

- **"ทำอะไรต่อ" (what to do next).** A short Thai summary at the top of Signals. It is built only
  from the ladder: today's fresh actions (start / add / trim / sell all, with the USDT amount when
  spot capital is set), what to keep holding (full / half), what stays in cash, and the three
  nearest trigger levels. It uses no AI text, so it is always available and never billed.
- **Holdings are not shown.** Profit and position tracking stay in the user's own exchange app; the
  co-trader follows the watchlist. The Holdings backend (`docs/holdings.md`) remains available but
  is dormant without a stored key.
- **Theme.** Dark by default; the topbar toggle stores `light`/`dark` in this browser only
  (`frontend/theme.js`, loaded in `<head>` so there is no flash). Charts read the theme colours.

AI retries: a Jev/Luna attempt blocked by the **budget** is final for its trigger (never re-billed),
but one blocked by **provider setup** (`fixture_provider`, `provider_unavailable`,
`provider_unsupported`) is retried after the normal 10-minute spacing, so configuring the real
providers scores the same close without a restart.


## Claude routine narrator (no per-call API cost)

`POST /api/cotrader/settings {"narrator": "claude_routine"}` switches the daily narration from Luna
to the user's own scheduled Claude routine (Claude Code desktop scheduled task on the user's plan):

- **Luna is off.** No Luna call is made or recorded: state-change reviews, the Jev-disagree
  escalation, the daily briefing and manual "Ask AI" all return `luna_off`. The UI hides the Ask AI
  button and says the routine updates each morning. Jev keeps scoring (it is cheap); turn it off
  with `{"jev_scoring": false}`.
- **`GET /api/cotrader/routine/context`** (read-only) returns, for the latest common close, the
  same typed inputs Luna receives (`build_spot_review_input` per coin plus the coin's ladder, and
  `build_spot_briefing_input`), the shared instructions, the JSON output contracts and the publish
  shape. Only closed bars are included.
- **`POST /api/cotrader/routine/publish`** `{model, data_cutoff, briefing, reviews: {BASE: review}}`
  is validated by the same parsers as Luna (`parse_spot_briefing`, `parse_spot_review`: Thai summary
  required, bounded lists, positive levels). A stale `data_cutoff`, an unknown coin or an invalid
  field rejects the whole publish (HTTP 400). Rows are stored as `source: "claude"`, `trigger:
  "routine"`, `cost_status: "subscription"`, and re-publishing the same close replaces them.
- The UI, journal and AI Scorecard treat `claude` as the narrative AI (newest of claude/luna), and
  the scorecard tracks Claude's stances separately.
- The routine only runs while the Claude desktop app is running and the Mac is awake; a missed
  morning simply leaves yesterday's narration (the rule, ladder and Jev still update on their own).
