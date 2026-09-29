"""Post-trade learning and prospective strategy-tournament analytics.

Reviews are generated deterministically after a position/holding closes. Tags come from
a fixed taxonomy; the lesson and hypothesis are templated, concise, and stored as
*candidates* for a future experiment version. Nothing here edits an active strategy,
prompt, or risk parameter.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from .contracts import digest
from .paper_contracts import parse_utc
from .portfolio_store import LEARNING_TAGS


REVIEW_SCHEMA_VERSION = "post-trade-review.v1"
TOURNAMENT_SCHEMA_VERSION = "strategy-tournament.v1"
MIN_PROMOTION_SAMPLE = 30
HYPOTHESES = {
    "BAD_DIRECTION": "Require stronger higher-timeframe alignment before entry in the next experiment version.",
    "LATE_ENTRY": "Cap distance-to-breakout at entry; test a pullback-entry variant.",
    "FALSE_BREAKOUT": "Require a second closed bar above the breakout level before entry.",
    "STOP_TOO_TIGHT": "Test a wider ATR stop multiple with proportionally smaller size.",
    "STOP_TOO_WIDE": "Test a tighter structural stop; review gap/slippage assumptions.",
    "TARGET_TOO_AMBITIOUS": "Test partial take-profit at 1R and a closer final target.",
    "PREMATURE_EXIT": "Compare manual/AI early exits with the original plan before changing exit policy.",
    "CORRELATION_OVEREXPOSURE": "Lower the correlated-exposure limit in the Portfolio Brain policy.",
    "FUNDING_DRAG": "Penalize persistent adverse funding or prefer Spot for long holds.",
    "SLIPPAGE_DRAG": "Prefer limit entries or avoid low-liquidity instruments.",
    "AI_COST_TOO_HIGH": "Raise the escalation threshold so deep reasoning is reserved for high-impact cases.",
    "HUMAN_OVERRIDE_HURT": "Track override outcomes before relaxing AI authority on similar setups.",
    "AI_OVERRIDE_HURT": "Restrict autonomous management actions of this type to RECOMMEND_ONLY.",
}


def _f(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def excursions(side: str, entry: float, bars: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """Maximum favorable / adverse excursion in price units over the holding path."""

    if not bars:
        return None, None
    highs = [float(bar["high"]) for bar in bars]
    lows = [float(bar["low"]) for bar in bars]
    if side == "long":
        return max(0.0, max(highs) - entry), max(0.0, entry - min(lows))
    return max(0.0, entry - min(lows)), max(0.0, max(highs) - entry)


def counterfactual_plan_pnl(
    side: str, entry: float, stop: float, target: float, quantity: float, bars: list[dict[str, Any]]
) -> float | None:
    """Gross PnL of the *original* stop/target over the observed path (stop wins ties)."""

    sign = 1 if side == "long" else -1
    for bar in bars:
        low, high = float(bar["low"]), float(bar["high"])
        stop_hit = low <= stop if side == "long" else high >= stop
        target_hit = high >= target if side == "long" else low <= target
        if stop_hit:
            return sign * (stop - entry) * quantity
        if target_hit:
            return sign * (target - entry) * quantity
    if not bars:
        return None
    return sign * (float(bars[-1]["close"]) - entry) * quantity


def build_review(context: dict[str, Any]) -> dict[str, Any]:
    """Typed post-trade diagnosis from a closed position context."""

    side = context["side"]
    entry = float(context["entry_price"])
    quantity = float(context["opened_quantity"])
    realized = _f(context.get("realized_pnl")) or 0.0
    initial_risk = _f(context.get("initial_risk_usdt"))
    r_unit = initial_risk if initial_risk and initial_risk > 0 else None
    r_multiple = realized / r_unit if r_unit else None
    bars = context.get("path_bars") or []
    mfe_price, mae_price = excursions(side, entry, bars)
    mfe_r = mfe_price * quantity / r_unit if mfe_price is not None and r_unit else None
    mae_r = mae_price * quantity / r_unit if mae_price is not None and r_unit else None
    exit_reason = context.get("exit_reason") or ""
    features = context.get("entry_features") or {}
    tags: list[str] = []
    if r_unit:
        loss = realized < -0.1 * r_unit
        if loss and mfe_r is not None and mfe_r < 0.25:
            tags.append("BAD_DIRECTION")
        if exit_reason == "stop" and mfe_r is not None and mfe_r >= 0.5:
            tags.append("STOP_TOO_TIGHT")
        if loss and realized < -1.2 * r_unit:
            tags.append("STOP_TOO_WIDE")
        target_distance = _f(context.get("target_distance"))
        if (
            target_distance
            and mfe_price is not None
            and exit_reason not in {"target", "target_partial"}
            and mfe_price >= 0.8 * target_distance
            and realized < 0.5 * r_unit
        ):
            tags.append("TARGET_TOO_AMBITIOUS")
        if exit_reason in {"reduce_only", "user_close", "ai_close", "replan_close", "user_sell", "ai_sell"} and 0 <= realized < 0.3 * r_unit:
            tags.append("PREMATURE_EXIT")
        if (
            loss
            and features.get("signal_trigger") == "breakout"
            and context.get("holding_minutes") is not None
            and context["holding_minutes"] <= 60
        ):
            tags.append("FALSE_BREAKOUT")
        distance = _f(features.get("distance_to_breakout"))
        if loss and distance is not None and distance > 0.01:
            tags.append("LATE_ENTRY")
        funding = _f(context.get("funding_paid")) or 0.0
        if funding > 0.2 * r_unit:
            tags.append("FUNDING_DRAG")
        slippage = _f(context.get("slippage_paid")) or 0.0
        if slippage > 0.1 * r_unit:
            tags.append("SLIPPAGE_DRAG")
        brain = context.get("brain") or {}
        group_after = (brain.get("exposure") or {}).get("group_risk_pct_after")
        limit = _f(context.get("correlated_limit_pct"))
        if loss and isinstance(group_after, (int, float)) and limit and group_after >= 0.75 * limit:
            tags.append("CORRELATION_OVEREXPOSURE")
        regime = context.get("entry_regime")
        if loss and regime in {"bull", "bear"} and ((regime == "bull") != (side == "long")):
            tags.append("REGIME_MISCLASSIFIED")
    ai_cost_usdt = _f(context.get("ai_cost_usdt"))
    if ai_cost_usdt is not None and ai_cost_usdt > 0 and ai_cost_usdt > 0.5 * max(abs(realized), 1e-9):
        tags.append("AI_COST_TOO_HIGH")
    counterfactual = _f(context.get("counterfactual_gross_pnl"))
    actual_gross = _f(context.get("realized_gross"))
    override_source = context.get("override_source")
    if counterfactual is not None and actual_gross is not None and override_source in {"USER", "AI"}:
        helped = actual_gross > counterfactual
        tags.append(f"{'HUMAN' if override_source == 'USER' else 'AI'}_OVERRIDE_{'HELPED' if helped else 'HURT'}")
    tags = [tag for tag in dict.fromkeys(tags) if tag in LEARNING_TAGS]
    if r_unit and abs(realized) < 0.1 * r_unit:
        outcome = "BREAKEVEN"
    else:
        outcome = "WIN" if realized > 0 else "LOSS" if realized < 0 else "BREAKEVEN"
    headline = tags[0] if tags else None
    lesson = (
        f"{outcome} {'' if r_multiple is None else f'{r_multiple:+.2f}R '}· exit {exit_reason or 'unknown'}"
        + (f" · primary factor {headline.replace('_', ' ').lower()}" if headline else " · no failure pattern detected")
    )
    review = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "position_ref": context["position_ref"],
        "market_type": context["market_type"],
        "symbol": context["symbol"],
        "side": side,
        "outcome": outcome,
        "tags": tags,
        "realized_pnl_usdt": realized,
        "r_multiple": r_multiple,
        "initial_risk_usdt": initial_risk,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "mfe_price": mfe_price,
        "mae_price": mae_price,
        "path_bars": len(bars),
        "path_status": "observed" if bars else "unavailable",
        "exit_reason": exit_reason,
        "holding_minutes": context.get("holding_minutes"),
        "fees_usdt": _f(context.get("fees_paid")),
        "funding_usdt": _f(context.get("funding_paid")),
        "slippage_usdt": _f(context.get("slippage_paid")),
        "ai_cost_usd": _f(context.get("ai_cost_usd")),
        "ai_cost_usdt": ai_cost_usdt,
        "decision_stack": context.get("decision_stack") or {},
        "management_actions": context.get("management_actions") or [],
        "user_overrides": context.get("user_overrides") or 0,
        "counterfactual_gross_pnl": counterfactual,
        "lesson": lesson,
        "hypothesis": HYPOTHESES.get(headline) if headline else None,
        "auto_applied_to_strategy": False,
    }
    review["review_id"] = f"rev-{digest({'ref': context['position_ref'], 'closed_at': context.get('closed_at')})[:20]}"
    return review


def _session(timestamp: str | None) -> str:
    if not timestamp:
        return "unknown"
    hour = parse_utc(timestamp, "session").hour
    return "asia" if hour < 8 else "europe" if hour < 16 else "us"


def _stats(trades: list[dict[str, Any]], equity: float) -> dict[str, Any]:
    pnl = [float(trade.get("realized_pnl") or 0.0) for trade in trades]
    gains = sum(value for value in pnl if value > 0)
    losses = -sum(value for value in pnl if value < 0)
    running = equity
    peak = equity
    drawdown = 0.0
    for trade in sorted(trades, key=lambda item: item.get("closed_at") or ""):
        running += float(trade.get("realized_pnl") or 0.0)
        peak = max(peak, running)
        if peak > 0:
            drawdown = max(drawdown, (peak - running) / peak)
    return {
        "closed_trades": len(pnl),
        "net_trading_pnl_usdt": sum(pnl),
        "expectancy_usdt": sum(pnl) / len(pnl) if pnl else None,
        "win_rate": sum(1 for value in pnl if value > 0) / len(pnl) if pnl else None,
        "profit_factor": gains / losses if losses > 0 else None,
        "max_drawdown": drawdown if pnl else None,
        "fees_usdt": sum(float(t.get("entry_fee") or 0) + float(t.get("exit_fees") or 0) for t in trades),
        "funding_usdt": sum(float(t.get("funding_paid") or 0) for t in trades),
        "slippage_usdt": sum(float(t.get("slippage_paid") or 0) for t in trades),
    }


ARM_COST_PATHS = {
    "quant": set(),
    "jev": {"shared:jev"},
    "quant_jev": {"shared:jev"},
    "luna": {"luna"},
    "luna_skill": {"luna_skill"},
    "hybrid": {"shared:jev", "hybrid"},
    "hybrid_brain": {"shared:jev", "hybrid"},
}


def strategy_tournament(
    *,
    positions: list[dict[str, Any]],
    usage_events: list[dict[str, Any]],
    cycles: list[dict[str, Any]],
    arms: list[str],
    starting_balance: float,
    fx: dict[str, Any] | None,
) -> dict[str, Any]:
    """Aligned-arm comparison. Every arm reads the same frozen snapshots per cycle."""

    usdt_per_usd = None
    if isinstance(fx, dict) and fx.get("mode") == "manual" and isinstance(fx.get("usdt_per_usd"), (int, float)):
        usdt_per_usd = float(fx["usdt_per_usd"])
    aligned_cycles = [cycle for cycle in cycles if cycle.get("status") == "complete"]
    eligible = sum(1 for cycle in aligned_cycles if (cycle.get("payload", {}).get("quant_gate") or {}).get("eligible"))
    rows = []
    for arm in arms:
        cohort = f"arm-{arm}"
        trades = [
            position for position in positions
            if position["cohort"] == cohort and position["status"] == "closed" and position["closed_pnl_recorded"]
        ]
        open_positions = [p for p in positions if p["cohort"] == cohort and p["status"] == "open"]
        stats = _stats(trades, starting_balance)
        paths = ARM_COST_PATHS.get(arm, set())
        arm_usage = [
            event for event in usage_events
            if event.get("arm") in paths and event.get("status") in {"ok", "failed"}
        ]
        unpriced = sum(1 for event in arm_usage if event.get("cost_status") == "unavailable")
        cost_usd = sum(float(event.get("estimated_cost_usd") or 0.0) for event in arm_usage)
        cost_usdt = None if usdt_per_usd is None else cost_usd * usdt_per_usd
        economic = None if cost_usdt is None or unpriced else stats["net_trading_pnl_usdt"] - cost_usdt
        decisions = [
            (cycle.get("payload", {}).get("arms") or {}).get(arm, {}).get("decision")
            for cycle in aligned_cycles
        ]
        rows.append({
            "arm": arm,
            "cohort": cohort,
            **stats,
            "open_positions": len(open_positions),
            "entries_proposed": sum(1 for decision in decisions if decision and decision.startswith("ENTER")),
            "no_trade_decisions": sum(1 for decision in decisions if decision == "NO_TRADE"),
            "ai_calls": len(arm_usage),
            "ai_cost_usd": cost_usd,
            "ai_cost_unpriced_calls": unpriced,
            "ai_cost_attribution": "standalone (shared Jev cost is attributed to every Jev-dependent arm)",
            "ai_cost_per_trade_usd": cost_usd / stats["closed_trades"] if stats["closed_trades"] else None,
            "economic_pnl_usdt": economic,
            "by_asset": _group(trades, lambda t: t.get("symbol") or "unknown"),
            "by_regime": _group(trades, lambda t: t.get("market_regime") or "unknown"),
            "by_session": _group(trades, lambda t: _session(t.get("opened_at"))),
        })
    baseline = next((row for row in rows if row["arm"] == "quant"), None)
    for row in rows:
        if baseline is None or row is baseline:
            row["incremental_vs_quant"] = None
            continue
        pnl_delta = row["net_trading_pnl_usdt"] - baseline["net_trading_pnl_usdt"]
        cost_delta_usd = row["ai_cost_usd"] - baseline["ai_cost_usd"]
        row["incremental_vs_quant"] = {
            "trading_pnl_usdt": pnl_delta,
            "ai_cost_usd": cost_delta_usd,
            "economic_value_usdt": None if usdt_per_usd is None else pnl_delta - cost_delta_usd * usdt_per_usd,
            "sample": {"arm": row["closed_trades"], "quant": baseline["closed_trades"]},
        }
        n = min(row["closed_trades"], baseline["closed_trades"])
        row["promotion_status"] = (
            "INSUFFICIENT_SAMPLE" if n < MIN_PROMOTION_SAMPLE
            else "NOT_PROMOTED_ON_HEADLINE_PNL"
        )
    if baseline is not None:
        baseline["promotion_status"] = "BASELINE"
    return {
        "schema_version": TOURNAMENT_SCHEMA_VERSION,
        "aligned_cycles": len(aligned_cycles),
        "eligible_cases": eligible,
        "fx": {"usdt_per_usd": usdt_per_usd, "available": usdt_per_usd is not None},
        "arms": rows,
        "promotion_rule": (
            f"No arm is promoted on headline PnL. Promotion requires >= {MIN_PROMOTION_SAMPLE} aligned closed trades "
            "per arm, positive economic value after AI cost, and a drawdown no worse than the baseline."
        ),
        "causal_claims": False,
    }


def _group(trades: list[dict[str, Any]], key) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        groups.setdefault(key(trade), []).append(trade)
    return [
        {
            "key": name,
            "closed_trades": len(items),
            "net_trading_pnl_usdt": sum(float(t.get("realized_pnl") or 0) for t in items),
        }
        for name, items in sorted(groups.items())
    ]


def ai_filter_counterfactuals(cycles: list[dict[str, Any]], positions: list[dict[str, Any]]) -> dict[str, Any]:
    """AI_FILTER_HELPED/HURT counts: hybrid NO_TRADE vs the aligned quant arm's closed outcome."""

    quant_by_cycle = {
        position["cycle_id"]: position
        for position in positions
        if position["cohort"] == "arm-quant" and position["status"] == "closed" and position["closed_pnl_recorded"]
    }
    helped = hurt = 0
    for cycle in cycles:
        payload = cycle.get("payload") or {}
        hybrid = (payload.get("arms") or {}).get("hybrid") or {}
        if hybrid.get("decision") != "NO_TRADE":
            continue
        quant = quant_by_cycle.get(cycle["cycle_id"])
        if quant is None:
            continue
        if float(quant.get("realized_pnl") or 0) < 0:
            helped += 1
        else:
            hurt += 1
    return {"AI_FILTER_HELPED": helped, "AI_FILTER_HURT": hurt, "sample": helped + hurt}


def update_hypotheses(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for review in reviews:
        for tag in review.get("tags", []):
            if tag in HYPOTHESES:
                counts[tag] = counts.get(tag, 0) + 1
    return [
        {
            "tag": tag,
            "statement": HYPOTHESES[tag],
            "evidence_count": count,
            "status": "candidate" if count >= 3 else "observing",
            "auto_applied": False,
        }
        for tag, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def holding_minutes(opened_at: str | None, closed_at: str | None) -> float | None:
    if not opened_at or not closed_at:
        return None
    return (parse_utc(closed_at, "closed_at") - parse_utc(opened_at, "opened_at")).total_seconds() / 60


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def path_window(opened_at: str, closed_at: str) -> tuple[datetime, datetime]:
    start = parse_utc(opened_at, "opened_at")
    end = parse_utc(closed_at, "closed_at") + timedelta(minutes=1)
    return start, end
