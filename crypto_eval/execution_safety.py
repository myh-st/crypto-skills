"""Deterministic crash, price, liquidity, and execution safety (PAPER).

Safety hierarchy (higher always wins):

    LIQUIDATION / ACCOUNTING SAFETY > RISK ENGINE > CRASH / PRICE / LIQUIDITY GUARDS
        > PORTFOLIO BRAIN > AI / HUMAN INTENT

AI and humans express *intent* (enter, hold, reduce, close, tighten). This module owns the
execution mechanics: order style, acceptable-price envelope, max slippage, slicing, TTLs,
sell velocity, and kill-switch restrictions. AI never sets any of them. Every function here
is pure except ``SafetyLedger``, which persists state transitions and guard outcomes.
"""

from __future__ import annotations

import json
import math
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .paper_contracts import PaperTradingError, iso_utc, parse_utc


SAFETY_SCHEMA_VERSION = "market-safety-state.v1"
PLAN_SCHEMA_VERSION = "execution-plan.v1"
MARKET_STATES = ("NORMAL", "VOLATILITY_ALERT", "CRASH_MODE", "RECOVERY", "MARKET_DATA_UNTRUSTED")
STATE_SEVERITY = {"NORMAL": 0, "RECOVERY": 1, "VOLATILITY_ALERT": 2, "CRASH_MODE": 3, "MARKET_DATA_UNTRUSTED": 4}
KILL_SWITCH_LEVELS = ("NORMAL", "NO_NEW_ENTRIES", "AI_MANAGEMENT_PAUSED", "RISK_REDUCING_ONLY", "FULL_AUTOMATION_HALT")
KILL_RANK = {level: index for index, level in enumerate(KILL_SWITCH_LEVELS)}
REASON_CODES = (
    "FLASH_MOVE_DETECTED",
    "PRICE_ANOMALY",
    "MARK_INDEX_DIVERGENCE",
    "CROSS_VENUE_DIVERGENCE",
    "SPREAD_EXPANSION",
    "LIQUIDITY_VACUUM",
    "FEED_STALE",
    "EXECUTION_DEFERRED",
    "SLIPPAGE_LIMIT",
    "SELL_VELOCITY_LIMIT",
    "STALE_DECISION",
    "DUPLICATE_PREVENTED",
    "WRONG_SIDE_BLOCK",
    "POSITION_SIZE_CLAMP",
    "LIQUIDATION_BUFFER_CRITICAL",
    "RECONCILIATION_FAILURE",
    "KILL_SWITCH_CHANGED",
    "CRASH_MODE_ENTERED",
    "CRASH_MODE_RECOVERED",
    "STRUCTURAL_BREAKDOWN",
    "TRANSIENT_WICK",
    "CORE_PROTECTED",
    "SUSPECT_PRINT",
)
ACTIONS = ("entry", "increase", "reduce", "close", "protect")
URGENCY = ("normal", "protective", "emergency")

DEFAULT_SAFETY_SETTINGS: dict[str, Any] = {
    "quote_stale_seconds": 30,
    "bad_print_band_pct": 0.005,
    "mark_index_alert_pct": 0.01,
    "mark_index_untrusted_pct": 0.03,
    "cross_venue_untrusted_pct": 0.02,
    "spread_alert_bps": 25.0,
    "spread_vacuum_bps": 100.0,
    "flash_alert_pct": 0.02,
    "crash_pct_5m": 0.05,
    "crash_pct_15m": 0.08,
    "structural_drawdown_pct": 0.08,
    "wick_recovery_ratio": 0.6,
    "recovery_minutes": 15,
    "suspect_print_range_multiple": 5.0,
    "suspect_print_min_pct": 0.03,
    "max_slippage_bps_entry": 20.0,
    "max_slippage_bps_reduce": 50.0,
    "max_slippage_bps_emergency": 300.0,
    "sell_velocity_window_minutes": 30,
    "sell_velocity_max_fraction": 0.5,
    "max_slices": 5,
    "decision_ttl_seconds": 180,
    "preview_ttl_seconds": 60,
    "liquidation_emergency_buffer_pct": 0.02,
    "emergency_reduce_fraction": 0.5,
    "emergency_cooldown_minutes": 5,
    "volatility_entry_multiplier": 0.5,
    "spot_stop_confirm_closes": 2,
    "reconciliation_tolerance_usdt": 0.000001,
}
SAFETY_BOUNDS: dict[str, tuple[float, float]] = {
    "quote_stale_seconds": (5, 600),
    "bad_print_band_pct": (0.0005, 0.1),
    "mark_index_alert_pct": (0.001, 0.2),
    "mark_index_untrusted_pct": (0.002, 0.5),
    "cross_venue_untrusted_pct": (0.002, 0.5),
    "spread_alert_bps": (1, 1000),
    "spread_vacuum_bps": (2, 5000),
    "flash_alert_pct": (0.002, 0.5),
    "crash_pct_5m": (0.005, 0.9),
    "crash_pct_15m": (0.005, 0.9),
    "structural_drawdown_pct": (0.005, 0.9),
    "wick_recovery_ratio": (0.1, 1.0),
    "recovery_minutes": (1, 1440),
    "suspect_print_range_multiple": (1.5, 100),
    "suspect_print_min_pct": (0.002, 0.9),
    "max_slippage_bps_entry": (1, 1000),
    "max_slippage_bps_reduce": (1, 2000),
    "max_slippage_bps_emergency": (1, 5000),
    "sell_velocity_window_minutes": (1, 1440),
    "sell_velocity_max_fraction": (0.05, 1.0),
    "max_slices": (1, 20),
    "decision_ttl_seconds": (10, 3600),
    "preview_ttl_seconds": (5, 600),
    "liquidation_emergency_buffer_pct": (0.002, 0.2),
    "emergency_reduce_fraction": (0.05, 1.0),
    "emergency_cooldown_minutes": (1, 240),
    "volatility_entry_multiplier": (0.0, 1.0),
    "spot_stop_confirm_closes": (1, 10),
    "reconciliation_tolerance_usdt": (0.0, 1.0),
}

