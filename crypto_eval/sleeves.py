"""Trend sleeves engine (EXP-002): three long/short futures strategies on separate sub-accounts.

Evidence (reports/paper-500/strategy-research.md, ~4 years of Gate 4h data, 7 coins): each
sleeve alone is Sharpe ~0.6-1.0; blended at equal risk they reached Sharpe ~1.5 with ~13% max
drawdown because their returns are nearly uncorrelated.

  donchian  4h breakout of the prior N bars; stop 2 ATR; trailing stop 6 ATR ratcheted on 4h
            closes; no fixed target (winners run). Long above the channel, short below.
  tsmom     daily time-series momentum: sign of the 60-day return, sized to a volatility target.
  xsmom     weekly cross-sectional momentum: long the k strongest, short the k weakest (60 d),
            rebalanced at 00:00 UTC Friday on Thursday's close (UTC day number % 7 == 0).

Each sleeve is its own cohort/wallet (a sub-account) with a third of the capital, so the
sleeves never fight over a symbol. Every order goes through the RiskEngine, kill switch,
automation pauses and feed checks; every fill is PAPER. Nothing here can place a real order.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from .paper_contracts import INTENT_SCHEMA_VERSION, PaperTradingError, TradingIntent, iso_utc, parse_utc

ENGINE_ID = "sleeves_v1"
H4 = 4 * 3600
COHORTS = {"donchian": "sleeve-don", "tsmom": "sleeve-ts", "xsmom": "sleeve-xs"}

DEFAULT_SLEEVES: dict[str, Any] = {
    "universe": ["BTCUSDT", "ETHUSDT", "NEARUSDT", "SEIUSDT", "SUIUSDT", "AVAXUSDT", "ENAUSDT"],
    "gross_cap": 3.0,
    # Donchian has tight ATR stops, so 3x isolated margin is safe. The momentum sleeves hold through
    # ordinary weekly swings (as in the research); 2x keeps liquidation (~50%) beyond their wide
    # catastrophe stop instead of cutting positions on noise.
    "donchian": {"enabled": True, "leverage": 3, "n": 30, "stop_atr": 2.0, "trail_atr": 6.0, "risk": 0.009, "atr_floor_pct": 0.004},
    "tsmom": {"enabled": True, "leverage": 2, "look_days": 60, "vol_target": 0.028, "vol_days": 30, "rebalance_band": 0.25},
    "xsmom": {"enabled": True, "leverage": 2, "look_days": 60, "k": 2, "vol_target": 0.0285, "vol_days": 30, "rebalance_band": 0.25},
    "catastrophe_stop_vol_mult": 3.0,
    "catastrophe_stop_max_pct": 0.4,
    "history_bars": 450,
    # Paper transfers between the sleeve wallets back to equal capital on the 1st of each month.
    # Without it the best sleeve slowly owns the book and the diversification that gives the blend
    # its edge fades (4-year replay: Sharpe 1.17 never rebalanced vs 1.32 monthly).
    "capital_rebalance": "monthly",
    # Scale a momentum sleeve's whole book to what its isolated margin can fund (see effective_gross_cap).
    # Off by default: at 1x risk the book almost always fits, and the 4-year replay was better without it
    # (Sharpe 1.44 vs 1.29). At 2x risk it stops the RiskEngine from squeezing whichever legs go last
    # (98 -> 8 margin rejections, no one-sided long/short books; Sharpe 1.28 -> 1.33).
    "margin_scaling": False,
}


def validate_sleeves(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PaperTradingError("sleeves must be an object")
    merged = {**DEFAULT_SLEEVES, **{k: v for k, v in value.items() if k not in {"donchian", "tsmom", "xsmom"}}}
    for key in ("donchian", "tsmom", "xsmom"):
        merged[key] = {**DEFAULT_SLEEVES[key], **(value.get(key) or {})}
    unknown = set(value) - set(DEFAULT_SLEEVES)
    if unknown:
        raise PaperTradingError(f"sleeves has unsupported fields: {', '.join(sorted(unknown))}")
    universe = merged["universe"]
    if not isinstance(universe, list) or not 2 <= len(universe) <= 20 or len(set(universe)) != len(universe) or not all(
        isinstance(s, str) and s.isalnum() and s.isupper() and s.endswith("USDT") for s in universe
    ):
        raise PaperTradingError("sleeves.universe must list 2-20 unique USDT perpetual symbols")
    for key in ("donchian", "tsmom", "xsmom"):
        lev = merged[key]["leverage"]
        if isinstance(lev, bool) or not isinstance(lev, int) or not 1 <= lev <= 5:
            raise PaperTradingError(f"sleeves.{key}.leverage must be an integer from 1 to 5")
    checks = [
        ("gross_cap", merged["gross_cap"], 0.5, 5.0), ("catastrophe_stop_vol_mult", merged["catastrophe_stop_vol_mult"], 1.0, 10.0),
        ("catastrophe_stop_max_pct", merged["catastrophe_stop_max_pct"], 0.05, 0.45), ("history_bars", merged["history_bars"], 200, 2000),
        ("donchian.n", merged["donchian"]["n"], 5, 200), ("donchian.stop_atr", merged["donchian"]["stop_atr"], 0.5, 10),
        ("donchian.trail_atr", merged["donchian"]["trail_atr"], 1, 20), ("donchian.risk", merged["donchian"]["risk"], 0.001, 0.03),
        ("tsmom.look_days", merged["tsmom"]["look_days"], 10, 250), ("tsmom.vol_target", merged["tsmom"]["vol_target"], 0.002, 0.06),
        ("xsmom.look_days", merged["xsmom"]["look_days"], 10, 250), ("xsmom.vol_target", merged["xsmom"]["vol_target"], 0.002, 0.06),
        ("xsmom.k", merged["xsmom"]["k"], 1, 5),
        ("tsmom.vol_days", merged["tsmom"]["vol_days"], 5, 250), ("xsmom.vol_days", merged["xsmom"]["vol_days"], 5, 250),
        ("tsmom.rebalance_band", merged["tsmom"]["rebalance_band"], 0, 1), ("xsmom.rebalance_band", merged["xsmom"]["rebalance_band"], 0, 1),
    ]
    for name, item, low, high in checks:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)) or not low <= float(item) <= high:
            raise PaperTradingError(f"sleeves.{name} must be between {low} and {high}")
    if not isinstance(merged["margin_scaling"], bool):
        raise PaperTradingError("sleeves.margin_scaling must be boolean")
    if merged["capital_rebalance"] not in {"monthly", "off"}:
        raise PaperTradingError("sleeves.capital_rebalance must be monthly or off")
    if 2 * int(merged["xsmom"]["k"]) > len(universe):
        raise PaperTradingError("sleeves.xsmom.k is too large for the universe")
    for key in ("donchian", "tsmom", "xsmom"):
        extra = set(value.get(key) or {}) - set(DEFAULT_SLEEVES[key])
        if extra:
            raise PaperTradingError(f"sleeves.{key} has unsupported fields: {', '.join(sorted(extra))}")
        if not isinstance(merged[key]["enabled"], bool):
            raise PaperTradingError(f"sleeves.{key}.enabled must be boolean")
    # Last, once every field is known to be well-typed: the fetched 4h history must reach back far enough.
    # N consecutive 4h bars ending at 00:00 give N // 6 daily closes; day d and d - look_days need look_days + 1.
    days_covered = int(merged["history_bars"]) // 6
    for key in ("tsmom", "xsmom"):
        if merged[key]["enabled"]:
            needed = max(int(merged[key]["look_days"]), int(merged[key]["vol_days"])) + 1
            if days_covered < needed:
                raise PaperTradingError(f"sleeves.history_bars ({merged['history_bars']} x 4h = {days_covered} daily closes) must cover "
                                        f"{key} look_days/vol_days + 1 = {needed}")
    return merged


# ------------------------------------------------------------------ pure signal functions
def wilder_atr(bars: list[dict[str, Any]], n: int = 14) -> list[float]:
    out, prev = [], None
    for bar in bars:
        h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
        tr = h - l if prev is None else max(h - l, abs(h - prev), abs(l - prev))
        out.append(tr if not out else (out[-1] * (n - 1) + tr) / n)
        prev = c
    return out


def donchian_signal(bars: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any] | None:
    """Breakout of the prior ``n`` closed 4h bars, evaluated on the latest closed bar."""

    n = int(params["n"])
    if len(bars) < n + 20:
        return None
    last = bars[-1]
    close = float(last["close"])
    window = bars[-n - 1:-1]
    hi = max(float(b["high"]) for b in window)
    lo = min(float(b["low"]) for b in window)
    atr = max(wilder_atr(bars)[-1], close * float(params["atr_floor_pct"]))
    if close > hi:
        side = "long"
    elif close < lo:
        side = "short"
    else:
        return None
    return {"side": side, "close": close, "atr": atr, "stop_dist": params["stop_atr"] * atr, "trail_dist": params["trail_atr"] * atr}


def trail_stop(side: str, stop: float, best: float, close: float, atr: float, trail_atr: float) -> tuple[float, float]:
    """Ratchet a trailing stop on a closed bar. The stop only ever moves to reduce risk."""

    if side == "long":
        best = max(best, close)
        return best, max(stop, best - trail_atr * atr)
    best = min(best, close)
    return best, min(stop, best + trail_atr * atr)


def daily_closes(bars: list[dict[str, Any]]) -> dict[int, float]:
    """UTC-day closes from 4h bars (the bar that closes at 00:00)."""

    out = {}
    for bar in bars:
        closed = parse_utc(bar["close_time"], "bar.close_time")
        if closed.hour == 0 and closed.minute == 0:
            out[int(closed.timestamp()) // 86400 - 1] = float(bar["close"])
    return out


def _daily_vol(closes: dict[int, float], day: int, days: int) -> float | None:
    series = [closes[d] for d in range(day - days, day + 1) if d in closes]
    if len(series) < days * 0.8:
        return None
    rets = [b / a - 1 for a, b in zip(series, series[1:])]
    return math.sqrt(sum(r * r for r in rets) / len(rets)) or None


def _cap(weights: dict[str, float], gross_cap: float) -> dict[str, float]:
    gross = sum(abs(w) for w in weights.values())
    return {s: w * gross_cap / gross for s, w in weights.items()} if gross > gross_cap else weights


def tsmom_weights(closes_by_symbol: dict[str, dict[int, float]], day: int, params: dict[str, Any], gross_cap: float) -> dict[str, float]:
    signals = {}
    look = int(params["look_days"])
    for symbol, closes in closes_by_symbol.items():
        if day not in closes or day - look not in closes:
            continue
        vol = _daily_vol(closes, day, int(params["vol_days"]))
        if vol is None:
            continue
        signals[symbol] = (closes[day] / closes[day - look] - 1, vol)
    weights = {s: (1 if m > 0 else -1) * params["vol_target"] / v / max(1, len(signals)) for s, (m, v) in signals.items() if m != 0}
    return _cap(weights, gross_cap)


def xsmom_weights(closes_by_symbol: dict[str, dict[int, float]], day: int, params: dict[str, Any], gross_cap: float) -> dict[str, float]:
    look, k = int(params["look_days"]), int(params["k"])
    scores = {}
    for symbol, closes in closes_by_symbol.items():
        if day not in closes or day - look not in closes:
            continue
        vol = _daily_vol(closes, day, int(params["vol_days"]))
        if vol is None:
            continue
        scores[symbol] = (closes[day] / closes[day - look] - 1, vol)
    ranked = sorted(scores, key=lambda s: scores[s][0])
    if len(ranked) < 2 * k:
        return {}
    weights = {s: params["vol_target"] / scores[s][1] / k for s in ranked[-k:]}
    weights.update({s: -params["vol_target"] / scores[s][1] / k for s in ranked[:k]})
    return _cap(weights, gross_cap)


# ------------------------------------------------------------------ engine
SLEEVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sleeve_decisions(
    experiment_id TEXT NOT NULL,
    boundary TEXT NOT NULL,
    sleeve TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(experiment_id, boundary, sleeve, symbol, action)
);
CREATE TABLE IF NOT EXISTS sleeve_ticks(
    experiment_id TEXT NOT NULL,
    boundary TEXT NOT NULL,
    status TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(experiment_id, boundary)
);
CREATE TABLE IF NOT EXISTS wallet_transfers(
    transfer_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    from_cohort TEXT NOT NULL,
    to_cohort TEXT NOT NULL,
    amount REAL NOT NULL CHECK(amount > 0),
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sleeve_trails(
    position_id TEXT PRIMARY KEY,
    best REAL NOT NULL,
    trail_dist REAL NOT NULL,
    updated_at TEXT NOT NULL
);
"""


