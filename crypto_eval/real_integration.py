"""Local REAL integration acceptance: Gate public REST/WS + TypeSafe Jev + Azure Foundry GPT.

Never substitutes fixtures. A capability that was not actually exercised is reported
NOT_VERIFIED, and the command exits non-zero when a required real check fails.
Secrets are resolved only inside adapters and are never printed or persisted outside
the OS credential store.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .ai_cost import default_budget_config
from .envfile import load_environment_file
from .gate_account import DisabledLiveExecutionAdapter, LiveExecutionBlocked, ReadOnlyGateClient
from .gate_market import GateUsdtFuturesMarketDataProvider
from .gate_stream import GateLiveMarketStream
from .paper_contracts import PaperTradingError, TradingIntent, iso_utc
from .paper_runtime import PaperRuntime, PaperStore, build_fast_intent, compute_features
from .secret_store import CredentialResolver, SecretStoreError, default_secret_store, validate_secret_id


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
JEV_PROVIDER_ID = "typesafe-jev"
GPT_PROVIDER_ID = "azure-gpt6-luna"
FALLBACK_PRICE_VERSION = "integration-check-explicit-fallback.v1"
FALLBACK_PRICE_SOURCE = (
    "EXPLICIT integration-check fallback, NOT contract pricing; replace via Settings > Cost & Budgets"
)
REQUIRED = "required"
OPTIONAL = "optional"


def _app_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "crypto-skills"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "crypto-skills"


def provider_presets(environ: dict[str, str] | os._Environ) -> list[dict[str, Any]]:
    endpoint = (environ.get("AZURE_OPENAI_API_ENDPOINT") or "").strip()
    return [
        {
            "provider_id": JEV_PROVIDER_ID,
            "kind": "typesafe_jev",
            "display_name": "TypeSafe Jev",
            "base_url": "https://api.typesafe.ai",
            "model": "jev-latest",
            "auth_scheme": "bearer",
            "timeout_seconds": 60,
            "reasoning_effort": "high",
            "enabled": True,
            "credential_env": "TYPESAFE_API_KEY",
            "credential_secret": f"provider.{JEV_PROVIDER_ID}",
            "pricing": None,
        },
        {
            "provider_id": GPT_PROVIDER_ID,
            "kind": "foundry_responses",
            "display_name": "Azure AI Foundry · GPT-6 Luna",
            "base_url": endpoint,
            "model": "gpt-6-luna",
            "auth_scheme": "bearer",
            "timeout_seconds": 120,
            "reasoning_effort": "max",
            "enabled": True,
            "credential_env": "AZURE_OPENAI_API_KEY",
            "credential_secret": f"provider.{GPT_PROVIDER_ID}",
            "pricing": None,
        },
    ]


def import_env_secrets(resolver: CredentialResolver, *, overwrite: bool = False) -> dict[str, str]:
    """Copy bootstrap env credentials into the OS store (values never printed)."""

    result = {}
    for env_name, secret_id in (
        ("TYPESAFE_API_KEY", f"provider.{JEV_PROVIDER_ID}"),
        ("AZURE_OPENAI_API_KEY", f"provider.{GPT_PROVIDER_ID}"),
    ):
        value = (os.environ.get(env_name) or "").strip()
        if not value:
            result[env_name] = "absent"
            continue
        try:
            existing = resolver.store.get(secret_id)
        except SecretStoreError:
            existing = None
        if existing and not overwrite:
            result[env_name] = "already_stored"
            continue
        resolver.store.set(secret_id, value)
        result[env_name] = f"stored:{resolver.store.backend}"
    return result


def configure_real_providers(
    runtime: PaperRuntime,
    *,
    symbols: list[str] | None = None,
    prices: list[dict[str, Any]] | None = None,
    force_escalation: bool = False,
    evaluation_arms: list[str] | None = None,
) -> dict[str, Any]:
    for preset in provider_presets(os.environ):
        if preset["kind"] == "foundry_responses" and not preset["base_url"]:
            raise PaperTradingError("AZURE_OPENAI_API_ENDPOINT is not configured")
        runtime.store.save_provider(preset)
    added = []
    for entry in prices or []:
        try:
            added.append(runtime.store.cost_ledger.add_price(entry))
        except PaperTradingError as exc:
            if "immutable" not in str(exc):
                raise
    experiment = runtime.store.experiment()
    config = dict(experiment["config"])
    if not runtime.store.list_cycles(experiment["experiment_id"], limit=1):
        config.update(
            {
                "market_data_mode": "gate_usdt",
                "jev_provider_id": JEV_PROVIDER_ID,
                "gpt_provider_id": GPT_PROVIDER_ID,
                "jev_enabled": True,
                "gpt_escalation_enabled": True,
                "force_escalation": force_escalation,
                "fee_schedule_version": "gate-usdt-default.v1",
            }
        )
        if symbols:
            config["symbols"] = symbols
        if evaluation_arms:
            config["evaluation_arms"] = evaluation_arms
        runtime.store.save_experiment(config)
    return {"prices_added": [item["version"] for item in added], "config": runtime.store.experiment()["config"]}


def fallback_prices() -> list[dict[str, Any]]:
    effective = "2026-01-01T00:00:00Z"
    return [
        {
            "provider_kind": "foundry_responses",
            "model": "gpt-6-luna",
            "version": FALLBACK_PRICE_VERSION,
            "effective_from": effective,
            "currency": "USD",
            "input_per_million": 15.0,
            "cached_input_per_million": 15.0,
            "output_per_million": 60.0,
            "reasoning_billing_rule": "included_in_output",
            "source": FALLBACK_PRICE_SOURCE,
        },
        {
            "provider_kind": "typesafe_jev",
            "model": "jev-latest",
            "version": FALLBACK_PRICE_VERSION,
            "effective_from": effective,
            "currency": "USD",
            "input_per_million": 5.0,
            "output_per_million": 20.0,
            "reasoning_billing_rule": "included_in_output",
            "source": FALLBACK_PRICE_SOURCE,
        },
    ]


class _Recorder:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def run(self, number: int, name: str, tier: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            evidence = fn() or {}
            status = evidence.pop("_status", "PASS")
        except _Skip as exc:
            status, evidence = "NOT_VERIFIED", {"reason": str(exc)}
        except Exception as exc:  # report sanitized failure, keep going
            status, evidence = "FAIL", {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
        row = {
            "number": number,
            "check": name,
            "tier": tier,
            "status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "evidence": evidence,
        }
        self.checks.append(row)
        marker = {"PASS": "PASS", "FAIL": "FAIL", "NOT_VERIFIED": "NOT VERIFIED", "BLOCKED_BY_DESIGN": "BLOCKED BY DESIGN"}[status]
        print(f"[{number:>2}] {marker:<18} {name}", flush=True)
        return row


class _Skip(Exception):
    pass


def _http_get_local(port: int, path: str, timeout: float = 30) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    connection.request("GET", path, headers={"Accept": "application/json"})
    response = connection.getresponse()
    return response.status, response.read()


def _read_sse(port: int, path: str, *, want: set[str], timeout: float = 30) -> dict[str, int]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    connection.request("GET", path, headers={"Accept": "text/event-stream"})
    response = connection.getresponse()
    if response.status != 200:
        raise PaperTradingError(f"SSE endpoint returned HTTP {response.status}")
    seen: dict[str, int] = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not want <= set(seen):
        line = response.fp.readline()
        if not line:
            break
        if line.startswith(b"event: "):
            name = line[7:].strip().decode()
            seen[name] = seen.get(name, 0) + 1
    connection.close()
    return seen


def run_real_integration_check(
    *,
    symbol: str = "BTCUSDT",
    database: Path | None = None,
    app_database: Path | None = None,
    use_fallback_prices: bool = True,
    ws_timeout: float = 45.0,
    out: Path | None = None,
) -> int:
    load_environment_file(REPOSITORY_ROOT / ".env")
    started_at = datetime.now(timezone.utc)
    check_id = f"ric-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    work_dir = _app_dir() / "integration"
    database = database or work_dir / f"{check_id}.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    print(f"REAL integration check {check_id}")
    print(f"Isolated PAPER database: {database}")
    print("Fixture transports are NOT used. Gate live writes are blocked by design.\n")

    recorder = _Recorder()
    store = PaperStore(database)
    resolver = CredentialResolver(default_secret_store())
    stream: GateLiveMarketStream | None = None
    state: dict[str, Any] = {}
    secrets_for_scan: list[str] = []

    def check_secret_store() -> dict[str, Any]:
        backend = resolver.store
        probe_id = validate_secret_id(f"integration-probe.{uuid.uuid4().hex[:8]}")
        probe_value = f"probe-{uuid.uuid4().hex}"
        backend.set(probe_id, probe_value)
        round_trip = backend.get(probe_id) == probe_value
        backend.delete(probe_id)
        imported = import_env_secrets(resolver)
        if not backend.persistent:
            return {"_status": "FAIL", "backend": backend.backend, "reason": "no persistent OS credential store"}
        if not round_trip:
            raise PaperTradingError("credential store round-trip failed")
        return {"backend": backend.backend, "persistent": True, "round_trip": True, "bootstrap_import": imported}

    recorder.run(1, "secret store available", REQUIRED, check_secret_store)
    for secret_id in (f"provider.{JEV_PROVIDER_ID}", f"provider.{GPT_PROVIDER_ID}"):
        value, _ = resolver.resolve(secret_id=secret_id, env_name=None)
        if value:
            secrets_for_scan.append(value)

    def check_gate_rest() -> dict[str, Any]:
        rest = GateUsdtFuturesMarketDataProvider()
        support = rest.validate_symbols(["BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "SEIUSDT"])
        candles = rest.fetch_candles(symbol, "15m", bars=10, as_of=datetime.now(timezone.utc))
        ticker = rest.fetch_ticker(symbol)
        book = rest.fetch_book_top(symbol)
        state["rest"] = rest
        if not candles or not support["supported"]:
            raise PaperTradingError("Gate REST returned no usable data")
        return {
            "endpoint": rest.base_url,
            "supported_symbols": support["supported"],
            "excluded_symbols": support["excluded"],
            "last_closed_15m": candles[-1]["close_time"],
            "last": ticker["last_price"],
            "mark": ticker["mark_price"],
            "index": ticker["index_price"],
            "funding_rate": ticker["funding_rate"],
            "best_bid": book["best_bid"],
            "best_ask": book["best_ask"],
            "spread_bps": round(book["spread_bps"], 4),
            "requests": rest.request_count,
        }

    recorder.run(2, "Gate public REST (real)", REQUIRED, check_gate_rest)

    def check_gate_ws() -> dict[str, Any]:
        nonlocal stream
        stream = GateLiveMarketStream(
            [symbol],
            rest=state.get("rest") or GateUsdtFuturesMarketDataProvider(),
            history_bars=200,
            health_sink=store.record_stream_health,
        )
        stream.start()
        deadline = time.monotonic() + ws_timeout
        while time.monotonic() < deadline:
            stream.refresh_state()
            if stream.state == "LIVE" and stream.freshness(symbol)["fresh"]:
                break
            time.sleep(0.5)
        status = stream.status()
        if status["state"] != "LIVE":
            raise PaperTradingError(f"Gate WS did not reach LIVE (state={status['state']})")
        return {"ws_url": status["ws_url"], "state": status["state"], "connected_since": status["connected_since"]}

    recorder.run(3, "Gate public WebSocket (real)", REQUIRED, check_gate_ws)

    def check_events() -> dict[str, Any]:
        if stream is None:
            raise _Skip("WebSocket did not start")
        time.sleep(5)
        counters = stream.status()["counters"]
        if not (counters["candle_updates"] and counters["ticker_updates"] and counters["book_updates"]):
            raise PaperTradingError(f"missing real stream events: {counters}")
        closed = [c for c in stream.candles(symbol, "1m") if c["closed"]]
        current = [c for c in stream.candles(symbol, "1m") if not c["closed"]]
        return {
            "counters": counters,
            "closed_1m_candles": len(closed),
            "current_1m_candle_open_time": current[-1]["open_time"] if current else None,
            "book": stream.symbol_state(symbol)["book"],
        }

    recorder.run(4, "real Gate candle/ticker/book events observed", REQUIRED, check_events)

    runtime = PaperRuntime(store, resolver=resolver, live_stream=stream)
    prices = fallback_prices() if use_fallback_prices else []
    setup = configure_real_providers(
        runtime,
        symbols=[symbol],
        prices=prices,
        force_escalation=True,
        evaluation_arms=["quant", "jev", "quant_jev", "hybrid"],
    )
    budget = default_budget_config()
    budget.update({"daily_usd": 5.0, "experiment_usd": 5.0, "max_gpt_call_usd": 3.0, "max_cycle_usd": 4.0})
    store.update_cost_controls(ai_budget=budget)

    def check_jev_test() -> dict[str, Any]:
        result = runtime.test_provider(JEV_PROVIDER_ID)
        if not result.get("real_external_call"):
            raise PaperTradingError("Jev test did not make a real call")
        return {
            "endpoint": "POST https://api.typesafe.ai/v1/systemone",
            "requested_model": result["requested_model"],
            "returned_model": result["model"],
            "typed_outputs": result["typed_outputs"],
            "usage": result["usage"],
            "latency_ms": round(result["latency_ms"], 1),
            "provider_request_id": result.get("provider_request_id"),
        }

    recorder.run(5, "TypeSafe Jev Test Connection (real, Choice/Score/Noul)", REQUIRED, check_jev_test)

    def check_gpt_test() -> dict[str, Any]:
        result = runtime.test_provider(GPT_PROVIDER_ID)
        state["gpt_test"] = result
        return {
            "endpoint_path": result["responses_endpoint_path"],
            "deployment": result["model"],
            "returned_model": result["returned_model"],
            "structured_output_validated": result["structured_output_validated"],
            "normalized_action": result["normalized_action"],
            "usage": result["usage"],
            "latency_ms": round(result["latency_ms"], 1),
            "provider_request_id": result.get("provider_request_id"),
            "provider_response_id": result.get("provider_response_id"),
        }

    recorder.run(6, "Azure Foundry GPT-6 Luna Test Connection (real)", REQUIRED, check_gpt_test)

    def check_reasoning() -> dict[str, Any]:
        result = state.get("gpt_test")
        if not result:
            raise _Skip("GPT test did not complete")
        if result.get("reasoning_effort_echoed") != "max":
            raise PaperTradingError(f"provider echoed reasoning effort {result.get('reasoning_effort_echoed')!r}")
        return {"requested": result["reasoning_effort_validated"], "echoed_by_provider": result["reasoning_effort_echoed"]}

    recorder.run(7, "reasoning effort = max accepted (echoed by provider)", REQUIRED, check_reasoning)

    def check_snapshot() -> dict[str, Any]:
        market = runtime._market(store.experiment()["config"])
        snapshot = market.fetch_snapshot(symbol, datetime.now(timezone.utc))
        features = compute_features(snapshot, minimum_signal_strength=0.55, signal_gate_enabled=True)
        state["snapshot"] = snapshot
        return {
            "data_origin": snapshot.data_origin,
            "data_cutoff": snapshot.data_cutoff,
            "last_closed_15m": snapshot.candles_15m[-1]["close_time"],
            "funding_rate": snapshot.funding_rate,
            "oi_change_1h": snapshot.open_interest_change_1h,
            "spread_bps": snapshot.spread_bps,
            "context_source": (snapshot.market_context or {}).get("source"),
            "quant_direction": features["quant_direction"],
            "gate_eligible": features["gate_eligible"],
            "snapshot_hash": snapshot.snapshot_hash,
        }

    recorder.run(8, "real Gate market snapshot + features", REQUIRED, check_snapshot)

    def run_cycle_once() -> dict[str, Any]:
        if "cycle" in state:
            return state["cycle"]
        runtime.start()
        cycle = runtime.run_cycle(symbol, manual=True)
        state["cycle"] = cycle
        return cycle

    def check_jev_decision() -> dict[str, Any]:
        cycle = run_cycle_once()
        if cycle.get("jev_status") != "completed" or not cycle.get("jev_vector"):
            raise PaperTradingError(f"Jev status is {cycle.get('jev_status')}")
        vector = cycle["jev_vector"]
        answers = vector["answers"]
        return {
            "cycle_id": cycle["cycle_id"],
            "returned_model": vector["model"],
            "regime": answers["market_regime"]["value"],
            "regime_confidence": answers["market_regime"].get("confidence"),
            "setup_quality": answers["setup_quality"]["value"],
            "escalation_needed_noul": answers["escalation_needed"]["value"],
            "latency_ms": round(vector["latency_ms"], 1),
            "usage": vector["usage"],
        }

    recorder.run(9, "real Jev decision on the real snapshot", REQUIRED, check_jev_decision)

    def gpt_events() -> list[dict[str, Any]]:
        experiment_id = store.experiment()["experiment_id"]
        return [
            e
            for e in store.cost_ledger.usage_events(experiment_id)
            if e["call_type"] == "gpt_escalation" and e["real_external_call"]
        ]

    def check_gpt_escalation() -> dict[str, Any]:
        cycle = run_cycle_once()
        hybrid = cycle["arms"]["hybrid"]
        events = gpt_events()
        if not events or hybrid.get("ai_path") != "gpt:hybrid":
            raise PaperTradingError(f"no real GPT escalation (ai_path={hybrid.get('ai_path')})")
        event = events[-1]
        if event["status"] != "ok":
            raise PaperTradingError(f"GPT escalation failed ({event['error_code']})")
        return {
            "escalation_reasons": (hybrid.get("escalation") or {}).get("reasons"),
            "deployment": event["model"],
            "returned_model": event["returned_model"],
            "reasoning_effort": event["reasoning_effort"],
            "input_tokens": event["input_tokens"],
            "output_tokens": event["output_tokens"],
            "reasoning_tokens": event["reasoning_tokens"],
            "latency_ms": round(event["latency_ms"] or 0, 1),
            "provider_request_id": event["provider_request_id"],
            "provider_response_id": event["provider_response_id"],
        }

    recorder.run(10, "real GPT-6 Luna escalation on the frozen snapshot", REQUIRED, check_gpt_escalation)

    def check_intent() -> dict[str, Any]:
        cycle = run_cycle_once()
        hybrid = cycle["arms"]["hybrid"]
        if not gpt_events():
            raise _Skip("GPT escalation did not run")
        intent = hybrid.get("intent")
        if intent is not None:
            TradingIntent.from_dict(intent)
        return {
            "decision": hybrid["decision"],
            "validated_intent": intent,
            "reason": hybrid["reason"][:300],
            "note": "no_trade is a valid structured outcome" if intent is None else "validated TradingIntent",
        }

    recorder.run(11, "structured TradingIntent validated", REQUIRED, check_intent)

    def check_risk() -> dict[str, Any]:
        cycle = run_cycle_once()
        risk = cycle["risk"]
        return {"code": risk["code"], "allowed": risk["allowed"], "reason": risk["reason"][:200]}

    recorder.run(12, "deterministic risk evaluation", REQUIRED, check_risk)

    def check_paper_only() -> dict[str, Any]:
        cycle = run_cycle_once()
        executions = cycle.get("executions", [])
        smoke = None
        if not any(item.get("status") == "filled" for item in executions):
            smoke = paper_execution_smoke(runtime, symbol)
        experiment_id = store.experiment()["experiment_id"]
        fills = store.export_records(experiment_id)["fills"]
        return {
            "live_execution_enabled": cycle["live_execution_enabled"],
            "cycle_executions": [
                {k: item.get(k) for k in ("cohort", "status", "filled_quantity", "reason")} for item in executions
            ],
            "execution_price_basis": cycle.get("execution_price_basis"),
            "paper_fills_in_sqlite": len(fills),
            "mechanics_smoke": smoke,
        }

    recorder.run(13, "PAPER execution only (no exchange order path)", REQUIRED, check_paper_only)

    def check_ledger() -> dict[str, Any]:
        experiment_id = store.experiment()["experiment_id"]
        events = [e for e in store.cost_ledger.usage_events(experiment_id) if e["real_external_call"]]
        if len(events) < 3:
            raise PaperTradingError("fewer than three real AI calls were ledgered")
        return {
            "real_calls": len(events),
            "by_call_type": {
                call_type: sum(1 for e in events if e["call_type"] == call_type)
                for call_type in sorted({e["call_type"] for e in events})
            },
            "estimated_cost_usd": round(sum(e["estimated_cost_usd"] or 0 for e in events), 6),
            "cost_status": sorted({e["cost_status"] for e in events}),
            "price_book_versions": sorted({e["price_book_version"] for e in events if e["price_book_version"]}),
            "pricing_note": FALLBACK_PRICE_SOURCE if use_fallback_prices else "configured price book",
        }

    recorder.run(14, "AI cost ledger persisted", REQUIRED, check_ledger)

    def check_budget() -> dict[str, Any]:
        experiment_id = store.experiment()["experiment_id"]
        reservations = store.cost_ledger.reservations(experiment_id)
        reconciled = [r for r in reservations if r["status"] == "reconciled"]
        if not reconciled or any(r["status"] == "reserved" for r in reservations):
            raise PaperTradingError("reservations were not reconciled")
        provider = store.provider(GPT_PROVIDER_ID)
        tight = default_budget_config()
        tight.update({"daily_usd": 0.000001, "experiment_usd": 0.000001})
        blocked = store.cost_ledger.reserve(
            experiment_id=experiment_id,
            cycle_id=None,
            provider=provider,
            call_type="gpt_escalation",
            budget=tight,
            payload_bytes=2000,
        )
        if blocked.allowed:
            raise PaperTradingError("tight budget did not block a paid call")
        return {
            "reservations": len(reservations),
            "reconciled": len(reconciled),
            "released_unused_usd": round(
                sum(max(0.0, r["reserved_usd"] - (r["charged_usd"] or 0)) for r in reconciled), 6
            ),
            "hard_block_probe": {"code": blocked["code"], "network_request_made": False},
        }

    recorder.run(15, "budget reservation / reconciliation / hard block", REQUIRED, check_budget)

    def check_chart_feed() -> dict[str, Any]:
        from .paper_server import PaperHTTPServer, PaperRequestHandler
        from .paper_runtime import PaperScheduler

        scheduler = PaperScheduler(runtime)
        server = PaperHTTPServer(("127.0.0.1", 0), PaperRequestHandler, runtime, scheduler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            status, body = _http_get_local(port, f"/api/market/candles?symbol={symbol}&interval=1m&limit=120")
            payload = json.loads(body)
            if status != 200 or not payload["candles"] or payload["synthetic"]:
                raise PaperTradingError("chart candle endpoint did not return real candles")
            seen = _read_sse(
                port,
                f"/api/market/stream?symbols={symbol}&interval=1m",
                want={"status", "book", "ticker", "candle"},
                timeout=40,
            )
            if not {"book", "candle"} <= set(seen):
                raise PaperTradingError(f"SSE did not deliver live candle/book events: {seen}")
            status, body = _http_get_local(port, "/api/dashboard")
            dashboard = json.loads(body)
        finally:
            server.shutdown()
            server.server_close()
            scheduler.shutdown(timeout=1)
        return {
            "candles_endpoint": {
                "count": len(payload["candles"]),
                "source": payload["source"],
                "data_origin": payload["data_origin"],
                "last": payload["candles"][-1]["open_time"],
                "last_closed": payload["candles"][-1]["closed"],
            },
            "sse_events_seen": seen,
            "dashboard_market_stream_state": (dashboard.get("market_stream") or {}).get("state"),
            "dashboard_ai_budget_remaining_today_usd": dashboard["ai_cost"]["budget_status"]["remaining_today_usd"],
        }

    recorder.run(16, "live chart feed (candles + SSE) and dashboard", REQUIRED, check_chart_feed)

    def check_export() -> dict[str, Any]:
        body, name = runtime.export_bundle()
        with zipfile.ZipFile(io.BytesIO(body)) as bundle:
            names = set(bundle.namelist())
            blob = b"".join(bundle.read(item) for item in names)
        required_files = {
            "ai-usage.csv",
            "ai-cost-ledger.csv",
            "ai-budget-events.csv",
            "provider-price-book.json",
            "economic-pnl.csv",
            "market-stream-health.csv",
            "provider-validation.json",
            "real-integration-summary.json",
        }
        missing = sorted(required_files - names)
        leaked = [index for index, secret in enumerate(secrets_for_scan) if secret.encode() in blob]
        if missing or leaked:
            raise PaperTradingError(f"export missing={missing} secret_leaks={len(leaked)}")
        return {"file": name, "bytes": len(body), "files": len(names), "secret_scan": "clean"}

    recorder.run(17, "export smoke + secret sentinel scan", REQUIRED, check_export)

    def check_gate_account() -> dict[str, Any]:
        accounts = store.list_exchange_accounts()
        app_store = None
        if not accounts and app_database is not None and app_database.exists():
            app_store = PaperStore(app_database)
            app_store.resolver = resolver
            accounts = app_store.list_exchange_accounts()
        ready = [a for a in accounts if a["credentials"]["api_key"] and a["credentials"]["api_secret"]]
        if not ready:
            if app_store is not None:
                app_store.close()
            raise _Skip("no Gate API credentials configured (Settings > Exchange Accounts)")
        account = ready[0]
        source_store = app_store or store
        source_runtime = PaperRuntime(source_store, resolver=resolver)
        result = source_runtime.sync_gate_account(account["account_id"])
        if app_store is not None:
            app_store.close()
        if result["non_get_requests_emitted"]:
            raise PaperTradingError("a non-GET Gate request was emitted")
        return {
            "environment": result["environment"],
            "capability": result["capability"],
            "positions": len(result["positions"]),
            "open_orders": len(result["open_orders"]),
            "errors": result["errors"],
        }

    recorder.run(18, "optional Gate read-only account sync", OPTIONAL, check_gate_account)

    def check_write_blocked() -> dict[str, Any]:
        adapter = DisabledLiveExecutionAdapter()
        refused = []
        for operation in ("place_order", "cancel_order", "amend_order", "set_leverage", "transfer", "withdraw"):
            try:
                getattr(adapter, operation)(contract="BTC_USDT", size=1)
            except LiveExecutionBlocked:
                refused.append(operation)
        calls: list[str] = []
        client = ReadOnlyGateClient(
            api_key="not-a-real-key",
            api_secret="not-a-real-secret",
            transport=lambda method, url, headers, timeout: calls.append(method) or b"{}",
        )
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            try:
                client.request(method, "/futures/usdt/orders")
            except LiveExecutionBlocked:
                pass
        if len(refused) != 6 or calls:
            raise PaperTradingError("a live write path was not blocked")
        return {"_status": "BLOCKED_BY_DESIGN", "refused_operations": refused, "write_requests_reaching_transport": 0}

    recorder.run(19, "Gate live write execution", REQUIRED, check_write_blocked)

    if stream is not None:
        stream.stop()
    required_failed = [
        row for row in recorder.checks if row["tier"] == REQUIRED and row["status"] in {"FAIL", "NOT_VERIFIED"}
    ]
    summary = {
        "schema_version": "real-integration-check.v1",
        "check_id": check_id,
        "started_at": iso_utc(started_at),
        "completed_at": iso_utc(datetime.now(timezone.utc)),
        "status": "PASS" if not required_failed else "FAIL",
        "symbol": symbol,
        "fixtures_used": False,
        "isolated_database": str(database),
        "credential_store": resolver.store.backend,
        "pricing": {
            "mode": "explicit_fallback" if use_fallback_prices else "configured",
            "version": FALLBACK_PRICE_VERSION if use_fallback_prices else None,
            "note": FALLBACK_PRICE_SOURCE if use_fallback_prices else None,
        },
        "gate_live_write_execution": "BLOCKED_BY_DESIGN",
        "checks": recorder.checks,
        "providers": setup["config"]["jev_provider_id"] + "," + setup["config"]["gpt_provider_id"],
    }
    blob = json.dumps(summary, sort_keys=True, default=str).encode()
    if any(secret.encode() in blob for secret in secrets_for_scan):
        raise SystemExit("refusing to write a summary containing a credential")
    store.record_integration_check(check_id, summary)
    store.close()
    if app_database is not None:
        try:
            app_store = PaperStore(app_database)
            app_store.record_integration_check(check_id, summary)
            app_store.close()
        except Exception:
            pass
    target = out or REPOSITORY_ROOT / "reports" / f"{check_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(f"\nOverall: {summary['status']}  ·  summary: {target}")
    return 0 if summary["status"] == "PASS" else 1


def paper_execution_smoke(runtime: PaperRuntime, symbol: str) -> dict[str, Any]:
    """Operator-forced PAPER mechanics check on real Gate prices; labeled, never an AI decision."""

    store = runtime.store
    experiment = store.experiment()
    config = experiment["config"]
    snapshot = runtime._market(config).fetch_snapshot(symbol, datetime.now(timezone.utc))
    features = compute_features(snapshot, minimum_signal_strength=0.0, signal_gate_enabled=False)
    side = features["quant_direction"] if features["quant_direction"] in {"long", "short"} else "long"
    intent = TradingIntent.from_dict(
        build_fast_intent(
            snapshot,
            features,
            side=side,
            source_arm="quant",
            reason="INTEGRATION_SMOKE: operator-forced PAPER mechanics check on real Gate prices; not a model decision",
        )
    )
    context = snapshot.market_context or {}
    reference = context.get("best_ask") if side == "long" else context.get("best_bid")
    reference = float(reference or features["market_mark_price"])
    wallet = store.wallet_summary(experiment["experiment_id"], "primary")
    risk = runtime.risk_engine.evaluate(
        intent,
        config,
        equity=float(wallet["equity"]),
        margin_used=float(wallet["margin_used"]),
        open_positions=store.open_positions(experiment["experiment_id"]),
        data_cutoff=snapshot.data_cutoff,
        decision_as_of=datetime.now(timezone.utc),
        signal_approved=True,
        current_price=float(features["market_mark_price"]),
        current_atr=float(features["atr"]),
        last_bar_close_time=features["last_bar_close_time"],
        daily_loss=0.0,
        drawdown=0.0,
        consecutive_losses=0,
    )
    cycle_id = f"{experiment['experiment_id']}:SMOKE:{symbol}:{int(time.time())}"
    store.begin_cycle(
        cycle_id,
        experiment["experiment_id"],
        symbol,
        f"smoke-{int(time.time())}",
        snapshot.snapshot_hash,
        snapshot.as_of,
        snapshot.data_origin,
    )
    payload = store.complete_cycle(
        cycle_id,
        {
            "cycle_id": cycle_id,
            "experiment_id": experiment["experiment_id"],
            "symbol": symbol,
            "as_of": snapshot.as_of,
            "data_origin": snapshot.data_origin,
            "integration_smoke": True,
            "primary_decision": {"decision": f"ENTER_{side.upper()}", "reason": intent.reason},
            "risk": risk.to_dict(),
            "live_execution_enabled": False,
        },
        ai_calls=[],
        executions=[
            {
                "cohort": "primary",
                "risk": risk.to_dict(),
                "intent": intent.to_dict(),
                "reference_price": reference,
                "reference_price_source": f"{context.get('source', 'gate')}_best_{'ask' if side == 'long' else 'bid'}",
                "slippage_bps": config["slippage_bps"],
                "fee_rate": config["taker_fee_rate"],
                "as_of": snapshot.as_of,
                "data_origin": snapshot.data_origin,
                "market_regime": features["quant_regime"],
                "regime_source": "quant",
            }
        ],
        risk_events=[],
        data_origin=snapshot.data_origin,
    )
    execution = (payload.get("executions") or [{}])[0]
    return {
        "label": "INTEGRATION_SMOKE (operator-forced, not an AI decision)",
        "cycle_id": cycle_id,
        "side": side,
        "risk_code": risk.code,
        "risk_allowed": risk.allowed,
        "reference_price": reference,
        "status": execution.get("status"),
        "filled_quantity": execution.get("filled_quantity"),
    }


def setup_real(*, prices_file: Path | None, database: Path | None, overwrite_secrets: bool) -> int:
    """Bootstrap the local app: OS-store the env credentials and configure real providers."""

    from .paper_server import default_database_path

    load_environment_file(REPOSITORY_ROOT / ".env")
    resolver = CredentialResolver(default_secret_store())
    imported = import_env_secrets(resolver, overwrite=overwrite_secrets)
    store = PaperStore(database or default_database_path())
    runtime = PaperRuntime(store, resolver=resolver)
    prices: list[dict[str, Any]] = []
    if prices_file is not None:
        loaded = json.loads(prices_file.read_text(encoding="utf-8"))
        prices = loaded if isinstance(loaded, list) else [loaded]
    try:
        result = configure_real_providers(runtime, prices=prices)
    finally:
        providers = store.list_providers()
        store.close()
    print(f"Credential store: {resolver.store.backend}")
    for name, status in imported.items():
        print(f"  {name}: {status}")
    for provider in providers:
        if provider["provider_id"] in {JEV_PROVIDER_ID, GPT_PROVIDER_ID}:
            print(f"  provider {provider['provider_id']}: {provider['model']} · credential {provider['credential_status']}")
    print(f"  market data: {result['config']['market_data_mode']} · symbols {', '.join(result['config']['symbols'])}")
    if not result["prices_added"]:
        print("  price book: no entries added. Paid calls fail closed until prices are configured")
        print("  (Settings > Cost & Budgets, or --prices-file with contract pricing).")
    return 0
