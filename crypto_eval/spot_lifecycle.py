"""Spot Cycle Lifecycle Manager (PAPER): typed lifecycle states, point-in-time regime evidence,
Core/Tactical policy, and a deterministic, auditable action planner.

The planner answers one question per review: given the holding, the market regime evidence and
the crash-safety state, what (if anything) should change? "HOLD" is a valid and common answer.
Every executed action still passes through the risk, crash, execution and reconciliation layers;
nothing here places an order.

Rules that are deliberately hard-coded:

- a transient crash (no structural breakdown) never sells Core and never averages down;
- Core is only sold on EXIT after a *confirmed* breakdown (N consecutive breakdown reviews or a
  structural breakdown from the safety layer);
- distribution is progressive (bounded tactical steps), never all-or-nothing;
- a strong trend may stay overweight up to a bounded tolerance; Core is never churned to restore
  a target allocation, and laggards are never bought because a winner became overweight;
- missing evidence stays explicitly unavailable and is excluded, never zero-filled.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from .paper_contracts import PaperTradingError, iso_utc, parse_utc

LIFECYCLE_SCHEMA_VERSION = "spot-lifecycle-plan.v1"
EVIDENCE_SCHEMA_VERSION = "spot-regime-evidence.v1"
POLICY_VERSION = "spot-lifecycle-policy.v1"

STATES = (
    "ACCUMULATE",
    "HOLD_CORE",
    "ADD_ON_PULLBACK",
    "TREND_EXPANSION",
    "PROTECT_PROFIT",
    "DISTRIBUTE",
    "REDUCE",
    "EXIT",
    "CASH_WAIT",
)
ACTIONS = ("HOLD", "ADD", "TAKE_PARTIAL_PROFIT", "PROTECT_PROFIT", "DISTRIBUTE", "REDUCE", "ROTATE_TO_CASH", "EXIT")
REGIMES = ("EARLY_BULL", "STRONG_TREND", "RANGE", "LATE_BULL", "BREAKDOWN", "UNKNOWN")
SELL_ACTIONS = {"TAKE_PARTIAL_PROFIT", "PROTECT_PROFIT", "DISTRIBUTE", "REDUCE", "ROTATE_TO_CASH", "EXIT"}
# Action aggressiveness, used to clamp AI/user recommendations to the deterministic bound.
ACTION_RANK = {"HOLD": 0, "ADD": 0, "PROTECT_PROFIT": 1, "TAKE_PARTIAL_PROFIT": 1, "DISTRIBUTE": 2,
               "REDUCE": 3, "ROTATE_TO_CASH": 4, "EXIT": 5}

# Typed transitions. Every state can fall back to HOLD_CORE; EXIT only leads to CASH_WAIT.
TRANSITIONS: dict[str, set[str]] = {
    "ACCUMULATE": {"ACCUMULATE", "HOLD_CORE", "ADD_ON_PULLBACK", "TREND_EXPANSION", "REDUCE", "EXIT"},
    "HOLD_CORE": {"HOLD_CORE", "ADD_ON_PULLBACK", "TREND_EXPANSION", "PROTECT_PROFIT", "DISTRIBUTE", "REDUCE", "EXIT"},
    "ADD_ON_PULLBACK": {"ADD_ON_PULLBACK", "HOLD_CORE", "TREND_EXPANSION", "REDUCE", "EXIT"},
    "TREND_EXPANSION": {"TREND_EXPANSION", "HOLD_CORE", "PROTECT_PROFIT", "DISTRIBUTE", "ADD_ON_PULLBACK", "REDUCE", "EXIT"},
    "PROTECT_PROFIT": {"PROTECT_PROFIT", "HOLD_CORE", "TREND_EXPANSION", "DISTRIBUTE", "REDUCE", "EXIT"},
    "DISTRIBUTE": {"DISTRIBUTE", "HOLD_CORE", "PROTECT_PROFIT", "TREND_EXPANSION", "REDUCE", "EXIT"},
    "REDUCE": {"REDUCE", "HOLD_CORE", "DISTRIBUTE", "EXIT"},
    "EXIT": {"CASH_WAIT", "EXIT"},
    "CASH_WAIT": {"CASH_WAIT", "ACCUMULATE"},
}

DEFAULT_LIFECYCLE_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "review_interval_minutes": 240,
    "min_minutes_between_actions": 240,
    "default_core_fraction": 0.5,
    "fast_ema_bars": 12,
    "slow_ema_bars": 48,
    "strong_trend_return_pct": 0.12,
    "late_bull_warning_signs": 2,
    "breakdown_below_slow_pct": 0.05,
    "breakdown_drawdown_pct": 0.15,
    "exit_confirm_reviews": 2,
    "pullback_band_pct": 0.02,
    "add_fraction": 0.1,
    "max_tactical_step_fraction": 0.34,
    "distribute_step_fraction": 0.25,
    "protect_giveback_pct": 0.35,
    "overweight_tolerance_x": 1.5,
    "min_bars": 60,
}
LIFECYCLE_BOUNDS: dict[str, tuple[float, float]] = {
    "review_interval_minutes": (15, 10_080),
    "min_minutes_between_actions": (15, 10_080),
    "default_core_fraction": (0.0, 1.0),
    "fast_ema_bars": (3, 100),
    "slow_ema_bars": (10, 300),
    "strong_trend_return_pct": (0.01, 5.0),
    "late_bull_warning_signs": (1, 5),
    "breakdown_below_slow_pct": (0.005, 0.5),
    "breakdown_drawdown_pct": (0.02, 0.9),
    "exit_confirm_reviews": (1, 10),
    "pullback_band_pct": (0.002, 0.2),
    "add_fraction": (0.0, 0.5),
    "max_tactical_step_fraction": (0.05, 1.0),
    "distribute_step_fraction": (0.05, 1.0),
    "protect_giveback_pct": (0.05, 0.95),
    "overweight_tolerance_x": (1.0, 3.0),
    "min_bars": (20, 1000),
}


def validate_lifecycle_settings(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PaperTradingError("lifecycle settings must be an object")
    merged = dict(DEFAULT_LIFECYCLE_SETTINGS)
    for key, item in value.items():
        if key not in DEFAULT_LIFECYCLE_SETTINGS:
            raise PaperTradingError(f"lifecycle.{key} is not supported")
        merged[key] = item
    if not isinstance(merged["enabled"], bool):
        raise PaperTradingError("lifecycle.enabled must be boolean")
    for key, (low, high) in LIFECYCLE_BOUNDS.items():
        item = merged[key]
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise PaperTradingError(f"lifecycle.{key} must be a finite number")
        if not low <= float(item) <= high:
            raise PaperTradingError(f"lifecycle.{key} must be between {low} and {high}")
    for key in ("fast_ema_bars", "slow_ema_bars", "late_bull_warning_signs", "exit_confirm_reviews", "min_bars"):
        merged[key] = int(merged[key])
    if merged["fast_ema_bars"] >= merged["slow_ema_bars"]:
        raise PaperTradingError("lifecycle.fast_ema_bars must be below slow_ema_bars")
    return merged


# ----------------------------------------------------------------- evidence
def _closed(bars: list[dict[str, Any]] | None, as_of: datetime) -> list[dict[str, Any]]:
    """Point-in-time filter: only bars that closed at or before ``as_of``."""

    result = []
    for bar in bars or []:
        try:
            if parse_utc(bar["close_time"], "bar.close_time") <= as_of:
                result.append(bar)
        except (KeyError, PaperTradingError):
            continue
    return result


def ema(values: list[float], span: int) -> list[float]:
    alpha = 2 / (span + 1)
    out: list[float] = []
    for value in values:
        out.append(value if not out else alpha * value + (1 - alpha) * out[-1])
    return out


def _item(value: Any, *, available: bool = True, reason: str | None = None) -> dict[str, Any]:
    if not available or value is None:
        return {"available": False, "value": None, "reason": reason or "insufficient point-in-time data"}
    return {"available": True, "value": value}


def _trend(closes: list[float], settings: dict[str, Any]) -> dict[str, Any] | None:
    if len(closes) < settings["min_bars"]:
        return None
    fast = ema(closes, settings["fast_ema_bars"])
    slow = ema(closes, settings["slow_ema_bars"])
    lookback = min(len(closes) - 1, settings["slow_ema_bars"])
    window = closes[-(lookback + 1):]
    peak = max(window)
    returns = [math.log(b / a) for a, b in zip(closes[-lookback - 1:-1], closes[-lookback:]) if a > 0 and b > 0]
    mean = sum(returns) / len(returns) if returns else 0.0
    vol = math.sqrt(sum((r - mean) ** 2 for r in returns) / max(1, len(returns) - 1)) if len(returns) > 1 else 0.0
    recent = returns[-12:]
    recent_vol = math.sqrt(sum(r * r for r in recent) / len(recent)) if recent else 0.0
    return {
        "close": closes[-1],
        "fast_ema": fast[-1],
        "slow_ema": slow[-1],
        "fast_above_slow": fast[-1] > slow[-1],
        "slow_slope": (slow[-1] - slow[-7]) / slow[-7] if len(slow) > 7 and slow[-7] > 0 else 0.0,
        "return_window": closes[-1] / window[0] - 1 if window[0] > 0 else 0.0,
        "distance_from_slow": closes[-1] / slow[-1] - 1 if slow[-1] > 0 else 0.0,
        "drawdown_from_peak": (peak - closes[-1]) / peak if peak > 0 else 0.0,
        "volatility": vol,
        "volatility_expansion": recent_vol / vol if vol > 1e-4 else None,
        "slow_slope_long": (slow[-1] - slow[-25]) / slow[-25] if len(slow) > 25 and slow[-25] > 0 else 0.0,
        "return_cycle": closes[-1] / closes[0] - 1 if closes[0] > 0 else 0.0,
        "efficiency": abs(window[-1] - window[0]) / path if (path := sum(abs(b - a) for a, b in zip(window, window[1:]))) > 0 else 0.0,
        "crossed_up_recently": any(f > s for f, s in zip(fast[-6:], slow[-6:])) and fast[-7] <= slow[-7] if len(fast) > 7 else False,
    }


def regime_evidence(
    *,
    symbol: str,
    as_of: datetime,
    asset_bars: list[dict[str, Any]] | None,
    btc_bars: list[dict[str, Any]] | None = None,
    eth_bars: list[dict[str, Any]] | None = None,
    basket: dict[str, list[dict[str, Any]]] | None = None,
    settings: dict[str, Any] | None = None,
    safety: dict[str, Any] | None = None,
    data_origin: str = "unknown",
) -> dict[str, Any]:
    """Point-in-time regime evidence. Unavailable inputs are explicit and excluded."""

    settings = settings or DEFAULT_LIFECYCLE_SETTINGS
    asset = _closed(asset_bars, as_of)
    closes = [float(bar["close"]) for bar in asset]
    trend = _trend(closes, settings)
    btc_closes = [float(bar["close"]) for bar in _closed(btc_bars, as_of)]
    btc = _trend(btc_closes, settings) if btc_closes else None
    evidence: dict[str, Any] = {}
    evidence["asset_trend"] = _item(trend, available=trend is not None,
                                    reason=f"need {settings['min_bars']} closed bars, have {len(closes)}")
    if trend and btc and symbol not in {"BTCUSDT", "BTC_USDT"}:
        evidence["relative_strength_vs_btc"] = _item(trend["return_window"] - btc["return_window"])
    elif symbol in {"BTCUSDT", "BTC_USDT"}:
        evidence["relative_strength_vs_btc"] = _item(None, available=False, reason="asset is BTC")
    else:
        evidence["relative_strength_vs_btc"] = _item(None, available=False, reason="BTC bars unavailable")
    evidence["btc_regime"] = _item(
        None if not btc else ("BULL" if btc["fast_above_slow"] and btc["slow_slope"] > 0 else
                              "BEAR" if not btc["fast_above_slow"] and btc["slow_slope"] < 0 else "RANGE"),
        available=btc is not None, reason="BTC bars unavailable",
    )
    eth_closes = [float(bar["close"]) for bar in _closed(eth_bars, as_of)]
    if btc_closes and eth_closes and len(eth_closes) == len(btc_closes) and len(eth_closes) >= settings["min_bars"]:
        ratio = [e / b for e, b in zip(eth_closes, btc_closes) if b > 0]
        ratio_trend = _trend(ratio, settings)
        evidence["eth_btc_trend"] = _item(None if not ratio_trend else ("UP" if ratio_trend["fast_above_slow"] else "DOWN"),
                                          available=ratio_trend is not None)
    else:
        evidence["eth_btc_trend"] = _item(None, available=False, reason="aligned ETH and BTC bars unavailable")
    above = []
    for other, bars in (basket or {}).items():
        other_closes = [float(bar["close"]) for bar in _closed(bars, as_of)]
        other_trend = _trend(other_closes, settings)
        if other_trend:
            above.append(other_trend["close"] > other_trend["slow_ema"])
    evidence["breadth_above_slow_ema"] = _item(sum(above) / len(above) if len(above) >= 3 else None,
                                               available=len(above) >= 3,
                                               reason=f"breadth needs 3 tracked assets, have {len(above)}")
    volumes = [float(bar.get("volume") or 0.0) for bar in asset]
    if len(volumes) >= 48 and sum(volumes[-48:-12]) > 0:
        evidence["volume_trend"] = _item((sum(volumes[-12:]) / 12) / (sum(volumes[-48:-12]) / 36))
    else:
        evidence["volume_trend"] = _item(None, available=False, reason="volume history too short")
    evidence["funding_crowding"] = _item(None, available=False, reason="not sourced for Spot lifecycle (no point-in-time history)")
    evidence["crash_safety"] = _item(
        None if not safety else {"state": safety.get("state"), "structural_breakdown": bool(safety.get("structural_breakdown"))},
        available=safety is not None, reason="no safety assessment",
    )
    available = [key for key, value in evidence.items() if value["available"]]
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "symbol": symbol,
        "as_of": iso_utc(as_of),
        "data_cutoff": asset[-1]["close_time"] if asset else None,
        "data_origin": data_origin,
        "bars": len(closes),
        "evidence": evidence,
        "coverage": len(available) / len(evidence),
        "available": available,
    }


def classify_regime(evidence: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or DEFAULT_LIFECYCLE_SETTINGS
    items = evidence["evidence"]
    trend = items["asset_trend"]["value"]
    if not trend:
        return {"regime": "UNKNOWN", "signals": ["ASSET_TREND_UNAVAILABLE"], "warning_signs": 0}
    signals: list[str] = []
    safety = items["crash_safety"]["value"] or {}
    structural = bool(safety.get("structural_breakdown"))
    below_slow = trend["distance_from_slow"] <= -settings["breakdown_below_slow_pct"]
    deep = trend["drawdown_from_peak"] >= settings["breakdown_drawdown_pct"]
    if structural:
        signals.append("STRUCTURAL_BREAKDOWN")
    if below_slow:
        signals.append("BELOW_SLOW_EMA")
    if deep:
        signals.append("DEEP_DRAWDOWN")
    if structural or (below_slow and not trend["fast_above_slow"] and (deep or trend["slow_slope"] < 0)):
        return {"regime": "BREAKDOWN", "signals": signals, "warning_signs": 0}
    warnings = []
    rs = items["relative_strength_vs_btc"]["value"]
    if isinstance(rs, (int, float)) and rs < 0:
        warnings.append("RELATIVE_WEAKNESS")
    volume = items["volume_trend"]["value"]
    if isinstance(volume, (int, float)) and volume < 0.7:
        warnings.append("VOLUME_CONTRACTION")
    breadth = items["breadth_above_slow_ema"]["value"]
    if isinstance(breadth, (int, float)) and breadth < 0.4:
        warnings.append("NARROW_BREADTH")
    if items["btc_regime"]["value"] == "BEAR":
        warnings.append("BTC_BEAR")
    if isinstance(trend.get("volatility_expansion"), (int, float)) and trend["volatility_expansion"] > 1.8:
        warnings.append("VOLATILITY_EXPANSION")
    if trend["drawdown_from_peak"] >= 0.06:
        warnings.append("OFF_PEAK")
    # Extension is measured over the whole cycle window; trend quality by slope and efficiency
    # (net move / path length), so an oscillating range is not mistaken for a trend.
    extended = trend["return_cycle"] >= settings["strong_trend_return_pct"]
    up = trend["fast_above_slow"] and trend["slow_slope_long"] > 0
    if extended and len(warnings) >= settings["late_bull_warning_signs"]:
        return {"regime": "LATE_BULL", "signals": signals + warnings, "warning_signs": len(warnings)}
    if up and extended:
        return {"regime": "STRONG_TREND", "signals": signals + warnings, "warning_signs": len(warnings)}
    if up and trend["efficiency"] >= 0.3:
        return {"regime": "EARLY_BULL", "signals": signals + warnings, "warning_signs": len(warnings)}
    return {"regime": "RANGE", "signals": signals + warnings, "warning_signs": len(warnings)}


# ----------------------------------------------------------------- policy
def validate_transition(before: str, after: str) -> bool:
    return before in TRANSITIONS and after in TRANSITIONS[before]


def plan_lifecycle(
    *,
    position_ref: str,
    holding: dict[str, Any],
    lifecycle: dict[str, Any],
    evidence: dict[str, Any],
    regime: dict[str, Any],
    settings: dict[str, Any],
    safety: dict[str, Any] | None,
    max_allocation_pct: float,
    now: datetime,
) -> dict[str, Any]:
    """Deterministic lifecycle decision for one Spot holding. Returns a plan; executes nothing.

    ``holding`` needs quantity, core_quantity, avg_cost, mark_price, allocation_pct.
    ``lifecycle`` is the stored lifecycle row (state, peak_mark, breakdown_streak, last_action_at).
    """

    state = lifecycle.get("state") or "ACCUMULATE"
    quantity = float(holding["quantity"])
    core = min(quantity, float(holding.get("core_quantity") or 0.0))
    tactical = max(0.0, quantity - core)
    mark = float(holding["mark_price"])
    avg = float(holding["avg_cost"])
    peak = max(float(lifecycle.get("peak_mark") or 0.0), mark)
    allocation = holding.get("allocation_pct")
    reasons: list[str] = []
    safety_state = (safety or {}).get("state", "NORMAL")
    structural = bool((safety or {}).get("structural_breakdown"))
    trend = evidence["evidence"]["asset_trend"]["value"] or {}
    breakdown_streak = int(lifecycle.get("breakdown_streak") or 0)
    name = regime["regime"]
    breakdown_streak = breakdown_streak + 1 if name == "BREAKDOWN" else 0

    def out(action: str, after: str, sell_qty: float = 0.0, *, add_fraction: float = 0.0, core_after: float | None = None) -> dict[str, Any]:
        if not validate_transition(state, after):
            reasons.append(f"INVALID_TRANSITION_{state}_TO_{after}")
            action, after, sell_qty, add_fraction = "HOLD", state, 0.0, 0.0
        sell_qty = max(0.0, min(sell_qty, quantity))
        return {
            "schema_version": LIFECYCLE_SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "position_ref": position_ref,
            "as_of": iso_utc(now),
            "state_before": state,
            "state_after": after,
            "action": action,
            "regime": name,
            "regime_signals": regime.get("signals", []),
            "holding_quantity": quantity,
            "sell_quantity": sell_qty,
            "sell_fraction": sell_qty / quantity if quantity > 0 else 0.0,
            "add_fraction": add_fraction,
            "core_quantity": core if core_after is None else core_after,
            "tactical_quantity": tactical,
            "sells_core": sell_qty > tactical + 1e-12,
            "peak_mark": peak,
            "breakdown_streak": breakdown_streak,
            "market_state": safety_state,
            "evidence_coverage": evidence["coverage"],
            "reasons": list(dict.fromkeys(reasons)),
        }

    if quantity <= 0:
        return out("HOLD", "CASH_WAIT" if state in {"EXIT", "CASH_WAIT"} else state)
    # 1. Crash safety is authoritative: a transient crash never sells Core and never adds.
    if safety_state in {"CRASH_MODE", "MARKET_DATA_UNTRUSTED"} and not structural:
        reasons.append("CRASH_SAFETY_HOLD")
        return out("HOLD", state)
    # An exit already decided continues until flat (sell velocity may have paced it).
    if state == "EXIT":
        reasons.append("EXIT_IN_PROGRESS")
        return out("EXIT", "EXIT", quantity, core_after=0.0)
    last_action = lifecycle.get("last_action_at")
    cooling = bool(last_action) and (now - parse_utc(last_action, "last_action_at")).total_seconds() / 60 < settings["min_minutes_between_actions"]
    # 2. Unknown evidence: no action is a valid action.
    if name == "UNKNOWN":
        reasons.append("EVIDENCE_UNAVAILABLE")
        return out("HOLD", state)
    step = settings["max_tactical_step_fraction"]
    # 3. Breakdown: shed Tactical first, exit Core only after confirmation.
    if name == "BREAKDOWN":
        confirmed = structural or breakdown_streak >= settings["exit_confirm_reviews"]
        if confirmed and (tactical <= 1e-12 or state == "REDUCE"):
            reasons.append("BREAKDOWN_CONFIRMED")
            return out("EXIT", "EXIT", quantity, core_after=0.0)
        if cooling:
            reasons.append("ACTION_COOLDOWN")
            return out("HOLD", state)
        reasons.append("BREAKDOWN_SHED_TACTICAL" if tactical > 0 else "BREAKDOWN_AWAITING_CONFIRMATION")
        return out("REDUCE" if tactical > 0 else "HOLD", "REDUCE", tactical)
    gain = (peak - avg) / avg if avg > 0 else 0.0
    giveback = (peak - mark) / (peak - avg) if peak > avg else 0.0
    # 4. Profit protection: a winner that gives back too much of its gain trims Tactical.
    if gain > 0.05 and giveback >= settings["protect_giveback_pct"] and tactical > 0 and not cooling:
        reasons.append("PROFIT_GIVEBACK")
        return out("PROTECT_PROFIT", "PROTECT_PROFIT", tactical * step)
    if name == "LATE_BULL":
        if cooling or tactical <= 0:
            reasons.append("ACTION_COOLDOWN" if cooling else "CORE_ONLY_REMAINS")
            return out("HOLD", "DISTRIBUTE" if validate_transition(state, "DISTRIBUTE") else state)
        reasons.append("LATE_CYCLE_EVIDENCE")
        return out("DISTRIBUTE", "DISTRIBUTE", tactical * settings["distribute_step_fraction"])
    if name == "STRONG_TREND":
        tolerance = max_allocation_pct * settings["overweight_tolerance_x"]
        if isinstance(allocation, (int, float)) and allocation > tolerance and tactical > 0 and not cooling:
            reasons.append("CONCENTRATION_ABOVE_TOLERANCE")
            excess_fraction = (allocation - tolerance) / allocation
            return out("TAKE_PARTIAL_PROFIT", "TREND_EXPANSION", min(tactical * step, quantity * excess_fraction + 1e-12))
        reasons.append("LET_WINNER_RUN" if (isinstance(allocation, (int, float)) and allocation > max_allocation_pct) else "TREND_INTACT")
        return out("HOLD", "TREND_EXPANSION")
    if name == "EARLY_BULL":
        band = settings["pullback_band_pct"]
        pullback = trend and abs(trend["distance_from_slow"]) <= band and trend["fast_above_slow"]
        room = not isinstance(allocation, (int, float)) or allocation < max_allocation_pct
        if pullback and room and safety_state == "NORMAL" and settings["add_fraction"] > 0 and not cooling:
            reasons.append("QUALIFIED_PULLBACK")
            return out("ADD", "ADD_ON_PULLBACK", add_fraction=settings["add_fraction"])
        reasons.append("ACCUMULATING" if state == "ACCUMULATE" else "TREND_BUILDING")
        return out("HOLD", "HOLD_CORE" if state not in {"ACCUMULATE"} or not pullback else state)
    # RANGE: hold; no grid-style churn from the lifecycle manager.
    reasons.append("RANGE_HOLD")
    return out("HOLD", "HOLD_CORE")


def merge_recommendation(plan: dict[str, Any], recommendation: dict[str, Any] | None) -> dict[str, Any]:
    """AI/user recommendation within the deterministic bound. It may choose a *less* aggressive
    action (for example HOLD instead of DISTRIBUTE) or a smaller sell; it can never sell more,
    sell Core without a confirmed breakdown, or pick an invalid transition."""

    if not recommendation:
        return plan
    action = recommendation.get("action")
    if action not in ACTIONS:
        raise PaperTradingError("lifecycle recommendation action is not supported")
    merged = dict(plan)
    merged["reasons"] = list(plan["reasons"])
    if ACTION_RANK[action] > ACTION_RANK[plan["action"]] or (action == "ADD" and plan["action"] != "ADD"):
        merged["reasons"].append("RECOMMENDATION_CLAMPED")
        return merged
    after = recommendation.get("state_after") or plan["state_after"]
    if after not in STATES or not validate_transition(plan["state_before"], after):
        merged["reasons"].append("RECOMMENDATION_INVALID_TRANSITION")
        return merged
    fraction = recommendation.get("sell_fraction")
    sell = plan["sell_quantity"] if action in SELL_ACTIONS else 0.0
    if action in SELL_ACTIONS and isinstance(fraction, (int, float)) and not isinstance(fraction, bool):
        sell = min(sell, max(0.0, float(fraction)) * plan["holding_quantity"])
    holding = plan["holding_quantity"]
    merged.update({"action": action, "state_after": after, "sell_quantity": sell,
                   "sell_fraction": sell / holding if holding > 0 else 0.0,
                   "sells_core": sell > plan["tactical_quantity"] + 1e-12,
                   "add_fraction": plan["add_fraction"] if action == "ADD" else 0.0})
    merged["reasons"].append(f"RECOMMENDATION_{recommendation.get('source', 'AI')}")
    return merged


LIFECYCLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS spot_lifecycle(
    position_ref TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    state TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 1,
    regime TEXT,
    peak_mark REAL,
    breakdown_streak INTEGER NOT NULL DEFAULT 0,
    last_review_at TEXT,
    next_review_at TEXT,
    last_action_at TEXT,
    pending_plan_json TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lifecycle_events(
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    position_ref TEXT NOT NULL,
    instrument_id TEXT,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    state_before TEXT NOT NULL,
    state_after TEXT NOT NULL,
    action TEXT NOT NULL,
    regime TEXT NOT NULL,
    status TEXT NOT NULL,
    sell_quantity REAL NOT NULL DEFAULT 0,
    add_quote_usdt REAL NOT NULL DEFAULT 0,
    reasons_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lifecycle_events_ref_idx ON lifecycle_events(experiment_id, position_ref);
CREATE TABLE IF NOT EXISTS lifecycle_benchmarks(
    report_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    report_json TEXT NOT NULL
);
"""
