"""Unified human-readable Activity journal and deterministic Attention queue.

Activity merges (a) events written by the portfolio OS (orders, spot fills, management,
re-plans, settings) with (b) a read-time projection of the futures runtime's existing
ledgers (cycle decisions, fills, closes, funding, runtime status). Nothing is written
twice, and low-level provider calls are never surfaced individually.

Attention items are derived from current runtime state, deduplicated by a stable key,
and auto-resolved when the underlying condition clears.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .contracts import digest
from .paper_contracts import parse_utc
from .portfolio_store import ACTIVITY_CATEGORIES, ACTIVITY_SOURCES, SEVERITIES


ACTIVITY_SCHEMA_VERSION = "activity-event.v1"
ATTENTION_SCHEMA_VERSION = "attention-event.v1"
SEVERITY_RANK = {name: index for index, name in enumerate(reversed(SEVERITIES))}  # CRITICAL=0


def activity_event(
    *,
    experiment_id: str,
    timestamp: str,
    source: str,
    category: str,
    severity: str,
    title: str,
    summary: str = "",
    instrument_id: str | None = None,
    symbol: str | None = None,
    market_type: str | None = None,
    position_ref: str | None = None,
    payload_ref: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    if source not in ACTIVITY_SOURCES or category not in ACTIVITY_CATEGORIES or severity not in SEVERITIES:
        raise ValueError("activity event enum is invalid")
    value = {
        "schema_version": ACTIVITY_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "timestamp": timestamp,
        "instrument_id": instrument_id,
        "symbol": symbol,
        "market_type": market_type,
        "position_ref": position_ref,
        "source": source,
        "category": category,
        "severity": severity,
        "title": title[:160],
        "summary": summary[:400],
        "payload_ref": payload_ref,
    }
    value["event_id"] = event_id or f"act-{digest(value)[:24]}"
    return value


def _fmt(value: Any, digits: int = 4) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    magnitude = abs(float(value))
    if magnitude >= 1000:
        return f"{float(value):,.2f}"
    if magnitude >= 1:
        return f"{float(value):.{min(digits, 4)}f}"
    return f"{float(value):.6g}"


def _perp_instrument(exchange: str, symbol: str | None) -> str | None:
    if not symbol or not symbol.endswith("USDT"):
        return None
    return f"{exchange}:perpetual:{symbol[:-4]}_USDT"


def project_runtime_activity(
    *,
    experiment_id: str,
    exchange: str,
    legacy_events: list[dict[str, Any]],
    cycles: list[dict[str, Any]],
    positions_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Read-time projection of existing futures-runtime ledgers into ActivityEvents."""

    events: list[dict[str, Any]] = []
    for cycle in cycles:
        if cycle.get("status") != "complete":
            if cycle.get("status") in {"failed", "interrupted"}:
                events.append(activity_event(
                    experiment_id=experiment_id, timestamp=cycle.get("updated_at") or cycle.get("created_at"),
                    source="SYSTEM", category="ALERT", severity="WATCH",
                    title=f"Cycle {cycle.get('status')}", summary=str(cycle.get("error_code") or ""),
                    symbol=cycle.get("symbol"), market_type="perpetual",
                    instrument_id=_perp_instrument(exchange, cycle.get("symbol")),
                    payload_ref=f"cycle:{cycle['cycle_id']}", event_id=f"cyc-{cycle['cycle_id']}-state",
                ))
            continue
        symbol = cycle.get("symbol")
        instrument = _perp_instrument(exchange, symbol)
        gate = cycle.get("quant_gate") or {}
        primary = cycle.get("primary_decision") or {}
        risk = cycle.get("risk") or {}
        timestamp = cycle.get("as_of") or cycle.get("created_at")
        if not gate.get("eligible") and primary.get("intent") is None:
            continue  # routine no-signal cycles are not journal noise
        if gate.get("eligible"):
            events.append(activity_event(
                experiment_id=experiment_id, timestamp=timestamp, source="SYSTEM", category="SIGNAL",
                severity="INFO", title=f"{str(gate.get('direction', '')).upper()} signal detected",
                summary=f"quant strength {_fmt(gate.get('strength'), 2)} · {gate.get('trigger', '')}",
                symbol=symbol, instrument_id=instrument, market_type="perpetual",
                payload_ref=f"cycle:{cycle['cycle_id']}", event_id=f"cyc-{cycle['cycle_id']}-signal",
            ))
        routing = cycle.get("arms", {}).get(cycle.get("primary_arm") or "", {})
        escalation = routing.get("escalation") or {}
        if cycle.get("jev_status") == "completed":
            events.append(activity_event(
                experiment_id=experiment_id, timestamp=timestamp, source="AI", category="DECISION",
                severity="INFO", title=f"Jev: {cycle.get('market_regime', 'unknown')} regime",
                summary="escalated to Luna" if escalation.get("escalate") else "resolved on fast path",
                symbol=symbol, instrument_id=instrument, market_type="perpetual",
                payload_ref=f"cycle:{cycle['cycle_id']}", event_id=f"cyc-{cycle['cycle_id']}-jev",
            ))
        intent = primary.get("intent")
        if intent:
            events.append(activity_event(
                experiment_id=experiment_id, timestamp=timestamp, source="AI", category="DECISION",
                severity="INFO", title=f"{intent['side'].upper()} plan proposed",
                summary=f"entry {_fmt(intent.get('entry_price'))} · stop {_fmt(intent.get('stop_price'))} · target {_fmt(intent.get('target_price'))}",
                symbol=symbol, instrument_id=instrument, market_type="perpetual",
                payload_ref=f"cycle:{cycle['cycle_id']}", event_id=f"cyc-{cycle['cycle_id']}-plan",
            ))
        else:
            events.append(activity_event(
                experiment_id=experiment_id, timestamp=timestamp, source="AI", category="DECISION",
                severity="INFO", title="No trade", summary=str(primary.get("reason") or "")[:200],
                symbol=symbol, instrument_id=instrument, market_type="perpetual",
                payload_ref=f"cycle:{cycle['cycle_id']}", event_id=f"cyc-{cycle['cycle_id']}-plan",
            ))
        brain = cycle.get("portfolio_brain")
        if brain and brain.get("action") not in {None, "ALLOW"}:
            events.append(activity_event(
                experiment_id=experiment_id, timestamp=timestamp, source="SYSTEM", category="RISK",
                severity="WATCH" if not brain.get("allowed") else "INFO",
                title=f"Portfolio Brain {brain['action']}", summary=", ".join(brain.get("reason_codes", []))[:200],
                symbol=symbol, instrument_id=instrument, market_type="perpetual",
                payload_ref=f"cycle:{cycle['cycle_id']}", event_id=f"cyc-{cycle['cycle_id']}-brain",
            ))
        if intent and risk.get("code"):
            allowed = bool(risk.get("allowed"))
            events.append(activity_event(
                experiment_id=experiment_id, timestamp=timestamp, source="SYSTEM", category="RISK",
                severity="INFO" if allowed else "WATCH",
                title="Risk approved" if allowed else f"Risk rejected · {risk['code']}",
                summary=(
                    f"qty {_fmt(risk.get('quantity'))} · max loss {_fmt(risk.get('risk_amount'), 2)} USDT · {risk.get('leverage')}x"
                    if allowed else str(risk.get("reason") or "")[:200]
                ),
                symbol=symbol, instrument_id=instrument, market_type="perpetual",
                payload_ref=f"cycle:{cycle['cycle_id']}", event_id=f"cyc-{cycle['cycle_id']}-risk",
            ))
    for event in legacy_events:
        payload = event.get("payload") or {}
        kind = event.get("event_type")
        timestamp = event.get("created_at")
        position = positions_by_id.get(payload.get("position_id") or "")
        if position is not None and position.get("cohort") != "primary":
            continue
        symbol = payload.get("symbol") or (position or {}).get("symbol")
        position_ref = f"perp:{payload['position_id']}" if payload.get("position_id") else None
        common = {
            "experiment_id": experiment_id,
            "timestamp": timestamp,
            "symbol": symbol,
            "instrument_id": _perp_instrument(exchange, symbol),
            "market_type": "perpetual",
            "position_ref": position_ref,
            "payload_ref": f"event:{event.get('event_id')}",
            "event_id": f"evt-{event.get('event_id')}",
        }
        if kind == "paper_fill":
            if payload.get("cohort", "primary") != "primary":
                continue
            events.append(activity_event(
                **common, source="SYSTEM", category="FILL", severity="INFO",
                title=f"PAPER {payload.get('side', '')} filled",
                summary=f"{_fmt(payload.get('quantity'))} @ {_fmt(payload.get('price'))} · fee {_fmt(payload.get('fee'))}",
            ))
        elif kind in {"paper_position_closed", "paper_position_reduced"}:
            if position is None:
                continue
            closed = kind == "paper_position_closed"
            reason = payload.get("exit_reason") or ""
            source = "SYSTEM" if reason in {"stop", "target", "liquidation", "target_partial"} else "USER"
            if reason == "reduce_only" and position.get("_last_mutation_source") == "AI":
                source = "AI"
            events.append(activity_event(
                **common, source=source, category="OUTCOME" if closed else "MANAGEMENT",
                severity="WATCH" if reason == "liquidation" else "INFO",
                title=("Position closed" if closed else "Position reduced") + (f" · {reason}" if reason else ""),
                summary=(
                    f"{_fmt(payload.get('quantity'))} @ {_fmt(payload.get('price'))}"
                    + (f" · realized {_fmt(payload.get('realized_pnl'), 2)} USDT" if closed and payload.get("realized_pnl") is not None else "")
                ),
            ))
        elif kind == "funding_settlement":
            if position is None:
                continue
            events.append(activity_event(
                **common, source="SYSTEM", category="COST", severity="INFO",
                title="Funding settled", summary=f"{_fmt(payload.get('funding_cost'))} USDT at rate {_fmt(payload.get('funding_rate'))}",
            ))
        elif kind == "runtime_status":
            events.append(activity_event(
                **{**common, "symbol": None, "instrument_id": None, "market_type": None, "position_ref": None},
                source="USER", category="ALERT", severity="INFO",
                title=f"Automation {str(payload.get('status', '')).upper()}", summary="scheduler state changed",
            ))
        elif kind == "market_feed_stale":
            events.append(activity_event(
                **{**common, "position_ref": None}, source="SYSTEM", category="ALERT", severity="WATCH",
                title="Market feed stale", summary="new entries and AI calls blocked for this cycle",
            ))
        elif kind == "pending_orders_cancelled":
            events.append(activity_event(
                **{**common, "symbol": None, "instrument_id": None, "market_type": None, "position_ref": None},
                source="SYSTEM", category="ORDER", severity="INFO",
                title=f"{payload.get('count')} pending AI order(s) cancelled", summary=f"runtime {payload.get('reason')}",
            ))
        elif kind in {"monitor_error", "market_data_failure"}:
            events.append(activity_event(
                **{**common, "position_ref": None}, source="SYSTEM", category="ALERT", severity="WATCH",
                title="Market data unavailable", summary=str(payload.get("code") or kind),
            ))
    return events