MARGIN_USE = 0.99  # scale only when the book cannot be funded at all (1% left for fees)


def effective_gross_cap(sleeves: dict[str, Any], sleeve: str) -> float:
    """Gross exposure (sum of |weight|, as a multiple of the sleeve's equity) the sleeve can actually carry.

    Isolated margin needs notional / leverage per position, so gross above leverage x equity cannot be
    funded. If the target book were larger, the RiskEngine would shrink whichever legs happen to go last,
    which can leave a long/short sleeve one-sided. Capping here scales every leg evenly instead."""

    if not sleeves.get("margin_scaling"):
        return float(sleeves["gross_cap"])
    return min(float(sleeves["gross_cap"]), MARGIN_USE * int(sleeves[sleeve]["leverage"]))


def capital_cohorts(config: dict[str, Any]) -> list[str]:
    if config.get("strategy_engine") == ENGINE_ID:
        return [COHORTS[k] for k in ("donchian", "tsmom", "xsmom")]
    return ["primary"]


def cohort_starting_balance(config: dict[str, Any], cohort: str) -> float:
    """A new wallet's starting balance: a sleeve gets its equal share, everything else the full balance.
    Every wallet-creation path uses this, so a sleeve wallet can never be seeded with the whole book."""

    total = float(config["starting_balance_usdt"])
    cohorts = capital_cohorts(config)
    return total / len(cohorts) if cohort in cohorts and len(cohorts) > 1 else total


