"""Read-only views for the day-trading UX: today's P&L, the next decision time, all running experiments,
and the day-trade strategy-search results. Nothing here writes state or reaches a non-loopback host.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .paper_contracts import iso_utc, parse_utc
from .sleeves import ENGINE_ID as SLEEVES_ENGINE, capital_cohorts

DAY = 86400
# Loopback ports probed for other PAPER experiment servers. 8766 is deliberately absent (a user's own
# unrelated static server). Override with PAPER_PEER_PORTS="8765,8768,...".
DEFAULT_PEER_PORTS = (8765, 8767, 8768, 8769, 8770, 8771)
MAX_INTRADAY_POINTS = 288


def next_decision_at(config: dict[str, Any], now: datetime) -> str:
    """When the experiment's engine next evaluates: every 4h for sleeves, every 15m for the breakout."""

    step = 4 * 3600 if config.get("strategy_engine") == SLEEVES_ENGINE else 15 * 60
    delay = int(config.get("schedule_delay_seconds", 60))
    epoch = int(now.timestamp())
    boundary = epoch - epoch % step
    nxt = boundary + delay if epoch < boundary + delay else boundary + step + delay
    return iso_utc(datetime.fromtimestamp(nxt, timezone.utc))


def uses_ai(config: dict[str, Any], settings: dict[str, Any]) -> bool:
    if config.get("strategy_engine") == SLEEVES_ENGINE:
        return False  # the sleeves engine never calls AI, and AI position review / AI Spot skip its experiments
    return bool(config.get("jev_enabled") or config.get("gpt_escalation_enabled")
                or (settings.get("ai_spot") or {}).get("enabled") or (settings.get("review") or {}).get("enabled"))


def _day_start(ts: datetime) -> datetime:
    return ts.replace(hour=0, minute=0, second=0, microsecond=0)