SAFETY_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_safety_states(
    experiment_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    state TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    entered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    crash_entered_at TEXT,
    PRIMARY KEY(experiment_id, instrument_id)
);
CREATE TABLE IF NOT EXISTS safety_events(
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    instrument_id TEXT,
    position_ref TEXT,
    kind TEXT NOT NULL,
    code TEXT NOT NULL,
    source TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS safety_events_idx ON safety_events(experiment_id, created_at);
CREATE TABLE IF NOT EXISTS execution_plans(
    plan_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    instrument_id TEXT NOT NULL,
    position_ref TEXT,
    action TEXT NOT NULL,
    source TEXT NOT NULL,
    decision TEXT NOT NULL,
    status TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kill_switch(
    experiment_id TEXT PRIMARY KEY,
    level TEXT NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def validate_safety_settings(value: dict[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULT_SAFETY_SETTINGS, **(value or {})}
    unknown = set(merged) - set(DEFAULT_SAFETY_SETTINGS)
    if unknown:
        raise PaperTradingError(f"safety setting {sorted(unknown)[0]} is not supported")
    for key, (low, high) in SAFETY_BOUNDS.items():
        item = merged[key]
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise PaperTradingError(f"safety.{key} must be a finite number")
        if not low <= float(item) <= high:
            raise PaperTradingError(f"safety.{key} must be between {low} and {high}")
    if merged["mark_index_untrusted_pct"] < merged["mark_index_alert_pct"]:
        raise PaperTradingError("safety.mark_index_untrusted_pct must be >= mark_index_alert_pct")
    if merged["spread_vacuum_bps"] < merged["spread_alert_bps"]:
        raise PaperTradingError("safety.spread_vacuum_bps must be >= spread_alert_bps")
    return merged


def _f(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _window_return(bars: list[dict[str, Any]], minutes: int) -> float | None:
    if len(bars) < 2:
        return None
    window = bars[-minutes:] if len(bars) >= minutes else bars
    first = _f(window[0]["open"])
    last = _f(window[-1]["close"])
    if first is None or last is None:
        return None
    return last / first - 1


def _window_drawdown(bars: list[dict[str, Any]], minutes: int) -> float:
    window = bars[-minutes:] if len(bars) >= minutes else bars
    if not window:
        return 0.0
    peak = max(float(bar["high"]) for bar in window)
    last = float(window[-1]["close"])
    return max(0.0, 1 - last / peak) if peak > 0 else 0.0


def suspect_print(bar: dict[str, Any], history: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, Any] | None:
    """An isolated spike: the wick extends far beyond open/close and the bar closes back near
    its body. Real trades rarely print like that without follow-through; a bad print does."""

    ranges = [float(item["high"]) - float(item["low"]) for item in history[-20:] if float(item["high"]) > 0]
    if len(ranges) < 5:
        return None
    typical = statistics.median(ranges) or 0.0
    o, h, l, c = (float(bar[key]) for key in ("open", "high", "low", "close"))
    body_low, body_high = min(o, c), max(o, c)
    down = body_low - l
    up = h - body_high
    wick, direction = (down, "down") if down >= up else (up, "up")
    threshold = max(settings["suspect_print_range_multiple"] * typical, settings["suspect_print_min_pct"] * body_low)
    if wick <= 0 or wick < threshold:
        return None
    body = body_high - body_low
    if body > wick * (1 - settings["wick_recovery_ratio"]):
        return None
    return {"code": "SUSPECT_PRINT", "direction": direction, "wick": wick, "typical_range": typical, "bar_close": bar["close_time"]}


def classify_market(
    *,
    instrument_id: str,
    market_type: str,
    quote: dict[str, Any] | None,
    bars_1m: list[dict[str, Any]],
    settings: dict[str, Any],
    now: datetime,
    reference_price: float | None = None,
    prior: dict[str, Any] | None = None,
    require_quote: bool = True,
) -> dict[str, Any]:
    """Deterministic market safety state. AI has no input here.

    ``require_quote=False`` is used only for venues whose snapshots carry no top-of-book
    (fixture, Binance USD-M): book checks are skipped and reported as unavailable instead of
    being fabricated; bar-based crash checks still apply.
    """

    reasons: list[str] = []
    metrics: dict[str, Any] = {}
    state = "NORMAL"

    def escalate(new_state: str) -> None:
        nonlocal state
        if STATE_SEVERITY[new_state] > STATE_SEVERITY[state]:
            state = new_state

    bid = _f((quote or {}).get("best_bid"))
    ask = _f((quote or {}).get("best_ask"))
    last = _f((quote or {}).get("last_price"))
    mark = _f((quote or {}).get("mark_price"))
    index = _f((quote or {}).get("index_price"))
    trusted = None
    if quote is None and not require_quote:
        metrics["book"] = "unavailable_for_venue"
    elif quote is None or not quote.get("observed_at"):
        reasons.append("FEED_STALE")
        escalate("MARKET_DATA_UNTRUSTED")
    else:
        age = (now - parse_utc(quote["observed_at"], "quote.observed_at")).total_seconds()
        metrics["quote_age_seconds"] = max(0.0, age)
        if age > settings["quote_stale_seconds"]:
            reasons.append("FEED_STALE")
            escalate("MARKET_DATA_UNTRUSTED")
    if quote is None and not require_quote:
        pass
    elif bid is None or ask is None or ask < bid:
        if quote is not None:
            reasons.append("PRICE_ANOMALY")
        escalate("MARKET_DATA_UNTRUSTED")
    else:
        mid = (bid + ask) / 2
        trusted = mid
        spread = (ask - bid) / mid * 10_000
        metrics["spread_bps"] = spread
        if spread >= settings["spread_vacuum_bps"]:
            reasons.append("LIQUIDITY_VACUUM")
            escalate("VOLATILITY_ALERT")
        elif spread >= settings["spread_alert_bps"]:
            reasons.append("SPREAD_EXPANSION")
            escalate("VOLATILITY_ALERT")
        if last is not None:
            band = settings["bad_print_band_pct"]
            if last < bid * (1 - band) or last > ask * (1 + band):
                metrics["last_deviation_pct"] = last / mid - 1
                reasons.append("PRICE_ANOMALY")
                # The book is trusted; only the last print is rejected as a price source.
        if market_type == "perpetual" and mark is not None and index is not None:
            divergence = abs(mark / index - 1)
            metrics["mark_index_divergence_pct"] = divergence
            if divergence >= settings["mark_index_untrusted_pct"]:
                reasons.append("MARK_INDEX_DIVERGENCE")
                escalate("MARKET_DATA_UNTRUSTED")
            elif divergence >= settings["mark_index_alert_pct"]:
                reasons.append("MARK_INDEX_DIVERGENCE")
                escalate("VOLATILITY_ALERT")
        if reference_price is not None:
            divergence = abs(mid / reference_price - 1)
            metrics["cross_venue_divergence_pct"] = divergence
            if divergence >= settings["cross_venue_untrusted_pct"]:
                reasons.append("CROSS_VENUE_DIVERGENCE")
                escalate("MARKET_DATA_UNTRUSTED")
    closed = [bar for bar in bars_1m if bar]
    move_5m = _window_return(closed, 5)
    move_15m = _window_return(closed, 15)
    metrics["move_5m_pct"] = move_5m
    metrics["move_15m_pct"] = move_15m
    structural = False
    if closed:
        latest = closed[-1]
        spike = suspect_print(latest, closed[:-1], settings)
        if spike:
            reasons.append("SUSPECT_PRINT")
            metrics["suspect_print"] = spike
        drawdown_30 = _window_drawdown(closed, 30)
        metrics["drawdown_30m_pct"] = drawdown_30
        worst = min(value for value in (move_5m, move_15m, 0.0) if value is not None)
        best = max(value for value in (move_5m, move_15m, 0.0) if value is not None)
        if abs(worst) >= settings["flash_alert_pct"] or best >= settings["flash_alert_pct"]:
            reasons.append("FLASH_MOVE_DETECTED")
            escalate("VOLATILITY_ALERT")
        crash = (move_5m is not None and move_5m <= -settings["crash_pct_5m"]) or (
            move_15m is not None and move_15m <= -settings["crash_pct_15m"]
        )
        # Transient wick: the window's low is far below, but price already recovered most of it.
        window = closed[-15:]
        low = min(float(bar["low"]) for bar in window)
        start = float(window[0]["open"])
        last_close = float(window[-1]["close"])
        drop = (start - low) / start if start > 0 else 0.0
        recovered = (last_close - low) / (start - low) if start > low else 1.0
        metrics["window_drop_pct"] = drop
        metrics["window_recovery_ratio"] = recovered
        if drop >= settings["crash_pct_5m"] and recovered >= settings["wick_recovery_ratio"] and not crash:
            reasons.append("TRANSIENT_WICK")
            escalate("VOLATILITY_ALERT")
        closes = [float(bar["close"]) for bar in closed[-30:]]
        buckets = [closes[i] for i in range(0, len(closes), 5)] + ([closes[-1]] if closes else [])
        lower_steps = sum(1 for a, b in zip(buckets, buckets[1:]) if b < a)
        structural = (
            drawdown_30 >= settings["structural_drawdown_pct"]
            and len(buckets) >= 4
            and lower_steps >= max(3, len(buckets) - 2)
            and recovered < settings["wick_recovery_ratio"]
        )
        if structural:
            reasons.append("STRUCTURAL_BREAKDOWN")
        if crash or structural:
            escalate("CRASH_MODE")
    previous = (prior or {}).get("state")
    crash_entered_at = (prior or {}).get("crash_entered_at")
    if state == "CRASH_MODE" and previous != "CRASH_MODE":
        reasons.append("CRASH_MODE_ENTERED")
        crash_entered_at = iso_utc(now)
    if previous in {"CRASH_MODE", "RECOVERY"} and state in {"NORMAL", "VOLATILITY_ALERT", "RECOVERY"}:
        # After a crash the market stays in RECOVERY (reduced entry size, velocity-limited
        # exits) for a cooldown before returning to NORMAL.
        recorded = (prior or {}).get("recovery_started_at")
        started = parse_utc(recorded, "recovery_started_at") if previous == "RECOVERY" and recorded else now
        if now - started < timedelta(minutes=settings["recovery_minutes"]):
            state = "RECOVERY"
            metrics["recovery_started_at"] = iso_utc(started)
        else:
            reasons.append("CRASH_MODE_RECOVERED")
    reasons = list(dict.fromkeys(reasons))
    if trusted is None and last is not None and "PRICE_ANOMALY" not in reasons:
        trusted = last
    confidence = {
        "MARKET_DATA_UNTRUSTED": "UNTRUSTED",
        "CRASH_MODE": "LOW",
        "VOLATILITY_ALERT": "REDUCED",
        "RECOVERY": "REDUCED",
        "NORMAL": "HIGH",
    }[state]
    if "PRICE_ANOMALY" in reasons and confidence == "HIGH":
        confidence = "REDUCED"
    return {
        "schema_version": SAFETY_SCHEMA_VERSION,
        "instrument_id": instrument_id,
        "market_type": market_type,
        "state": state,
        "price_confidence": confidence,
        "trusted_price": trusted,
        "structural_breakdown": structural,
        "reasons": reasons,
        "metrics": metrics,
        "crash_entered_at": crash_entered_at,
        "as_of": iso_utc(now),
        "restrictions": restrictions_for(state, reasons),
    }


def restrictions_for(state: str, reasons: list[str]) -> dict[str, Any]:
    blocked_entries = state in {"CRASH_MODE", "MARKET_DATA_UNTRUSTED"} or "LIQUIDITY_VACUUM" in reasons
    return {
        "new_entries": "BLOCKED" if blocked_entries else "REDUCED_SIZE" if state in {"VOLATILITY_ALERT", "RECOVERY"} else "ALLOWED",
        "averaging_down": "BLOCKED" if state != "NORMAL" else "ALLOWED",
        "leverage_increase": "BLOCKED" if state != "NORMAL" else "ALLOWED",
        "discretionary_full_exit": "DEFERRED" if state == "MARKET_DATA_UNTRUSTED" else "VELOCITY_LIMITED" if state == "CRASH_MODE" else "ALLOWED",
        "protective_reduce": "ALLOWED" if state != "MARKET_DATA_UNTRUSTED" else "DEFERRED_UNLESS_EMERGENCY",
        "emergency_liquidation_reduce": "ALLOWED",
    }


def kill_switch_allows(level: str, *, action: str, source: str, risk_reducing: bool) -> tuple[bool, str | None]:
    """Kill-switch policy. Deterministic SYSTEM emergency actions are never blocked."""

    if source == "SYSTEM" and risk_reducing:
        return True, None
    rank = KILL_RANK[level]
    if action in {"entry", "increase"} and rank >= KILL_RANK["NO_NEW_ENTRIES"]:
        return False, f"KILL_SWITCH_{level}: new entries are blocked"
    if source == "AI" and rank >= KILL_RANK["AI_MANAGEMENT_PAUSED"]:
        return False, f"KILL_SWITCH_{level}: AI position management is paused"
    if rank >= KILL_RANK["RISK_REDUCING_ONLY"] and not risk_reducing:
        return False, f"KILL_SWITCH_{level}: only risk-reducing actions are allowed"
    if rank >= KILL_RANK["FULL_AUTOMATION_HALT"] and source != "USER":
        return False, f"KILL_SWITCH_{level}: automation is halted"
    return True, None


def plan_execution(
    *,
    action: str,
    source: str,
    market_type: str,
    side: str,
    quantity: float,
    assessment: dict[str, Any],
    quote: dict[str, Any] | None,
    settings: dict[str, Any],
    urgency: str = "normal",
    top_size: float | None = None,
    quantity_step: float | None = None,
    recent_reduced_fraction: float = 0.0,
    position_quantity: float | None = None,
    core_quantity: float = 0.0,
    kill_switch_level: str = "NORMAL",
) -> dict[str, Any]:
    """Economic intent -> execution mechanics. AI never supplies style, slippage, slices, or TTL."""

    if action not in ACTIONS or urgency not in URGENCY:
        raise PaperTradingError("execution plan action/urgency is invalid")
    reasons: list[str] = []
    state = assessment["state"]
    risk_reducing = action in {"reduce", "close", "protect"}
    now = parse_utc(assessment["as_of"], "assessment.as_of")
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_id": f"xp-{uuid.uuid4().hex[:16]}",
        "action": action,
        "source": source,
        "market_type": market_type,
        "side": side,
        "urgency": urgency,
        "requested_quantity": float(quantity),
        "market_state": state,
        "kill_switch_level": kill_switch_level,
        "expires_at": iso_utc(now + timedelta(seconds=settings["decision_ttl_seconds"])),
        "ai_controls_mechanics": False,
    }

    def finish(decision: str, *, quantity_out: float = 0.0, style: str | None = None, slices: list[float] | None = None,
               max_slip: float | None = None, envelope: dict[str, Any] | None = None) -> dict[str, Any]:
        plan.update({
            "decision": decision,
            "quantity": quantity_out,
            "style": style,
            "slices": slices or ([] if quantity_out <= 0 else [quantity_out]),
            "max_slippage_bps": max_slip,
            "price_envelope": envelope,
            "reasons": list(dict.fromkeys(reasons)),
        })
        return plan

    allowed, why = kill_switch_allows(kill_switch_level, action=action, source=source, risk_reducing=risk_reducing or urgency == "emergency")
    if not allowed:
        reasons.append("KILL_SWITCH_CHANGED")
        plan["blocked_reason"] = why
        return finish("REJECT")
    if quantity <= 0 or not math.isfinite(quantity):
        reasons.append("POSITION_SIZE_CLAMP")
        return finish("REJECT")
    if risk_reducing and position_quantity is not None and quantity > position_quantity + 1e-12:
        reasons.append("WRONG_SIDE_BLOCK")
        quantity = position_quantity
        if quantity <= 0:
            return finish("REJECT")
    emergency = urgency == "emergency"
    if not emergency and state == "MARKET_DATA_UNTRUSTED":
        reasons.append("EXECUTION_DEFERRED")
        return finish("DEFER")
    if action in {"entry", "increase"}:
        if state in {"CRASH_MODE", "MARKET_DATA_UNTRUSTED"} or "LIQUIDITY_VACUUM" in assessment["reasons"]:
            reasons.extend(["EXECUTION_DEFERRED", *[r for r in assessment["reasons"] if r in {"LIQUIDITY_VACUUM", "CRASH_MODE_ENTERED", "STRUCTURAL_BREAKDOWN"}]])
            return finish("REJECT")
        if state in {"VOLATILITY_ALERT", "RECOVERY"}:
            quantity *= settings["volatility_entry_multiplier"]
            reasons.append("POSITION_SIZE_CLAMP")
            if quantity <= 0:
                return finish("REJECT")
    # Spot Core protection: without a confirmed structural breakdown, discretionary sells stop at Core.
    if market_type == "spot" and risk_reducing and not emergency and core_quantity > 0 and position_quantity is not None:
        if not assessment.get("structural_breakdown") and state in {"CRASH_MODE", "VOLATILITY_ALERT", "MARKET_DATA_UNTRUSTED", "RECOVERY"}:
            sellable = max(0.0, position_quantity - core_quantity)
            if quantity > sellable + 1e-12:
                quantity = sellable
                reasons.append("CORE_PROTECTED")
                if quantity <= 0:
                    return finish("DEFER")
    # Sell velocity: bounded discretionary reduction per window. Applies to AI on Spot, to AI on
    # perpetuals outside NORMAL markets, and to users in crash/recovery (user may override with
    # an explicit, journaled confirmation). Liquidation emergencies are never velocity-limited.
    velocity_applies = (
        (source == "AI" and (market_type == "spot" or state != "NORMAL"))
        or (source == "USER" and state in {"CRASH_MODE", "RECOVERY"})
    )
    if risk_reducing and not emergency and position_quantity and velocity_applies:
        cap = settings["sell_velocity_max_fraction"]
        base = position_quantity / max(1e-12, 1 - recent_reduced_fraction) if recent_reduced_fraction < 1 else position_quantity
        allowed_qty = max(0.0, cap * base - recent_reduced_fraction * base)
        if quantity > allowed_qty + 1e-12:
            quantity = allowed_qty
            reasons.append("SELL_VELOCITY_LIMIT")
            if quantity <= 0:
                return finish("DEFER")
    if quantity_step:
        stepped = math.floor(quantity / quantity_step + 1e-9) * quantity_step
        quantity = stepped if stepped > 0 else quantity
    bid = _f((quote or {}).get("best_bid"))
    ask = _f((quote or {}).get("best_ask"))
    if bid is None or ask is None:
        reasons.append("EXECUTION_DEFERRED")
        return finish("DEFER")
    mid = (bid + ask) / 2
    half_spread_bps = (ask - bid) / mid * 10_000 / 2
    max_slip = settings[
        "max_slippage_bps_emergency" if emergency else "max_slippage_bps_entry" if action in {"entry", "increase"} else "max_slippage_bps_reduce"
    ]
    buying = side in {"long", "buy"} if action in {"entry", "increase"} else side in {"short"}
    reference = ask if buying else bid
    worst = reference * (1 + max_slip / 10_000) if buying else reference * (1 - max_slip / 10_000)
    envelope = {"reference_price": reference, "worst_acceptable_price": worst, "mid": mid, "half_spread_bps": half_spread_bps}
    if half_spread_bps > max_slip:
        reasons.append("SLIPPAGE_LIMIT")
        return finish("DEFER" if not emergency else "REJECT", envelope=envelope, max_slip=max_slip)
    slices = [quantity]
    style = "MARKET" if emergency else "MARKETABLE_LIMIT"
    if top_size and top_size > 0 and quantity > top_size and not emergency:
        count = min(int(settings["max_slices"]), math.ceil(quantity / top_size))
        size = quantity / count
        slices = [size] * count
        reasons.append("LIQUIDITY_VACUUM" if "LIQUIDITY_VACUUM" in assessment["reasons"] else "EXECUTION_DEFERRED")
    elif "LIQUIDITY_VACUUM" in assessment["reasons"] and not emergency:
        count = min(int(settings["max_slices"]), 3)
        slices = [quantity / count] * count
        reasons.append("LIQUIDITY_VACUUM")
    if emergency:
        reasons.append("LIQUIDATION_BUFFER_CRITICAL")
    decision = "SLICE" if len(slices) > 1 else "EXECUTE"
    if "POSITION_SIZE_CLAMP" in reasons or "SELL_VELOCITY_LIMIT" in reasons or "CORE_PROTECTED" in reasons:
        decision = "RESIZE" if len(slices) == 1 else "SLICE"
    return finish(decision, quantity_out=quantity, style=style, slices=slices, max_slip=max_slip, envelope=envelope)


def within_envelope(price: float, plan: dict[str, Any], buying: bool) -> bool:
    envelope = plan.get("price_envelope") or {}
    worst = envelope.get("worst_acceptable_price")
    if worst is None:
        return False
    return price <= worst if buying else price >= worst


class SafetyLedger:
    """Persistence for market safety states, guard outcomes, execution plans, and the kill switch."""

    def __init__(self, store: Any, clock) -> None:
        self.store = store
        self._clock = clock

    def _now(self) -> str:
        return iso_utc(self._clock().astimezone(timezone.utc))

    def event(self, experiment_id: str, *, kind: str, code: str, source: str, detail: dict[str, Any],
              instrument_id: str | None = None, position_ref: str | None = None) -> None:
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO safety_events(experiment_id, instrument_id, position_ref, kind, code, source, detail_json, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (experiment_id, instrument_id, position_ref, kind, code, source,
                 json.dumps(detail, sort_keys=True, default=str), self._now()),
            )

    def events(self, experiment_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.store._query(
            "SELECT * FROM safety_events WHERE experiment_id=? ORDER BY event_id DESC LIMIT ?", (experiment_id, limit)
        )
        return [{**{k: row[k] for k in row.keys() if k != "detail_json"}, "detail": json.loads(row["detail_json"])} for row in rows]

    def counts(self, experiment_id: str) -> dict[str, int]:
        rows = self.store._query(
            "SELECT code, COUNT(*) AS n FROM safety_events WHERE experiment_id=? GROUP BY code", (experiment_id,)
        )
        return {row["code"]: int(row["n"]) for row in rows}

    def prior_state(self, experiment_id: str, instrument_id: str) -> dict[str, Any] | None:
        rows = self.store._query(
            "SELECT * FROM market_safety_states WHERE experiment_id=? AND instrument_id=?", (experiment_id, instrument_id)
        )
        if not rows:
            return None
        row = rows[0]
        metrics = json.loads(row["metrics_json"])
        return {"state": row["state"], "crash_entered_at": row["crash_entered_at"],
                "recovery_started_at": metrics.get("recovery_started_at"), "entered_at": row["entered_at"]}

    def record_state(self, experiment_id: str, assessment: dict[str, Any]) -> None:
        prior = self.prior_state(experiment_id, assessment["instrument_id"])
        now = self._now()
        changed = prior is None or prior["state"] != assessment["state"]
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO market_safety_states(experiment_id, instrument_id, state, reasons_json, metrics_json, entered_at, "
                "updated_at, crash_entered_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(experiment_id, instrument_id) DO UPDATE SET "
                "state=excluded.state, reasons_json=excluded.reasons_json, metrics_json=excluded.metrics_json, "
                "entered_at=CASE WHEN market_safety_states.state=excluded.state THEN market_safety_states.entered_at ELSE excluded.entered_at END, "
                "updated_at=excluded.updated_at, crash_entered_at=excluded.crash_entered_at",
                (experiment_id, assessment["instrument_id"], assessment["state"], json.dumps(assessment["reasons"]),
                 json.dumps(assessment["metrics"], default=str), now, now, assessment.get("crash_entered_at")),
            )
        if changed and not (prior is None and assessment["state"] == "NORMAL"):
            code = "CRASH_MODE_ENTERED" if assessment["state"] == "CRASH_MODE" else (
                "CRASH_MODE_RECOVERED" if prior and prior["state"] in {"CRASH_MODE", "RECOVERY"} and assessment["state"] == "NORMAL"
                else (assessment["reasons"][0] if assessment["reasons"] else "STATE_CHANGE"))
            self.event(experiment_id, kind="market_state", code=code, source="SYSTEM",
                       detail={"from": None if prior is None else prior["state"], "to": assessment["state"], "reasons": assessment["reasons"]},
                       instrument_id=assessment["instrument_id"])

    def states(self, experiment_id: str) -> list[dict[str, Any]]:
        rows = self.store._query("SELECT * FROM market_safety_states WHERE experiment_id=? ORDER BY instrument_id", (experiment_id,))
        return [
            {"instrument_id": row["instrument_id"], "state": row["state"], "reasons": json.loads(row["reasons_json"]),
             "metrics": json.loads(row["metrics_json"]), "entered_at": row["entered_at"], "updated_at": row["updated_at"]}
            for row in rows
        ]

    def kill_switch(self, experiment_id: str) -> dict[str, Any]:
        rows = self.store._query("SELECT * FROM kill_switch WHERE experiment_id=?", (experiment_id,))
        if not rows:
            return {"level": "NORMAL", "reason": "default", "source": "SYSTEM", "updated_at": None}
        return dict(rows[0])

    def set_kill_switch(self, experiment_id: str, level: str, *, reason: str, source: str) -> dict[str, Any]:
        if level not in KILL_SWITCH_LEVELS:
            raise PaperTradingError(f"kill switch level must be one of {', '.join(KILL_SWITCH_LEVELS)}")
        current = self.kill_switch(experiment_id)
        now = self._now()
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO kill_switch(experiment_id, level, reason, source, updated_at) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(experiment_id) DO UPDATE SET level=excluded.level, reason=excluded.reason, "
                "source=excluded.source, updated_at=excluded.updated_at",
                (experiment_id, level, reason[:300], source, now),
            )
        if current["level"] != level:
            self.event(experiment_id, kind="kill_switch", code="KILL_SWITCH_CHANGED", source=source,
                       detail={"from": current["level"], "to": level, "reason": reason[:300]})
        return self.kill_switch(experiment_id)

    def save_plan(self, experiment_id: str, plan: dict[str, Any], *, instrument_id: str, position_ref: str | None,
                  idempotency_key: str | None, status: str, result: dict[str, Any] | None = None) -> None:
        now = self._now()
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO execution_plans(plan_id, experiment_id, idempotency_key, instrument_id, position_ref, action, source, "
                "decision, status, plan_json, result_json, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(plan_id) DO UPDATE SET status=excluded.status, result_json=excluded.result_json, updated_at=excluded.updated_at",
                (plan["plan_id"], experiment_id, idempotency_key, instrument_id, position_ref, plan["action"], plan["source"],
                 plan["decision"], status, json.dumps(plan, sort_keys=True, default=str),
                 json.dumps(result or {}, sort_keys=True, default=str), now, now),
            )

    def plan_by_key(self, key: str) -> dict[str, Any] | None:
        rows = self.store._query("SELECT * FROM execution_plans WHERE idempotency_key=?", (key,))
        if not rows:
            return None
        row = rows[0]
        return {"plan": json.loads(row["plan_json"]), "status": row["status"], "result": json.loads(row["result_json"])}

    def plans(self, experiment_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.store._query(
            "SELECT * FROM execution_plans WHERE experiment_id=? ORDER BY created_at DESC LIMIT ?", (experiment_id, limit)
        )
        return [{**{k: row[k] for k in row.keys() if not k.endswith("_json")}, "plan": json.loads(row["plan_json"]),
                 "result": json.loads(row["result_json"])} for row in rows]


def reconcile_ledgers(store: Any, experiment_id: str, *, tolerance: float = 1e-6) -> dict[str, Any]:
    """Recompute wallets and holdings from immutable fills/events and compare with stored state."""

    problems: list[dict[str, Any]] = []
    q = store._query
    from .sleeves import capital_cohorts

    experiment = q("SELECT config_json FROM experiments WHERE experiment_id=?", (experiment_id,))
    cohorts = capital_cohorts(json.loads(experiment[0]["config_json"])) if experiment else ["primary"]
    for cohort in cohorts:
        wallet = q("SELECT * FROM wallets WHERE experiment_id=? AND cohort=?", (experiment_id, cohort))
        if not wallet:
            continue
        starting = float(wallet[0]["starting_balance"])
        realized = q(
            "SELECT COALESCE(SUM(realized_gross),0) AS g, COALESCE(SUM(entry_fee),0) AS ef, COALESCE(SUM(exit_fees),0) AS xf, "
            "COALESCE(SUM(funding_paid),0) AS fu FROM positions WHERE experiment_id=? AND cohort=?",
            (experiment_id, cohort),
        )[0]
        transfers = q(
            "SELECT COALESCE(SUM(CASE WHEN to_cohort=? THEN amount ELSE 0 END),0) - "
            "COALESCE(SUM(CASE WHEN from_cohort=? THEN amount ELSE 0 END),0) AS net FROM wallet_transfers WHERE experiment_id=?",
            (cohort, cohort, experiment_id),
        )[0]
        expected = (starting + float(realized["g"]) - float(realized["ef"]) - float(realized["xf"]) - float(realized["fu"])
                    + float(transfers["net"]))
        # Entry fees of still-open positions are deducted at fill; realized_gross covers partial exits.
        if abs(expected - float(wallet[0]["cash_balance"])) > max(tolerance, 1e-6):
            problems.append({"ledger": "perp_wallet", "cohort": cohort, "expected": expected, "actual": float(wallet[0]["cash_balance"])})
        for position in q("SELECT * FROM positions WHERE experiment_id=? AND cohort=?", (experiment_id, cohort)):
            fills = q("SELECT side, quantity FROM fills WHERE position_id=?", (position["position_id"],))
            opened = sum(float(f["quantity"]) for f in fills if f["side"] == ("buy" if position["side"] == "long" else "sell"))
            closed = sum(float(f["quantity"]) for f in fills if f["side"] != ("buy" if position["side"] == "long" else "sell"))
            if abs(opened - closed - float(position["quantity"])) > max(tolerance, 1e-9):
                problems.append({"ledger": "perp_position", "position_id": position["position_id"],
                                 "expected": opened - closed, "actual": float(position["quantity"])})
            if position["status"] == "open" and float(position["quantity"]) <= 0:
                problems.append({"ledger": "perp_position", "position_id": position["position_id"], "issue": "open_without_quantity"})
            if position["status"] == "open":
                long = position["side"] == "long"
                stop, liq, target = float(position["stop_price"]), float(position["liquidation_price"]), float(position["target_price"])
                if (long and not liq < stop) or (not long and not liq > stop):
                    problems.append({"ledger": "stop_integrity", "position_id": position["position_id"], "issue": "stop_beyond_liquidation"})
                if (long and target <= stop) or (not long and target >= stop):
                    problems.append({"ledger": "stop_integrity", "position_id": position["position_id"], "issue": "target_stop_inverted"})
    spot_wallet = q("SELECT * FROM spot_wallets WHERE experiment_id=?", (experiment_id,))
    if spot_wallet:
        fills = q("SELECT side, quantity, price, fee FROM spot_fills WHERE experiment_id=?", (experiment_id,))
        cash = float(spot_wallet[0]["starting_balance"]) + sum(
            (-(float(f["quantity"]) * float(f["price"]) + float(f["fee"]))) if f["side"] == "buy"
            else (float(f["quantity"]) * float(f["price"]) - float(f["fee"]))
            for f in fills
        )
        if abs(cash - float(spot_wallet[0]["cash_balance"])) > max(tolerance, 1e-6):
            problems.append({"ledger": "spot_wallet", "expected": cash, "actual": float(spot_wallet[0]["cash_balance"])})
        reserved = q(
            "SELECT COALESCE(SUM(reserved_quote),0) AS r FROM spot_orders WHERE experiment_id=? AND status IN ('pending','partially_filled')",
            (experiment_id,),
        )[0]["r"]
        if abs(float(reserved) - float(spot_wallet[0]["reserved_quote"])) > max(tolerance, 1e-6):
            problems.append({"ledger": "spot_reserved_quote", "expected": float(reserved), "actual": float(spot_wallet[0]["reserved_quote"])})
        for holding in q("SELECT * FROM spot_holdings WHERE experiment_id=?", (experiment_id,)):
            rows = q("SELECT side, quantity FROM spot_fills WHERE holding_id=?", (holding["holding_id"],))
            qty = sum(float(r["quantity"]) * (1 if r["side"] == "buy" else -1) for r in rows)
            if abs(qty - float(holding["quantity"])) > 1e-9:
                problems.append({"ledger": "spot_holding", "holding_id": holding["holding_id"], "expected": qty, "actual": float(holding["quantity"])})
            if float(holding["quantity"]) < -1e-12 or float(holding["reserved_quantity"]) > float(holding["quantity"]) + 1e-9:
                problems.append({"ledger": "spot_holding", "holding_id": holding["holding_id"], "issue": "reserved_exceeds_quantity"})
    return {"ok": not problems, "problems": problems, "checked_at": iso_utc(datetime.now(timezone.utc))}
