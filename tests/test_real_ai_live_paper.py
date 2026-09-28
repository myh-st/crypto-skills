"""Offline, deterministic tests for the real-AI + Gate live PAPER integration phase.

Every external API here is a fake transport. No network, OS keychain, or credential is used.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import socket
import threading
import unittest
import urllib.error
import urllib.parse
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from crypto_eval.ai_cost import (
    BudgetError,
    default_budget_config,
    economic_summary,
    estimate_cost,
    normalize_usage,
    provider_ledger_summary,
    validate_budget_config,
    validate_fx_policy,
    worst_case_cost,
)
from crypto_eval.gate_account import (
    DisabledLiveExecutionAdapter,
    LiveExecutionBlocked,
    ReadOnlyGateClient,
    gate_signature,
    sync_read_only_account,
)
from crypto_eval.gate_market import (
    GateUsdtFuturesMarketDataProvider,
    from_gate_contract,
    normalize_book_ticker,
    normalize_candle,
    normalize_ticker,
    to_gate_contract,
)
from crypto_eval.gate_stream import GateLiveMarketStream
from crypto_eval.paper_ai import AIProviderError, JevAdapter, ResponsesAdapter, build_jev_questions
from crypto_eval.paper_contracts import PaperTradingError, default_experiment_config, iso_utc
from crypto_eval.paper_market import FixtureFuturesMarketDataProvider, MarketSnapshot
from crypto_eval.paper_runtime import PaperRuntime, PaperStore
from crypto_eval.paper_server import PaperHTTPServer, PaperRequestHandler
from crypto_eval.secret_store import (
    CredentialResolver,
    MacKeychainSecretStore,
    MemorySecretStore,
    SecretStoreError,
)
from crypto_eval.ws_client import OP_TEXT, WebSocketConnection, encode_frame

from tests.test_paper_futures import FixedClock, make_type_safe_response


SENTINEL = "SENTINEL-SECRET-VALUE-9f8e7d6c5b4a-NOT-REAL"
GATE_SENTINEL = "GATE-SENTINEL-SECRET-0a1b2c3d-NOT-REAL"
NOW = datetime(2026, 9, 28, 12, 1, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------- fakes
def gate_contract_row(name="BTC_USDT", quanto="0.0001"):
    return {
        "name": name,
        "quanto_multiplier": quanto,
        "order_size_min": 1,
        "order_size_max": 1000000,
        "order_price_round": "0.1",
        "mark_price_round": "0.01",
        "leverage_min": "1",
        "leverage_max": "100",
        "maintenance_rate": "0.004",
        "taker_fee_rate": "0.00075",
        "maker_fee_rate": "-0.0001",
        "funding_interval": 28800,
        "funding_next_apply": 1790611200,
        "status": "trading",
        "in_delisting": False,
    }


class FakeGateRest:
    """Serves Gate REST shapes, including the in-progress candle Gate always returns."""

    def __init__(self, now: datetime, *, price=100.0):
        self.now = now
        self.price = price
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float) -> bytes:
        self.urls.append(url)
        parsed = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        path = parsed.path.replace("/api/v4", "")
        if path == "/futures/usdt/contracts":
            return json.dumps([gate_contract_row(), gate_contract_row("ETH_USDT", "0.01")]).encode()
        if path == "/futures/usdt/candlesticks":
            step = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}[query["interval"]]
            start = int(query["from"])
            end = min(int(query["to"]), int(self.now.timestamp()) // step * step)
            rows = []
            t = start - start % step
            while t <= end:
                close = self.price * (1 + (t // step % 7) * 0.001)
                rows.append(
                    {"t": t, "o": str(close * 0.999), "h": str(close * 1.002), "l": str(close * 0.997),
                     "c": str(close), "v": 1000, "sum": str(close * 0.1)}
                )
                t += step
            return json.dumps(rows).encode()
        if path == "/futures/usdt/tickers":
            return json.dumps([
                {"contract": query["contract"], "last": "100.5", "mark_price": "100.4", "index_price": "100.3",
                 "funding_rate": "0.0001", "highest_bid": "100.4", "lowest_ask": "100.6",
                 "change_percentage": "-1.2", "volume_24h_quote": "12345", "total_size": "999"}
            ]).encode()
        if path == "/futures/usdt/order_book":
            return json.dumps({"current": self.now.timestamp(), "id": 7,
                               "bids": [{"p": "100.4", "s": 10}], "asks": [{"p": "100.6", "s": 12}]}).encode()
        if path == "/futures/usdt/funding_rate":
            base = int(self.now.timestamp()) // 28800 * 28800
            return json.dumps([{"t": base, "r": "0.0002"}, {"t": base - 28800, "r": "0.0001"}]).encode()
        if path == "/futures/usdt/contract_stats":
            hour = int(self.now.timestamp()) // 3600 * 3600
            return json.dumps([
                {"time": hour - 7200, "open_interest": 1000},
                {"time": hour - 3600, "open_interest": 1100},
                {"time": hour, "open_interest": 1200},
            ]).encode()
        raise AssertionError(f"unexpected Gate path {path}")


def jev_transport(calls: list):
    def transport(url, headers, body, timeout):
        payload = json.loads(body)
        calls.append({"url": url, "headers": headers, "payload": payload})
        response = make_type_safe_response(payload["questions"])
        response["usage"] = {"input_tokens": 1000, "output_tokens": 100}
        return json.dumps(response).encode(), {"x-request-id": "jev-req-1"}
    return transport


def responses_transport(calls: list, *, echo_effort="max", status="completed", no_trade=False):
    def transport(url, headers, body, timeout):
        payload = json.loads(body)
        calls.append({"url": url, "headers": headers, "payload": payload})
        user = json.loads(payload["input"])
        symbol = user["snapshot"]["symbol"]
        if no_trade or not user.get("features"):
            content = {"action": "no_trade", "symbol": symbol, "side": None, "entry_price": None,
                       "stop_price": None, "target_price": None, "reason": "test"}
        else:
            price = float(user["features"]["market_mark_price"])
            atr = max(float(user["features"]["atr"]), price * 0.002)
            content = {"action": "open", "symbol": symbol, "side": "long", "entry_price": price,
                       "stop_price": price - 1.5 * atr, "target_price": price + 2 * atr, "reason": "fake"}
        response = {
            "id": "resp_fake_1", "status": status, "model": payload["model"],
            "reasoning": {"effort": echo_effort},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(content)}]}],
            "usage": {"input_tokens": 2000, "input_tokens_details": {"cached_tokens": 500},
                      "output_tokens": 800, "output_tokens_details": {"reasoning_tokens": 600}},
        }
        return json.dumps(response).encode(), {"x-request-id": "gpt-req-1", "x-ms-region": "Test"}
    return transport


JEV_PROVIDER = {
    "provider_id": "typesafe-jev", "kind": "typesafe_jev", "display_name": "TypeSafe Jev",
    "base_url": "https://api.typesafe.ai", "model": "jev-latest", "enabled": True,
    "credential_secret": "provider.typesafe-jev", "timeout_seconds": 20,
}
GPT_PROVIDER = {
    "provider_id": "azure-gpt6-luna", "kind": "foundry_responses", "display_name": "Azure GPT-6 Luna",
    "base_url": "https://example-resource.services.ai.azure.com/api/projects/demo-project",
    "model": "gpt-6-luna", "enabled": True, "reasoning_effort": "max",
    "credential_secret": "provider.azure-gpt6-luna", "timeout_seconds": 60,
}
PRICES = [
    {"provider_kind": "foundry_responses", "model": "gpt-6-luna", "version": "test-v1",
     "effective_from": "2026-01-01T00:00:00Z", "input_per_million": 10.0, "cached_input_per_million": 1.0,
     "output_per_million": 40.0, "reasoning_billing_rule": "included_in_output", "source": "unit-test"},
    {"provider_kind": "typesafe_jev", "model": "jev-latest", "version": "test-v1",
     "effective_from": "2026-01-01T00:00:00Z", "input_per_million": 1.0, "output_per_million": 2.0,
     "reasoning_billing_rule": "included_in_output", "source": "unit-test"},
]


def memory_resolver() -> CredentialResolver:
    store = MemorySecretStore()
    store.set("provider.typesafe-jev", SENTINEL)
    store.set("provider.azure-gpt6-luna", SENTINEL + "-gpt")
    return CredentialResolver(store, environ={})


# ---------------------------------------------------------------- secrets
class SecretStoreTests(unittest.TestCase):
    def test_keychain_backend_never_places_secret_on_argv(self):
        captured = []

        class Result:
            def __init__(self, code, out=b""):
                self.returncode = code
                self.stdout = out

        def fake_run(argv, input=None, **kwargs):
            captured.append((argv, input))
            if argv[1] == "find-generic-password":
                return Result(0, (SENTINEL + "\n").encode())
            return Result(0)

        store = MacKeychainSecretStore(binary="/usr/bin/security")
        with patch("crypto_eval.secret_store.subprocess.run", fake_run):
            store.set("provider.test", SENTINEL)
            self.assertEqual(store.get("provider.test"), SENTINEL)
        for argv, stdin in captured:
            self.assertNotIn(SENTINEL, " ".join(argv))
        add = next(stdin for argv, stdin in captured if argv[1] == "-i")
        self.assertIn(SENTINEL.encode().hex().encode(), add)
        self.assertNotIn(SENTINEL.encode(), add)

    def test_resolver_prefers_os_store_then_env_and_reports_only_metadata(self):
        store = MemorySecretStore()
        resolver = CredentialResolver(store, environ={"FALLBACK": "env-value"})
        self.assertEqual(resolver.resolve(secret_id="a.b", env_name="FALLBACK"), ("env-value", "environment"))
        store.set("a.b", "stored-value")
        self.assertEqual(resolver.resolve(secret_id="a.b", env_name="FALLBACK"), ("stored-value", "session-memory"))
        status = resolver.status(secret_id="a.b", env_name=None)
        self.assertNotIn("stored-value", json.dumps(status))
        with self.assertRaises(SecretStoreError):
            store.set("Invalid ID", "x")
        with self.assertRaises(SecretStoreError):
            store.set("ok.id", "line1\nline2")


# ---------------------------------------------------------------- cost math
class CostMathTests(unittest.TestCase):
    def test_usage_parsing_keeps_unknown_fields_none(self):
        usage = normalize_usage({"input_tokens": 100, "input_tokens_details": {"cached_tokens": 40},
                                 "output_tokens": 50, "output_tokens_details": {"reasoning_tokens": 30}})
        self.assertEqual(usage, {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 50,
                                 "reasoning_tokens": 30})
        self.assertEqual(normalize_usage({"input_tokens": 10}),
                         {"input_tokens": 10, "cached_input_tokens": None, "output_tokens": None,
                          "reasoning_tokens": None})
        self.assertEqual(normalize_usage(None)["input_tokens"], None)

    def test_cost_calculation_cached_and_reasoning_rules(self):
        price = dict(PRICES[0])
        cost, status = estimate_cost(price, normalize_usage(
            {"input_tokens": 1_000_000, "input_tokens_details": {"cached_tokens": 500_000},
             "output_tokens": 100_000, "output_tokens_details": {"reasoning_tokens": 90_000}}))
        self.assertEqual(status, "estimated")
        self.assertAlmostEqual(cost, 0.5 * 10 + 0.5 * 1 + 0.1 * 40)
        separate = {**price, "reasoning_billing_rule": "separate_rate", "reasoning_per_million": 5.0}
        cost2, _ = estimate_cost(separate, normalize_usage(
            {"input_tokens": 0, "output_tokens": 1_000_000, "output_tokens_details": {"reasoning_tokens": 1_000_000}}))
        self.assertAlmostEqual(cost2, 45.0)
        self.assertEqual(estimate_cost(None, normalize_usage({"input_tokens": 1, "output_tokens": 1})),
                         (None, "unavailable"))
        self.assertEqual(estimate_cost(price, normalize_usage({"input_tokens": 1})), (None, "unavailable"))
        self.assertEqual(estimate_cost({**price, "reasoning_billing_rule": "unknown"},
                                       normalize_usage({"input_tokens": 1, "output_tokens": 1})),
                         (None, "unavailable"))
        self.assertAlmostEqual(worst_case_cost(price, input_tokens=1_000_000, max_output_tokens=1_000_000), 50.0)

    def test_budget_and_fx_validation(self):
        with self.assertRaises(BudgetError):
            validate_budget_config({**default_budget_config(), "limit_action": "SPEND_ANYWAY"})
        with self.assertRaises(BudgetError):
            validate_budget_config({**default_budget_config(), "unknown_price_policy": "TREAT_AS_ZERO"})
        self.assertEqual(validate_fx_policy(None)["mode"], "none")
        with self.assertRaises(BudgetError):
            validate_fx_policy({"version": "ai-cost-fx.v1", "mode": "manual", "usdt_per_usd": 1.0,
                                "source": "", "recorded_at": "2026-09-28T00:00:00Z"})
        fx = validate_fx_policy({"version": "ai-cost-fx.v1", "mode": "manual", "usdt_per_usd": 1.0,
                                 "source": "operator assumption USD=USDT", "recorded_at": "2026-09-28T00:00:00Z"})
        self.assertEqual(fx["usdt_per_usd"], 1.0)

    def test_economics_keep_currencies_separate_without_fx(self):
        positions = [
            {"cohort": "primary", "status": "closed", "closed_pnl_recorded": 1, "realized_gross": 12.0,
             "entry_fee": 0.5, "exit_fees": 0.5, "funding_paid": 0.2, "slippage_paid": 0.3, "realized_pnl": 10.5,
             "side": "long"},
            {"cohort": "primary", "status": "closed", "closed_pnl_recorded": 1, "realized_gross": -3.0,
             "entry_fee": 0.1, "exit_fees": 0.1, "funding_paid": 0.0, "slippage_paid": 0.1, "realized_pnl": -3.2,
             "side": "short"},
        ]
        usage = [
            {"call_type": "jev_decision", "real_external_call": 1, "provider_kind": "typesafe_jev",
             "provider_id": "j", "cost_status": "estimated", "estimated_cost_usd": 0.5, "status": "ok",
             "symbol": "BTCUSDT", "arm": "shared:jev", "cycle_id": "c1", "started_at": "2026-09-28T00:00:00Z"},
            {"call_type": "gpt_escalation", "real_external_call": 1, "provider_kind": "foundry_responses",
             "provider_id": "g", "cost_status": "estimated", "estimated_cost_usd": 1.5, "status": "ok",
             "symbol": "BTCUSDT", "arm": "hybrid", "cycle_id": "c1", "started_at": "2026-09-28T00:00:00Z"},
            {"call_type": "jev_decision", "real_external_call": 0, "provider_kind": "fixture_jev",
             "provider_id": "f", "cost_status": "exact", "estimated_cost_usd": 0.0, "status": "ok"},
        ]
        cycles = [{"cycle_id": "c1", "status": "complete",
                   "payload": {"primary_decision": {"decision": "NO_TRADE"}, "quant_gate": {"eligible": True}}}]
        none = economic_summary(positions=positions, usage_events=usage, cycles=cycles,
                                fx={"mode": "none"}, starting_balance_usdt=100)
        self.assertIsNone(none["net_economic_pnl_usdt"])
        self.assertIn("USD/USDT", none["net_economic_unavailable_reason"])
        self.assertAlmostEqual(none["trading"]["net_trading_pnl_usdt"], 7.3)
        self.assertAlmostEqual(none["ai_cost"]["total_ai_cost_usd"], 2.0)
        self.assertEqual(none["ai_cost"]["paid_calls"], 2)
        self.assertAlmostEqual(none["ai_cost"]["cost_on_no_trade_usd"], 2.0)
        manual = economic_summary(positions=positions, usage_events=usage, cycles=cycles,
                                  fx={"mode": "manual", "usdt_per_usd": 1.0}, starting_balance_usdt=100)
        self.assertAlmostEqual(manual["net_economic_pnl_usdt"], 5.3)
        self.assertAlmostEqual(manual["kpis"]["net_economic_expectancy_per_trade_usdt"], 2.65)
        self.assertEqual(manual["aligned_arm_value"]["status"], "insufficient_sample")
        summary = provider_ledger_summary(usage, [{"event_type": "blocked", "provider_id": "g"}])
        self.assertEqual({row["provider_id"]: row["budget_blocks"] for row in summary}, {"f": 0, "g": 1, "j": 0})


# ---------------------------------------------------------------- budget guard
class BudgetGuardTests(unittest.TestCase):
    def setUp(self):
        self.store = PaperStore(":memory:")
        self.clock = FixedClock(NOW)
        self.store.cost_ledger._clock = self.clock
        self.provider = {**GPT_PROVIDER}
        self.budget = default_budget_config()

    def tearDown(self):
        self.store.close()

    def reserve(self, **overrides):
        budget = {**self.budget, **overrides.pop("budget", {})}
        return self.store.cost_ledger.reserve(
            experiment_id="EXP-001", cycle_id=overrides.get("cycle_id"), provider=self.provider,
            call_type=overrides.get("call_type", "gpt_escalation"), budget=budget,
            payload_bytes=overrides.get("payload_bytes", 3000),
        )

    def test_unknown_price_fails_closed_without_zero(self):
        decision = self.reserve()
        self.assertFalse(decision.allowed)
        self.assertEqual(decision["code"], "UNKNOWN_PRICE")
        events = self.store.cost_ledger.budget_events("EXP-001")
        self.assertEqual(events[0]["code"], "UNKNOWN_PRICE")
        # An explicit operator connection test may proceed unpriced (ledger marks it unavailable).
        self.assertTrue(self.reserve(call_type="test_connection").allowed)

    def test_price_book_versions_are_append_only_and_effective_dated(self):
        ledger = self.store.cost_ledger
        ledger.add_price(PRICES[0])
        with self.assertRaises(BudgetError):
            ledger.add_price(PRICES[0])
        ledger.add_price({**PRICES[0], "version": "test-v2", "effective_from": "2026-10-01T00:00:00Z",
                          "input_per_million": 20.0})
        self.assertEqual(ledger.current_price("foundry_responses", "gpt-6-luna")["version"], "test-v1")
        self.assertEqual(
            ledger.current_price("foundry_responses", "gpt-6-luna",
                                 at=datetime(2026, 10, 2, tzinfo=timezone.utc))["version"], "test-v2")
        self.assertEqual(len(ledger.price_book()), 2)

    def test_limits_block_before_any_call(self):
        self.store.cost_ledger.add_price(PRICES[0])
        # worst case = input + 32k output * $40/M ≈ $1.3
        self.assertEqual(self.reserve(budget={"max_gpt_call_usd": 0.5})["code"], "MAX_CALL_COST")
        self.assertEqual(self.reserve(payload_bytes=10_000_000)["code"], "INPUT_TOKEN_LIMIT")
        big = {"max_gpt_call_usd": None, "max_cycle_usd": None}
        first = self.reserve(budget={**big, "daily_usd": 2.0})
        self.assertTrue(first.allowed)
        self.assertEqual(self.reserve(budget={**big, "daily_usd": 2.0})["code"], "DAILY_BUDGET")
        self.assertEqual(self.reserve(budget={**big, "daily_usd": None, "experiment_usd": 1.0})["code"],
                         "EXPERIMENT_BUDGET")
        self.assertEqual(
            self.reserve(budget={**big, "daily_usd": None, "experiment_usd": None, "max_gpt_calls_per_hour": 1})["code"],
            "GPT_HOURLY_CALLS")
        self.assertEqual(
            self.reserve(budget={**big, "daily_usd": None, "experiment_usd": None, "max_gpt_calls_per_day": 1})["code"],
            "GPT_DAILY_CALLS")
        self.assertEqual(
            self.reserve(budget={**big, "daily_usd": None, "experiment_usd": None, "max_paid_calls": 1})["code"],
            "MAX_PAID_CALLS")

    def test_reservation_reconciles_releases_and_warns_once(self):
        self.store.cost_ledger.add_price(PRICES[0])
        budget = {**self.budget, "max_gpt_call_usd": None, "max_cycle_usd": None, "daily_usd": 2.0}
        decision = self.store.cost_ledger.reserve(
            experiment_id="EXP-001", cycle_id=None, provider=self.provider, call_type="gpt_escalation",
            budget=budget, payload_bytes=3000)
        settled = self.store.cost_ledger.settle(decision["reservation_id"], experiment_id="EXP-001",
                                                charged_usd=1.7, keep_reserved_when_unknown=True, budget=budget)
        self.assertEqual(settled["status"], "reconciled")
        codes = {w["code"] for w in settled["warnings"]}
        self.assertEqual(codes, {"WARN_50", "WARN_80"})
        status = self.store.cost_ledger.budget_status("EXP-001", budget)
        self.assertAlmostEqual(status["spent_today_usd"], 1.7)
        self.assertAlmostEqual(status["remaining_today_usd"], 0.3)
        self.assertEqual(status["projection"]["label"], "ESTIMATE")
        with self.assertRaises(BudgetError):
            self.store.cost_ledger.settle(decision["reservation_id"], experiment_id="EXP-001",
                                          charged_usd=0.1, keep_reserved_when_unknown=True, budget=budget)
        again = self.store.cost_ledger.reserve(
            experiment_id="EXP-001", cycle_id=None, provider=self.provider, call_type="gpt_escalation",
            budget={**budget, "daily_usd": 100}, payload_bytes=3000)
        self.store.cost_ledger.settle(again["reservation_id"], experiment_id="EXP-001", charged_usd=None,
                                      keep_reserved_when_unknown=False, budget=budget)
        warnings = [e for e in self.store.cost_ledger.budget_events("EXP-001") if e["event_type"] == "warning"]
        self.assertEqual(len([e for e in warnings if e["code"] == "WARN_50"]), 1)

    def test_concurrent_workers_cannot_overspend(self):
        self.store.cost_ledger.add_price(PRICES[0])
        budget = {**self.budget, "max_gpt_call_usd": None, "max_cycle_usd": None, "daily_usd": 5.0,
                  "max_gpt_calls_per_hour": None, "max_gpt_calls_per_day": None}
        worst = worst_case_cost(PRICES[0], input_tokens=1064, max_output_tokens=budget["gpt_max_output_tokens"])
        results = []

        def worker():
            decision = self.store.cost_ledger.reserve(
                experiment_id="EXP-001", cycle_id=None, provider=self.provider, call_type="gpt_escalation",
                budget=budget, payload_bytes=3000)
            results.append(decision.allowed)

        threads = [threading.Thread(target=worker) for _ in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        allowed = sum(results)
        self.assertEqual(allowed, int(5.0 // worst))
        self.assertLessEqual(self.store.cost_ledger.budget_status("EXP-001", budget)["spent_today_usd"], 5.0)


# ---------------------------------------------------------------- provider contracts
class ProviderContractTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot("BTCUSDT", NOW)

    def test_foundry_project_endpoint_maps_to_resource_v1_responses_and_verifies_effort(self):
        calls = []
        adapter = ResponsesAdapter(GPT_PROVIDER, resolver=memory_resolver(), transport=responses_transport(calls),
                                   max_output_tokens=4096)
        result = adapter.test_connection()
        self.assertEqual(calls[0]["url"], "https://example-resource.services.ai.azure.com/openai/v1/responses")
        self.assertEqual(calls[0]["headers"]["api-key"], SENTINEL + "-gpt")
        self.assertNotIn("Authorization", calls[0]["headers"])
        self.assertEqual(calls[0]["payload"]["reasoning"], {"effort": "max"})
        self.assertEqual(calls[0]["payload"]["max_output_tokens"], 4096)
        self.assertEqual(calls[0]["payload"]["text"]["format"]["type"], "json_schema")
        self.assertEqual(result["reasoning_effort_echoed"], "max")
        self.assertEqual(result["provider_request_id"], "gpt-req-1")
        self.assertEqual(result["provider_response_id"], "resp_fake_1")
        self.assertNotIn(SENTINEL, json.dumps(result))

    def test_silent_reasoning_downgrade_and_incomplete_output_are_rejected(self):
        with self.assertRaisesRegex(AIProviderError, "silent downgrades"):
            ResponsesAdapter(GPT_PROVIDER, resolver=memory_resolver(),
                             transport=responses_transport([], echo_effort="high")).test_connection()
        with self.assertRaisesRegex(AIProviderError, "reasoning effort or structured"):
            ResponsesAdapter(GPT_PROVIDER, resolver=memory_resolver(),
                             transport=responses_transport([], status="incomplete")).test_connection()
        with self.assertRaisesRegex(AIProviderError, "reasoning effort is unsupported"):
            ResponsesAdapter({**GPT_PROVIDER, "reasoning_effort": "extreme"}, resolver=memory_resolver(),
                             transport=responses_transport([])).test_connection()

    def test_missing_credential_makes_no_request(self):
        calls = []
        empty = CredentialResolver(MemorySecretStore(), environ={})
        with self.assertRaisesRegex(AIProviderError, "not stored"):
            ResponsesAdapter(GPT_PROVIDER, resolver=empty, transport=responses_transport(calls)).test_connection()
        self.assertEqual(calls, [])

    def test_jev_contract_choice_score_noul_and_usage(self):
        calls = []
        adapter = JevAdapter(JEV_PROVIDER, resolver=memory_resolver(), transport=jev_transport(calls))
        result = adapter.test_connection()
        self.assertEqual(calls[0]["url"], "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer " + SENTINEL)
        self.assertEqual({q["type"] for q in calls[0]["payload"]["questions"].values()}, {"choice", "score", "noul"})
        self.assertIn("probabilities", result["typed_outputs"]["choice"])
        self.assertEqual(result["provider_request_id"], "jev-req-1")
        self.assertNotIn(SENTINEL, json.dumps(result))


# ---------------------------------------------------------------- Gate REST
class GateRestTests(unittest.TestCase):
    def test_symbol_mapping_stays_inside_adapter(self):
        self.assertEqual(to_gate_contract("BTCUSDT"), "BTC_USDT")
        self.assertEqual(from_gate_contract("SEI_USDT"), "SEIUSDT")
        with self.assertRaises(PaperTradingError):
            to_gate_contract("BTCUSD")

    def test_normalization_of_candle_ticker_book(self):
        candle = normalize_candle({"t": 1790604000, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": 2000,
                                   "sum": "3000"}, "15m", contract_size=0.0001)
        self.assertEqual(candle["close_time"], "2026-09-28T14:15:00.000Z")
        self.assertAlmostEqual(candle["volume"], 0.2)
        with self.assertRaises(PaperTradingError):
            normalize_candle({"t": 1790604001, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": 1}, "15m",
                             contract_size=1)
        ticker = normalize_ticker({"contract": "BTC_USDT", "last": "10", "mark_price": "10.1", "index_price": "10.2",
                                   "funding_rate": "0.0001", "highest_bid": "9.9", "lowest_ask": "10.1"},
                                  observed_at=NOW)
        self.assertEqual((ticker["symbol"], ticker["mark_price"], ticker["index_price"]), ("BTCUSDT", 10.1, 10.2))
        book = normalize_book_ticker({"t": 1790604000000, "s": "BTC_USDT", "b": "99", "B": 1, "a": "101", "A": 2, "u": 5})
        self.assertAlmostEqual(book["spread_bps"], 200.0)
        with self.assertRaises(PaperTradingError):
            normalize_book_ticker({"t": 1, "s": "BTC_USDT", "b": "101", "a": "99"})

    def test_snapshot_uses_closed_candles_only_and_real_context(self):
        fake = FakeGateRest(NOW)
        provider = GateUsdtFuturesMarketDataProvider(transport=fake)
        snapshot = provider.fetch_snapshot("BTCUSDT", NOW)
        self.assertEqual(snapshot.data_origin, "GATE_USDT_PUBLIC")
        self.assertEqual(snapshot.candles_15m[-1]["close_time"], "2026-09-28T12:00:00.000Z")
        self.assertEqual(snapshot.candles_1m[-1]["close_time"], "2026-09-28T12:01:00.000Z")
        self.assertTrue(all(set(c) == {"open_time", "close_time", "open", "high", "low", "close", "volume"}
                            for c in snapshot.candles_15m))
        self.assertAlmostEqual(snapshot.funding_rate, 0.0002)
        self.assertAlmostEqual(snapshot.open_interest_change_1h, 0.1)
        self.assertEqual(snapshot.market_context["best_ask"], 100.6)
        self.assertEqual(snapshot.market_context["source"], "gate_rest")
        self.assertTrue(all("BTC_USDT" in url or "contracts" in url for url in fake.urls))

    def test_history_pages_and_rejects_gaps(self):
        fake = FakeGateRest(NOW)
        provider = GateUsdtFuturesMarketDataProvider(transport=fake)
        bars = provider.fetch_history("BTCUSDT", "1m", bars=2500, as_of=NOW)
        self.assertEqual(len(bars), 2500)
        self.assertGreaterEqual(sum("candlesticks" in url for url in fake.urls), 3)
        self.assertEqual(provider.fetch_monitor_bars("BTCUSDT", NOW - timedelta(minutes=5), NOW)[-1]["close_time"],
                         "2026-09-28T12:01:00.000Z")

        def gappy(url, timeout):
            body = json.loads(fake(url, timeout))
            if "candlesticks" in url and isinstance(body, list) and len(body) > 3:
                body.pop(2)
            return json.dumps(body).encode()

        with self.assertRaises(PaperTradingError):
            GateUsdtFuturesMarketDataProvider(transport=gappy).fetch_history("BTCUSDT", "1m", bars=100, as_of=NOW)


# ---------------------------------------------------------------- Gate WS
class FakeWS:
    def __init__(self, messages, *, fail_after=None):
        self.messages = list(messages)
        self.sent = []
        self.closed = False
        self.fail_after = fail_after

    def send_text(self, text):
        self.sent.append(json.loads(text))

    def recv(self):
        if self.messages:
            return self.messages.pop(0)
        raise OSError("socket closed")

    def close(self):
        self.closed = True


def ws_candle(t, close, *, closed=False, interval="1m"):
    return json.dumps({"channel": "futures.candlesticks", "event": "update",
                       "result": [{"t": t, "o": str(close), "h": str(close + 1), "l": str(close - 1), "c": str(close),
                                   "v": 10, "a": "5", "n": f"{interval}_BTC_USDT", "w": closed}]})


class GateStreamTests(unittest.TestCase):
    def make_stream(self, clock, **kwargs):
        rest = GateUsdtFuturesMarketDataProvider(transport=FakeGateRest(NOW))
        return GateLiveMarketStream(["BTCUSDT"], rest=rest, intervals=("1m",), clock=clock, history_bars=30, **kwargs)

    def test_messages_normalize_dedupe_and_distinguish_closed_candles(self):
        clock = FixedClock(NOW)
        stream = self.make_stream(clock)
        stream.state = "LIVE"
        q = stream.subscribe()
        t = int(NOW.timestamp()) // 60 * 60
        stream.handle_message(ws_candle(t, 100.0))
        stream.handle_message(ws_candle(t, 100.0))  # duplicate
        stream.handle_message(ws_candle(t, 101.0, closed=True))
        stream.handle_message(ws_candle(t, 99.0))  # late partial must not reopen a closed candle
        stream.handle_message(ws_candle(t + 60, 102.0))
        lane = stream.candles("BTCUSDT", "1m")
        self.assertEqual([c["closed"] for c in lane], [True, False])
        self.assertEqual(lane[0]["close"], 101.0)
        book = {"channel": "futures.book_ticker", "event": "update",
                "result": {"t": int(NOW.timestamp() * 1000), "u": 10, "s": "BTC_USDT", "b": "100", "B": 1, "a": "100.2", "A": 1}}
        stream.handle_message(json.dumps(book))
        stream.handle_message(json.dumps(book))  # stale sequence
        self.assertGreaterEqual(stream.counters["duplicates"], 3)
        stream.handle_message(json.dumps({"channel": "futures.tickers", "event": "update",
                                          "result": [{"contract": "BTC_USDT", "last": "100.1", "mark_price": "100.05",
                                                      "index_price": "100.0", "funding_rate": "0.0001"}]}))
        state = stream.symbol_state("BTCUSDT")
        self.assertTrue(state["fresh"])
        self.assertEqual(state["ticker"]["mark_price"], 100.05)
        types = set()
        while not q.empty():
            types.add(q.get()["type"])
        self.assertTrue({"candle", "book", "ticker"} <= types)
        clock.value = NOW + timedelta(seconds=60)
        self.assertFalse(stream.symbol_state("BTCUSDT")["fresh"])
        self.assertEqual(stream.refresh_state(), "STALE")

    def test_reconnect_backoff_resubscribe_and_rest_gap_fill(self):
        clock = FixedClock(NOW)
        t = int(NOW.timestamp()) // 60 * 60
        sockets = [FakeWS([ws_candle(t, 100.0)]), FakeWS([ws_candle(t, 100.5, closed=True)])]
        created = []
        pauses = []
        stream = None

        def factory(url):
            ws = sockets[len(created)]
            created.append(ws)
            if len(created) == len(sockets):
                stream._stop.set()
            return ws

        stream = self.make_stream(clock, ws_factory=factory, sleep=pauses.append)
        stream._run()
        self.assertEqual(len(created), 2)
        self.assertEqual(stream.counters["reconnects"], 1)
        self.assertEqual(stream.counters["gap_fills"], 1)
        self.assertEqual(len(pauses), 1)
        self.assertLessEqual(pauses[0], stream.max_backoff_seconds * 1.2)
        subscriptions = [m for m in created[1].sent if m.get("event") == "subscribe"]
        self.assertEqual({m["channel"] for m in subscriptions},
                         {"futures.candlesticks", "futures.book_ticker", "futures.tickers"})
        self.assertTrue(all(c["source"].startswith(("rest", "ws")) for c in stream.candles("BTCUSDT", "1m")))
        self.assertFalse(stream.status()["synthetic_candles"])
        self.assertEqual(stream.state, "OFFLINE")


class WebSocketFrameTests(unittest.TestCase):
    def test_client_frames_are_masked_and_server_frames_decode(self):
        frame = encode_frame(OP_TEXT, b"hello", mask_key=b"\x01\x02\x03\x04")
        self.assertEqual(frame[1] & 0x80, 0x80)
        client_sock, server_sock = socket.socketpair()
        connection = WebSocketConnection("wss://example.test/ws", socket_factory=lambda h, p, t: client_sock)

        def server():
            request = b""
            while b"\r\n\r\n" not in request:
                request += server_sock.recv(4096)
            key = [line.split(b": ")[1] for line in request.split(b"\r\n") if line.lower().startswith(b"sec-websocket-key")][0]
            import base64
            accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
            server_sock.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                                b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            server_sock.sendall(bytes([0x09, 0x02]) + b"hi")  # ping
            server_sock.sendall(bytes([0x01, 0x03]) + b"abc")  # fragmented text
            server_sock.sendall(bytes([0x80, 0x03]) + b"def")

        thread = threading.Thread(target=server)
        thread.start()
        connection.connect()
        self.assertEqual(connection.recv(), "abcdef")
        thread.join()
        pong = server_sock.recv(64)
        self.assertEqual(pong[0] & 0x0F, 0x0A)
        connection.close()
        server_sock.close()


# ---------------------------------------------------------------- Gate account
class GateAccountTests(unittest.TestCase):
    def test_signature_matches_gate_v4_spec(self):
        expected = hmac.new(b"s3cret", "GET\n/api/v4/futures/usdt/accounts\n\n"
                            f"{hashlib.sha512(b'').hexdigest()}\n1700000000".encode(), hashlib.sha512).hexdigest()
        self.assertEqual(gate_signature("s3cret", "GET", "/api/v4/futures/usdt/accounts", "", b"", "1700000000"),
                         expected)

    def test_writes_are_blocked_before_transport(self):
        calls = []
        client = ReadOnlyGateClient(api_key="k", api_secret=GATE_SENTINEL,
                                    transport=lambda *args: calls.append(args) or b"{}")
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.assertRaises(LiveExecutionBlocked):
                client.request(method, "/futures/usdt/orders")
        with self.assertRaises(LiveExecutionBlocked):
            client.get("/futures/usdt/dual_mode")
        with self.assertRaises(LiveExecutionBlocked):
            client.get("/futures/usdt/orders", {"size": 1})
        with self.assertRaises(LiveExecutionBlocked):
            client.get("/withdrawals")
        self.assertEqual(calls, [])
        adapter = DisabledLiveExecutionAdapter()
        for operation in ("place_order", "cancel_order", "amend_order", "set_leverage", "set_margin_mode",
                          "transfer", "withdraw"):
            with self.assertRaisesRegex(LiveExecutionBlocked, "BLOCKED BY DESIGN"):
                getattr(adapter, operation)(contract="BTC_USDT")

    def test_read_only_sync_capability_matrix(self):
        responses = {
            "/account/detail": {"user_id": 1, "tier": 0},
            "/futures/usdt/accounts": {"total": "250.5", "available": "200", "unrealised_pnl": "4.5", "currency": "USDT"},
            "/futures/usdt/positions": [{"contract": "BTC_USDT", "size": -3, "entry_price": "80000",
                                         "mark_price": "79000", "liq_price": "90000", "leverage": "5"},
                                        {"contract": "ETH_USDT", "size": 0}],
            "/futures/usdt/orders": [],
            "/futures/usdt/my_trades": [{"id": 9, "order_id": 8, "contract": "BTC_USDT", "size": -3, "price": "80000",
                                         "fee": "0.1", "create_time": 1790000000}],
        }
        seen = []

        def transport(method, url, headers, timeout):
            path = urllib.parse.urlsplit(url).path.replace("/api/v4", "")
            seen.append((method, path, headers["SIGN"]))
            self.assertNotIn(GATE_SENTINEL, url + json.dumps(headers))
            return json.dumps(responses[path]).encode()

        client = ReadOnlyGateClient(api_key="k", api_secret=GATE_SENTINEL, transport=transport)
        result = sync_read_only_account(client)
        self.assertTrue(result["capability"]["authenticated"])
        self.assertTrue(result["capability"]["positions_sync"])
        self.assertFalse(result["capability"]["write_execution"])
        self.assertEqual(result["positions"][0]["side"], "short")
        self.assertAlmostEqual(result["balance"]["equity"], 255.0)
        self.assertEqual(result["non_get_requests_emitted"], 0)
        self.assertTrue(all(method == "GET" for method, _, _ in seen))
        self.assertNotIn(GATE_SENTINEL, json.dumps(result))


# ---------------------------------------------------------------- runtime integration
class FakeStream:
    def __init__(self, fresh=True):
        self.fresh = fresh
        self.symbols = ["BTCUSDT"]

    def symbol_state(self, symbol):
        return {"symbol": symbol, "stream_state": "LIVE" if self.fresh else "STALE", "fresh": self.fresh,
                "age_seconds": 1 if self.fresh else 120, "ticker": None, "book": None}

    def refresh_state(self):
        return "LIVE"

    def status(self):
        return {"state": "LIVE" if self.fresh else "STALE", "synthetic_candles": False}


class ContextMarket(FixtureFuturesMarketDataProvider):
    """Fixture candles plus an explicit exchange-like book context for execution-basis tests."""

    def fetch_snapshot(self, symbol, as_of):
        snapshot = super().fetch_snapshot(symbol, as_of)
        last = snapshot.candles_1m[-1]["close"]
        values = snapshot.to_dict()
        values["market_context"] = {"source": "gate_ws", "best_bid": last * 0.999, "best_ask": last * 1.001,
                                    "spread_bps": 20.0, "maintenance_rate": 0.01}
        return MarketSnapshot(**values).validate()


class RuntimeBudgetIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.store = PaperStore(":memory:")
        self.clock = FixedClock(NOW)
        self.resolver = memory_resolver()
        self.jev_calls: list = []
        self.gpt_calls: list = []

    def tearDown(self):
        self.store.close()

    def runtime(self, *, limit_action="PAUSE_NEW_ENTRIES", prices=True, market=None, stream=None, budget=None,
                gpt_no_trade=False):
        for provider in (JEV_PROVIDER, GPT_PROVIDER):
            self.store.save_provider(provider)
        if prices:
            for entry in PRICES:
                try:
                    self.store.cost_ledger.add_price(entry)
                except BudgetError:
                    pass
        config = default_experiment_config()
        config.update({"symbols": ["BTCUSDT"], "evaluation_arms": ["quant", "jev", "hybrid"], "primary_arm": "hybrid",
                       "jev_provider_id": "typesafe-jev", "gpt_provider_id": "azure-gpt6-luna",
                       "force_escalation": True})
        ai_budget = default_budget_config()
        ai_budget.update({"limit_action": limit_action, "max_gpt_call_usd": 5.0, "max_cycle_usd": 10.0})
        ai_budget.update(budget or {})
        config["ai_budget"] = ai_budget
        self.store.save_experiment(config)
        jev = JevAdapter(JEV_PROVIDER, resolver=self.resolver, transport=jev_transport(self.jev_calls))
        gpt = ResponsesAdapter(GPT_PROVIDER, resolver=self.resolver,
                               transport=responses_transport(self.gpt_calls, no_trade=gpt_no_trade))
        runtime = PaperRuntime(self.store, market_provider=market or FixtureFuturesMarketDataProvider(),
                               jev_provider=jev, gpt_provider=gpt, clock=self.clock, resolver=self.resolver,
                               live_stream=stream)
        self.store.set_status("running")
        return runtime

    def test_priced_cycle_ledgers_every_paid_call_and_reconciles(self):
        runtime = self.runtime()
        cycle = runtime.run_cycle("BTCUSDT", as_of=NOW, manual=True)
        self.assertEqual(len(self.jev_calls), 1)
        self.assertEqual(len(self.gpt_calls), 1)
        events = self.store.cost_ledger.usage_events("EXP-001")
        self.assertEqual({e["call_type"] for e in events}, {"jev_decision", "gpt_escalation"})
        gpt = next(e for e in events if e["call_type"] == "gpt_escalation")
        self.assertEqual((gpt["input_tokens"], gpt["cached_input_tokens"], gpt["output_tokens"], gpt["reasoning_tokens"]),
                         (2000, 500, 800, 600))
        self.assertAlmostEqual(gpt["estimated_cost_usd"], (1500 * 10 + 500 * 1 + 800 * 40) / 1e6)
        self.assertEqual(gpt["reasoning_effort"], "max")
        self.assertEqual(gpt["provider_request_id"], "gpt-req-1")
        self.assertEqual(gpt["price_book_version"], "test-v1")
        reservations = self.store.cost_ledger.reservations("EXP-001")
        self.assertTrue(all(r["status"] == "reconciled" for r in reservations))
        self.assertEqual(cycle["ai_budget"]["blocks"], [])
        economics = runtime.economics()
        self.assertGreater(economics["ai_cost"]["gpt_cost_usd"], 0)
        self.assertIsNone(economics["net_economic_pnl_usdt"])

    def test_unknown_price_blocks_paid_calls_and_pauses_new_entries(self):
        runtime = self.runtime(prices=False)
        cycle = runtime.run_cycle("BTCUSDT", as_of=NOW, manual=True)
        self.assertEqual(self.jev_calls, [])
        self.assertEqual(self.gpt_calls, [])
        self.assertEqual(cycle["risk"]["code"], "AI_BUDGET_PAUSE_NEW_ENTRIES")
        self.assertEqual(cycle.get("executions"), [])
        self.assertEqual(cycle["arms"]["hybrid"]["decision"], "NO_TRADE")
        codes = {e["code"] for e in self.store.cost_ledger.budget_events("EXP-001")}
        self.assertIn("UNKNOWN_PRICE", codes)
        self.assertIn("PAUSE_NEW_ENTRIES", codes)

    def test_fallback_quant_policy_is_labeled(self):
        runtime = self.runtime(limit_action="FALLBACK_QUANT", budget={"daily_usd": 0.0000001})
        cycle = runtime.run_cycle("BTCUSDT", as_of=NOW, manual=True)
        self.assertEqual(self.jev_calls, [])
        hybrid = cycle["arms"]["hybrid"]
        self.assertTrue(hybrid["reason"].startswith("AI_BUDGET_FALLBACK"))
        if cycle["features"]["gate_eligible"]:
            self.assertIsNotNone(hybrid["intent"])
            self.assertIn("AI_BUDGET_FALLBACK", hybrid["intent"]["reason"])

    def test_jev_only_policy_allows_jev_and_blocks_gpt(self):
        runtime = self.runtime(limit_action="JEV_ONLY", budget={"max_gpt_calls_per_day": 0})
        cycle = runtime.run_cycle("BTCUSDT", as_of=NOW, manual=True)
        self.assertEqual(len(self.jev_calls), 1)
        self.assertEqual(self.gpt_calls, [])
        self.assertIn("GPT_BUDGET_BLOCK", cycle["arms"]["hybrid"]["reason"])
        self.assertIsNone(cycle["ai_budget"]["entries_paused"])

    def test_stale_feed_blocks_ai_and_new_entries(self):
        runtime = self.runtime(stream=FakeStream(fresh=False))
        config = self.store.experiment()["config"]
        self.store.set_status("stopped")
        self.store.save_experiment({**config, "market_data_mode": "gate_usdt"})
        self.store.set_status("running")
        cycle = runtime.run_cycle("BTCUSDT", as_of=NOW, manual=True)
        self.assertEqual(self.jev_calls, [])
        self.assertEqual(self.gpt_calls, [])
        self.assertEqual(cycle["risk"]["code"], "STALE_MARKET_FEED")
        self.assertEqual(cycle["executions"], [])
        self.assertFalse(cycle["market_feed"]["fresh"])

    def test_market_entries_use_best_bid_ask_and_conservative_maintenance(self):
        runtime = self.runtime(market=ContextMarket())
        cycle = runtime.run_cycle("BTCUSDT", as_of=NOW, manual=True)
        self.assertEqual(cycle["execution_price_basis"], "exchange_best_bid_ask_plus_slippage_model")
        orders = self.store.export_records("EXP-001")["orders"]
        if orders:
            execution = json.loads(orders[0]["risk_json"])["execution"]
            self.assertTrue(execution["reference_price_source"].startswith("gate_ws_best_"))

    def test_secret_sentinels_never_reach_db_export_or_api(self):
        path = Path(self.id().replace(".", "_") + ".sqlite3")
        store = PaperStore(path)
        try:
            resolver = memory_resolver()
            store.save_provider(JEV_PROVIDER)
            runtime = PaperRuntime(store, resolver=resolver, jev_provider=JevAdapter(
                JEV_PROVIDER, resolver=resolver, transport=jev_transport([])), clock=self.clock)
            runtime.save_provider_secret("typesafe-jev", SENTINEL + "-rotated")
            store.save_exchange_account({"account_id": "gate-main", "display_name": "Gate", "environment": "live"})
            runtime.save_account_secrets("gate-main", "gate-key-" + GATE_SENTINEL, GATE_SENTINEL)
            runtime.test_provider("typesafe-jev")
            body, _ = runtime.export_bundle()
            dashboard = json.dumps(runtime.dashboard())
            providers = json.dumps(store.list_providers())
            accounts = json.dumps(store.list_exchange_accounts())
            store.close()
            database = path.read_bytes()
            for artifact in (database, body, dashboard.encode(), providers.encode(), accounts.encode()):
                self.assertNotIn(SENTINEL.encode(), artifact)
                self.assertNotIn(GATE_SENTINEL.encode(), artifact)
            with zipfile.ZipFile(io.BytesIO(body)) as bundle:
                names = set(bundle.namelist())
                blob = b"".join(bundle.read(name) for name in names)
            self.assertNotIn(SENTINEL.encode(), blob)
            self.assertTrue({"ai-cost-ledger.csv", "ai-budget-events.csv", "provider-price-book.json",
                             "economic-pnl.csv", "market-stream-health.csv", "provider-validation.json",
                             "real-integration-summary.json"} <= names)
            self.assertIn("stored_in_os_credential_store", providers)
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)


class ServerEndpointTests(unittest.TestCase):
    def setUp(self):
        self.store = PaperStore(":memory:")
        self.runtime = PaperRuntime(self.store, resolver=memory_resolver())
        self.server = PaperHTTPServer(("127.0.0.1", 0), PaperRequestHandler, self.runtime, None)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.store.close()

    def request(self, path, body=None):
        import urllib.request

        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method="POST" if body is not None else "GET",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_secret_endpoints_return_only_masked_metadata(self):
        self.store.save_provider(JEV_PROVIDER)
        status, payload = self.request("/api/providers/typesafe-jev/secret", {"value": SENTINEL})
        self.assertEqual(status, 200)
        self.assertNotIn(SENTINEL, json.dumps(payload))
        self.assertTrue(payload["secret"]["stored"])
        status, payload = self.request("/api/providers")
        self.assertNotIn(SENTINEL, json.dumps(payload))
        status, payload = self.request("/api/exchange-accounts", {"account_id": "gate-main", "api_key": "x"})
        self.assertEqual(status, 400)
        status, payload = self.request("/api/exchange-accounts", {"account_id": "gate-main", "environment": "testnet"})
        self.assertEqual(payload["account"]["write_execution"], False)
        status, payload = self.request("/api/exchange-accounts/gate-main/secrets",
                                       {"api_key": "key-" + GATE_SENTINEL, "api_secret": GATE_SENTINEL})
        self.assertEqual(status, 200)
        self.assertNotIn(GATE_SENTINEL, json.dumps(payload))
        status, payload = self.request("/api/exchange-accounts")
        self.assertTrue(payload["accounts"][0]["credentials"]["api_secret"])
        self.assertEqual(payload["live_execution_status"], "BLOCKED_BY_DESIGN")

    def test_cost_controls_price_book_and_stream_absence(self):
        status, payload = self.request("/api/price-book", PRICES[0])
        self.assertEqual(status, 200)
        status, payload = self.request("/api/price-book", PRICES[0])
        self.assertEqual(status, 400)
        budget = {**default_budget_config(), "daily_usd": 1.25, "limit_action": "BLOCK_PAID_AI"}
        status, payload = self.request("/api/cost-controls", {"ai_budget": budget})
        self.assertEqual(payload["config"]["ai_budget"]["daily_usd"], 1.25)
        status, payload = self.request("/api/cost")
        self.assertEqual(payload["budget_status"]["limit_action"], "BLOCK_PAID_AI")
        status, payload = self.request("/api/market/stream")
        self.assertEqual(status, 503)
        status, payload = self.request("/api/economics")
        self.assertEqual(payload["trading_currency"], "USDT")


if __name__ == "__main__":
    unittest.main()