def today(runtime: Any, now: datetime | None = None, *, calendar_days: int = 42) -> dict[str, Any]:
    """Today's (UTC trading day) P&L and trades, the daily-loss-limit usage, the intraday equity curve, and a
    calendar of recent days. Equity comes from the portfolio snapshots the monitor records every minute."""

    store = runtime.store
    now = (now or runtime._clock()).astimezone(timezone.utc)
    experiment = store.experiment()
    experiment_id, config = experiment["experiment_id"], experiment["config"]
    portfolio = runtime.portfolio
    settings = portfolio.settings()
    start_capital = float(config["starting_balance_usdt"]) + float(portfolio.spot_wallet()["starting_balance_usdt"])
    day0 = _day_start(now)
    first_day = day0 - timedelta(days=calendar_days - 1)
    # Snapshots taken before the experiment was frozen (set-up with default capital) are not the experiment.
    frozen = runtime.governance.frozen() if hasattr(runtime, "governance") else None
    since = frozen["frozen_at"] if frozen else "0000"
    snaps = store._query(
        "SELECT as_of, total_equity FROM portfolio_snapshots WHERE experiment_id=? AND as_of>=? AND as_of>=? ORDER BY as_of",
        (experiment_id, iso_utc(first_day - timedelta(days=1)), since))
    before = store._query(
        "SELECT total_equity FROM portfolio_snapshots WHERE experiment_id=? AND as_of<? AND as_of>=? ORDER BY as_of DESC LIMIT 1",
        (experiment_id, iso_utc(first_day), since))
    perp = portfolio._perp_wallet()
    equity_now = float(perp["equity"]) + float(portfolio.spot_wallet()["equity_usdt"])
    # end-of-day equity per UTC day (last snapshot of the day); the day before the window seeds the first delta
    eod: dict[str, float] = {}
    for row in snaps:
        eod[row["as_of"][:10]] = float(row["total_equity"])
    prev = float(before[0]["total_equity"]) if before else start_capital
    cohorts = capital_cohorts(config)
    closed = store._query(
        "SELECT substr(closed_at,1,10) AS d, COUNT(*) AS n, SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) AS w, "
        "SUM(realized_pnl) AS pnl, SUM(entry_fee+exit_fees) AS fees, SUM(funding_paid) AS funding FROM positions "
        "WHERE experiment_id=? AND status='closed' AND closed_pnl_recorded AND closed_at>=? AND cohort IN (%s) GROUP BY d"
        % ",".join("?" * len(cohorts)), (experiment_id, iso_utc(first_day), *cohorts))
    by_day = {row["d"]: row for row in closed}
    calendar, running = [], prev
    for k in range(calendar_days):
        d = (first_day + timedelta(days=k)).date().isoformat()
        is_today = d == day0.date().isoformat()
        end = equity_now if is_today else eod.get(d)
        trades = by_day.get(d)
        if end is None:
            calendar.append({"date": d, "pnl_usdt": None, "pnl_pct": None, "trades": int(trades["n"]) if trades else 0})
            continue
        pnl = end - running
        calendar.append({"date": d, "pnl_usdt": round(pnl, 4), "pnl_pct": round(pnl / running, 6) if running else None,
                         "trades": int(trades["n"]) if trades else 0, "today": is_today})
        running = end
    day_start_equity = next((float(r["total_equity"]) for r in reversed(snaps) if r["as_of"] < iso_utc(day0)), None)
    if day_start_equity is None:
        day_start_equity = prev if not any(r["as_of"] < iso_utc(day0) for r in snaps) else start_capital
    pnl_today = equity_now - day_start_equity
    t = by_day.get(day0.date().isoformat())
    intraday = [[r["as_of"], round(float(r["total_equity"]), 4)] for r in snaps if r["as_of"] >= iso_utc(day0)]
    if len(intraday) > MAX_INTRADAY_POINTS:
        step = len(intraday) / MAX_INTRADAY_POINTS
        intraday = [intraday[int(i * step)] for i in range(MAX_INTRADAY_POINTS)] + [intraday[-1]]
    intraday.append([iso_utc(now), round(equity_now, 4)])
    limit = float(config["max_daily_loss"])
    loss_frac = max(0.0, -pnl_today) / day_start_equity if day_start_equity > 0 else 0.0
    open_positions = [p for p in store.open_positions(experiment_id) if p["cohort"] in cohorts]
    paused = any(i["kind"] == "RISK_PAUSE" for i in runtime.resilience.incidents(status="OPEN", limit=50))
    days_with_pnl = [c for c in calendar if c["pnl_usdt"] is not None]
    ai = store._query("SELECT COUNT(*) AS n, COALESCE(SUM(cost_estimate),0) AS cost, "
                      "SUM(CASE WHEN observed_at>=? THEN 1 ELSE 0 END) AS today FROM ai_calls WHERE experiment_id=?",
                      (iso_utc(day0), experiment_id))[0]
    return {
        "experiment_id": experiment_id, "label": config.get("label") or experiment_id, "engine": config.get("strategy_engine", "breakout_15m"),
        "status": experiment["status"], "as_of": iso_utc(now), "trading_day_utc": day0.date().isoformat(),
        "starting_capital_usdt": round(start_capital, 4), "day_start_equity_usdt": round(day_start_equity, 4),
        "equity_usdt": round(equity_now, 4), "pnl_today_usdt": round(pnl_today, 4),
        "pnl_today_pct": round(pnl_today / day_start_equity, 6) if day_start_equity else None,
        "pnl_total_usdt": round(equity_now - start_capital, 4),
        "closed_today": {"trades": int(t["n"]) if t else 0, "wins": int(t["w"] or 0) if t else 0,
                         "realized_usdt": round(float(t["pnl"] or 0), 4) if t else 0.0,
                         "fees_usdt": round(float(t["fees"] or 0), 4) if t else 0.0,
                         "funding_usdt": round(float(t["funding"] or 0), 4) if t else 0.0},
        "open_positions": len(open_positions),
        "daily_loss_limit": {"limit_pct": limit, "loss_pct": round(loss_frac, 6), "used": round(min(1.0, loss_frac / limit), 4) if limit else None,
                             "paused": paused},
        "next_decision_at": next_decision_at(config, now),
        "uses_ai": uses_ai(config, settings),
        "ai": {"enabled": uses_ai(config, settings), "calls_total": int(ai["n"] or 0), "calls_today": int(ai["today"] or 0),
               "cost_usd_estimate": round(float(ai["cost"] or 0), 6)},
        "intraday": intraday,
        "calendar": calendar,
        "summary": {"days": len(days_with_pnl), "green_days": sum(1 for c in days_with_pnl if c["pnl_usdt"] > 0),
                    "best_day_usdt": max((c["pnl_usdt"] for c in days_with_pnl), default=None),
                    "worst_day_usdt": min((c["pnl_usdt"] for c in days_with_pnl), default=None)},
    }


# ------------------------------------------------------------------ all experiments (loopback peers)
def peer_ports() -> list[int]:
    raw = os.environ.get("PAPER_PEER_PORTS")
    if raw:
        return [int(p) for p in raw.split(",") if p.strip().isdigit() and 1024 <= int(p) <= 65535]
    return list(DEFAULT_PEER_PORTS)


def _get(port: int, path: str, timeout: float = 2.0) -> Any:
    # Loopback only, GET only, JSON only.
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
        return json.loads(response.read())


