"""Spot AI Co-Trader: a daily spot trend rule, a position ladder, and cost-controlled AI context.

PAPER / decision support only. The user places spot trades manually on Gate. Nothing in this module
places, amends or cancels an order: the co-trader has no exchange write path. Market data comes from
unauthenticated public GETs (Gate spot daily candlesticks and tickers).

Signals are deterministic and pre-declared, computed on CLOSED daily bars only (a bar closes at
00:00 UTC). The live price is shown next to them for display and is never used by a signal.

  rule     HOLD if ret_60d > 0 and close > SMA100; CASH if both fail; otherwise WATCH.
  ladder   OUT when the rule is off; STARTER (0.5 slot) when it is on; FULL (2 slots) after a 20-day
           closing high, back to STARTER when a close falls below EMA20. Total capped at 100%.
  cdc      CDC Action Zone colour (reference only).

AI never changes a rule state, a ladder state or a position. Jev (typed scores) screens every coin
once per close; Luna (Responses + the crypto-market-trading-analysis skill) explains on a state
change, on a Jev "disagree", on the daily briefing, and on demand. Every call goes through
``PaperRuntime._paid_call`` with the co-trader budget; blocked or failed calls are recorded with
their reason and are never faked.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .ai_cost import validate_budget_config
from .market_catalog import GateSpotMarketDataProvider, SPOT_DATA_ORIGIN, _num
from .paper_ai import (
    AIProviderError,
    SPOT_REVIEW_RESPONSE_SCHEMA,
    build_spot_briefing_input,
    build_spot_review_input,
    jev_score_unit,
    parse_spot_briefing,
    parse_spot_review,
    spot_briefing_instructions,
    spot_briefing_response_schema,
    spot_review_instructions,
)
from .paper_contracts import PaperTradingError, iso_utc
from .paper_market import MarketDataError

SCHEMA_VERSION = "spot-cotrader.v1"
DAY = 86400
LEDGER_SCOPE = "cotrader"  # cost-ledger scope: co-trader spend never mixes with an experiment's economics
MARKET_COIN = "__market__"
DEFAULT_UNIVERSE = ("BTC", "ETH", "NEAR", "SEI", "SUI", "AVAX", "ENA")
STATES = ("HOLD", "CASH", "WATCH")
LADDER_STATES = ("OUT", "STARTER", "FULL")
LADDER_SLOTS = {"OUT": 0.0, "STARTER": 0.5, "FULL": 2.0}
LADDER_CODES = {"BUY_STARTER": "B", "ADD": "A", "TRIM": "T", "SELL_ALL": "S"}
CDC_ZONES = ("green", "blue", "yellow", "red")
SIZING_METHODS = ("equal_weight", "inverse_vol")
STRATEGIES = ("ladder", "rule", "cdc_1d", "buy_hold")
JOURNAL_ACTIONS = ("buy", "sell", "hold", "skip")
TRIGGERS = ("state_change", "daily", "manual", "jev_disagree")
FEE_RATE = 0.001  # Gate spot taker fee
SLIPPAGE = 0.0005  # 5 bps
SWITCH_COST = FEE_RATE + SLIPPAGE
EVIDENCE_FEE = 0.0025  # per side, the replay-evidence cost (Addendum D)
EVIDENCE_MIN_BARS = 365
EVIDENCE_MAX_BARS = 1000
EVIDENCE_LADDER_EXPOSURE = {"OUT": 0.0, "STARTER": 0.25, "FULL": 1.0}  # 0.5 : 2 slots, FULL = 100%
INITIAL_HISTORY_DAYS = 1000
REFETCH_DAYS = 3
CANDLE_VIEW_BARS = 400
SPARK_BARS = 90
AI_CONTEXT_BARS = 120
JEV_CONTEXT_BARS = 60
MIN_BACKTEST_DAYS = 60
MANUAL_DAILY_CAP = 3
MAX_FAILED_ATTEMPTS = 3
RETRY_SPACING_SECONDS = 600
TICKER_TTL_SECONDS = 30
SCORECARD_HORIZONS = (7, 30)
SCORECARD_MIN_SAMPLES = 20
THIN_LIQUIDITY = ("SEI", "ENA")
STABLECOINS = ("USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE")
MAX_UNIVERSE = 20
PROVIDER_SETUP_REASONS = ("fixture_provider", "provider_unavailable", "provider_unsupported")
THIN_QUOTE_VOLUME_USDT = 5_000_000.0  # below this 24h Gate spot volume, fills can slip; warn
BASE_RE = re.compile(r"[A-Z0-9]{1,20}")
COTRADER_BUDGET_DEFAULTS: dict[str, Any] = {
    "daily_usd": 0.5,
    "experiment_usd": 15.0,
    "limit_action": "BLOCK_PAID_AI",
    "unknown_price_policy": "FAIL_CLOSED",
    # The worst-case reservation is input + max output at list price; small caps keep a Luna review
    # reservable inside the 0.50 USD day. A structured review needs far fewer tokens than this.
    "gpt_max_output_tokens": 4000,
    "jev_max_output_tokens": 1000,
}
# Who writes the daily narration (briefing + per-coin reviews):
#   "luna"            the paid Responses provider, triggered by the scheduler and on demand;
#   "claude_routine"  the user's own scheduled Claude routine reads GET /api/cotrader/routine/context and
#                     publishes validated JSON to POST /api/cotrader/routine/publish (no per-call API cost).
NARRATORS = ("luna", "claude_routine")
NARRATION_SOURCES = ("claude", "luna")  # sources the UI treats as the narrative AI, newest first
ROUTINE_SCHEMA_VERSION = "cotrader-routine.v1"
LUNA_OFF_REASON = "luna_off: the Claude routine writes the analysis once a day"
BUDGET_OVERRIDE_FIELDS = ("daily_usd", "experiment_usd", "gpt_max_output_tokens", "jev_max_output_tokens")
LUNA_CALL_TYPES = ("gpt_spot_review", "gpt_spot_briefing")
JEV_CALL_TYPE = "jev_spot"
DISCLAIMER = (
    "PAPER decision support from a deterministic rule, not investment advice. "
    "You decide and trade manually; nothing here places an order."
)

COTRADER_SCHEMA = """
CREATE TABLE IF NOT EXISTS cotrader_candles(
    base TEXT NOT NULL,
    t INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    data_origin TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY(base, t)
);
CREATE TABLE IF NOT EXISTS cotrader_events(
    base TEXT NOT NULL,
    at TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    close REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY(base, at)
);
CREATE TABLE IF NOT EXISTS cotrader_analyses(
    analysis_id TEXT PRIMARY KEY,
    coin TEXT NOT NULL,
    source TEXT NOT NULL,
    trigger TEXT NOT NULL,
    trigger_key TEXT NOT NULL,
    rule_state TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    stance TEXT,
    conviction TEXT,
    payload_json TEXT,
    model TEXT,
    cost_usd REAL,
    cost_status TEXT,
    latency_ms REAL,
    prompt_hash TEXT,
    call_id TEXT NOT NULL,
    ref_close REAL,
    ref_close_ts INTEGER,
    as_of TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cotrader_analyses_coin_idx ON cotrader_analyses(coin, as_of);
CREATE INDEX IF NOT EXISTS cotrader_analyses_key_idx ON cotrader_analyses(trigger_key);
CREATE TABLE IF NOT EXISTS cotrader_journal(
    entry_id TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    coin TEXT NOT NULL,
    action TEXT NOT NULL,
    price REAL,
    amount_usdt REAL,
    note TEXT NOT NULL,
    rule_state TEXT,
    ai_stance TEXT
);
CREATE TABLE IF NOT EXISTS cotrader_settings(
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cotrader_runs(
    run_key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    last_attempt_at TEXT,
    detail_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class CoTraderConfirmationRequired(PaperTradingError):
    """A manual review past the per-coin daily cap needs an explicit confirm=true."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CoTraderNotFound(PaperTradingError):
    """The coin is not in the co-trader universe."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _iso(ts: int | float) -> str:
    return iso_utc(datetime.fromtimestamp(int(ts), tz=timezone.utc)).replace(".000Z", "Z")


def _date(ts: int | float) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()


def _r(value: Any, digits: int = 6) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _floor_day(now: datetime) -> int:
    epoch = int(now.timestamp())
    return epoch - epoch % DAY


def normalize_base(value: Any) -> str:
    if not isinstance(value, str):
        raise PaperTradingError("coin must be a string such as BTC or BTC_USDT")
    base = value.strip().upper()
    for suffix in ("_USDT", "USDT"):
        if base.endswith(suffix) and len(base) > len(suffix):
            base = base[: -len(suffix)]
            break
    if not BASE_RE.fullmatch(base):
        raise PaperTradingError("coin must be uppercase letters and digits")
    return base


def fmt_price(value: float | None) -> str:
    if value is None:
        return "the trend line"
    if value >= 1000:
        return f"{value:,.1f}"
    if value >= 1:
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return f"{value:.6g}"


def ema_series(values: list[float], n: int) -> list[float]:
    """EMA seeded with the first value (the same convention as the research replay)."""

    alpha = 2 / (n + 1)
    out: list[float] = []
    current: float | None = None
    for value in values:
        current = value if current is None else alpha * value + (1 - alpha) * current
        out.append(current)
    return out


def _contiguous(times: list[int], i: int, n: int) -> bool:
    return i >= n and times[i] - times[i - n] == n * DAY


def _candle_rows(daily_bars: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for bar in daily_bars:
        if isinstance(bar, dict):
            rows.append({"t": int(bar["t"]), "o": float(bar["o"]), "h": float(bar["h"]), "l": float(bar["l"]),
                         "c": float(bar["c"]), "v": float(bar.get("v", 0.0))})
        else:
            rows.append({"t": int(bar[0]), "o": float(bar[1]), "h": float(bar[2]), "l": float(bar[3]),
                         "c": float(bar[4]), "v": float(bar[5]) if len(bar) > 5 else 0.0})
    return sorted(rows, key=lambda row: row["t"])


# ---------------------------------------------------------------------------
# deterministic signal math (pure functions)
# ---------------------------------------------------------------------------


def signal_rows(candles: list[Any]) -> list[dict[str, Any]]:
    """Per closed daily bar: rule inputs and state, trend line, ladder inputs, CDC zone.

    A statistic whose window is incomplete or crosses a data gap is None, never zero-filled."""

    bars = _candle_rows(candles)
    times = [bar["t"] for bar in bars]
    closes = [bar["c"] for bar in bars]
    ema20 = ema_series(closes, 20)
    ohlc4 = [(bar["o"] + bar["h"] + bar["l"] + bar["c"]) / 4 for bar in bars]
    ap = ema_series(ohlc4, 2)
    fast = ema_series(ap, 12)
    slow = ema_series(ap, 26)
    rows: list[dict[str, Any]] = []
    for i, bar in enumerate(bars):
        close = closes[i]
        ret_60d = close / closes[i - 60] - 1 if _contiguous(times, i, 60) else None
        sma100 = sum(closes[i - 99 : i + 1]) / 100 if _contiguous(times, i, 99) else None
        dist = close / sma100 - 1 if sma100 else None
        vol = None
        if _contiguous(times, i, 20):
            returns = [closes[j] / closes[j - 1] - 1 for j in range(i - 19, i + 1)]
            mean = sum(returns) / 20
            vol = math.sqrt(sum((value - mean) ** 2 for value in returns) / 19)  # sample standard deviation
        state = None
        if ret_60d is not None and sma100 is not None:
            up, above = ret_60d > 0, close > sma100
            state = "HOLD" if up and above else "CASH" if not up and not above else "WATCH"
        # For tomorrow's close c: ret_60d > 0 <=> c > close[i-59]; c > SMA100 <=> c > sum(close[i-98..i]) / 99.
        trend_line = (
            max(closes[i - 59], sum(closes[i - 98 : i + 1]) / 99) if _contiguous(times, i, 98) else None
        )
        hi20 = close >= max(closes[i - 19 : i + 1]) if _contiguous(times, i, 19) else None
        # Tomorrow's close is a 20-day closing high iff it is >= the highest of the latest 19 closes.
        add_above = max(closes[i - 18 : i + 1]) if _contiguous(times, i, 18) else None
        zone = None
        if i >= 26:
            bull = fast[i] > slow[i]
            above_fast = ap[i] > fast[i]
            zone = ("green" if above_fast else "blue") if bull else ("yellow" if above_fast else "red")
        rows.append({
            **bar,
            "close_ts": bar["t"] + DAY,
            "close": close,
            "ret_60d": ret_60d,
            "sma100": sma100,
            "dist_sma100": dist,
            "vol_20d": vol,
            "state": state,
            "trend_line_next": trend_line,
            "ema20": ema20[i],
            "hi20": hi20,
            "add_above": add_above,
            "cdc": zone,
        })
    ladder = ladder_states(rows)
    for row, value in zip(rows, ladder):
        row["ladder"] = value
    return rows


def ladder_states(rows: list[dict[str, Any]]) -> list[str | None]:
    """OUT / STARTER / FULL per closed bar (sticky FULL until a close below EMA20 or the rule is off)."""

    out: list[str | None] = []
    full = False
    for row in rows:
        if row["state"] is None:
            out.append(None)
            full = False
            continue
        if row["state"] != "HOLD":
            out.append("OUT")
            full = False
            continue
        if row["hi20"]:
            full = True
        if row["c"] < row["ema20"]:
            full = False
        out.append("FULL" if full else "STARTER")
    return out


def ladder_action(previous: str | None, current: str | None) -> str | None:
    if current is None:
        return None
    if previous is None or previous == current:
        return "HOLD"
    if current == "OUT":
        return "SELL_ALL"
    if current == "FULL":
        return "ADD"
    if previous == "FULL":
        return "TRIM"
    return "BUY_STARTER"


def ladder_transitions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    previous = None
    for row in rows:
        current = row["ladder"]
        if previous is not None and current is not None and current != previous:
            action = ladder_action(previous, current)
            result.append({
                "at": _iso(row["close_ts"]), "t": row["t"], "from": previous, "to": current,
                "action": action, "code": LADDER_CODES[action], "close": row["close"],
            })
        previous = current
    return result


def ladder_weights(states: dict[str, str | None], universe: list[str]) -> dict[str, float | None]:
    """Target weight of capital per coin: slot = 1/N; STARTER 0.5 slot, FULL 2 slots; total <= 100%."""

    n = len(universe)
    raw = {base: (None if states.get(base) is None else LADDER_SLOTS[states[base]] / n) for base in universe}
    total = sum(value for value in raw.values() if value is not None)
    scale = 1 / total if total > 1 else 1.0
    return {base: (None if value is None else value * scale) for base, value in raw.items()}


def state_periods(rows: list[dict[str, Any]], key: str = "state") -> list[dict[str, Any]]:
    periods: list[dict[str, Any]] = []
    for row in rows:
        value = row[key]
        if value is None:
            continue
        day = _date(row["close_ts"])
        if periods and periods[-1]["state"] == value and periods[-1]["_last"] == row["t"] - DAY:
            periods[-1]["to"] = day
            periods[-1]["_last"] = row["t"]
        else:
            periods.append({"from": day, "to": day, "state": value, "_last": row["t"]})
    return [{key_: value for key_, value in period.items() if key_ != "_last"} for period in periods]


def state_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    previous = None
    for row in rows:
        if row["state"] is None:
            previous = None
            continue
        if previous is not None and row["state"] != previous:
            events.append({"from": previous, "to": row["state"], "at": _iso(row["close_ts"]), "close": row["close"]})
        previous = row["state"]
    return events


def _since(rows: list[dict[str, Any]], key: str) -> str | None:
    if not rows or rows[-1][key] is None:
        return None
    current = rows[-1][key]
    start = rows[-1]
    for row in reversed(rows):
        if row[key] != current:
            break
        start = row
    return _date(start["close_ts"])


def _max_dd(curve: list[float]) -> float:
    peak, worst = -math.inf, 0.0
    for value in curve:
        peak = max(peak, value)
        worst = max(worst, 1 - value / peak if peak > 0 else 0.0)
    return worst


def rule_backtest(rows: list[dict[str, Any]], *, cost: float = SWITCH_COST) -> dict[str, Any]:
    """Hold 100% of the coin while HOLD, otherwise cash; cost per switch. Buy & hold pays one entry.

    Decisions happen at a close and earn the next bar's return. Neither curve pays a final exit."""

    indices = [i for i, row in enumerate(rows) if row["state"] is not None]
    empty = {"stats": None, "reason": "insufficient_history", "equity": {"rule": [], "buy_hold": []}, "trades": []}
    if not indices:
        return empty
    i0 = indices[0]
    span_days = (rows[-1]["t"] - rows[i0]["t"]) // DAY
    equity = 1.0
    position = 0
    switches = 0
    held = 0
    rule_curve: list[list[float]] = []
    bh_curve: list[list[float]] = []
    trades: list[dict[str, Any]] = []
    base_close = rows[i0]["close"]

    def decide(i: int) -> None:
        nonlocal equity, position, switches
        target = 1 if rows[i]["state"] == "HOLD" else 0
        if target == position:
            return
        equity *= 1 - cost
        switches += 1
        if target:
            trades.append({"entry_at": _iso(rows[i]["close_ts"]), "entry_t": rows[i]["t"],
                           "entry_price": rows[i]["close"], "_i": i})
        else:
            trade = trades[-1]
            trade["exit_at"] = _iso(rows[i]["close_ts"])
            trade["exit_t"] = rows[i]["t"]
            trade["exit_price"] = rows[i]["close"]
            trade["return_pct"] = _r(rows[i]["close"] / trade["entry_price"] * (1 - cost) ** 2 - 1)
            trade["days"] = (rows[i]["t"] - rows[trade["_i"]]["t"]) // DAY
        position = target

    decide(i0)
    rule_curve.append([rows[i0]["t"], equity])
    bh_curve.append([rows[i0]["t"], 1 - cost])
    for i in range(i0 + 1, len(rows)):
        if position:
            equity *= rows[i]["close"] / rows[i - 1]["close"]
            held += 1
        decide(i)
        rule_curve.append([rows[i]["t"], equity])
        bh_curve.append([rows[i]["t"], (1 - cost) * rows[i]["close"] / base_close])
    for trade in trades:
        if "exit_at" not in trade:
            trade.update({"exit_at": None, "exit_t": None, "exit_price": None, "return_pct": None,
                          "days": (rows[-1]["t"] - rows[trade["_i"]]["t"]) // DAY})
        trade.pop("_i")
    curves = {
        "rule": [[t, _r(v)] for t, v in rule_curve],
        "buy_hold": [[t, _r(v)] for t, v in bh_curve],
    }
    intervals = len(rows) - 1 - i0
    if span_days < MIN_BACKTEST_DAYS or intervals <= 0:
        return {"stats": None, "reason": "insufficient_history", "equity": curves, "trades": trades}
    years = span_days / 365
    stats = {
        "cagr": _r(rule_curve[-1][1] ** (1 / years) - 1),
        "max_dd": _r(_max_dd([v for _, v in rule_curve])),
        "time_in_market": _r(held / intervals),
        "switches": switches,
        "bh_cagr": _r(bh_curve[-1][1] ** (1 / years) - 1),
        "bh_max_dd": _r(_max_dd([v for _, v in bh_curve])),
    }
    return {"stats": stats, "reason": None, "equity": curves, "trades": trades}


def _exposure(rows: list[dict[str, Any]], strategy: str) -> list[float]:
    if strategy == "buy_hold":
        return [1.0] * len(rows)
    if strategy == "rule":
        return [1.0 if row["state"] == "HOLD" else 0.0 for row in rows]
    if strategy == "ladder":
        return [EVIDENCE_LADDER_EXPOSURE.get(row["ladder"], 0.0) if row["ladder"] else 0.0 for row in rows]
    if strategy == "cdc_1d":
        result, position = [], 0.0
        for row in rows:
            if row["cdc"] == "green":
                position = 1.0
            elif row["cdc"] == "red":
                position = 0.0
            result.append(position if row["cdc"] is not None else 0.0)
        return result
    raise PaperTradingError("strategy must be ladder, rule, cdc_1d, or buy_hold")


def backtest_coin(
    daily_bars: list[Any], strategy: str, *, fee: float = EVIDENCE_FEE, min_bars: int = EVIDENCE_MIN_BARS
) -> dict[str, Any]:
    """Single-coin replay of one strategy on closed daily bars ([t, o, h, l, c, v] or dicts).

    A signal at a close is held over the next bar. ``fee`` is charged per side on every change of
    exposure (buy & hold pays its entry). Exposure is a fraction of the coin's capital: the rule and
    CDC hold 100%; the ladder holds 25% as STARTER and 100% as FULL (its 0.5 : 2 slot ratio without
    leverage). All ``*_pct`` values are fractions (0.12 = 12%). Fewer than 365 bars returns
    ``insufficient_history`` and no metrics."""

    if strategy not in {"ladder", "rule", "cdc_1d", "buy_hold"}:
        raise PaperTradingError("strategy must be ladder, rule, cdc_1d, or buy_hold")
    return _backtest_rows(signal_rows(daily_bars), strategy, fee, min_bars)


def _backtest_rows(
    rows: list[dict[str, Any]], strategy: str, fee: float, min_bars: int = EVIDENCE_MIN_BARS
) -> dict[str, Any]:
    span = {
        "strategy": strategy,
        "bars": len(rows),
        "from": _date(rows[0]["t"]) if rows else None,
        "to": _date(rows[-1]["close_ts"]) if rows else None,
    }
    if len(rows) < max(2, min_bars):
        return {**span, "insufficient_history": True}
    exposure = _exposure(rows, strategy)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    returns: list[float] = []
    in_market = 0
    trades: list[float] = []
    trade_start: float | None = None
    for i in range(1, len(rows)):
        held = exposure[i - 1]
        before = exposure[i - 2] if i >= 2 else 0.0
        if held > 0 and before == 0:
            trade_start = equity
        daily = held * (rows[i]["c"] / rows[i - 1]["c"] - 1) - fee * abs(held - before)
        equity *= 1 + daily
        returns.append(daily)
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
        in_market += held > 0
        if held == 0 and before > 0 and trade_start is not None:
            trades.append(equity / trade_start - 1)
            trade_start = None
    if trade_start is not None:
        trades.append(equity / trade_start - 1)
    wins = [value for value in trades if value > 0]
    losses = [value for value in trades if value <= 0]
    mean = sum(returns) / len(returns)
    sd = math.sqrt(sum((value - mean) ** 2 for value in returns) / len(returns))
    return {
        **span,
        "insufficient_history": False,
        "trades": len(trades),
        "win_rate": _r(len(wins) / len(trades)) if trades else None,
        "avg_win_pct": _r(sum(wins) / len(wins)) if wins else None,
        "avg_loss_pct": _r(sum(losses) / len(losses)) if losses else None,
        "total_return_pct": _r(equity - 1),
        "max_dd_pct": _r(max_dd),
        "sharpe": _r(mean / sd * math.sqrt(365), 4) if sd > 0 else None,
        "time_in_market_pct": _r(in_market / len(returns)),
    }


def evidence_card(daily_bars: list[Any]) -> dict[str, Any]:
    rows = signal_rows(_candle_rows(daily_bars)[-EVIDENCE_MAX_BARS:])
    results = {name: _backtest_rows(rows, name, EVIDENCE_FEE) for name in STRATEGIES}
    first = results["buy_hold"]
    card: dict[str, Any] = {
        "label": "What would have happened on this coin (past, not a promise)",
        "bars": first["bars"],
        "from": first["from"],
        "to": first["to"],
        "fee_per_side": EVIDENCE_FEE,
        "insufficient_history": first["insufficient_history"],
        **{name: None for name in STRATEGIES},
        "verdict": None,
    }
    if first["insufficient_history"]:
        return card
    keys = ("trades", "win_rate", "avg_win_pct", "avg_loss_pct", "total_return_pct", "max_dd_pct", "sharpe",
            "time_in_market_pct")
    for name, result in results.items():
        card[name] = {key: result[key] for key in keys}
    ranked = [(name, card[name]["sharpe"]) for name in STRATEGIES if card[name]["sharpe"] is not None]
    card["verdict"] = {"best_sharpe": max(ranked, key=lambda item: item[1])[0]} if ranked else None
    return card


def market_regime(states: dict[str, str | None], btc_state: str | None) -> dict[str, Any]:
    known = [state for state in states.values() if state is not None]
    hold = sum(1 for state in known if state == "HOLD")
    breadth = hold / len(known) if known else None
    label = None
    if breadth is not None:
        label = "RISK_ON" if breadth >= 0.6 else "RISK_OFF" if breadth <= 0.2 else "MIXED"
    return {"label": label, "breadth": _r(breadth, 4), "hold": hold, "total": len(known), "btc_state": btc_state}


def sizing_weights(method: str, vols: dict[str, float | None], universe: list[str]) -> dict[str, float | None]:
    if method == "equal_weight":
        return {base: 1 / len(universe) for base in universe}
    inverse = {base: 1 / vol for base, vol in vols.items() if base in universe and vol}
    total = sum(inverse.values())
    return {base: (inverse[base] / total if base in inverse and total else None) for base in universe}


def rule_action(state: str | None, held: bool | None, trend_line: float | None, distance: float | None,
                fresh: bool) -> dict[str, Any] | None:
    if state is None:
        return None
    level = fmt_price(trend_line)
    if state == "HOLD":
        kind = "close_below"
        action, text = {
            False: ("BUY", "In trend. The rule holds this coin."),
            True: ("HOLD", f"Keep. Exit if a daily close is below {level}."),
            None: ("IN", "Rule: hold."),
        }[held]
    else:
        kind = "close_above"
        action, text = {
            True: ("SELL", "Out of trend. The rule holds cash."),
            False: ("WAIT", f"Stay out. Buy if a daily close is above {level}."),
            None: ("OUT", "Rule: cash."),
        }[held]
    return {"type": action, "text": text, "fresh": fresh,
            "trigger": {"kind": kind, "price": _r(trend_line, 10), "distance_pct": _r(distance)}}


LADDER_TEXT = {
    "BUY_STARTER": "Rule turned on: buy a starter position (half a slot).",
    "ADD": "20-day closing high in an uptrend: add up to the full position (two slots).",
    "TRIM": "Cut back to starter, trend still up (the close fell below EMA20).",
    "SELL_ALL": "Rule turned off: sell the whole position.",
}


def scorecard_rows(samples: list[dict[str, Any]], horizons: tuple[int, ...] = SCORECARD_HORIZONS) -> list[dict[str, Any]]:
    """Aggregate forward returns by (source, stance); ``n`` counts samples whose horizon has closed."""

    order = [("jev", "agree"), ("jev", "caution"), ("jev", "disagree"), ("jev", "reversal_risk_low"),
             ("jev", "reversal_risk_mid"), ("jev", "reversal_risk_high"), ("luna", "agree"), ("luna", "caution"),
             ("luna", "disagree"), ("claude", "agree"), ("claude", "caution"),
             ("claude", "disagree")]
    rows = []
    for source, stance in order:
        bucket = [sample for sample in samples if sample["source"] == source and sample["stance"] == stance]
        row: dict[str, Any] = {"source": source, "stance": stance}
        for horizon in horizons:
            values = [sample[f"fwd_{horizon}d"] for sample in bucket if sample.get(f"fwd_{horizon}d") is not None]
            same = [sample[f"rule_same_{horizon}d"] for sample in bucket if sample.get(f"rule_same_{horizon}d") is not None]
            row[f"n_{horizon}d"] = len(values)
            row[f"avg_ret_{horizon}d"] = _r(sum(values) / len(values)) if values else None
            row[f"hit_{horizon}d"] = _r(sum(1 for value in values if value > 0) / len(values)) if values else None
            row[f"rule_same_{horizon}d"] = _r(sum(1 for value in same if value) / len(same)) if same else None
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# market data (public GET only)
# ---------------------------------------------------------------------------


class GateSpotDailySource:
    """Closed Gate spot daily candles and tickers through the existing public spot provider."""

    data_origin = SPOT_DATA_ORIGIN

    def __init__(self, provider: GateSpotMarketDataProvider | None = None) -> None:
        self.provider = provider or GateSpotMarketDataProvider()

    def daily_candles(self, pair: str, *, start_ts: int, now: datetime) -> list[dict[str, Any]]:
        now_ts = int(now.timestamp())
        cutoff = now_ts - now_ts % DAY  # the latest 00:00 UTC close
        result: dict[int, dict[str, Any]] = {}
        cursor = start_ts - start_ts % DAY
        while cursor < cutoff:
            page_end = min(cutoff, cursor + DAY * 900)
            rows = self.provider._json_get(
                "/spot/candlesticks",
                {"currency_pair": pair, "interval": "1d", "from": cursor, "to": page_end - DAY},
            )
            if not isinstance(rows, list):
                raise MarketDataError("Gate spot daily candlesticks are malformed")
            for raw in rows:
                candle = self._candle(raw)
                if candle is None:
                    continue
                if cursor <= candle["t"] < page_end and candle["t"] + DAY <= now_ts:
                    result[candle["t"]] = candle
            cursor = page_end
        return [result[key] for key in sorted(result)]

    @staticmethod
    def _candle(raw: Any) -> dict[str, Any] | None:
        """Gate spot row [t, quote_vol, close, high, low, open, base_vol, window_closed]."""

        if not isinstance(raw, list) or len(raw) < 7:
            raise MarketDataError("Gate spot daily candle is malformed")
        try:
            opened = int(raw[0])
        except (TypeError, ValueError):
            raise MarketDataError("Gate spot daily candle timestamp is malformed") from None
        if opened % DAY:
            raise MarketDataError("Gate spot daily candle is not aligned to 00:00 UTC")
        if len(raw) > 7 and str(raw[7]).lower() == "false":
            return None  # the exchange says the window is still open
        values = {"o": _num(raw[5], positive=True), "h": _num(raw[3], positive=True), "l": _num(raw[4], positive=True),
                  "c": _num(raw[2], positive=True), "v": _num(raw[6])}
        if any(value is None for value in values.values()):
            raise MarketDataError("Gate spot daily candle values are not finite")
        if values["h"] < max(values["o"], values["c"], values["l"]) or values["l"] > min(values["o"], values["c"]):
            raise MarketDataError("Gate spot daily candle values are inconsistent")
        return {"t": opened, **values}

    def tickers(self) -> dict[str, dict[str, float | None]]:
        result = {}
        for pair, row in self.provider.tickers().items():
            change = _num(row.get("change_percentage"))
            result[pair] = {"price": _num(row.get("last"), positive=True),
                            "change_24h": None if change is None else change / 100,
                            "quote_volume": _num(row.get("quote_volume"))}
        return result


# ---------------------------------------------------------------------------
# the co-trader service
# ---------------------------------------------------------------------------


class SpotCoTrader:
    """Signals, AI triggers, journal and settings on top of a PaperRuntime's store and ledger."""

    def __init__(
        self,
        runtime: Any,
        *,
        source: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        holdings: Any | None = None,
        holdings_provider: Callable[[str], dict[str, Any] | None] | None = None,
        holdings_total_provider: Callable[[], float | None] | None = None,
        allow_fixture_ai: bool = False,
    ) -> None:
        self.runtime = runtime
        self.holdings = holdings  # crypto_eval.holdings.Holdings (held / payload / holding_for_ai / sync_if_due)
        self.store = runtime.store
        self.source = source or GateSpotDailySource()
        self._clock = clock or runtime._clock
        self.holdings_provider = holdings_provider
        self.holdings_total_provider = holdings_total_provider
        self.allow_fixture_ai = allow_fixture_ai
        self._ai_lock = threading.Lock()
        self._memo_lock = threading.Lock()
        self._memo: dict[str, tuple[tuple, list[dict[str, Any]], dict[str, Any]]] = {}
        self._tickers: tuple[float, dict[str, dict[str, Any]], str | None] | None = None
        self._evidence_memo: dict[str, tuple[tuple, dict[str, Any]]] = {}

    # ------------------------------------------------------------ basics
    def now(self) -> datetime:
        return self._clock().astimezone(timezone.utc)

    def _setting(self, key: str, default: Any) -> Any:
        rows = self.store._query("SELECT value_json FROM cotrader_settings WHERE key=?", (key,))
        return json.loads(rows[0]["value_json"]) if rows else default

    def _put_settings(self, values: dict[str, Any]) -> None:
        stamp = iso_utc(self.now())
        with self.store.transaction() as db:
            for key, value in values.items():
                db.execute(
                    "INSERT INTO cotrader_settings(key, value_json, updated_at) VALUES(?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (key, json.dumps(value), stamp),
                )

    def universe(self) -> list[str]:
        return list(self._setting("universe", list(DEFAULT_UNIVERSE)))

    def require_coin(self, value: Any) -> str:
        base = normalize_base(value)
        if base not in self.universe():
            raise CoTraderNotFound("coin is not in the co-trader universe")
        return base

    def _capital(self) -> tuple[float | None, str | None]:
        user = self._setting("cotrader_capital_usdt", None)
        if user is not None:
            return float(user), "user"
        total = None
        if self.holdings_total_provider is not None:
            try:
                total = self.holdings_total_provider()
            except Exception:
                total = None
        elif self.holdings is not None:
            total = self._holdings_total()
        if total is not None:
            if isinstance(total, (int, float)) and not isinstance(total, bool) and math.isfinite(total) and total > 0:
                return float(total), "holdings"
        return None, None

    def settings_view(self) -> dict[str, Any]:
        capital, source = self._capital()
        overrides = self._setting("ai_budget", {})
        budget = self.budget()
        return {
            "cotrader_capital_usdt": _r(capital, 2),
            "capital_source": source,
            "sizing_method": self._setting("sizing_method", "equal_weight"),
            "universe": self.universe(),
            "jev_scoring": bool(self._setting("jev_scoring", True)),
            "narrator": self.narrator(),
            "ai_budget": {**{key: budget[key] for key in BUDGET_OVERRIDE_FIELDS}, "overridden": sorted(overrides)},
        }

    def update_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        allowed = {"cotrader_capital_usdt", "sizing_method", "universe", "jev_scoring", "ai_budget", "narrator"}
        unknown = set(body) - allowed
        if unknown:
            raise PaperTradingError("unsupported co-trader settings: " + ", ".join(sorted(unknown)))
        updates: dict[str, Any] = {}
        if "cotrader_capital_usdt" in body:
            value = body["cotrader_capital_usdt"]
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0
            ):
                raise PaperTradingError("cotrader_capital_usdt must be a positive number or null")
            updates["cotrader_capital_usdt"] = None if value is None else float(value)
        if "sizing_method" in body:
            if body["sizing_method"] not in SIZING_METHODS:
                raise PaperTradingError("sizing_method must be equal_weight or inverse_vol")
            updates["sizing_method"] = body["sizing_method"]
        if "universe" in body:
            value = body["universe"]
            if not isinstance(value, list) or not 2 <= len(value) <= 20:
                raise PaperTradingError("universe must list 2 to 20 coins")
            bases = list(dict.fromkeys(normalize_base(item) for item in value))
            if len(bases) != len(value):
                raise PaperTradingError("universe must not repeat a coin")
            updates["universe"] = bases
        if "narrator" in body:
            if body["narrator"] not in NARRATORS:
                raise PaperTradingError("narrator must be luna or claude_routine")
            updates["narrator"] = body["narrator"]
        if "jev_scoring" in body:
            if not isinstance(body["jev_scoring"], bool):
                raise PaperTradingError("jev_scoring must be boolean")
            updates["jev_scoring"] = body["jev_scoring"]
        if "ai_budget" in body:
            value = body["ai_budget"]
            if not isinstance(value, dict) or set(value) - set(BUDGET_OVERRIDE_FIELDS):
                raise PaperTradingError("ai_budget accepts only " + ", ".join(BUDGET_OVERRIDE_FIELDS))
            merged = {**self._setting("ai_budget", {}), **value}
            self.budget(overrides=merged)  # validates
            updates["ai_budget"] = merged
        if updates:
            self._put_settings(updates)
        return self.settings_view()

    def update_watchlist(self, body: dict[str, Any]) -> dict[str, Any]:
        """Add or remove one coin. An added coin must be listed on Gate spot as <BASE>_USDT (public
        tickers); its daily candles are fetched right away. Thin coins are added with a warning."""

        if not isinstance(body, dict) or len(body) != 1 or not set(body) <= {"add", "remove"}:
            raise PaperTradingError("watchlist accepts exactly one of add or remove")
        universe = self.universe()
        if "remove" in body:
            base = normalize_base(body["remove"])
            if base not in universe:
                raise CoTraderNotFound("coin is not in the watchlist")
            if len(universe) <= 1:
                raise PaperTradingError("the watchlist needs at least one coin")
            self._put_settings({"universe": [item for item in universe if item != base]})
            return {"universe": self.universe(), "removed": base}
        base = normalize_base(body["add"])
        if base in STABLECOINS:
            raise PaperTradingError("stablecoins are cash, not a watchlist coin")
        if base in universe:
            raise PaperTradingError(f"{base} is already in the watchlist")
        if len(universe) >= MAX_UNIVERSE:
            raise PaperTradingError(f"the watchlist holds at most {MAX_UNIVERSE} coins; remove one first")
        try:
            tickers = self.source.tickers()
        except Exception:
            raise PaperTradingError("cannot check Gate spot listings right now; try again") from None
        row = tickers.get(f"{base}_USDT")
        if not row or row.get("price") is None:
            raise PaperTradingError(f"{base}_USDT is not listed on Gate spot")
        volume = row.get("quote_volume")
        self._put_settings({"universe": [*universe, base]})
        self._tickers = None  # the next overview fetches live prices for the new coin
        fetched = self.refresh(bases=[base])["coins"].get(base, {})
        bars = len(self._candles(base))
        return {
            "universe": self.universe(),
            "added": {
                "base": base,
                "quote_volume_24h_usdt": _r(volume, 0),
                "thin": volume is None or volume < THIN_QUOTE_VOLUME_USDT,
                "daily_bars": bars,
                "history_ok": bars >= EVIDENCE_MIN_BARS,
                "fetch_error": None if fetched.get("ok") else fetched.get("error"),
            },
        }

    def config(self) -> dict[str, Any]:
        return self.store.experiment()["config"]

    def budget(self, config: dict[str, Any] | None = None, *, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """This DB's ai_budget with the co-trader defaults and the user's co-trader overrides on top."""

        config = config or self.config()
        user = self._setting("ai_budget", {}) if overrides is None else overrides
        return validate_budget_config({**config["ai_budget"], **COTRADER_BUDGET_DEFAULTS, **user})

    # ------------------------------------------------------------ candles
    def _candles(self, base: str) -> list[dict[str, Any]]:
        rows = self.store._query(
            "SELECT t, open, high, low, close, volume FROM cotrader_candles WHERE base=? ORDER BY t", (base,)
        )
        return [{"t": row["t"], "o": row["open"], "h": row["high"], "l": row["low"], "c": row["close"],
                 "v": row["volume"]} for row in rows]

    def refresh(self, now: datetime | None = None, *, bases: list[str] | None = None) -> dict[str, Any]:
        """Fetch closed daily candles (public GET). Only the latest few days are refetched."""

        now = (now or self.now()).astimezone(timezone.utc)
        boundary = _floor_day(now)
        coins: dict[str, Any] = {}
        for base in (self.universe() if bases is None else bases):
            latest = self.store._query("SELECT MAX(t) AS t FROM cotrader_candles WHERE base=?", (base,))[0]["t"]
            start = boundary - INITIAL_HISTORY_DAYS * DAY if latest is None else int(latest) - REFETCH_DAYS * DAY
            try:
                candles = self.source.daily_candles(f"{base}_USDT", start_ts=start, now=now)
            except (MarketDataError, PaperTradingError) as exc:
                coins[base] = {"ok": False, "error": str(exc)[:160]}
                continue
            except Exception:
                coins[base] = {"ok": False, "error": "market data request failed"}
                continue
            stamp = iso_utc(now)
            with self.store.transaction() as db:
                for candle in candles:
                    if candle["t"] + DAY > int(now.timestamp()):
                        continue  # never store an unclosed bar
                    db.execute(
                        "INSERT OR REPLACE INTO cotrader_candles(base, t, open, high, low, close, volume, data_origin, "
                        "recorded_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (base, candle["t"], candle["o"], candle["h"], candle["l"], candle["c"], candle["v"],
                         getattr(self.source, "data_origin", "UNKNOWN"), stamp),
                    )
            latest = self.store._query("SELECT MAX(t) AS t FROM cotrader_candles WHERE base=?", (base,))[0]["t"]
            coins[base] = {"ok": True, "fetched": len(candles),
                           "latest_close": None if latest is None else _iso(int(latest) + DAY),
                           "current": latest is not None and int(latest) + DAY >= boundary}
        return {"boundary": _iso(boundary), "complete": all(item.get("current") for item in coins.values()),
                "coins": coins}

    def signals(self, base: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """(signal rows, rule backtest) for a coin, memoized on the cached candle set."""

        candles = self._candles(base)
        key = (len(candles), candles[-1]["t"] if candles else None, candles[-1]["c"] if candles else None)
        with self._memo_lock:
            cached = self._memo.get(base)
            if cached and cached[0] == key:
                return cached[1], cached[2]
        rows = signal_rows(candles)
        backtest = rule_backtest(rows)
        with self._memo_lock:
            self._memo[base] = (key, rows, backtest)
        return rows, backtest

    def evidence(self, base: str) -> dict[str, Any]:
        candles = self._candles(base)
        key = (len(candles), candles[-1]["t"] if candles else None, candles[-1]["c"] if candles else None)
        with self._memo_lock:
            cached = self._evidence_memo.get(base)
            if cached and cached[0] == key:
                return cached[1]
        card = evidence_card(candles)
        with self._memo_lock:
            self._evidence_memo[base] = (key, card)
        return card

    # ------------------------------------------------------------ prices / holdings
    def prices(self) -> tuple[dict[str, dict[str, Any]], str | None]:
        stamp = time.monotonic()
        cached = self._tickers
        if cached and stamp - cached[0] < TICKER_TTL_SECONDS:
            return cached[1], cached[2]
        try:
            raw = self.source.tickers()
            result = {}
            for base in self.universe():
                item = raw.get(f"{base}_USDT") or {}
                result[base] = {"price": item.get("price"), "change_24h": item.get("change_24h")}
            error = None
        except Exception:
            result, error = {}, "live price unavailable"
        self._tickers = (stamp, result, error)
        return result, error

    def rule_state(self, base: str) -> str | None:
        """``Holdings(rule_state_provider=...)`` hook: the latest closed-bar rule state, or None."""

        try:
            normalized = normalize_base(base)
        except PaperTradingError:
            return None
        if normalized not in self.universe():
            return None
        rows, _ = self.signals(normalized)
        return rows[-1]["state"] if rows else None

    def _holdings_payload(self) -> dict[str, Any] | None:
        if self.holdings is None:
            return None
        try:
            return self.holdings.payload()
        except Exception:
            return None

    def _holdings_total(self) -> float | None:
        """Holdings value + cash, only when the Gate wallet is synced (otherwise cash is unknown)."""

        payload = self._holdings_payload()
        if not payload or ((payload.get("sources") or {}).get("gate") or {}).get("status") != "OK":
            return None
        totals = payload.get("totals") or {}
        value, cash = totals.get("value_usdt"), totals.get("cash_usdt")
        if value is None or cash is None:
            return None
        return float(value) + float(cash)

    def _holdings_map(self) -> dict[str, dict[str, Any] | None]:
        """base -> {"qty", "value_usdt"} (qty 0 when known not held) or None when unknown."""

        universe = self.universe()
        if self.holdings_provider is not None:
            return {base: self._holding(base) for base in universe}
        if self.holdings is None:
            return {base: None for base in universe}
        try:
            known = {base: self.holdings.held(base) for base in universe}
        except Exception:
            return {base: None for base in universe}
        payload = self._holdings_payload() if any(known.values()) else None
        rows = {row["base"]: row for row in (payload or {}).get("holdings", [])}
        result: dict[str, dict[str, Any] | None] = {}
        for base, held in known.items():
            if held is None:
                result[base] = None
            elif not held:
                result[base] = {"qty": 0.0, "value_usdt": 0.0}
            else:
                row = rows.get(base) or {}
                result[base] = {"qty": float(row.get("qty") or 0.0), "value_usdt": row.get("value_usdt")}
        return result

    def _holding_for_ai(self, base: str) -> dict[str, Any] | None:
        if self.holdings is None:
            return None
        try:
            return self.holdings.holding_for_ai(base)
        except Exception:
            return None

    def _holding(self, base: str) -> dict[str, Any] | None:
        if self.holdings_provider is None:
            return None
        try:
            value = self.holdings_provider(base)
        except Exception:
            return None
        if not isinstance(value, dict):
            return None
        qty = value.get("qty")
        worth = value.get("value_usdt")
        if isinstance(qty, bool) or not isinstance(qty, (int, float)) or not math.isfinite(qty) or qty < 0:
            return None
        if isinstance(worth, bool) or not isinstance(worth, (int, float)) or not math.isfinite(worth):
            worth = None
        return {"qty": float(qty), "value_usdt": None if worth is None else float(worth)}

    # ------------------------------------------------------------ AI rows
    def _analysis_rows(self, sql: str, params: tuple) -> list[dict[str, Any]]:
        return [dict(row) for row in self.store._query(sql, params)]

    def _latest_ok(self, coin: str, source: str) -> dict[str, Any] | None:
        rows = self._analysis_rows(
            "SELECT * FROM cotrader_analyses WHERE coin=? AND source=? AND status='ok' ORDER BY as_of DESC, rowid DESC LIMIT 1",
            (coin, source),
        )
        return rows[0] if rows else None

    def _latest_narration(self, coin: str) -> dict[str, Any] | None:
        """Newest successful narrative analysis for a coin (Claude routine or Luna)."""

        marks = ",".join("?" for _ in NARRATION_SOURCES)
        rows = self._analysis_rows(
            f"SELECT * FROM cotrader_analyses WHERE coin=? AND source IN ({marks}) AND status='ok' "
            "ORDER BY as_of DESC, rowid DESC LIMIT 1",
            (coin, *NARRATION_SOURCES),
        )
        return rows[0] if rows else None

    def narrator(self) -> str:
        value = self._setting("narrator", "luna")
        return value if value in NARRATORS else "luna"

    @staticmethod
    def _jev_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = json.loads(row["payload_json"] or "{}")
        return {"as_of": row["as_of"], **payload.get("summary", {})}

    @staticmethod
    def analysis_view(row: dict[str, Any]) -> dict[str, Any]:
        payload = json.loads(row["payload_json"] or "{}")
        base = {"analysis_id": row["analysis_id"], "coin": row["coin"], "source": row["source"], "as_of": row["as_of"],
                "trigger": row["trigger"], "state": row["rule_state"], "stance": row["stance"],
                "conviction": row["conviction"], "model": row["model"], "cost_usd": _r(row["cost_usd"], 8)}
        if row["source"] == "jev":
            return {**base, "answers": payload.get("summary", {})}
        return {**base, **{key: payload.get(key) for key in ("summary_th", "bull_points", "bear_points", "key_levels",
                                                              "risks", "change_my_mind")}}

    @staticmethod
    def _briefing_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = json.loads(row["payload_json"] or "{}")
        return {"as_of": row["as_of"], "stance_market": payload.get("stance_market"),
                "summary_th": payload.get("summary_th"), "highlights": payload.get("highlights", []),
                "model": row["model"], "cost_usd": _r(row["cost_usd"], 8)}

    def _attempt_allowed(self, key: str, now: datetime) -> tuple[bool, dict[str, Any] | None]:
        rows = self._analysis_rows(
            "SELECT * FROM cotrader_analyses WHERE trigger_key=? ORDER BY as_of DESC, rowid DESC", (key,)
        )
        # A budget block is final for this trigger; a provider-setup block (fixture, unavailable,
        # unsupported) is not, so fixing the provider lets the same close be scored without a restart.
        final = next((row for row in rows if row["status"] == "ok" or (
            row["status"] == "blocked" and not str(row["reason"] or "").startswith(PROVIDER_SETUP_REASONS))), None)
        if final is not None:
            return False, final
        failed = [row for row in rows if row["status"] == "failed"]
        if len(failed) >= MAX_FAILED_ATTEMPTS:
            return False, failed[0]
        if rows:  # the latest attempt was a failure or a provider-setup block: space the retries
            last = datetime.fromisoformat(rows[0]["as_of"].replace("Z", "+00:00"))
            if (now - last).total_seconds() < RETRY_SPACING_SECONDS:
                return False, rows[0]
        return True, None

    def _record(self, **fields: Any) -> dict[str, Any]:
        row = {
            "analysis_id": f"cta-{uuid.uuid4().hex[:16]}", "coin": fields["coin"], "source": fields["source"],
            "trigger": fields["trigger"], "trigger_key": fields["trigger_key"], "rule_state": fields.get("rule_state"),
            "status": fields["status"], "reason": fields.get("reason"), "stance": fields.get("stance"),
            "conviction": fields.get("conviction"),
            "payload_json": None if fields.get("payload") is None else json.dumps(fields["payload"], ensure_ascii=False),
            "model": fields.get("model"), "cost_usd": fields.get("cost_usd"), "cost_status": fields.get("cost_status"),
            "latency_ms": fields.get("latency_ms"), "prompt_hash": fields.get("prompt_hash"),
            "call_id": fields["call_id"], "ref_close": fields.get("ref_close"), "ref_close_ts": fields.get("ref_close_ts"),
            "as_of": iso_utc(self.now()),
        }
        with self.store.transaction() as db:
            db.execute(
                f"INSERT INTO cotrader_analyses({', '.join(row)}) VALUES({', '.join('?' for _ in row)})",
                tuple(row.values()),
            )
        return row

    def _call_cost(self, call_id: str) -> tuple[float | None, str | None]:
        rows = self.store._query(
            "SELECT estimated_cost_usd, cost_status FROM ai_usage_events WHERE experiment_id=? AND cycle_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (LEDGER_SCOPE, call_id),
        )
        if not rows:
            return None, "unavailable"
        return rows[0]["estimated_cost_usd"], rows[0]["cost_status"]

    def _provider(self, kind: str, config: dict[str, Any], budget: dict[str, Any]) -> tuple[Any, dict[str, Any] | None, str | None]:
        try:
            if kind == "jev":
                adapter, provider_config = self.runtime._jev(config)
            else:
                adapter, provider_config = self.runtime._gpt({**config, "ai_budget": budget})
        except (AIProviderError, PaperTradingError):
            return None, None, "provider_unavailable"
        if str(provider_config.get("kind", "")).startswith("fixture_") and not self.allow_fixture_ai:
            return None, provider_config, "fixture_provider: configure a real provider for the co-trader"
        method = "evaluate_spot" if kind == "jev" else None
        if method and not hasattr(adapter, method):
            return None, provider_config, "provider_unsupported"
        return adapter, provider_config, None

    def _paid(
        self,
        *,
        source: str,
        coin: str,
        trigger: str,
        trigger_key: str,
        rule_state: str | None,
        method: str,
        call_type: str,
        context: dict[str, Any],
        ref: tuple[float | None, int | None],
    ) -> dict[str, Any]:
        """One budget-guarded provider call. Returns the stored row (status ok, blocked or failed)."""

        config = self.config()
        budget = self.budget(config)
        call_id = f"ctc-{uuid.uuid4().hex[:16]}"
        common = {"coin": coin, "source": source, "trigger": trigger, "trigger_key": trigger_key,
                  "rule_state": rule_state, "call_id": call_id, "ref_close": ref[0], "ref_close_ts": ref[1]}
        adapter, provider_config, problem = self._provider("jev" if source == "jev" else "luna", config, budget)
        if problem is None and not hasattr(adapter, method):
            problem = "provider_unsupported"
        if problem is not None:
            return self._record(**common, status="blocked", reason=problem)
        payload_bytes = len(json.dumps(context, default=str)) + (0 if source == "jev" else 16_000)
        try:
            result, blocked = self.runtime._paid_call(
                experiment_id=LEDGER_SCOPE, cycle_id=call_id, symbol=None if coin == MARKET_COIN else f"{coin}_USDT",
                arm=f"cotrader:{source}:{trigger}", provider_config=provider_config, adapter=adapter,
                call_type=call_type, payload_bytes=payload_bytes, budget=budget,
                invoke=lambda: getattr(adapter, method)(context),
            )
        except (AIProviderError, PaperTradingError) as exc:
            return self._record(**common, status="failed", reason=f"provider_error: {str(exc)[:160]}",
                                model=provider_config.get("model"))
        if blocked is not None:
            return self._record(**common, status="blocked", reason=str(blocked.get("code") or "BUDGET_BLOCKED"),
                                model=provider_config.get("model"))
        cost, cost_status = self._call_cost(call_id)
        model = result.get("returned_model") or result.get("model") or provider_config.get("model")
        if source == "jev":
            answers = result["answers"]
            summary = {
                "trend_regime": answers["trend_regime"]["value"],
                "trend_strength": _r(jev_score_unit(answers["trend_strength"]), 4),
                "reversal_risk": _r(jev_score_unit(answers["reversal_risk"]), 4),
                "rule_agreement": answers["rule_agreement"]["value"],
                "entry_timing": answers["entry_timing"]["value"],
                "key_risk": answers["key_risk"]["value"],
            }
            payload = {"summary": summary, "answers": answers,
                       "question_schema_version": result.get("question_schema_version")}
            return self._record(**common, status="ok", stance=summary["rule_agreement"], payload=payload, model=model,
                                cost_usd=cost, cost_status=cost_status, latency_ms=result.get("latency_ms"),
                                prompt_hash=result.get("snapshot_hash"))
        payload = result["review"] if "review" in result else result["briefing"]
        return self._record(**common, status="ok", stance=payload.get("stance") or payload.get("stance_market"),
                            conviction=payload.get("conviction"), payload=payload, model=model, cost_usd=cost,
                            cost_status=cost_status, latency_ms=result.get("latency_ms"),
                            prompt_hash=result.get("prompt_hash"))

    # ------------------------------------------------------------ contexts
    def _coin_facts(self, base: str, now: datetime) -> dict[str, Any]:
        rows, backtest = self.signals(base)
        last = rows[-1] if rows else None
        state = last["state"] if last else None
        since = _since(rows, "state")
        days = (now.date() - datetime.fromisoformat(since).date()).days if since else None
        boundary = _floor_day(now)
        events = state_events(rows)
        event = events[-1] if events else None
        changed_today = bool(event and last and event["at"] == _iso(boundary) and last["close_ts"] == boundary)
        return {"rows": rows, "backtest": backtest, "last": last, "state": state, "state_since": since,
                "days_in_state": days, "event": event, "changed_today": changed_today}

    def _regime(self, facts: dict[str, dict[str, Any]]) -> dict[str, Any]:
        states = {base: item["state"] for base, item in facts.items()}
        btc = facts.get("BTC", {}).get("state") if "BTC" in facts else None
        return market_regime(states, btc)

    def _review_context(self, base: str, facts: dict[str, Any], regime: dict[str, Any]) -> dict[str, Any]:
        last = facts["last"]
        return {
            "coin": base, "symbol": f"{base}_USDT", "data_cutoff": _iso(last["close_ts"]),
            "state": facts["state"], "state_since": facts["state_since"], "days_in_state": facts["days_in_state"],
            "ret_60d": _r(last["ret_60d"]), "sma100": _r(last["sma100"], 10), "dist_sma100": _r(last["dist_sma100"]),
            "vol_20d": _r(last["vol_20d"]), "trend_line_next": _r(last["trend_line_next"], 10),
            "rule": facts["backtest"]["stats"] or {}, "regime": regime,
            "jev": self._jev_view(self._latest_ok(base, "jev")),
            "holding": self._holding_for_ai(base),  # only when share_holdings_with_ai is on
            "candles": [[row["t"], row["o"], row["h"], row["l"], row["c"], row["v"]]
                        for row in facts["rows"][-AI_CONTEXT_BARS:]],
        }

    def _jev_state(self, base: str, facts: dict[str, Any], regime: dict[str, Any]) -> dict[str, Any]:
        last = facts["last"]
        trend = last["trend_line_next"]
        holding = self._holding_for_ai(base)
        state = {
            "schema_version": "spot-cotrader-state.v1",
            "coin": base,
            "data_cutoff": _iso(last["close_ts"]),
            "rule": {"state": facts["state"], "state_since": facts["state_since"],
                     "definition": "HOLD if ret_60d > 0 and close > SMA100; CASH if both fail; else WATCH"},
            "ret_60d": _r(last["ret_60d"], 5), "dist_sma100": _r(last["dist_sma100"], 5),
            "vol_20d": _r(last["vol_20d"], 5), "trend_line_next": _r(trend, 8),
            "close_vs_trend_line": _r(last["close"] / trend - 1, 5) if trend else None,
            "regime": {"label": regime["label"], "breadth": regime["breadth"], "btc_state": regime["btc_state"]},
            "daily_close_volume": [[row["t"], float(f"{row['c']:.6g}"), round(row["v"])]
                                   for row in facts["rows"][-JEV_CONTEXT_BARS:]],
        }
        if holding is not None:  # share_holdings_with_ai: {held, unrealized_pct} only
            state["holding"] = {"held": holding.get("held"), "unrealized_pct": holding.get("unrealized_pct")}
        return state

    def _all_facts(self, now: datetime) -> dict[str, dict[str, Any]]:
        return {base: self._coin_facts(base, now) for base in self.universe()}

    # ------------------------------------------------------------ AI triggers
    def review(self, value: Any, *, trigger: str, confirm: bool = False, event_at: str | None = None) -> dict[str, Any]:
        """Luna review of one coin. Returns {"analysis": view|None, "blocked_reason": str|None, "cached": bool}."""

        if trigger not in {"state_change", "manual", "jev_disagree"}:
            raise PaperTradingError("review trigger is unsupported")
        base = self.require_coin(value)
        if self.narrator() == "claude_routine":  # Luna is off: nothing is called or recorded
            return {"analysis": None, "blocked_reason": LUNA_OFF_REASON, "cached": False}
        with self._ai_lock:
            now = self.now()
            day = now.date().isoformat()
            facts_all = self._all_facts(now)
            facts = facts_all[base]
            if facts["state"] is None:
                return {"analysis": None, "blocked_reason": "market_data_unavailable: not enough closed daily bars",
                        "cached": False}
            if trigger == "manual":
                used = self.store._query(
                    "SELECT COUNT(*) AS n FROM cotrader_analyses WHERE coin=? AND source='luna' AND trigger='manual' "
                    "AND status!='blocked' AND substr(as_of, 1, 10)=?",
                    (base, day),
                )[0]["n"]
                if used >= MANUAL_DAILY_CAP and not confirm:
                    raise CoTraderConfirmationRequired("manual_cap")
                key = f"manual:{base}:{day}:{uuid.uuid4().hex[:8]}"
            else:
                key = (f"state_change:{base}:{event_at}" if trigger == "state_change"
                       else f"jev_disagree:{base}:{day}")
                allowed, existing = self._attempt_allowed(key, now)
                if not allowed:
                    ok = existing if existing and existing["status"] == "ok" else None
                    return {"analysis": self.analysis_view(ok) if ok else None,
                            "blocked_reason": None if ok else (existing or {}).get("reason"), "cached": True}
                cached = self._analysis_rows(
                    "SELECT * FROM cotrader_analyses WHERE coin=? AND source='luna' AND status='ok' AND rule_state=? "
                    "AND substr(as_of, 1, 10)=? ORDER BY as_of DESC LIMIT 1",
                    (base, facts["state"], day),
                )
                if cached:  # the same coin, state and day is never re-billed
                    return {"analysis": self.analysis_view(cached[0]), "blocked_reason": None, "cached": True}
            context = self._review_context(base, facts, self._regime(facts_all))
            row = self._paid(source="luna", coin=base, trigger=trigger, trigger_key=key, rule_state=facts["state"],
                             method="generate_spot_review", call_type="gpt_spot_review", context=context,
                             ref=(facts["last"]["close"], facts["last"]["close_ts"]))
        if row["status"] != "ok":
            return {"analysis": None, "blocked_reason": row["reason"], "cached": False}
        return {"analysis": self.analysis_view(row), "blocked_reason": None, "cached": False}

    def jev_daily(self, now: datetime | None = None) -> dict[str, Any]:
        """One Jev screen per coin per close (cached; a restart never re-bills). Disagree escalates to Luna."""

        now = (now or self.now()).astimezone(timezone.utc)
        if not self._setting("jev_scoring", True):
            return {"status": "disabled"}
        boundary = _floor_day(now)
        results: dict[str, Any] = {}
        disagree: list[str] = []
        with self._ai_lock:
            facts_all = self._all_facts(now)
            regime = self._regime(facts_all)
            for base, facts in facts_all.items():
                last = facts["last"]
                if facts["state"] is None or last["close_ts"] != boundary:
                    results[base] = "no_current_close"
                    continue
                key = f"jev:{base}:{_iso(boundary)}"
                allowed, existing = self._attempt_allowed(key, now)
                if not allowed:
                    results[base] = f"cached:{existing['status']}" if existing else "cached"
                    continue
                row = self._paid(source="jev", coin=base, trigger="daily", trigger_key=key, rule_state=facts["state"],
                                 method="evaluate_spot", call_type=JEV_CALL_TYPE,
                                 context=self._jev_state(base, facts, regime), ref=(last["close"], last["close_ts"]))
                results[base] = row["status"] if row["status"] == "ok" else f"{row['status']}:{row['reason']}"
                if row["status"] == "ok" and row["stance"] == "disagree":
                    disagree.append(base)
        for base in disagree:
            results[f"{base}:luna"] = self.review(base, trigger="jev_disagree")["blocked_reason"] or "reviewed"
        return {"status": "ran", "close": _iso(boundary), "coins": results}

    def daily_briefing(self, now: datetime | None = None) -> dict[str, Any]:
        now = (now or self.now()).astimezone(timezone.utc)
        if self.narrator() == "claude_routine":
            latest = self._latest_ok(MARKET_COIN, "claude")
            return {"briefing": self._briefing_view(latest), "blocked_reason": None if latest else "routine_pending",
                    "cached": True}
        day = now.date().isoformat()
        key = f"daily:{day}"
        with self._ai_lock:
            allowed, existing = self._attempt_allowed(key, now)
            if not allowed:
                ok = existing if existing and existing["status"] == "ok" else None
                return {"briefing": self._briefing_view(ok), "blocked_reason": None if ok else (existing or {}).get("reason"),
                        "cached": True}
            facts_all = self._all_facts(now)
            known = {base: facts for base, facts in facts_all.items() if facts["state"] is not None}
            if not known:
                return {"briefing": None, "blocked_reason": "market_data_unavailable", "cached": False}
            regime = self._regime(facts_all)
            coins = []
            for base, facts in known.items():
                last = facts["last"]
                coins.append({
                    "coin": base, "state": facts["state"], "state_since": facts["state_since"],
                    "days_in_state": facts["days_in_state"], "changed_today": facts["changed_today"],
                    "ret_60d": _r(last["ret_60d"], 5), "dist_sma100": _r(last["dist_sma100"], 5),
                    "vol_20d": _r(last["vol_20d"], 5), "trend_line_next": _r(last["trend_line_next"], 8),
                    "rule": facts["backtest"]["stats"], "jev": self._jev_view(self._latest_ok(base, "jev")),
                    "recent_closes": [float(f"{row['c']:.6g}") for row in facts["rows"][-14:]],
                })
            cutoff = max(facts["last"]["close_ts"] for facts in known.values())
            context = {"data_cutoff": _iso(cutoff), "regime": regime, "coins": coins}
            row = self._paid(source="luna", coin=MARKET_COIN, trigger="daily", trigger_key=key, rule_state=None,
                             method="generate_spot_briefing", call_type="gpt_spot_briefing", context=context,
                             ref=(None, None))
        if row["status"] != "ok":
            return {"briefing": None, "blocked_reason": row["reason"], "cached": False}
        return {"briefing": self._briefing_view(row), "blocked_reason": None, "cached": False}

    def process_close(self, now: datetime | None = None, *, run_ai: bool = True) -> dict[str, Any]:
        """Record state-change events (idempotent per coin per close) and run the state-change reviews."""

        now = (now or self.now()).astimezone(timezone.utc)
        boundary = _floor_day(now)
        recorded = 0
        todays: list[tuple[str, str]] = []
        stamp = iso_utc(now)
        for base in self.universe():
            rows, _ = self.signals(base)
            events = state_events(rows)
            with self.store.transaction() as db:
                for event in events:
                    cursor = db.execute(
                        "INSERT OR IGNORE INTO cotrader_events(base, at, from_state, to_state, close, recorded_at) "
                        "VALUES(?, ?, ?, ?, ?, ?)",
                        (base, event["at"], event["from"], event["to"], event["close"], stamp),
                    )
                    recorded += cursor.rowcount
            if events and events[-1]["at"] == _iso(boundary):
                todays.append((base, events[-1]["at"]))
        reviews = {}
        if run_ai:
            for base, at in todays:
                result = self.review(base, trigger="state_change", event_at=at)
                reviews[base] = "reviewed" if result["analysis"] else result["blocked_reason"]
        return {"recorded_events": recorded, "changed_today": [base for base, _ in todays], "reviews": reviews}

    def events(self, base: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT base, at, from_state, to_state, close FROM cotrader_events"
        params: tuple = ()
        if base:
            sql += " WHERE base=?"
            params = (base,)
        return [{"coin": row["base"], "from": row["from_state"], "to": row["to_state"], "at": row["at"],
                 "close": row["close"]} for row in self.store._query(sql + " ORDER BY at DESC", params)]

    # ------------------------------------------------------------ AI status / scorecard
    def _price_is_fallback(self, config: dict[str, Any]) -> bool:
        ledger = self.store.cost_ledger
        for provider_id in (config.get("gpt_provider_id"), config.get("jev_provider_id")):
            provider = self.store.provider(provider_id) if provider_id else None
            if not provider:
                continue
            price = ledger.current_price(provider["kind"], provider["model"], at=self.now())
            source = str((price or {}).get("source") or "").lower()
            version = str((price or {}).get("version") or "").lower()
            if "fallback" in source or "not contract" in source or "fallback" in version:
                return True
        return False

    def _spend(self, since: str | None) -> dict[str, float]:
        rows = self.store._query(
            "SELECT call_type, status, reserved_usd, charged_usd, created_at FROM ai_budget_reservations WHERE experiment_id=?",
            (LEDGER_SCOPE,),
        )
        totals = {"jev": 0.0, "luna": 0.0}
        for row in rows:
            if since is not None and row["created_at"] < since:
                continue
            amount = float(row["reserved_usd"]) if row["status"] == "reserved" else float(row["charged_usd"] or 0.0) \
                if row["status"] == "reconciled" else 0.0
            totals["jev" if row["call_type"] == JEV_CALL_TYPE else "luna"] += amount
        return totals

    def _calls(self, since: str | None) -> dict[str, int]:
        sql = ("SELECT call_type, COUNT(*) AS n FROM ai_usage_events WHERE experiment_id=? AND status IN ('ok', 'failed')")
        params: tuple = (LEDGER_SCOPE,)
        if since is not None:
            sql += " AND started_at>=?"
            params += (since,)
        counts = {"jev": 0, "luna": 0}
        for row in self.store._query(sql + " GROUP BY call_type", params):
            if row["call_type"] == JEV_CALL_TYPE:
                counts["jev"] += row["n"]
            elif row["call_type"] in LUNA_CALL_TYPES:
                counts["luna"] += row["n"]
        return counts

    def ai_status(self) -> dict[str, Any]:
        now = self.now()
        config = self.config()
        budget = self.budget(config)
        status = self.store.cost_ledger.budget_status(LEDGER_SCOPE, budget)
        day_start = iso_utc(now.replace(hour=0, minute=0, second=0, microsecond=0))
        narrator = self.narrator()
        # With the Claude routine narrating, only Jev is a paid provider here.
        _, _, problem = self._provider("jev" if narrator == "claude_routine" else "luna", config, budget)
        blocked = problem
        if blocked is None and status["exhausted"]:
            blocked = "DAILY_BUDGET" if (status["utilization_today"] or 0) >= 1 else "EXPERIMENT_BUDGET"
        if blocked is None:
            last = self._analysis_rows(
                "SELECT status, reason FROM cotrader_analyses WHERE source=? AND as_of>=? ORDER BY as_of DESC, rowid DESC LIMIT 1",
                ("jev" if narrator == "claude_routine" else "luna", day_start),
            )
            if last and last[0]["status"] != "ok":
                blocked = last[0]["reason"]
        spend = self._spend(day_start)
        calls = self._calls(day_start)
        return {
            "enabled": problem is None,
            "spent_today_usd": _r(status["spent_today_usd"], 6),
            "daily_cap_usd": budget["daily_usd"],
            "spent_total_usd": _r(status["spent_experiment_usd"], 6),
            "total_cap_usd": budget["experiment_usd"],
            "blocked_reason": blocked,
            "jev_calls_today": calls["jev"],
            "luna_calls_today": calls["luna"],
            "jev_spent_usd": _r(spend["jev"], 6),
            "luna_spent_usd": _r(spend["luna"], 6),
            "pricing_is_fallback": self._price_is_fallback(config),
            "narrator": narrator,
            "routine": self.routine_status(),
        }

    # ------------------------------------------------------------ Claude routine (narration without API cost)
    def routine_status(self) -> dict[str, Any]:
        rows = self._analysis_rows(
            "SELECT as_of, model, ref_close_ts FROM cotrader_analyses WHERE source='claude' AND status='ok' "
            "ORDER BY as_of DESC, rowid DESC LIMIT 1", (),
        )
        last = rows[0] if rows else None
        return {"last_published_at": last["as_of"] if last else None, "model": last["model"] if last else None}

    def routine_context(self, now: datetime | None = None) -> dict[str, Any]:
        """Everything the scheduled Claude routine needs for today's narration, grounded in closed bars.

        The same typed inputs, instructions and JSON contracts as the Luna path, plus each coin's ladder
        state, so the routine and Luna are directly comparable. Read-only; nothing is billed."""

        now = (now or self.now()).astimezone(timezone.utc)
        facts_all = self._all_facts(now)
        known = {base: facts for base, facts in facts_all.items() if facts["state"] is not None}
        if not known:
            raise PaperTradingError("market data unavailable: no closed daily bars yet")
        regime = self._regime(facts_all)
        views, _, _ = self._views(now)
        cutoff = max(facts["last"]["close_ts"] for facts in known.values())
        coins: dict[str, Any] = {}
        briefing_coins = []
        for base, facts in known.items():
            last = facts["last"]
            if last["close_ts"] != cutoff:
                continue  # stale coin: never narrate data older than the common close
            review_input = build_spot_review_input(self._review_context(base, facts, regime))
            ladder = views.get(base, {}).get("ladder")
            coins[base] = {**review_input, "ladder": ladder}
            briefing_coins.append({
                "coin": base, "state": facts["state"], "state_since": facts["state_since"],
                "days_in_state": facts["days_in_state"], "changed_today": facts["changed_today"],
                "ret_60d": _r(last["ret_60d"], 5), "dist_sma100": _r(last["dist_sma100"], 5),
                "vol_20d": _r(last["vol_20d"], 5), "trend_line_next": _r(last["trend_line_next"], 8),
                "rule": facts["backtest"]["stats"], "jev": self._jev_view(self._latest_ok(base, "jev")),
                "recent_closes": [float(f"{row['c']:.6g}") for row in facts["rows"][-14:]],
            })
        briefing_input = build_spot_briefing_input({"data_cutoff": _iso(cutoff), "regime": regime, "coins": briefing_coins})
        return {
            "schema_version": ROUTINE_SCHEMA_VERSION,
            "data_cutoff": _iso(cutoff),
            "narrator": self.narrator(),
            "skill": "skills/crypto-market-trading-analysis (SKILL.md; references/technical-analysis.md, point-in-time.md)",
            "instructions": {"review": spot_review_instructions(), "briefing": spot_briefing_instructions()},
            "output_contract": {
                "review": SPOT_REVIEW_RESPONSE_SCHEMA,
                "briefing": spot_briefing_response_schema(sorted(coins)),
            },
            "briefing_input": briefing_input,
            "coins": coins,
            "publish": {
                "method": "POST",
                "path": "/api/cotrader/routine/publish",
                "body": {"model": "<the model you are>", "data_cutoff": _iso(cutoff),
                         "briefing": "<briefing JSON>", "reviews": {"<BASE>": "<review JSON>"}},
            },
        }

    def publish_routine(self, body: dict[str, Any]) -> dict[str, Any]:
        """Store the Claude routine's narration after strict validation (same parsers as Luna).

        Idempotent per close: re-publishing the same close replaces that close's routine rows."""

        if not isinstance(body, dict) or set(body) - {"model", "data_cutoff", "briefing", "reviews"}:
            raise PaperTradingError("publish accepts model, data_cutoff, briefing and reviews only")
        model = body.get("model")
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9 ._:/()-]{1,80}", model):
            raise PaperTradingError("model must be a short model name")
        context = self.routine_context()
        if body.get("data_cutoff") != context["data_cutoff"]:
            raise PaperTradingError(
                f"data_cutoff must be the current close {context['data_cutoff']}; fetch a fresh context and retry")
        coins = sorted(context["coins"])
        try:
            briefing = parse_spot_briefing(body.get("briefing"), coins)
        except AIProviderError as exc:
            raise PaperTradingError(f"briefing rejected: {exc}") from None
        reviews_in = body.get("reviews") or {}
        if not isinstance(reviews_in, dict):
            raise PaperTradingError("reviews must be an object keyed by coin")
        unknown = sorted(set(reviews_in) - set(coins))
        if unknown:
            raise PaperTradingError("reviews for coins outside today's context: " + ", ".join(unknown))
        reviews: dict[str, dict[str, Any]] = {}
        for base, review in reviews_in.items():
            try:
                reviews[base] = parse_spot_review(review)
            except AIProviderError as exc:
                raise PaperTradingError(f"review for {base} rejected: {exc}") from None
        cutoff = context["data_cutoff"]
        cutoff_ts = int(datetime.fromisoformat(cutoff.replace("Z", "+00:00")).timestamp())
        facts_all = self._all_facts(self.now())
        with self._ai_lock:
            with self.store.transaction() as db:
                db.execute("DELETE FROM cotrader_analyses WHERE source='claude' AND trigger_key LIKE ?",
                           (f"claude:%:{cutoff}",))
            common = {"source": "claude", "trigger": "routine", "model": model, "cost_usd": 0.0,
                      "cost_status": "subscription"}
            self._record(coin=MARKET_COIN, trigger_key=f"claude:{MARKET_COIN}:{cutoff}", rule_state=None,
                         call_id=f"ctr-{uuid.uuid4().hex[:16]}", status="ok", stance=briefing["stance_market"],
                         payload=briefing, ref_close=None, ref_close_ts=None, **common)
            for base, review in reviews.items():
                last = facts_all[base]["last"]
                self._record(coin=base, trigger_key=f"claude:{base}:{cutoff}", rule_state=facts_all[base]["state"],
                             call_id=f"ctr-{uuid.uuid4().hex[:16]}", status="ok", stance=review["stance"],
                             conviction=review["conviction"], payload=review, ref_close=last["close"],
                             ref_close_ts=cutoff_ts, **common)
        return {"published": {"data_cutoff": cutoff, "briefing": True, "reviews": sorted(reviews), "model": model}}

    def scorecard(self) -> dict[str, Any]:
        rows = self._analysis_rows(
            "SELECT * FROM cotrader_analyses WHERE status='ok' AND coin!=? AND ref_close_ts IS NOT NULL ORDER BY as_of",
            (MARKET_COIN,),
        )
        latest: dict[tuple, dict[str, Any]] = {}
        for row in rows:  # one sample per source / family / coin / close (repeated manual asks are not new evidence)
            payload = json.loads(row["payload_json"] or "{}")
            if row["source"] == "jev":
                summary = payload.get("summary", {})
                latest[("jev", "stance", row["coin"], row["ref_close_ts"])] = {**row, "stance": summary.get("rule_agreement")}
                risk = summary.get("reversal_risk")
                if isinstance(risk, (int, float)):
                    bucket = "reversal_risk_high" if risk >= 0.75 else "reversal_risk_low" if risk <= 0.25 else "reversal_risk_mid"
                    latest[("jev", "reversal", row["coin"], row["ref_close_ts"])] = {**row, "stance": bucket}
            else:
                latest[(row["source"], "stance", row["coin"], row["ref_close_ts"])] = row
        by_coin: dict[str, dict[int, dict[str, Any]]] = {}
        samples = []
        for sample in latest.values():
            coin = sample["coin"]
            if coin not in by_coin:
                signal = self.signals(coin)[0] if coin in self.universe() else signal_rows(self._candles(coin))
                by_coin[coin] = {row["close_ts"]: row for row in signal}
            index = by_coin[coin]
            ref_ts = int(sample["ref_close_ts"])
            ref = index.get(ref_ts)
            item = {"source": sample["source"], "stance": sample["stance"]}
            for horizon in SCORECARD_HORIZONS:
                future = index.get(ref_ts + horizon * DAY)
                item[f"fwd_{horizon}d"] = (future["close"] / float(sample["ref_close"]) - 1) if future and sample["ref_close"] else None
                item[f"rule_same_{horizon}d"] = (future["state"] == ref["state"]) if future and ref and ref["state"] else None
            samples.append(item)
        table = scorecard_rows(samples)
        spreads = []
        for source in ("jev", "luna", "claude"):
            agree = next(row for row in table if row["source"] == source and row["stance"] == "agree")
            disagree = next(row for row in table if row["source"] == source and row["stance"] == "disagree")
            spreads.append({"source": source, **{
                f"agree_minus_disagree_{h}d": (_r(agree[f"avg_ret_{h}d"] - disagree[f"avg_ret_{h}d"])
                                               if agree[f"avg_ret_{h}d"] is not None and disagree[f"avg_ret_{h}d"] is not None else None)
                for h in SCORECARD_HORIZONS}})
        spend = self._spend(None)
        calls = self._calls(None)
        return {
            "as_of": iso_utc(self.now()),
            "horizons": list(SCORECARD_HORIZONS),
            "min_samples": SCORECARD_MIN_SAMPLES,
            "rows": table,
            "spreads": spreads,
            "cost": {"jev_usd": _r(spend["jev"], 6), "luna_usd": _r(spend["luna"], 6),
                     "calls": {"jev": calls["jev"], "luna": calls["luna"]}},
            "note_pricing_fallback": self._price_is_fallback(self.config()),
        }

    # ------------------------------------------------------------ views
    def _coin_view(
        self,
        base: str,
        facts: dict[str, Any],
        prices: dict[str, dict[str, Any]],
        price_error: str | None,
        weights: dict[str, float | None],
        ladder_w: dict[str, float | None],
        capital: float | None,
        method: str,
        holding: dict[str, Any] | None,
    ) -> dict[str, Any]:
        rows = facts["rows"]
        last = facts["last"]
        quote = prices.get(base) or {}
        price = quote.get("price")
        reason = None
        if last is None:
            reason = "no closed daily candles cached yet"
        elif facts["state"] is None:
            reason = "not enough closed daily bars for the rule (needs 100)"
        elif price is None:
            reason = price_error or "live price unavailable"
        trend = last["trend_line_next"] if last else None
        distance = price / trend - 1 if price and trend else None
        held = None if holding is None else holding["qty"] > 0
        current = None if holding is None else holding["value_usdt"]
        state = facts["state"]
        sizing = None
        if state is not None:
            weight = weights.get(base)
            target = None if capital is None or weight is None else (weight * capital if state == "HOLD" else 0.0)
            delta = target - current if target is not None and current is not None else None
            risk = None
            if target == 0.0:
                risk = 0.0
            elif target is not None and price and trend:
                risk = target * max(0.0, price - trend) / price
            sizing = {
                "method": method, "weight": _r(weight), "target_usdt": _r(target, 2), "current_usdt": _r(current, 2),
                "delta_usdt": _r(delta, 2), "delta_qty": _r(delta / price, 8) if delta is not None and price else None,
                "risk_to_trend_line_usdt": _r(risk, 2),
                "scale_in": ({"days": 3, "per_day_usdt": _r(delta / 3, 2)} if delta is not None and delta > 0 else None),
            }
        action = rule_action(state, held, trend, distance, facts["changed_today"])
        ladder_state = last["ladder"] if last else None
        ladder = None
        if ladder_state is not None:
            previous = rows[-2]["ladder"] if len(rows) >= 2 else None
            step = ladder_action(previous, ladder_state) if last["close_ts"] == _floor_day(self.now()) else "HOLD"
            target_weight = ladder_w.get(base)
            target_usdt = None if capital is None or target_weight is None else target_weight * capital
            delta = target_usdt - current if target_usdt is not None and current is not None else None
            ladder = {
                "state": ladder_state,
                "since": _since(rows, "ladder"),
                "action": {
                    "type": step,
                    "text": LADDER_TEXT.get(step, f"Hold ({ladder_state})."),
                    "fresh": step != "HOLD",
                    # slots (slot = capital / N): OUT 0, STARTER 0.5, FULL 2, scaled down when the total exceeds N
                    "target_weight": _r(None if target_weight is None else target_weight * len(ladder_w), 4),
                    "capital_fraction": _r(target_weight),
                    "target_usdt": _r(target_usdt, 2),
                    "delta_usdt": _r(delta, 2),
                    "delta_qty": _r(delta / price, 8) if delta is not None and price else None,
                    "add_above": _r(last["add_above"], 10) if ladder_state != "FULL" else None,
                    "trim_below": _r(last["ema20"], 10) if ladder_state == "FULL" else None,
                    "exit_below": _r(trend, 10) if ladder_state != "OUT" else None,
                },
            }
        last_luna = self._latest_narration(base)
        event = facts["event"]
        return {
            "symbol": f"{base}_USDT",
            "base": base,
            "price": price,
            "change_24h": _r(quote.get("change_24h")),
            "state": state,
            "state_since": facts["state_since"],
            "days_in_state": facts["days_in_state"],
            "changed_today": facts["changed_today"],
            "ret_60d": _r(last["ret_60d"]) if last else None,
            "dist_sma100": _r(last["dist_sma100"]) if last else None,
            "vol_20d": _r(last["vol_20d"]) if last else None,
            "rule": facts["backtest"]["stats"],
            "spark": [[row["t"], row["c"]] for row in rows[-SPARK_BARS:]],
            "last_ai": (None if last_luna is None else {
                "as_of": last_luna["as_of"], "trigger": last_luna["trigger"], "stance": last_luna["stance"],
                "conviction": last_luna["conviction"],
                "summary_th": json.loads(last_luna["payload_json"] or "{}").get("summary_th"),
            }),
            "event": None if event is None else {"from": event["from"], "to": event["to"], "at": event["at"]},
            "trend_line_next": _r(trend, 10),
            "distance_to_trend_line": _r(distance),
            "action": action,
            "sizing": sizing,
            "held": held,
            "ladder_state": ladder_state,
            "ladder": ladder,
            "cdc": None if not last or last["cdc"] is None else {"zone": last["cdc"], "since": _since(rows, "cdc")},
            "jev": self._jev_view(self._latest_ok(base, "jev")),
            "data_through": _iso(last["close_ts"]) if last else None,
            "unavailable_reason": reason,
        }

    def _views(self, now: datetime) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]]]:
        facts_all = self._all_facts(now)
        prices, price_error = self.prices()
        universe = self.universe()
        method = self._setting("sizing_method", "equal_weight")
        vols = {base: (facts["last"] or {}).get("vol_20d") if facts["last"] else None for base, facts in facts_all.items()}
        weights = sizing_weights(method, vols, universe)
        ladder_w = ladder_weights({base: (facts["last"] or {}).get("ladder") if facts["last"] else None
                                   for base, facts in facts_all.items()}, universe)
        capital, _ = self._capital()
        holdings = self._holdings_map()
        views = {base: self._coin_view(base, facts_all[base], prices, price_error, weights, ladder_w, capital, method,
                                       holdings.get(base))
                 for base in universe}
        return views, self._regime(facts_all), facts_all

    def overview(self) -> dict[str, Any]:
        now = self.now()
        boundary = _floor_day(now)
        views, regime, _ = self._views(now)
        briefing = self._analysis_rows(
            "SELECT * FROM cotrader_analyses WHERE coin=? AND status='ok' ORDER BY as_of DESC LIMIT 1", (MARKET_COIN,)
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "as_of": iso_utc(now).replace(".000Z", "Z"),
            "last_close": _iso(boundary),
            "next_close": _iso(boundary + DAY),
            "regime": regime,
            "briefing": self._briefing_view(briefing[0] if briefing else None),
            "ai": self.ai_status(),
            "settings": {key: value for key, value in self.settings_view().items()
                         if key in {"cotrader_capital_usdt", "capital_source", "sizing_method"}},
            "coins": [views[base] for base in self.universe()],
            "execution": {
                "decide_at": "daily close, 00:00 UTC (07:00 Bangkok); not intraday wicks",
                "order_type": "limit orders near the price; optionally scale in over 2-3 days",
                "exit": "a daily close below the trend line; NOT an intraday stop",
                "fee_rate": FEE_RATE,
                "slippage_bps": SLIPPAGE * 10_000,
                "thin_liquidity": [base for base in THIN_LIQUIDITY if base in self.universe()],
            },
            "disclaimer": DISCLAIMER,
        }

    def coin_detail(self, value: Any) -> dict[str, Any]:
        base = self.require_coin(value)
        now = self.now()
        views, _, facts_all = self._views(now)
        facts = facts_all[base]
        rows = facts["rows"]
        span = rows[-CANDLE_VIEW_BARS:]
        start = span[0]["t"] if span else None
        periods = []
        if span:
            first_day = _date(span[0]["close_ts"])
            for period in state_periods(rows):
                if period["to"] < first_day:
                    continue
                periods.append({**period, "from": max(period["from"], first_day)})
        analyses = self._analysis_rows(
            "SELECT * FROM cotrader_analyses WHERE coin=? AND status='ok' ORDER BY as_of DESC, rowid DESC LIMIT 30", (base,)
        )
        return {
            **views[base],
            "candles": [[row["t"], row["o"], row["h"], row["l"], row["c"], row["v"]] for row in span],
            "sma100": [[row["t"], _r(row["sma100"], 10)] for row in span if row["sma100"] is not None],
            "periods": periods,
            "equity": facts["backtest"]["equity"],
            "analyses": [self.analysis_view(row) for row in analyses],
            "narrator": self.narrator(),
            "journal": self.journal(base),
            "trend_line_series": [[row["close_ts"], _r(row["trend_line_next"], 10)] for row in span
                                  if row["trend_line_next"] is not None],
            "trades": facts["backtest"]["trades"],
            "ladder_transitions": [item for item in ladder_transitions(rows) if start is None or item["t"] >= start],
            "cdc_series": [[row["t"], row["cdc"]] for row in span if row["cdc"] is not None],
            "evidence": self.evidence(base),
            "events": self.events(base)[:20],
        }

    # ------------------------------------------------------------ journal
    def add_journal(self, body: dict[str, Any]) -> dict[str, Any]:
        allowed = {"coin", "action", "price", "amount_usdt", "note"}
        if set(body) - allowed:
            raise PaperTradingError("unsupported journal fields: " + ", ".join(sorted(set(body) - allowed)))
        base = self.require_coin(body.get("coin"))
        action = body.get("action")
        if action not in JOURNAL_ACTIONS:
            raise PaperTradingError("action must be buy, sell, hold, or skip")
        numbers = {}
        for field in ("price", "amount_usdt"):
            value = body.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0
            ):
                raise PaperTradingError(f"{field} must be a positive number or null")
            numbers[field] = None if value is None else float(value)
        note = body.get("note", "")
        if note is None:
            note = ""
        if not isinstance(note, str) or len(note) > 500:
            raise PaperTradingError("note must be text of at most 500 characters")
        facts = self._coin_facts(base, self.now())
        last_luna = self._latest_narration(base)
        entry_id = f"j-{uuid.uuid4().hex[:12]}"
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO cotrader_journal(entry_id, at, coin, action, price, amount_usdt, note, rule_state, ai_stance) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry_id, iso_utc(self.now()), base, action, numbers["price"], numbers["amount_usdt"], note.strip(),
                 facts["state"], None if last_luna is None else last_luna["stance"]),
            )
        return next(entry for entry in self.journal(base) if entry["id"] == entry_id)

    def journal(self, base: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM cotrader_journal"
        params: tuple = ()
        if base is not None:
            sql += " WHERE coin=?"
            params = (base,)
        prices, _ = self.prices()
        result = []
        for row in self.store._query(sql + " ORDER BY at DESC, rowid DESC", params):
            now_price = (prices.get(row["coin"]) or {}).get("price")
            agreed = None
            if row["rule_state"] is not None:
                in_trend = row["rule_state"] == "HOLD"
                agreed = {"buy": in_trend, "hold": in_trend, "sell": not in_trend, "skip": not in_trend}[row["action"]]
            result.append({
                "id": row["entry_id"], "at": row["at"], "coin": row["coin"], "action": row["action"],
                "price": row["price"], "amount_usdt": row["amount_usdt"], "note": row["note"],
                "rule_state": row["rule_state"], "ai_stance": row["ai_stance"], "price_now": now_price,
                "change_since": _r(now_price / row["price"] - 1) if now_price and row["price"] else None,
                "rule_agreed": agreed,
            })
        return result


# ---------------------------------------------------------------------------
# scheduler
# ---------------------------------------------------------------------------


class CoTraderScheduler:
    """Background daily loop: refresh after the 00:00 UTC close (with retries), record events and
    state-change reviews, run Jev at ~00:05 and the Luna briefing at ~00:10. Started only by
    ``paper-server --cotrader``; it has no exchange write path."""

    def __init__(
        self,
        service: SpotCoTrader,
        *,
        poll_seconds: float = 30.0,
        refresh_delay_seconds: int = 60,
        jev_delay_seconds: int = 300,
        briefing_delay_seconds: int = 600,
        retry_seconds: int = 120,
        max_refresh_attempts: int = 30,
    ) -> None:
        self.service = service
        self.poll_seconds = poll_seconds
        self.refresh_delay = refresh_delay_seconds
        self.jev_delay = jev_delay_seconds
        self.briefing_delay = briefing_delay_seconds
        self.retry_seconds = retry_seconds
        self.max_refresh_attempts = max_refresh_attempts
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.last_tick: dict[str, Any] | None = None

    def _run_state(self, key: str) -> dict[str, Any]:
        rows = self.service.store._query("SELECT * FROM cotrader_runs WHERE run_key=?", (key,))
        return dict(rows[0]) if rows else {"run_key": key, "status": "pending", "attempts": 0, "last_attempt_at": None}

    def _save_run(self, key: str, status: str, attempts: int, now: datetime, detail: dict[str, Any]) -> None:
        stamp = iso_utc(now)
        with self.service.store.transaction() as db:
            db.execute(
                "INSERT INTO cotrader_runs(run_key, status, attempts, last_attempt_at, detail_json, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(run_key) DO UPDATE SET status=excluded.status, "
                "attempts=excluded.attempts, last_attempt_at=excluded.last_attempt_at, detail_json=excluded.detail_json, "
                "updated_at=excluded.updated_at",
                (key, status, attempts, stamp, json.dumps(detail, default=str), stamp),
            )

    def tick(self, now: datetime | None = None) -> dict[str, Any]:
        now = (now or self.service.now()).astimezone(timezone.utc)
        boundary = _floor_day(now)
        elapsed = int(now.timestamp()) - boundary
        key = f"refresh:{_iso(boundary)}"
        run = self._run_state(key)
        out: dict[str, Any] = {"boundary": _iso(boundary)}
        if self.service.holdings is not None:
            try:  # READ-ONLY Gate spot wallet sync, every 15 minutes when an account is configured
                synced = self.service.holdings.sync_if_due()
                if synced is not None:
                    out["holdings_sync"] = ((synced.get("sources") or {}).get("gate") or {}).get("status")
            except Exception:
                out["holdings_sync"] = "ERROR"
        if elapsed >= self.refresh_delay and run["status"] not in {"done", "gave_up"}:
            last = run["last_attempt_at"]
            due = last is None or (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() >= self.retry_seconds
            if due:
                attempts = int(run["attempts"]) + 1
                refreshed = self.service.refresh(now)
                processed = self.service.process_close(now)
                status = "done" if refreshed["complete"] else (
                    "gave_up" if attempts >= self.max_refresh_attempts else "retrying")
                self._save_run(key, status, attempts, now, {"refresh": refreshed, "process": processed})
                run = {**run, "status": status}
                out["refresh"] = status
        if run["status"] in {"done", "gave_up"}:
            if elapsed >= self.jev_delay:
                out["jev"] = self.service.jev_daily(now).get("status")
            if elapsed >= self.briefing_delay:
                result = self.service.daily_briefing(now)
                out["briefing"] = "ok" if result["briefing"] else result["blocked_reason"]
        self.last_tick = out
        return out

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                self.last_error = None
            except Exception as exc:  # never let the loop die; never print request data
                self.last_error = type(exc).__name__
            self._stop.wait(self.poll_seconds)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cotrader-scheduler", daemon=True)
        self._thread.start()

    def shutdown(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None


def attach_cotrader(runtime: Any, **kwargs: Any) -> SpotCoTrader:
    """Server wiring: a co-trader that reads the runtime's Holdings, whose alignment reads the rule."""

    from .holdings import Holdings

    service = SpotCoTrader(runtime, **kwargs)
    runtime.holdings = Holdings(runtime, rule_state_provider=service.rule_state)
    service.holdings = runtime.holdings
    return service
