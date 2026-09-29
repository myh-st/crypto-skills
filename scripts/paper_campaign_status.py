"""Read-only status of the running PAPER campaign servers (EXP-001 and EXP-002).

    python3 scripts/paper_campaign_status.py [--json] [--server NAME=URL ...]

Only GET requests to loopback servers; it never changes state and never prints secrets.
Exit code: 0 = all OK, 1 = warnings (look soon), 2 = failure (act now).
Used by the scheduled daily check; safe to run by hand at any time.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Any

DEFAULT_SERVERS = {"EXP-001": "http://127.0.0.1:8765", "EXP-002": "http://127.0.0.1:8768"}
LOOPBACK = ("http://127.0.0.1:", "http://localhost:", "http://[::1]:")
TICK_GRACE_SECONDS = 4 * 3600 + 20 * 60  # a sleeves tick is due every 4h (+ schedule delay)


def _get(base: str, path: str) -> Any:
    with urllib.request.urlopen(base + path, timeout=20) as response:
        return json.loads(response.read())


def _age_seconds(stamp: str | None, now: datetime) -> float | None:
    if not stamp:
        return None
    return (now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds()


def check_server(name: str, base: str, now: datetime) -> dict[str, Any]:
    result: dict[str, Any] = {"server": name, "url": base, "level": "OK", "problems": [], "warnings": []}

    def fail(message: str) -> None:
        result["problems"].append(message)
        result["level"] = "FAIL"

    def warn(message: str) -> None:
        result["warnings"].append(message)
        if result["level"] == "OK":
            result["level"] = "WARN"

    try:
        health = _get(base, "/api/health")
    except Exception as exc:  # server down, port closed, timeout
        fail(f"server unreachable: {type(exc).__name__}")
        return result
    if health.get("real_money_execution") is not False:
        fail("real_money_execution is not false")
    runtime = _get(base, "/api/runtime-health")
    campaign = _get(base, "/api/campaign")
    safety = _get(base, "/api/safety")
    result.update({
        "label": campaign.get("label") or campaign.get("experiment_id"),
        "engine": campaign.get("engine"),
        "status": campaign.get("status"),
        "day": campaign.get("elapsed_days"),
        "completed_trades": campaign.get("completed_trades"),
        "target_trades": campaign.get("target_trades"),
        "manifest_version": campaign.get("manifest_version"),
        "overall": runtime.get("overall"),
        "kill_switch": (safety.get("kill_switch") or {}).get("level"),
        "reconciliation_ok": (safety.get("reconciliation") or {}).get("ok"),
    })
    if campaign.get("status") != "running":
        fail(f"experiment status is {campaign.get('status')}")
    if runtime.get("overall") not in ("OK",):
        (fail if runtime.get("overall") in ("CRITICAL", "FAIL", "DOWN") else warn)(f"runtime health {runtime.get('overall')}")
    bad = {k: v.get("status") for k, v in (runtime.get("components") or {}).items() if v.get("status") != "OK"}
    if bad:
        warn(f"components not OK: {bad}")
    if result["reconciliation_ok"] is not True:
        fail("ledger reconciliation is not OK")
    if result["kill_switch"] not in ("NORMAL", None):
        warn(f"kill switch {result['kill_switch']}")
    if campaign.get("drift"):
        fail("configuration drift from the frozen manifest")
    for incident in campaign.get("risk_incidents") or []:
        (fail if incident.get("kind") == "RISK_HALT" else warn)(f"{incident.get('kind')}: {incident.get('summary')}")
    open_incidents = runtime.get("open_incidents") or []
    if open_incidents:
        warn(f"{len(open_incidents)} open incident(s): " + "; ".join(
            f"{i.get('kind')} {i.get('severity')}" for i in open_incidents[:5]))
    budget = campaign.get("ai_budget") or {}
    result["ai_budget_left_usd"] = budget.get("remaining_experiment_usd")
    if budget.get("exhausted"):
        warn(f"AI budget exhausted ({budget.get('limit_action')})")
    sleeves = campaign.get("sleeves")
    if sleeves:
        tick = sleeves.get("last_tick") or {}
        age = _age_seconds(tick.get("boundary"), now)
        result["last_tick_age_hours"] = None if age is None else round(age / 3600, 2)
        result["combined_drawdown"] = tick.get("combined_drawdown")
        result["sleeves"] = {r["sleeve"]: {"equity": r["equity_usdt"], "pnl": r["pnl_usdt"], "long": r["long"], "short": r["short"]}
                             for r in sleeves.get("sleeves") or []}
        if age is None or age > TICK_GRACE_SECONDS:
            fail(f"sleeves tick is stale (last boundary {tick.get('boundary')})")
        if tick.get("blocked"):
            warn(f"blocked entries: {tick['blocked']}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--server", action="append", default=[], metavar="NAME=URL", help="override the servers to check")
    args = parser.parse_args()
    servers = dict(item.split("=", 1) for item in args.server) if args.server else DEFAULT_SERVERS
    for url in servers.values():
        if not url.startswith(LOOPBACK):
            parser.error("only loopback servers can be checked")
    now = datetime.now(timezone.utc)
    reports = []
    for name, url in servers.items():
        try:
            reports.append(check_server(name, url.rstrip("/"), now))
        except Exception as exc:
            reports.append({"server": name, "url": url, "level": "FAIL", "problems": [f"check failed: {type(exc).__name__}: {exc}"[:200]],
                            "warnings": []})
    level = "FAIL" if any(r["level"] == "FAIL" for r in reports) else "WARN" if any(r["level"] == "WARN" for r in reports) else "OK"
    if args.json:
        print(json.dumps({"checked_at": now.isoformat(timespec="seconds"), "level": level, "servers": reports}, indent=1, default=str))
    else:
        print(f"PAPER campaign status {now:%Y-%m-%d %H:%M} UTC: {level}")
        for r in reports:
            line = f"- {r['server']} [{r['level']}]"
            if "status" in r:
                line += (f" {r.get('status')} · day {r.get('day')} · trades {r.get('completed_trades')}/{r.get('target_trades')}"
                         f" · reconcile {'OK' if r.get('reconciliation_ok') else 'NOT OK'} · kill {r.get('kill_switch')}")
            if r.get("last_tick_age_hours") is not None:
                line += f" · tick {r['last_tick_age_hours']}h ago · drawdown {r.get('combined_drawdown')}"
            print(line)
            for name, s in (r.get("sleeves") or {}).items():
                print(f"    {name}: equity {s['equity']:.2f} pnl {s['pnl']:+.2f} long {','.join(x[:-4] for x in s['long']) or '-'}"
                      f" short {','.join(x[:-4] for x in s['short']) or '-'}")
            for p in r["problems"]:
                print(f"    FAIL: {p}")
            for w in r["warnings"]:
                print(f"    WARN: {w}")
    return {"OK": 0, "WARN": 1, "FAIL": 2}[level]


if __name__ == "__main__":
    sys.exit(main())