def _row(port: int, campaign: dict[str, Any], day: dict[str, Any] | None, health: dict[str, Any] | None, is_self: bool) -> dict[str, Any]:
    sleeves = campaign.get("sleeves") or {}
    return {
        "port": port, "self": is_self, "url": f"http://127.0.0.1:{port}/frontend/#/overview",
        "label": campaign.get("label") or campaign.get("experiment_id"), "experiment_id": campaign.get("experiment_id"),
        "engine": campaign.get("engine") or "breakout_15m", "status": campaign.get("status"),
        "day": campaign.get("elapsed_days"), "completed_trades": campaign.get("completed_trades"), "target_trades": campaign.get("target_trades"),
        "min_days": campaign.get("min_days"), "next_checkpoint": campaign.get("next_checkpoint"),
        "equity_usdt": (day or {}).get("equity_usdt"), "pnl_total_usdt": (day or {}).get("pnl_total_usdt"),
        "pnl_today_usdt": (day or {}).get("pnl_today_usdt"), "pnl_today_pct": (day or {}).get("pnl_today_pct"),
        "starting_capital_usdt": (day or {}).get("starting_capital_usdt"),
        "open_positions": (day or {}).get("open_positions"),
        "drawdown": (sleeves.get("last_tick") or {}).get("combined_drawdown"),
        "health": (health or {}).get("overall"), "open_incidents": len((health or {}).get("open_incidents") or []),
        "risk_incidents": campaign.get("risk_incidents") or [],
        "calendar": [c for c in ((day or {}).get("calendar") or [])][-14:],
        "ai": (day or {}).get("ai"),
    }


def experiments(runtime: Any, own_port: int | None) -> dict[str, Any]:
    rows = []
    try:
        rows.append(_row(own_port or 0, runtime.governance.campaign_summary(), today(runtime),
                         runtime.health(), True))
    except Exception as exc:  # the local view must never break the page
        rows.append({"port": own_port, "self": True, "error": type(exc).__name__})
    for port in peer_ports():
        if port == own_port:
            continue
        try:
            health = _get(port, "/api/health", timeout=1.0)
        except Exception:
            continue  # nothing listening, or not a paper server
        if not isinstance(health, dict) or health.get("service") != "paper-futures":
            continue
        try:
            campaign = _get(port, "/api/campaign")
        except Exception as exc:
            rows.append({"port": port, "self": False, "error": f"campaign unavailable ({type(exc).__name__})"})
            continue
        try:
            day = _get(port, "/api/today")
        except Exception:
            day = None  # an older server without /api/today still shows its campaign row
        try:
            runtime_health = _get(port, "/api/runtime-health")
        except Exception:
            runtime_health = None
        rows.append(_row(port, campaign, day, runtime_health, False))
    rows.sort(key=lambda r: str(r.get("label") or r.get("port")))
    return {"as_of": iso_utc(datetime.now(timezone.utc)), "experiments": rows, "probed_ports": peer_ports()}


# ------------------------------------------------------------------ day-trade strategy search (research files)
def strategy_search(repo_root: Path) -> dict[str, Any]:
    """Results of reports/day-trade (git-ignored research output). Missing files -> available: false."""

    base = repo_root / "reports" / "day-trade"

    def read(name: str) -> Any:
        path = base / name
        try:
            return json.loads(path.read_text()) if path.exists() else None
        except (OSError, ValueError):
            return None

    status = read("status.json") or {}
    status = {k: status.get(k) for k in ("stage", "coins_total", "coins_downloaded", "coins_searched", "configs_per_coin",
                                          "updated_at", "started_at", "finished_at", "done", "error", "note")}
    analysis, deep, cross = read("analysis.json"), read("deep.json"), read("gate_crosscheck.json")
    out: dict[str, Any] = {"available": analysis is not None, "status": status}
    if analysis:
        sel = analysis.get("is_selection") or {}
        out["search"] = {"n_configs": analysis.get("n_configs"), "n_coins": analysis.get("n_coins"), "n_runs": analysis.get("n_runs"),
                         "periods": analysis.get("periods")}
        out["is_selection"] = {k: sel.get(k) for k in ("rule", "selected", "of_runs", "oos_net_pos_rate_selected", "oos_net_pos_rate_all",
                                                        "oos_median_sharpe_selected", "holdout_net_pos_rate_selected")}
        out["is_selection"]["top"] = (sel.get("top") or [])[:15]
        out["coins"] = analysis.get("coins") or []
        out["families"] = analysis.get("families") or []
        out["wfo"] = {k: {kk: v.get(kk) for kk in ("oos_sharpe", "oos_ret", "oos_pos_weeks", "holdout_sharpe", "holdout_ret")}
                      for k, v in (analysis.get("wfo") or {}).items()}
        latest = ((analysis.get("wfo") or {}).get("K20_sharpe") or {}).get("quarters") or []
        out["latest_picks"] = latest[-1] if latest else None
    if deep:
        out["deep"] = {k: deep.get(k) for k in ("procedure", "n_trades", "period", "leverage_grid", "best_within_20pct_dd", "scenarios",
                                                 "monte_carlo", "regimes")}
        out["deep"]["contribution"] = (deep.get("contribution") or [])[:15]
    if cross:
        out["gate_crosscheck"] = cross
    return out
