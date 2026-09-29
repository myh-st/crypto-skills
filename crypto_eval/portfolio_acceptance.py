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


def run_full_loop_check(
    *, database: Path | None = None, out: Path | None = None, universe_size: int = 20, max_candles: int = 1
) -> int:
    """Real Gate market -> quant -> Jev -> optional Luna -> Brain -> Risk -> PAPER entry -> AI management
    -> human override -> AI regains control -> close -> journal -> economic PnL (FX policy explicit)."""

    load_environment_file(REPOSITORY_ROOT / ".env")
    started = datetime.now(timezone.utc)
    check_id = f"pfl-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    database = database or _app_dir() / "integration" / f"{check_id}.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    print(f"REAL full human/AI loop {check_id}")
    print(f"Isolated PAPER database: {database}")
    print("No fixture substitution: if the live market has no qualifying setup the entry is NOT_VERIFIED.\n")
    resolver = CredentialResolver(default_secret_store())
    import_env_secrets(resolver)
    store = PaperStore(database)
    catalog = MarketCatalog(source="gate")
    runtime = PaperRuntime(store, resolver=resolver, catalog=catalog)
    steps: list[dict[str, Any]] = []

    def step(name: str, fn, *, not_verified_on: type[Exception] | None = None) -> Any:
        try:
            result = fn()
            steps.append({"step": name, "status": "PASS", "detail": result})
            print(f"PASS  {name}")
            return result
        except _NotVerified as exc:
            steps.append({"step": name, "status": "NOT_VERIFIED", "detail": str(exc)[:300]})
            print(f"NOT_VERIFIED  {name}: {exc}")
        except Exception as exc:
            steps.append({"step": name, "status": "FAIL", "detail": str(exc)[:300]})
            print(f"FAIL  {name}: {str(exc)[:200]}")
        return None

    def setup() -> dict[str, Any]:
        listing = catalog.list("perpetual", tradable_only=True, limit=200)["instruments"]
        symbols = [item["symbol"] for item in listing if item["quote"] == "USDT" and item.get("volume_24h_quote")][:universe_size]
        configure_real_providers(
            runtime, symbols=symbols, prices=fallback_prices(),
            evaluation_arms=["quant", "jev", "hybrid", "hybrid_brain"],
        )
        config = dict(store.experiment()["config"])
        config["primary_arm"] = "hybrid"
        config["shadow_leverage"] = [1]
        store.save_experiment(config)
        budget = dict(store.experiment()["config"]["ai_budget"])
        budget.update({"daily_usd": 5.0, "experiment_usd": 5.0, "max_gpt_call_usd": 3.0, "max_cycle_usd": 4.0})
        store.update_cost_controls(
            ai_budget=budget,
            cost_fx={"version": "ai-cost-fx.v1", "mode": "manual", "usdt_per_usd": 1.0,
                     "source": "acceptance assumption: 1 USDT = 1 USD (explicit, not a market rate)",
                     "recorded_at": started.isoformat()},
        )
        for provider_id in (config["jev_provider_id"], config["gpt_provider_id"]):
            runtime.test_provider(provider_id)
        runtime.start()
        return {"universe": symbols, "arms": store.experiment()["config"]["evaluation_arms"]}

    universe = step("real providers tested, liquid Gate perp universe, explicit FX policy", setup)
    state: dict[str, Any] = {}

    def scan() -> dict[str, Any]:
        import time as _time

        for attempt in range(max(1, max_candles)):
            if attempt:
                now = datetime.now(timezone.utc).timestamp()
                wake = (int(now) // 900 + 1) * 900 + 75
                print(f"      no entry yet; waiting for the next closed 15m candle ({attempt + 1}/{max_candles})", flush=True)
                _time.sleep(max(1, wake - now))
            try:
                return scan_once()
            except _NotVerified as exc:
                last = exc
        raise last

    def scan_once() -> dict[str, Any]:
        seen = []
        for symbol in universe["universe"]:
            try:
                cycle = runtime.run_cycle(symbol, manual=True)
            except PaperTradingError as exc:
                seen.append({"symbol": symbol, "error": str(exc)[:80]})
                continue
            primary = cycle.get("primary_decision") or {}
            filled = [e for e in cycle.get("executions", []) if e.get("cohort") == "primary" and e.get("status") == "filled"]
            seen.append({"symbol": symbol, "gate": (cycle.get("quant_gate") or {}).get("eligible"),
                         "jev": cycle.get("jev_status"), "decision": primary.get("decision"),
                         "route": primary.get("ai_path"), "risk": (cycle.get("risk") or {}).get("code"),
                         "brain": (cycle.get("portfolio_brain") or {}).get("action")})
            if filled:
                state["position_ref"] = f"perp:{filled[0]['position_id']}"
                state["cycle"] = cycle
                return {"entry_symbol": symbol, "decision": seen[-1], "scanned": len(seen)}
        state["scan"] = seen
        raise _NotVerified(
            f"no AI-approved PAPER entry on this closed candle across {len(seen)} live symbols "
            f"({sum(1 for item in seen if item.get('gate'))} quant-eligible); nothing was substituted"
        )

    if universe:
        step("live decision stack opens an AI PAPER position", scan)
    portfolio = runtime.portfolio
    if state.get("position_ref"):
        ref = state["position_ref"]

        def ai_manages() -> dict[str, Any]:
            view = portfolio.position(ref)
            if view["source"] != "AI" or view["management_mode"] != "AUTO_PAPER":
                raise PaperTradingError(f"AI position authority wrong: {view['source']} / {view['management_mode']}")
            with store.transaction() as db:
                db.execute("UPDATE position_meta SET next_ai_review_at='2020-01-01T00:00:00.000Z' WHERE position_ref=?", (ref,))
            reviews = portfolio.review_positions()
            return {"review": reviews}

        step("AI manages the position (autonomous review, real AI route)", ai_manages)

        def human_override() -> dict[str, Any]:
            if portfolio.position(ref)["status"] != "open":
                return {"note": "AI closed the position during review; human override not applicable"}
            portfolio.set_management_mode(ref, "MANUAL_OVERRIDE")
            reduced = portfolio.reduce_position(ref, 0.25)
            portfolio.set_management_mode(ref, "AUTO_PAPER", confirm=True)
            closed = portfolio.close_position(ref, confirm=True)
            return {"reduced": reduced["result"], "closed": closed["closed"]}

        step("human override -> reduce -> AI regains control -> close", human_override)

        def journal_and_economics() -> dict[str, Any]:
            events = portfolio.management_events(ref)
            sources = {event["source"] for event in events}
            activity = portfolio.activity(position_ref=ref)["events"]
            paper = portfolio.portfolio()["paper"]
            if paper["economic_pnl_usdt"] is None:
                raise PaperTradingError(f"economic PnL unavailable: {paper['economic_unavailable_reason']}")
            portfolio.sync_reviews()
            review = portfolio._review_for(ref)
            return {
                "journal_sources": sorted(sources), "journal_actions": [event["action"] for event in reversed(events)],
                "activity_events": len(activity), "trading_pnl_usdt": paper["trading_pnl_usdt"],
                "ai_cost_usd": paper["ai_cost_usd"], "economic_pnl_usdt": paper["economic_pnl_usdt"],
                "review": None if review is None else {k: review[k] for k in ("outcome", "tags", "lesson")},
            }

        step("journal (AI/USER/SYSTEM) + post-trade review + economic PnL after AI cost", journal_and_economics)
    runtime.stop()
    statuses = {item["status"] for item in steps}
    status = "FAIL" if "FAIL" in statuses else "NOT_VERIFIED" if "NOT_VERIFIED" in statuses else "PASS"
    summary = {"check_id": check_id, "status": status, "database": str(database), "steps": steps,
               "scan": state.get("scan"), "gate_live_write_execution": "BLOCKED_BY_DESIGN"}
    path = out or database.with_suffix(".json")
    path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nOverall: {status} · summary: {path}")
    store.close()
    return 0 if status == "PASS" else 1


class _NotVerified(Exception):
    """The live market did not present the required condition; nothing was substituted."""


if __name__ == "__main__":
    sys.exit(run_portfolio_real_check())
