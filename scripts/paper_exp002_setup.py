"""Configure and start EXP-002 (trend sleeves) on a running, freshly created paper-server.

    python3 scripts/paper_exp002_setup.py http://127.0.0.1:8768 [--no-start]

Loopback only, PAPER only. Everything goes through the server's own API, so every value is
validated server-side. ``experiment_config`` and ``portfolio_settings`` are pure functions (and
unit-tested offline) so the exact EXP-002 setup can be audited without running a server.
Starting the experiment freezes the manifest; run this once per fresh database.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

LOOPBACK = ("http://127.0.0.1:", "http://localhost:", "http://[::1]:")


def experiment_config(current: dict[str, Any], *, recorded_at: str) -> dict[str, Any]:
    """EXP-002 experiment config on top of the server's current (default) config."""

    budget = dict(current.get("ai_budget") or {})
    # The sleeves engine makes no AI calls; a tiny cap that blocks paid AI makes that explicit.
    budget.update({"enabled": True, "experiment_usd": 1.0, "daily_usd": 0.5, "limit_action": "BLOCK_PAID_AI"})
    return {
        **current,
        "label": "EXP-002",
        "strategy_engine": "sleeves_v1",
        "sleeves": {},  # defaults: BTC ETH NEAR SEI SUI AVAX ENA, see docs/trend-sleeves-engine.md
        "market_data_mode": "gate_usdt",
        "starting_balance_usdt": 500.0,
        "evaluation_arms": ["quant"], "primary_arm": "quant",
        "jev_enabled": False, "gpt_escalation_enabled": False,
        "fee_schedule_version": "gate-usdt-default.v1", "taker_fee_rate": 0.0005, "slippage_bps": 2.0,
        "max_drawdown_stop": 0.25, "max_daily_loss": 0.08,
        "ai_budget": budget,
        "cost_fx": {"mode": "manual", "usdt_per_usd": 1.0005, "version": "ai-cost-fx.v1",
                    "source": "Gate public USDC_USDT last 1.0005 (USDC as USD proxy), same as EXP-001",
                    "recorded_at": recorded_at},
    }


def portfolio_settings(current: dict[str, Any]) -> dict[str, Any]:
    """EXP-002 portfolio settings: perp-only, no AI Spot, no AI position review."""

    settings = json.loads(json.dumps(current))
    settings["spot_starting_balance_usdt"] = 1.0  # the minimum; the Spot wallet is unused
    settings["ai_spot"]["enabled"] = False
    settings["review"]["enabled"] = False
    # The 4-year replay closed ~400 sleeve trades a year, about 100 per 90 days.
    settings.setdefault("promotion", {})["min_completed_trades"] = 80
    return settings


def _call(base: str, method: str, path: str, body: Any = None) -> Any:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base_url", help="paper-server URL, e.g. http://127.0.0.1:8768")
    parser.add_argument("--no-start", action="store_true", help="configure only; do not start (the manifest stays unfrozen)")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    if not base.startswith(LOOPBACK):
        parser.error("the paper-server is loopback only")
    current = _call(base, "GET", "/api/experiment")["experiment"]
    if current["status"] != "stopped":
        parser.error(f"experiment is {current['status']}; use a fresh database")
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    saved = _call(base, "POST", "/api/experiment", experiment_config(current["config"], recorded_at=now))["experiment"]["config"]
    print("engine", saved["strategy_engine"], "label", saved["label"], "universe", " ".join(saved["sleeves"]["universe"]))
    settings = _call(base, "GET", "/api/portfolio/settings")
    _call(base, "POST", "/api/portfolio/settings", portfolio_settings(settings.get("settings", settings)))
    print("portfolio settings saved")
    universe = saved["sleeves"]["universe"]
    symbols: dict[str, Any] = {}
    for _ in range(90):  # the feed resubscribes after the config change; start once every symbol is fresh
        symbols = (_call(base, "GET", "/api/market/status").get("stream") or {}).get("symbols") or {}
        if all((symbols.get(s) or {}).get("fresh") for s in universe):
            break
        time.sleep(1)
    print("feed fresh:", {s: bool((symbols.get(s) or {}).get("fresh")) for s in universe})
    if not args.no_start:
        started = _call(base, "POST", "/api/runtime/start", {})
        print("started:", started.get("experiment", {}).get("status", started))


if __name__ == "__main__":
    main()