def filter_activity(
    events: list[dict[str, Any]],
    *,
    symbol: str | None = None,
    position_ref: str | None = None,
    category: str | None = None,
    source: str | None = None,
    market_type: str | None = None,
    severity: str | None = None,
    since: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    result = []
    for event in events:
        if symbol and (event.get("symbol") or "").upper() != symbol.upper():
            continue
        if position_ref and event.get("position_ref") != position_ref:
            continue
        if category and event.get("category") != category:
            continue
        if source and event.get("source") != source:
            continue
        if market_type and event.get("market_type") != market_type:
            continue
        if severity and SEVERITY_RANK.get(event.get("severity"), 9) > SEVERITY_RANK.get(severity, 9):
            continue
        if since and (event.get("timestamp") or "") <= since:
            continue
        result.append(event)
    result.sort(key=lambda item: (item.get("timestamp") or "", item["event_id"]), reverse=True)
    return result[: max(1, min(int(limit), 1000))]


def attention_candidate(
    *,
    kind: str,
    key: str,
    severity: str,
    category: str,
    title: str,
    summary: str,
    action: dict[str, Any] | None = None,
    symbol: str | None = None,
    instrument_id: str | None = None,
    position_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "dedupe_key": f"{kind}:{key}",
        "severity": severity,
        "category": category,
        "title": title[:160],
        "summary": summary[:300],
        "action": action or {},
        "symbol": symbol,
        "instrument_id": instrument_id,
        "position_ref": position_ref,
    }


def derive_attention(context: dict[str, Any], settings: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Deterministic attention conditions from runtime state; one candidate per condition."""

    attention = settings["attention"]
    items: list[dict[str, Any]] = []
    positions = context.get("positions", [])
    experiment = context.get("experiment", {})
    stream = context.get("market_stream")
    market_mode = experiment.get("config", {}).get("market_data_mode")
    perp_open = [p for p in positions if p["market_type"] == "perpetual"]
    if market_mode == "gate_usdt" and perp_open:
        state = (stream or {}).get("state", "OFFLINE")
        if state not in {"LIVE"}:
            items.append(attention_candidate(
                kind="feed_stale", key="gate", severity="CRITICAL", category="ALERT",
                title=f"Market feed {state} with open positions",
                summary="Positions are monitored from closed REST bars; new entries fail closed until the feed recovers.",
                action={"route": "trade"},
            ))
    for position in positions:
        ref = position["position_ref"]
        live = position.get("live_price") or position.get("mark_price")
        stop = position.get("stop_price")
        side = position.get("side")
        common = {"symbol": position.get("symbol"), "instrument_id": position.get("instrument_id"), "position_ref": ref}
        if position["market_type"] == "perpetual":
            buffer = position.get("liquidation_buffer_pct")
            if isinstance(buffer, (int, float)) and buffer <= attention["liquidation_buffer_critical_pct"]:
                items.append(attention_candidate(
                    kind="liquidation_buffer", key=ref, severity="CRITICAL", category="RISK",
                    title=f"{position['symbol']} liquidation buffer {buffer * 100:.1f}%",
                    summary="Mark is close to the isolated liquidation estimate.",
                    action={"route": "position", "position_ref": ref}, **common,
                ))
        if isinstance(live, (int, float)) and isinstance(stop, (int, float)) and live > 0:
            distance = (live - stop) / live if side == "long" else (stop - live) / live
            if 0 <= distance <= attention["near_stop_pct"]:
                items.append(attention_candidate(
                    kind="near_stop", key=ref, severity="ACTION", category="RISK",
                    title=f"{position['symbol']} {distance * 100:.2f}% from stop",
                    summary="Review protection or reduce.", action={"route": "position", "position_ref": ref}, **common,
                ))
        r_multiple = position.get("r_multiple")
        entry = position.get("entry_price")
        if (
            isinstance(r_multiple, (int, float))
            and r_multiple >= settings["brain"]["protect_profit_r"]
            and isinstance(stop, (int, float))
            and isinstance(entry, (int, float))
            and ((side == "long" and stop < entry) or (side == "short" and stop > entry))
        ):
            items.append(attention_candidate(
                kind="protect_profit", key=ref, severity="ACTION", category="MANAGEMENT",
                title=f"{position['symbol']} +{r_multiple:.1f}R · stop below breakeven",
                summary="Consider a protect-profit re-plan.",
                action={"route": "position", "position_ref": ref, "replan_intent": "protect_profit"}, **common,
            ))
        if position.get("thesis_status") in {"weakening", "broken"}:
            items.append(attention_candidate(
                kind="thesis", key=ref, severity="WATCH" if position["thesis_status"] == "weakening" else "ACTION",
                category="MANAGEMENT", title=f"{position['symbol']} thesis {position['thesis_status']}",
                summary="Latest review found evidence against the position.",
                action={"route": "position", "position_ref": ref, "replan_intent": "exit_if_thesis_weakened"}, **common,
            ))
        if position.get("management_mode") == "MANUAL_OVERRIDE" and position.get("last_user_override_at"):
            age = now - parse_utc(position["last_user_override_at"], "override")
            if age > timedelta(hours=24):
                items.append(attention_candidate(
                    kind="manual_override_stale", key=ref, severity="WATCH", category="MANAGEMENT",
                    title=f"{position['symbol']} under manual control > 24h",
                    summary="AI observes but cannot manage this position.",
                    action={"route": "position", "position_ref": ref}, **common,
                ))
    for proposal in context.get("pending_proposals", []):
        items.append(attention_candidate(
            kind="replan", key=proposal["position_ref"], severity="ACTION", category="MANAGEMENT",
            title=f"{proposal.get('symbol', '')} AI re-plan: {proposal.get('headline', 'review')}",
            summary=", ".join(proposal.get("reason_codes", []))[:200],
            action={"route": "position", "position_ref": proposal["position_ref"], "proposal_id": proposal["proposal_id"]},
            symbol=proposal.get("symbol"), position_ref=proposal["position_ref"],
        ))
    for order in context.get("pending_orders", []):
        created = parse_utc(order["created_at"], "order.created_at")
        if now - created >= timedelta(minutes=attention["pending_order_review_minutes"]):
            items.append(attention_candidate(
                kind="pending_order", key=order["order_ref"], severity="ACTION", category="ORDER",
                title=f"{order['display_symbol']} {order['side']} limit pending",
                summary=f"open for {int((now - created).total_seconds() // 60)} min · {order['filled_pct'] * 100:.0f}% filled",
                action={"route": "orders", "order_ref": order["order_ref"]},
                symbol=order.get("symbol"), instrument_id=order.get("instrument_id"),
            ))
    budget = context.get("budget_status") or {}
    utilization = budget.get("utilization_today")
    if budget.get("exhausted"):
        items.append(attention_candidate(
            kind="budget", key="exhausted", severity="CRITICAL", category="COST",
            title="AI budget exhausted", summary=f"Limit action {budget.get('budget', {}).get('limit_action')} is in force.",
            action={"route": "settings"},
        ))
    elif isinstance(utilization, (int, float)) and utilization >= attention["budget_warning_utilization"]:
        items.append(attention_candidate(
            kind="budget", key="warning", severity="ACTION", category="COST",
            title=f"AI budget {utilization * 100:.0f}% used today", summary="Paid AI calls stop at the hard limit.",
            action={"route": "settings"},
        ))
    if experiment.get("status") == "running":
        last_cycle_at = context.get("last_cycle_at")
        if last_cycle_at and now - parse_utc(last_cycle_at, "cycle") > timedelta(minutes=attention["scheduler_stall_minutes"]):
            items.append(attention_candidate(
                kind="scheduler", key="stall", severity="CRITICAL", category="ALERT",
                title="Scheduler has not completed a cycle recently",
                summary=f"last cycle {last_cycle_at}", action={"route": "settings"},
            ))
    reconciliation = context.get("reconciliation") or {}
    if reconciliation and not all(bool(value) for value in reconciliation.values() if isinstance(value, bool)):
        items.append(attention_candidate(
            kind="reconciliation", key="portfolio", severity="CRITICAL", category="ALERT",
            title="Accounting reconciliation failed", summary="Equity does not reconcile to cash plus unrealized PnL.",
            action={"route": "portfolio"},
        ))
    for breach in context.get("concentration", []):
        items.append(attention_candidate(
            kind="concentration", key=breach["key"], severity="WATCH", category="RISK",
            title=breach["title"], summary=breach["summary"], action={"route": "portfolio"},
        ))
    kill = (context.get("kill_switch") or {}).get("level", "NORMAL")
    if kill != "NORMAL":
        items.append(attention_candidate(
            kind="kill_switch", key=kill, severity="CRITICAL" if kill in {"RISK_REDUCING_ONLY", "FULL_AUTOMATION_HALT"} else "ACTION",
            category="ALERT", title=f"Kill switch {kill}", summary=str((context.get("kill_switch") or {}).get("reason") or "")[:200],
            action={"route": "overview"},
        ))
    open_instruments = {p.get("instrument_id") for p in positions}
    for market in context.get("market_states", []):
        if market["state"] in {"CRASH_MODE", "MARKET_DATA_UNTRUSTED"} and market["instrument_id"] in open_instruments:
            items.append(attention_candidate(
                kind="market_safety", key=market["instrument_id"], severity="CRITICAL", category="RISK",
                title=f"{market['instrument_id'].split(':')[-1]} {market['state']}", summary=", ".join(market["reasons"])[:200],
                action={"route": "trade"}, instrument_id=market["instrument_id"],
            ))
    automation = settings["automation"]
    if automation["emergency_stop"]:
        items.append(attention_candidate(
            kind="automation", key="emergency_stop", severity="ACTION", category="ALERT",
            title="Emergency stop active", summary="New PAPER entries are blocked; monitoring continues.",
            action={"route": "overview"},
        ))
    for info in context.get("recent_info", []):
        items.append(attention_candidate(
            kind="info", key=info["key"], severity="INFO", category=info["category"],
            title=info["title"], summary=info["summary"], action=info.get("action"),
            symbol=info.get("symbol"), position_ref=info.get("position_ref"),
        ))
    return items