def stream_symbols(config: dict[str, Any]) -> list[str]:
    """Symbols the live feed must cover: the experiment's symbols plus the sleeve universe."""

    symbols = list(config["symbols"])
    if config.get("strategy_engine") == ENGINE_ID and config.get("sleeves"):
        symbols += [s for s in config["sleeves"]["universe"] if s not in symbols]
    return symbols


class SleeveEngine:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.store = runtime.store

    # -------------------------------------------------------------- helpers
    def _config(self) -> dict[str, Any]:
        return self.store.experiment()["config"]

    def _open_positions(self, experiment_id: str, cohort: str) -> list[dict[str, Any]]:
        return [p for p in self.store.open_positions(experiment_id) if p["cohort"] == cohort]

    def ensure_wallets(self, experiment_id: str, config: dict[str, Any], as_of: str) -> None:
        with self.store.transaction() as db:
            for cohort in capital_cohorts(config):
                self.store._record_equity_locked(db, experiment_id, cohort, as_of, "GATE_USDT_PUBLIC"
                                                 if config["market_data_mode"] == "gate_usdt" else "FIXTURE",
                                                 starting_balance=cohort_starting_balance(config, cohort))

    def _combined_risk(self, experiment_id: str, config: dict[str, Any]) -> tuple[float, float]:
        """Portfolio-level daily loss and drawdown across the three sleeves."""

        daily, dd = 0.0, 0.0
        start = sum(float(self.store.wallet_summary(experiment_id, c)["starting_balance"]) for c in capital_cohorts(config))
        equity = sum(float(self.store.wallet_summary(experiment_id, c)["equity"]) for c in capital_cohorts(config))
        rows = self.store._query(
            "SELECT as_of, SUM(equity) AS e FROM equity WHERE experiment_id=? AND cohort IN (%s) GROUP BY as_of ORDER BY as_of"
            % ",".join("?" * 3), (experiment_id, *capital_cohorts(config)))
        peak = start
        for row in rows:
            peak = max(peak, float(row["e"]))
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        now = self.runtime._clock().astimezone(timezone.utc)
        day_start = iso_utc(now.replace(hour=0, minute=0, second=0, microsecond=0))
        before = [r for r in rows if r["as_of"] < day_start]
        base = float(before[-1]["e"]) if before else start
        daily = max(0.0, (base - equity) / base) if base > 0 else 0.0
        return daily, dd

    def _entries_blocked(self, config: dict[str, Any], symbol: str) -> str | None:
        from .execution_safety import KILL_RANK

        portfolio = self.runtime.portfolio
        level = portfolio.kill_switch()["level"]
        if KILL_RANK[level] >= KILL_RANK["NO_NEW_ENTRIES"]:
            return f"KILL_SWITCH_{level}"
        automation = portfolio.settings()["automation"]
        if automation["emergency_stop"] or automation["new_entries_paused"]:
            return "AUTOMATION_PAUSED"
        feed = self.runtime.feed_status(config, symbol)
        if feed["required"] and not feed["fresh"] and config["stale_feed_blocks_entries"]:
            return "STALE_MARKET_FEED"
        return None

    def _reference(self, provider: Any, symbol: str, now: datetime, fallback: float) -> tuple[float, str]:
        try:
            snapshot = provider.fetch_snapshot(symbol, now)
            return float(snapshot.candles_1m[-1]["close"]), snapshot.data_origin
        except Exception:
            return fallback, getattr(provider, "data_origin", "GATE_USDT_PUBLIC")

    def _decided(self, experiment_id: str, boundary: str, sleeve: str, symbol: str, action: str, detail: dict[str, Any]) -> bool:
        """Record a decision once; returns False if it was already recorded (idempotent retry)."""

        import json

        with self.store.transaction() as db:
            cur = db.execute(
                "INSERT OR IGNORE INTO sleeve_decisions(experiment_id, boundary, sleeve, symbol, action, detail_json, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (experiment_id, boundary, sleeve, symbol, action, json.dumps(detail, sort_keys=True, default=str), iso_utc(self.runtime._clock())))
            return cur.rowcount == 1

    # -------------------------------------------------------------- orders
    def _open(self, experiment_id: str, config: dict[str, Any], sleeves: dict[str, Any], cohort: str, sleeve: str, symbol: str,
              side: str, reference: float, origin: str, stop_dist: float, risk_fraction: float, boundary: str, as_of: datetime,
              trail_dist: float | None = None) -> dict[str, Any]:
        wallet = self.store.wallet_summary(experiment_id, cohort)
        positions = self._open_positions(experiment_id, cohort)
        entry = reference
        stop = entry - stop_dist if side == "long" else entry + stop_dist
        target = entry * 20 if side == "long" else entry * 0.02  # no fixed target: exits are stops/trails/rebalances
        if stop <= 0:
            return {"status": "skipped", "reason": "stop below zero"}
        intent = TradingIntent.from_dict({
            "schema_version": INTENT_SCHEMA_VERSION, "action": "open", "symbol": symbol, "side": side, "order_type": "market",
            "entry_price": entry, "stop_price": stop, "target_price": target, "reduce_only": False, "reduce_fraction": None,
            "position_id": None, "reason": f"{sleeve} sleeve entry", "source_arm": "quant", "as_of": iso_utc(as_of),
        })
        leverage = int(sleeves[sleeve]["leverage"])
        risk_config = {**config, "symbols": list(sleeves["universe"]), "max_positions": len(sleeves["universe"]),
                       "risk_per_trade": max(1e-6, min(0.25, risk_fraction)), "primary_leverage": leverage,
                       "max_drawdown_stop": 1.0, "max_daily_loss": 1.0, "max_consecutive_losses": 100,
                       "max_stop_distance_pct": float(sleeves["catastrophe_stop_max_pct"])}
        risk = self.runtime.risk_engine.evaluate(
            intent, risk_config, equity=float(wallet["equity"]), margin_used=float(wallet["margin_used"]),
            open_positions=[{**p, "cohort": "primary"} for p in positions], data_cutoff=iso_utc(as_of), decision_as_of=as_of,
            leverage=leverage,
        )
        cycle_id = f"{experiment_id}:{sleeve}:{symbol}:{int(parse_utc(boundary, 'boundary').timestamp())}"
        execution = {"cohort": cohort, "risk": risk.to_dict(), "intent": intent.to_dict(), "reference_price": reference,
                     "reference_price_source": "sleeve_reference", "slippage_bps": config["slippage_bps"], "fee_rate": config["taker_fee_rate"],
                     "as_of": iso_utc(as_of), "data_origin": origin, "market_regime": None, "regime_source": ENGINE_ID}
        with self.store.transaction() as db:
            receipt = self.store._insert_order_locked(db, experiment_id, cycle_id, execution)
            if receipt.get("status") == "filled" or receipt.get("filled_quantity"):
                if trail_dist:
                    db.execute("INSERT OR REPLACE INTO sleeve_trails(position_id, best, trail_dist, updated_at) VALUES(?, ?, ?, ?)",
                               (receipt["position_id"], entry, trail_dist, iso_utc(as_of)))
            self.store._record_equity_locked(db, experiment_id, cohort, iso_utc(as_of), origin)
        return {"status": receipt.get("status"), "code": risk.code, "quantity": risk.quantity, "position_id": receipt.get("position_id")}

    def _close(self, experiment_id: str, config: dict[str, Any], position: dict[str, Any], mark: float, origin: str,
               boundary: str, as_of: datetime, fraction: float = 1.0) -> dict[str, Any]:
        intent = TradingIntent.from_dict({
            "schema_version": INTENT_SCHEMA_VERSION, "action": "close" if fraction >= 1 else "reduce", "symbol": position["symbol"],
            "side": position["side"], "order_type": "market", "entry_price": None, "stop_price": None, "target_price": None,
            "reduce_only": True, "reduce_fraction": None if fraction >= 1 else fraction, "position_id": position["position_id"],
            "reason": "sleeve rebalance", "source_arm": position["source_arm"], "as_of": iso_utc(as_of),
        })
        lookup = {position["position_id"]: {**position, "cohort": "primary"}}
        risk = self.runtime.risk_engine.evaluate(intent, config, equity=0.0, margin_used=0.0, open_positions=[], data_cutoff=iso_utc(as_of),
                                                 decision_as_of=as_of, position_lookup=lookup)
        side_sign = -1 if position["side"] == "long" else 1
        fill = mark * (1 + side_sign * config["slippage_bps"] / 10_000)
        result = self.store.execute_reduce(experiment_id, intent, risk, mark_price=fill, fee_rate=config["taker_fee_rate"],
                                           as_of=iso_utc(as_of), data_origin=origin, slippage_cost=abs(fill - mark) * float(risk.quantity),
                                           order_key=f"sleeve:{boundary}", cohorts={position["cohort"]})
        with self.store.transaction() as db:
            self.store._record_equity_locked(db, experiment_id, position["cohort"], iso_utc(as_of), origin)
        return result

    # -------------------------------------------------------------- sleeves
    def _rebalance(self, experiment_id: str, config: dict[str, Any], sleeves: dict[str, Any], sleeve: str, targets: dict[str, float],
                   bars: dict[str, list[dict[str, Any]]], provider: Any, boundary: str, now: datetime, summary: dict[str, Any],
                   only: set[str] | None = None) -> None:
        cohort = COHORTS[sleeve]
        params = sleeves[sleeve]
        equity = float(self.store.wallet_summary(experiment_id, cohort)["equity"])
        open_by_symbol = {p["symbol"]: p for p in self._open_positions(experiment_id, cohort)}
        for symbol in sleeves["universe"]:
            if only is not None and symbol not in only:
                continue
            weight = targets.get(symbol, 0.0)
            if symbol not in bars or not bars[symbol]:
                continue
            last_close = float(bars[symbol][-1]["close"])
            mark, origin = self._reference(provider, symbol, now, last_close)
            target_notional = weight * equity
            current = open_by_symbol.get(symbol)
            current_signed = 0.0
            if current:
                current_signed = (1 if current["side"] == "long" else -1) * float(current["quantity"]) * mark
            same_side = current and target_notional and (current_signed > 0) == (target_notional > 0)
            band = float(params["rebalance_band"])
            if current and not same_side:
                if self._decided(experiment_id, boundary, sleeve, symbol, "close", {"target": weight}):
                    self._close(experiment_id, config, current, mark, origin, boundary, now)
                    summary.setdefault(sleeve, []).append(f"close {symbol}")
                current = None
            elif current and same_side:
                ratio = abs(current_signed) / abs(target_notional)
                if ratio > 1 + band:
                    fraction = 1 - 1 / ratio
                    if self._decided(experiment_id, boundary, sleeve, symbol, "reduce", {"ratio": ratio}):
                        self._close(experiment_id, config, current, mark, origin, boundary, now, fraction=fraction)
                        summary.setdefault(sleeve, []).append(f"reduce {symbol} {fraction:.0%}")
                    continue
                if ratio >= 1 - band:
                    continue
                if self._decided(experiment_id, boundary, sleeve, symbol, "resize-close", {"ratio": ratio}):
                    self._close(experiment_id, config, current, mark, origin, boundary, now)
                current = None
            if current is None and target_notional:
                blocked = self._entries_blocked(config, symbol)
                if blocked:
                    summary.setdefault("blocked", []).append(f"{sleeve} {symbol} {blocked}")
                    # Retried on the next 4h ticks with this slot's weight until it fills or the next slot replaces it.
                    summary.setdefault("pending", {}).setdefault(sleeve, {})[symbol] = weight
                    continue
                closes = daily_closes(bars[symbol])
                day = max(closes) if closes else None
                vol = _daily_vol(closes, day, int(params["vol_days"])) if day is not None else None
                if not vol:
                    continue
                stop_pct = min(float(sleeves["catastrophe_stop_max_pct"]), float(sleeves["catastrophe_stop_vol_mult"]) * vol * math.sqrt(10))
                stop_dist = mark * stop_pct
                risk_fraction = abs(target_notional) * stop_pct / max(equity, 1e-9)
                side = "long" if target_notional > 0 else "short"
                if self._decided(experiment_id, boundary, sleeve, symbol, "open", {"weight": weight, "stop_pct": stop_pct}):
                    result = self._open(experiment_id, config, sleeves, cohort, sleeve, symbol, side, mark, origin, stop_dist,
                                        risk_fraction, boundary, now)
                    summary.setdefault(sleeve, []).append(f"open {side} {symbol} {result.get('code')}")

    def _donchian(self, experiment_id: str, config: dict[str, Any], sleeves: dict[str, Any], bars: dict[str, list[dict[str, Any]]],
                  provider: Any, boundary: str, now: datetime, summary: dict[str, Any]) -> None:
        params = sleeves["donchian"]
        cohort = COHORTS["donchian"]
        open_by_symbol = {p["symbol"]: p for p in self._open_positions(experiment_id, cohort)}
        for symbol in sleeves["universe"]:
            lane = bars.get(symbol) or []
            if len(lane) < int(params["n"]) + 20:
                continue
            close = float(lane[-1]["close"])
            position = open_by_symbol.get(symbol)
            if position:
                self._ratchet(position, lane, params, now)
                continue
            signal = donchian_signal(lane, params)
            if not signal:
                continue
            blocked = self._entries_blocked(config, symbol)
            if blocked:
                summary.setdefault("blocked", []).append(f"donchian {symbol} {blocked}")
                continue
            if self._decided(experiment_id, boundary, "donchian", symbol, "open", signal):
                mark, origin = self._reference(provider, symbol, now, close)
                result = self._open(experiment_id, config, sleeves, cohort, "donchian", symbol, signal["side"], mark, origin,
                                    signal["stop_dist"], float(params["risk"]), boundary, now, trail_dist=signal["trail_dist"])
                summary.setdefault("donchian", []).append(f"open {signal['side']} {symbol} {result.get('code')}")

    def _has_rebalanced(self, experiment_id: str, sleeve: str) -> bool:
        return bool(self.store._query("SELECT 1 FROM sleeve_decisions WHERE experiment_id=? AND sleeve=? LIMIT 1", (experiment_id, sleeve)))

    def _rebalance_capital(self, experiment_id: str, config: dict[str, Any], boundary: str, now: datetime, summary: dict[str, Any]) -> None:
        """Move free cash between sleeve wallets toward equal equity. Only free cash moves (equity minus
        locked margin), never an open position, and each transfer is recorded once per boundary."""

        cohorts = capital_cohorts(config)
        wallets = {c: self.store.wallet_summary(experiment_id, c) for c in cohorts}
        equity = {c: float(w["equity"]) for c, w in wallets.items()}
        target = sum(equity.values()) / len(cohorts)
        if target <= 0 or max(abs(e - target) for e in equity.values()) < 0.02 * target:
            return
        free = {c: max(0.0, float(w["equity"]) - float(w["margin_used"])) for c, w in wallets.items()}
        give = {c: min(equity[c] - target, free[c]) for c in cohorts if equity[c] > target}
        need = {c: target - equity[c] for c in cohorts if equity[c] < target}
        origin = "GATE_USDT_PUBLIC" if config["market_data_mode"] == "gate_usdt" else "FIXTURE"
        moved = []
        with self.store.transaction() as db:
            for donor in sorted(give, key=give.get, reverse=True):
                for taker in sorted(need, key=need.get, reverse=True):
                    amount = round(min(give[donor], need[taker]), 8)
                    if amount < 0.01:
                        continue
                    cur = db.execute(
                        "INSERT OR IGNORE INTO wallet_transfers(transfer_id, experiment_id, as_of, from_cohort, to_cohort, amount, reason) "
                        "VALUES(?, ?, ?, ?, ?, ?, 'monthly_capital_rebalance')",
                        (f"{experiment_id}:{boundary}:{donor}:{taker}", experiment_id, iso_utc(now), donor, taker, amount))
                    if cur.rowcount != 1:
                        continue
                    db.execute("UPDATE wallets SET cash_balance=cash_balance-? WHERE experiment_id=? AND cohort=?", (amount, experiment_id, donor))
                    db.execute("UPDATE wallets SET cash_balance=cash_balance+? WHERE experiment_id=? AND cohort=?", (amount, experiment_id, taker))
                    give[donor] -= amount
                    need[taker] -= amount
                    moved.append(f"{donor}->{taker} {amount:.2f}")
            for cohort in cohorts:
                self.store._record_equity_locked(db, experiment_id, cohort, iso_utc(now), origin)
        if moved:
            summary["capital"] = moved

    def status(self) -> dict[str, Any] | None:
        """Per-sleeve sub-account view for the campaign panel (read-only)."""

        import json

        experiment = self.store.experiment()
        config = experiment["config"]
        if config.get("strategy_engine") != ENGINE_ID:
            return None
        experiment_id = experiment["experiment_id"]
        open_positions = self.store.open_positions(experiment_id)
        rows = []
        for sleeve, cohort in COHORTS.items():
            wallet = self.store.wallet_summary(experiment_id, cohort)
            net = self.store._query(
                "SELECT COALESCE(SUM(CASE WHEN to_cohort=? THEN amount ELSE 0 END),0) - "
                "COALESCE(SUM(CASE WHEN from_cohort=? THEN amount ELSE 0 END),0) AS net FROM wallet_transfers WHERE experiment_id=?",
                (cohort, cohort, experiment_id))[0]["net"]
            equity, starting = float(wallet["equity"]), float(wallet["starting_balance"])
            held = [p for p in open_positions if p["cohort"] == cohort]
            rows.append({
                "sleeve": sleeve, "cohort": cohort, "enabled": bool(config["sleeves"][sleeve]["enabled"]),
                "leverage": int(config["sleeves"][sleeve]["leverage"]), "equity_usdt": round(equity, 4),
                "net_transfers_usdt": round(float(net), 4), "pnl_usdt": round(equity - starting - float(net), 4),
                "margin_used_usdt": round(float(wallet["margin_used"]), 4),
                "long": sorted(p["symbol"] for p in held if p["side"] == "long"),
                "short": sorted(p["symbol"] for p in held if p["side"] == "short"),
            })
        last = self.store._query("SELECT boundary, summary_json FROM sleeve_ticks WHERE experiment_id=? ORDER BY boundary DESC LIMIT 1",
                                 (experiment_id,))
        tick = json.loads(last[0]["summary_json"]) if last else None
        return {"engine": ENGINE_ID, "universe": list(config["sleeves"]["universe"]), "sleeves": rows,
                "last_tick": None if tick is None else {"boundary": last[0]["boundary"], "blocked": tick.get("blocked", []),
                                                         "combined_drawdown": tick.get("combined_drawdown"),
                                                         "combined_daily_loss": tick.get("combined_daily_loss")}}

    # -------------------------------------------------------------- tick
    def tick(self, now: datetime | None = None) -> dict[str, Any]:
        """Run the sleeves on the latest closed 4h bar (idempotent per boundary)."""

        import json

        now = (now or self.runtime._clock()).astimezone(timezone.utc)
        experiment = self.store.experiment()
        config = experiment["config"]
        if experiment["status"] != "running" or config.get("strategy_engine") != ENGINE_ID:
            return {"status": "idle"}
        from .execution_safety import KILL_RANK

        if KILL_RANK[self.runtime.portfolio.kill_switch()["level"]] >= KILL_RANK["FULL_AUTOMATION_HALT"]:
            return {"status": "halted"}
        experiment_id = experiment["experiment_id"]
        epoch = int(now.timestamp())
        boundary_ts = epoch - epoch % H4
        if epoch < boundary_ts + int(config["schedule_delay_seconds"]):
            return {"status": "waiting"}
        boundary = iso_utc(datetime.fromtimestamp(boundary_ts, timezone.utc))
        if self.store._query("SELECT 1 FROM sleeve_ticks WHERE experiment_id=? AND boundary=? AND status='done'", (experiment_id, boundary)):
            return {"status": "already_done", "boundary": boundary}
        sleeves = config["sleeves"]
        self.ensure_wallets(experiment_id, config, iso_utc(now))
        provider = self.runtime._market(config)
        bars: dict[str, list[dict[str, Any]]] = {}
        for symbol in sleeves["universe"]:
            try:
                lane = provider.fetch_history(symbol, "4h", bars=int(sleeves["history_bars"]), as_of=now)
            except Exception:
                lane = []
            bars[symbol] = [b for b in lane if parse_utc(b["close_time"], "bar.close_time") <= datetime.fromtimestamp(boundary_ts, timezone.utc)]
        summary: dict[str, Any] = {"boundary": boundary}
        daily_loss, drawdown = self._combined_risk(experiment_id, config)
        summary["combined_daily_loss"], summary["combined_drawdown"] = round(daily_loss, 4), round(drawdown, 4)
        halt = drawdown >= float(config["max_drawdown_stop"])
        pause = daily_loss >= float(config["max_daily_loss"])
        if halt:
            self.runtime._record_risk_pause(experiment_id, "DRAWDOWN_LIMIT", config)
        elif pause:
            self.runtime._record_risk_pause(experiment_id, "DAILY_LOSS_LIMIT", config)
        entries_allowed = not (halt or pause)
        if sleeves["donchian"]["enabled"]:
            if entries_allowed:
                self._donchian(experiment_id, config, sleeves, bars, provider, boundary, now, summary)
            else:
                self._donchian_trails_only(experiment_id, sleeves, bars, now)
        is_daily = datetime.fromtimestamp(boundary_ts, timezone.utc).hour == 0
        day = boundary_ts // 86400 - 1  # the latest fully closed UTC day
        # A sleeve that has never rebalanced starts from the latest closed day instead of waiting
        # for the next daily/weekly slot (up to 7 days for XSMOM); still point-in-time safe.
        due = {"tsmom": is_daily or not self._has_rebalanced(experiment_id, "tsmom"),
               "xsmom": (is_daily and day % 7 == 0) or not self._has_rebalanced(experiment_id, "xsmom")}
        previous = self.store._query("SELECT summary_json FROM sleeve_ticks WHERE experiment_id=? AND boundary<? ORDER BY boundary DESC LIMIT 1",
                                     (experiment_id, boundary))
        pending = (json.loads(previous[0]["summary_json"]).get("pending") or {}) if previous else {}
        for sleeve, weights in pending.items():
            if due.get(sleeve) or not sleeves[sleeve]["enabled"]:
                continue  # a fresh slot recomputes every target
            if not entries_allowed:
                summary.setdefault("pending", {})[sleeve] = weights
                continue
            self._rebalance(experiment_id, config, sleeves, sleeve, weights, bars, provider, boundary, now, summary, only=set(weights))
        if any(due.values()):
            closes = {s: daily_closes(lane) for s, lane in bars.items() if lane}
            for sleeve, fn in (("tsmom", tsmom_weights), ("xsmom", xsmom_weights)):
                if not sleeves[sleeve]["enabled"] or not due[sleeve]:
                    continue
                targets = fn(closes, day, sleeves[sleeve], effective_gross_cap(sleeves, sleeve))
                if not entries_allowed:
                    # Halted/paused: only reductions (flatten to what is already held, never add).
                    targets = {}
                    if not halt:
                        continue
                self._rebalance(experiment_id, config, sleeves, sleeve, targets, bars, provider, boundary, now, summary)
        if entries_allowed and is_daily and sleeves["capital_rebalance"] == "monthly" and datetime.fromtimestamp(boundary_ts, timezone.utc).day == 1:
            self._rebalance_capital(experiment_id, config, boundary, now, summary)
        if entries_allowed:
            self.runtime._record_risk_pause(experiment_id, "APPROVED", config)
        with self.store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO sleeve_ticks(experiment_id, boundary, status, summary_json, created_at) VALUES(?, ?, 'done', ?, ?)",
                       (experiment_id, boundary, json.dumps(summary, default=str), iso_utc(now)))
        return {"status": "ran", **summary}

    def _ratchet(self, position: dict[str, Any], lane: list[dict[str, Any]], params: dict[str, Any], now: datetime) -> None:
        """Trail on the latest closed 4h bar. The distance is fixed at entry (as in the research), so a
        volatility spike never loosens it and a calm spell never strangles a winner."""

        close = float(lane[-1]["close"])
        trail = self.store._query("SELECT best, trail_dist FROM sleeve_trails WHERE position_id=?", (position["position_id"],))
        if trail:
            best, dist = float(trail[0]["best"]), float(trail[0]["trail_dist"])
        else:
            best = float(position["entry_price"])
            dist = float(params["trail_atr"]) * max(wilder_atr(lane)[-1], close * float(params["atr_floor_pct"]))
        best, new_stop = trail_stop(position["side"], float(position["stop_price"]), best, close, dist, 1.0)
        with self.store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO sleeve_trails(position_id, best, trail_dist, updated_at) VALUES(?, ?, ?, ?)",
                       (position["position_id"], best, dist, iso_utc(now)))
            if new_stop != float(position["stop_price"]):
                db.execute("UPDATE positions SET stop_price=? WHERE position_id=? AND status='open'", (new_stop, position["position_id"]))

    def _donchian_trails_only(self, experiment_id: str, sleeves: dict[str, Any], bars: dict[str, list[dict[str, Any]]], now: datetime) -> None:
        for position in self._open_positions(experiment_id, COHORTS["donchian"]):
            lane = bars.get(position["symbol"]) or []
            if lane:
                self._ratchet(position, lane, sleeves["donchian"], now)
