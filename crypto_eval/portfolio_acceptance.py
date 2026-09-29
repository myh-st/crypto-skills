"""REAL local acceptance for the Portfolio OS AI paths (never used by CI).

Uses real Gate public market data and the configured TypeSafe Jev + Azure Foundry
GPT-6 Luna providers (credentials from the OS credential store / .env, never printed).
Everything runs in an isolated PAPER database. Gate live writes remain blocked by design;
no step can place, amend, or cancel a real order.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .envfile import load_environment_file
from .market_catalog import MarketCatalog
from .paper_contracts import PaperTradingError
from .paper_runtime import PaperRuntime, PaperStore
from .real_integration import REPOSITORY_ROOT, _app_dir, configure_real_providers, fallback_prices, import_env_secrets
from .secret_store import CredentialResolver, default_secret_store


def _usage(runtime: PaperRuntime, call_types: set[str]) -> list[dict[str, Any]]:
    experiment_id = runtime.store.experiment()["experiment_id"]
    return [event for event in runtime.store.cost_ledger.usage_events(experiment_id) if event["call_type"] in call_types]


def run_portfolio_real_check(*, database: Path | None = None, out: Path | None = None) -> int:
    load_environment_file(REPOSITORY_ROOT / ".env")
    started = datetime.now(timezone.utc)
    check_id = f"prc-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    database = database or _app_dir() / "integration" / f"{check_id}.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    print(f"REAL Portfolio OS acceptance {check_id}")
    print(f"Isolated PAPER database: {database}")
    print("Real Gate public data + real Jev/Luna. Gate live writes are blocked by design.\n")
    resolver = CredentialResolver(default_secret_store())
    import_env_secrets(resolver)
    store = PaperStore(database)
    runtime = PaperRuntime(store, resolver=resolver, catalog=MarketCatalog(source="gate"))
    steps: list[dict[str, Any]] = []

    def step(name: str, fn) -> Any:
        try:
            result = fn()
            steps.append({"step": name, "status": "PASS", "detail": result})
            print(f"PASS  {name}")
            return result
        except Exception as exc:  # acceptance reports every failure explicitly
            steps.append({"step": name, "status": "FAIL", "detail": str(exc)[:300]})
            print(f"FAIL  {name}: {str(exc)[:200]}")
            return None

    step("configure real providers + explicit fallback price book",
         lambda: {"prices": configure_real_providers(runtime, symbols=["BTCUSDT", "ETHUSDT"], prices=fallback_prices())["prices_added"]})
    portfolio = runtime.portfolio

    def acceptance_budget() -> dict[str, Any]:
        # Same explicit, audited acceptance budget as real-integration-check: a reasoning=max
        # Luna call can reserve up to ~$1.92 worst case at the fallback price, above the $0.50
        # default per-call cap. Isolated database only.
        budget = dict(runtime.store.experiment()["config"]["ai_budget"])
        budget.update({"daily_usd": 5.0, "experiment_usd": 5.0, "max_gpt_call_usd": 3.0, "max_cycle_usd": 4.0})
        runtime.store.update_cost_controls(ai_budget=budget)
        return {k: budget[k] for k in ("daily_usd", "experiment_usd", "max_gpt_call_usd")}

    step("explicit acceptance AI budget (isolated database)", acceptance_budget)

    def open_positions() -> dict[str, Any]:
        quote = portfolio.quote("gate:perpetual:BTC_USDT")
        ask = quote["best_ask"]
        perp = portfolio.create_order({
            "client_request_id": f"{check_id}-perp", "instrument_id": "gate:perpetual:BTC_USDT", "action": "long",
            "risk_pct": 0.005, "stop_price": round(ask * 0.985, 1), "targets": [round(ask * 1.02, 1), round(ask * 1.04, 1)], "leverage": 3,
        })
        spot = portfolio.create_order({
            "client_request_id": f"{check_id}-spot", "instrument_id": "gate:spot:ETH_USDT", "action": "buy", "quote_amount": 20,
        })
        if perp["status"] != "filled" or spot["status"] != "filled":
            raise PaperTradingError(f"PAPER entries not filled: perp={perp.get('code')} spot={spot.get('code')}")
        return {"perp": perp["position_ref"], "spot": spot["position_ref"], "quote_source": quote["source"]}

    refs = step("PAPER perp long + Spot buy on live Gate quotes", open_positions) or {}

    def real_replan() -> dict[str, Any]:
        before = len(_usage(runtime, {"jev_replan", "gpt_replan"}))
        proposal = portfolio.request_replan(refs["perp"], intent="reassess", use_ai=True)
        events = _usage(runtime, {"jev_replan", "gpt_replan"})[before:]
        real = [e for e in events if e.get("real_external_call")]
        if proposal["ai"]["jev"] != "completed" or proposal["ai"]["luna"] != "completed" or proposal["source"] != "luna":
            raise PaperTradingError(f"real AI path incomplete: {proposal['ai']}")
        if {e["call_type"] for e in real} != {"jev_replan", "gpt_replan"}:
            raise PaperTradingError("expected one real Jev and one real Luna re-plan call in the cost ledger")
        return {
            "status": proposal["status"], "headline": proposal.get("headline"), "reason_codes": proposal["reason_codes"],
            "thesis": proposal["thesis_status"], "risk_before": proposal["risk_before_usdt"], "risk_after": proposal["risk_after_usdt"],
            "calls": [{k: e.get(k) for k in ("call_type", "provider_kind", "returned_model", "reasoning_effort", "latency_ms",
                                              "input_tokens", "output_tokens", "estimated_cost_usd", "cost_status")} for e in real],
            "proposal_id": proposal["proposal_id"],
        }

    replan = step("perp re-plan via real Jev -> real GPT-6 Luna (structured, validated)", real_replan) if refs else None

    def decide() -> dict[str, Any]:
        proposal = portfolio._proposal(replan["proposal_id"])
        if proposal["status"] != "proposed":
            return {"decision": "nothing to apply", "status": proposal["status"]}
        if proposal["risk_increased"] or proposal["proposal"]["action"] == "close":
            return {"decision": "rejected", "status": portfolio.reject_replan(replan["proposal_id"], reason="acceptance")["status"]}
        return {"decision": "applied", "status": portfolio.apply_replan(replan["proposal_id"], confirm=True)["status"]}

    if replan:
        step("apply or reject the real proposal", decide)

    def spot_replan() -> dict[str, Any]:
        proposal = portfolio.request_replan(refs["spot"], intent="tighten_risk", use_ai=True)
        return {"status": proposal["status"], "source": proposal.get("source"), "ai": proposal.get("ai"), "headline": proposal.get("headline")}

    if refs:
        step("Spot re-plan via real AI route", spot_replan)

    def budget_block() -> dict[str, Any]:
        config = runtime.store.experiment()["config"]
        spent = runtime.store.cost_ledger.budget_status(config["experiment_id"], config["ai_budget"])["spent_today_usd"]
        runtime.store.update_cost_controls(ai_budget={**config["ai_budget"], "daily_usd": max(1e-6, spent)})
        before = [e for e in _usage(runtime, {"jev_replan", "gpt_replan"}) if e.get("real_external_call")]
        proposal = portfolio.request_replan(refs["perp"], intent="tighten_risk", use_ai=True)
        after = [e for e in _usage(runtime, {"jev_replan", "gpt_replan"}) if e.get("real_external_call")]
        if len(after) != len(before) or proposal["ai"]["budget_block"] is None:
            raise PaperTradingError("budget guard did not block the paid re-plan before transport")
        return {"budget_block": proposal["ai"]["budget_block"], "source": proposal.get("source"), "new_real_calls": 0}

    if refs:
        step("hard budget guard blocks paid re-plan calls before transport", budget_block)

    def export_scan() -> dict[str, Any]:
        body, name = runtime.export_bundle()
        return {"bundle": name, "bytes": len(body)}

    step("export bundle passes the configured-credential secret scan", export_scan)
    ok = all(item["status"] == "PASS" for item in steps)
    summary = {
        "check_id": check_id,
        "started_at": started.isoformat(),
        "database": str(database),
        "status": "PASS" if ok else "FAIL",
        "gate_live_write_execution": "BLOCKED_BY_DESIGN",
        "steps": steps,
    }
    path = out or database.with_suffix(".json")
    path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nOverall: {summary['status']} · summary: {path}")
    store.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run_portfolio_real_check())
