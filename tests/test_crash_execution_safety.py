"""Crash & execution safety: deterministic torture scenarios (fixtures/fakes only, no network)."""

from __future__ import annotations

import unittest
from datetime import timedelta

from crypto_eval.execution_safety import (
    DEFAULT_SAFETY_SETTINGS,
    KILL_SWITCH_LEVELS,
    classify_market,
    kill_switch_allows,
    plan_execution,
    suspect_print,
    validate_safety_settings,
)
from crypto_eval.market_catalog import FixtureSpotMarketDataProvider, MarketCatalog
from crypto_eval.paper_ai import AIProviderError, parse_replan
from crypto_eval.paper_contracts import PaperTradingError, default_experiment_config, iso_utc
from crypto_eval.portfolio_os import ConfirmationRequired

from tests.test_portfolio_os import NOW, PortfolioCase, minute_bars

S = DEFAULT_SAFETY_SETTINGS


def bars_from_closes(closes, *, start=None, wick_low=None):
    start = start or (NOW.replace(second=0) - timedelta(minutes=len(closes)))
    rows = []
    previous = closes[0]
    for index, close in enumerate(closes):
        o = previous
        low = min(o, close) * 0.999
        if wick_low and index in wick_low:
            low = wick_low[index]
        rows.append((o, max(o, close) * 1.001, low, close))
        previous = close
    return minute_bars(start, rows)


def quote(mid=100.0, spread_bps=2.0, last=None, mark=None, index=None, age=0):
    half = mid * spread_bps / 20_000
    return {"best_bid": mid - half, "best_ask": mid + half, "last_price": mid if last is None else last,
            "mark_price": mark, "index_price": index, "observed_at": iso_utc(NOW - timedelta(seconds=age))}


def classify(closes=None, bars=None, **kwargs):
    return classify_market(instrument_id="fixture:spot:ETH_USDT", market_type=kwargs.pop("market_type", "spot"),
                           quote=kwargs.pop("q", quote(closes[-1] if closes else 100.0)),
                           bars_1m=bars if bars is not None else bars_from_closes(closes or [100.0] * 20),
                           settings=S, now=NOW, **kwargs)


class ClassifierTests(unittest.TestCase):
    def test_normal_market(self):
        result = classify([100 + i * 0.01 for i in range(30)])
        self.assertEqual(result["state"], "NORMAL")
        self.assertEqual(result["restrictions"]["new_entries"], "ALLOWED")

    def test_flash_crash_then_recovery_is_transient_not_structural(self):
        crash = classify([100.0] * 10 + [99, 98, 70])
        self.assertEqual(crash["state"], "CRASH_MODE")
        self.assertIn("CRASH_MODE_ENTERED", crash["reasons"])
        self.assertEqual(crash["restrictions"]["new_entries"], "BLOCKED")
        recovered = classify([100.0] * 10 + [99, 98, 70, 96, 99], prior={"state": "CRASH_MODE"})
        self.assertIn("TRANSIENT_WICK", recovered["reasons"])
        self.assertFalse(recovered["structural_breakdown"])
        self.assertEqual(recovered["state"], "RECOVERY")

    def test_structural_crash_is_confirmed(self):
        closes = [100 - i * 1.0 for i in range(30)]
        result = classify(closes)
        self.assertEqual(result["state"], "CRASH_MODE")
        self.assertTrue(result["structural_breakdown"])
        self.assertIn("STRUCTURAL_BREAKDOWN", result["reasons"])

    def test_liquidity_vacuum_and_spread(self):
        vacuum = classify([100.0] * 20, q=quote(spread_bps=250))
        self.assertIn("LIQUIDITY_VACUUM", vacuum["reasons"])
        self.assertEqual(vacuum["restrictions"]["new_entries"], "BLOCKED")
        wide = classify([100.0] * 20, q=quote(spread_bps=40))
        self.assertIn("SPREAD_EXPANSION", wide["reasons"])
        self.assertEqual(wide["state"], "VOLATILITY_ALERT")

    def test_bad_print_and_divergences(self):
        bad_last = classify([100.0] * 20, q=quote(last=80.0))
        self.assertIn("PRICE_ANOMALY", bad_last["reasons"])
        self.assertAlmostEqual(bad_last["trusted_price"], 100.0)
        self.assertNotEqual(bad_last["state"], "MARKET_DATA_UNTRUSTED")
        cross = classify([100.0] * 20, reference_price=94.0)
        self.assertEqual(cross["state"], "MARKET_DATA_UNTRUSTED")
        self.assertIn("CROSS_VENUE_DIVERGENCE", cross["reasons"])
        mark_index = classify([100.0] * 20, market_type="perpetual", q=quote(mark=100.0, index=95.0))
        self.assertIn("MARK_INDEX_DIVERGENCE", mark_index["reasons"])
        self.assertEqual(mark_index["state"], "MARKET_DATA_UNTRUSTED")
        stale = classify([100.0] * 20, q=quote(age=120))
        self.assertIn("FEED_STALE", stale["reasons"])
        self.assertEqual(stale["price_confidence"], "UNTRUSTED")
        no_book = classify([100.0] * 20, q=None, require_quote=False)
        self.assertEqual(no_book["state"], "NORMAL")

    def test_suspect_print_detection(self):
        history = bars_from_closes([100.0] * 25)
        spike = {"open": 100.0, "high": 100.1, "low": 80.0, "close": 99.9, "close_time": "x"}
        self.assertEqual(suspect_print(spike, history, S)["code"], "SUSPECT_PRINT")
        real = {"open": 100.0, "high": 100.1, "low": 80.0, "close": 81.0, "close_time": "x"}
        self.assertIsNone(suspect_print(real, history, S))

    def test_settings_validation(self):
        with self.assertRaisesRegex(PaperTradingError, "between"):
            validate_safety_settings({"crash_pct_5m": 5})
        with self.assertRaisesRegex(PaperTradingError, "not supported"):
            validate_safety_settings({"unknown": 1})
        with self.assertRaisesRegex(PaperTradingError, ">="):
            validate_safety_settings({"spread_alert_bps": 50, "spread_vacuum_bps": 30})


class PlannerTests(unittest.TestCase):
    def plan(self, **kwargs):
        assessment = kwargs.pop("assessment", None) or classify([100.0] * 20)
        base = dict(action="reduce", source="AI", market_type="spot", side="long", quantity=1.0, assessment=assessment,
                    quote=quote(), settings=S, position_quantity=1.0)
        base.update(kwargs)
        return plan_execution(**base)

    def test_ai_never_controls_mechanics(self):
        plan = self.plan(action="entry", side="buy", position_quantity=None)
        self.assertFalse(plan["ai_controls_mechanics"])
        for key in ("style", "max_slippage_bps", "slices", "expires_at", "price_envelope"):
            self.assertIn(key, plan)
        self.assertEqual(plan["style"], "MARKETABLE_LIMIT")

    def test_ai_hallucinated_quantity_is_rejected_or_clamped(self):
        self.assertEqual(self.plan(quantity=float("inf"))["decision"], "REJECT")
        self.assertEqual(self.plan(quantity=-3)["decision"], "REJECT")
        clamped = self.plan(quantity=50.0, source="USER")
        self.assertIn("WRONG_SIDE_BLOCK", clamped["reasons"])
        self.assertLessEqual(clamped["quantity"], 1.0)

    def test_sell_velocity_limits_ai_but_not_emergency(self):
        limited = self.plan(quantity=1.0, recent_reduced_fraction=0.3)
        self.assertIn("SELL_VELOCITY_LIMIT", limited["reasons"])
        self.assertLess(limited["quantity"], 1.0)
        spent = self.plan(quantity=0.5, recent_reduced_fraction=0.5)
        self.assertEqual(spent["decision"], "DEFER")
        emergency = self.plan(quantity=1.0, recent_reduced_fraction=0.9, urgency="emergency", market_type="perpetual")
        self.assertEqual(emergency["decision"], "EXECUTE")
        self.assertIn("LIQUIDATION_BUFFER_CRITICAL", emergency["reasons"])
        self.assertEqual(emergency["style"], "MARKET")

    def test_untrusted_data_defers_discretionary_but_not_emergency(self):
        untrusted = classify([100.0] * 20, q=quote(age=300))
        self.assertEqual(self.plan(assessment=untrusted)["decision"], "DEFER")
        self.assertEqual(self.plan(assessment=untrusted, urgency="emergency", market_type="perpetual")["decision"], "EXECUTE")

    def test_liquidity_vacuum_slices_and_rejects_entries(self):
        vacuum = classify([100.0] * 20, q=quote(spread_bps=90))
        self.assertIn("LIQUIDITY_VACUUM", classify([100.0] * 20, q=quote(spread_bps=150))["reasons"])
        sliced = self.plan(assessment=classify([100.0] * 20, q=quote(spread_bps=150)), quote=quote(spread_bps=80), quantity=1.0, source="USER")
        self.assertEqual(sliced["decision"], "SLICE")
        self.assertEqual(len(sliced["slices"]), 3)
        self.assertAlmostEqual(sum(sliced["slices"]), 1.0)
        too_wide = self.plan(assessment=vacuum, quote=quote(spread_bps=150), quantity=1.0)
        self.assertEqual(too_wide["decision"], "DEFER")
        self.assertIn("SLIPPAGE_LIMIT", too_wide["reasons"])
        entry = self.plan(action="entry", side="buy", assessment=classify([100.0] * 20, q=quote(spread_bps=150)), position_quantity=None)
        self.assertEqual(entry["decision"], "REJECT")

    def test_core_protection_unless_structural(self):
        crash = classify([100.0] * 10 + [99, 98, 70])
        protected = self.plan(assessment=crash, quantity=1.0, core_quantity=0.6, source="USER")
        self.assertIn("CORE_PROTECTED", protected["reasons"])
        self.assertAlmostEqual(protected["quantity"], 0.4)
        structural = classify([100 - i for i in range(30)])
        released = self.plan(assessment=structural, quantity=0.5, core_quantity=0.6, source="USER", recent_reduced_fraction=0.0)
        self.assertNotIn("CORE_PROTECTED", released["reasons"])
        self.assertGreater(released["quantity"], 0)

    def test_kill_switch_matrix(self):
        self.assertEqual(KILL_SWITCH_LEVELS[0], "NORMAL")
        cases = {
            "NO_NEW_ENTRIES": [("entry", "USER", False, False), ("reduce", "AI", True, True)],
            "AI_MANAGEMENT_PAUSED": [("reduce", "AI", True, False), ("reduce", "USER", True, True)],
            "RISK_REDUCING_ONLY": [("protect", "USER", False, False), ("close", "USER", True, True)],
            "FULL_AUTOMATION_HALT": [("reduce", "AI", True, False), ("close", "USER", True, True), ("reduce", "SYSTEM", True, True)],
        }
        for level, rows in cases.items():
            for action, source, risk_reducing, expected in rows:
                self.assertEqual(kill_switch_allows(level, action=action, source=source, risk_reducing=risk_reducing)[0], expected,
                                 (level, action, source))


class PerpQuoteCatalog(MarketCatalog):
    overrides: dict[str, dict] = {}

    def quote(self, instrument):
        value = super().quote(instrument)
        value.update(self.overrides.get(instrument, {}))
        return value


class TortureTests(PortfolioCase):
    def setUp(self):
        super().setUp()
        PerpQuoteCatalog.overrides = {}
        self.make_runtime(catalog=PerpQuoteCatalog(
            source="fixture", spot_provider=FixtureSpotMarketDataProvider(clock=self.clock, future_path=self.spot_path), clock=self.clock))

    def feed_spot(self, closes, *, minutes_back=None, wick_low=None):
        mid = self.os.quote("fixture:spot:ETH_USDT")["mid_price"]
        scaled = [mid * c / 100 for c in closes]
        start = NOW.replace(second=0) - timedelta(minutes=minutes_back or len(closes))
        self.spot_path["ETHUSDT"] = bars_from_closes(scaled, start=start, wick_low=wick_low)
        return mid

    def test_FLASH_CRASH_RECOVERY_does_not_dump_protected_core(self):
        buy = self.buy_spot(amount=30.0)
        ref = buy["position_ref"]
        quantity = self.os.position(ref)["quantity"]
        self.os.set_core_quantity(ref, core_fraction=0.6)
        mid = self.feed_spot([100.0] * 10 + [99, 98, 70])
        self.os.update_protection(ref, stop_price=mid * 0.9, confirm_risk_increase=True)
        self.assertEqual(self.os.assess("fixture:spot:ETH_USDT", force=True)["state"], "CRASH_MODE")
        self.os.set_management_mode(ref, "AUTO_PAPER", confirm=True)
        result = self.os.reduce_position(ref, 1.0, source="AI")
        self.assertIn("CORE_PROTECTED", result["execution_plan"]["reasons"])
        remaining = self.os.position(ref)["quantity"]
        self.assertGreaterEqual(remaining, quantity * 0.6 - 1e-9)
        self.feed_spot([100.0] * 10 + [99, 98, 70, 96, 99])
        self.clock.value = NOW + timedelta(minutes=5)
        self.os.monitor_spot()
        self.assertAlmostEqual(self.os.position(ref)["quantity"], remaining)

    def test_STRUCTURAL_CRASH_still_allows_de_risking(self):
        buy = self.buy_spot(amount=30.0)
        ref = buy["position_ref"]
        self.os.set_core_quantity(ref, core_fraction=0.6)
        self.feed_spot([100 - i for i in range(30)])
        assessment = self.os.assess("fixture:spot:ETH_USDT", force=True)
        self.assertTrue(assessment["structural_breakdown"])
        self.os.set_management_mode(ref, "AUTO_PAPER", confirm=True)
        before = self.os.position(ref)["quantity"]
        result = self.os.reduce_position(ref, 0.8, source="AI")
        self.assertGreater(result["result"]["filled_quantity"], 0)
        self.assertIn("SELL_VELOCITY_LIMIT", result["execution_plan"]["reasons"])
        self.assertNotIn("CORE_PROTECTED", result["execution_plan"]["reasons"])
        self.assertLess(self.os.position(ref)["quantity"], before)
        with self.assertRaises(ConfirmationRequired) as ctx:
            self.os.reduce_position(ref, 0.9)
        self.assertEqual(ctx.exception.code, "CONFIRM_SAFETY_OVERRIDE")
        override = self.os.reduce_position(ref, 0.9, confirm=True)
        self.assertGreater(override["result"]["filled_quantity"], 0)
        codes = self.os.safety.counts(self.experiment_id)
        self.assertIn("SAFETY_OVERRIDE", codes)

    @property
    def experiment_id(self):
        return self.store.experiment()["experiment_id"]

    def test_LIQUIDITY_VACUUM_defers_entries_and_slices_reductions(self):
        buy = self.buy_spot(amount=30.0)
        mid = self.os.quote("fixture:spot:ETH_USDT")["mid_price"]
        PerpQuoteCatalog.overrides["fixture:spot:ETH_USDT"] = {"best_bid": mid * (1 - 0.0075), "best_ask": mid * (1 + 0.0075)}
        self.os._assess_cache.clear()
        preview = self.os.preview_order({"instrument_id": "fixture:spot:ETH_USDT", "action": "buy", "quote_amount": 4})
        self.assertFalse(preview["allowed"])
        self.assertEqual(preview["code"], "EXECUTION_DEFERRED")
        self.assertEqual(preview["market_safety"]["restrictions"]["new_entries"], "BLOCKED")
        PerpQuoteCatalog.overrides["fixture:spot:ETH_USDT"] = {"best_bid": mid * (1 - 0.004), "best_ask": mid * (1 + 0.004)}
        self.os._assess_cache.clear()
        self.os.update_settings({"safety": {"spread_vacuum_bps": 60, "spread_alert_bps": 30}})
        result = self.os.reduce_position(buy["position_ref"], 0.5)
        self.assertEqual(result["execution_plan"]["decision"], "SLICE")
        self.assertEqual(len(result["execution_plan"]["slices"]), 3)
        fills = self.os.position(buy["position_ref"])["fills"]
        self.assertEqual(len([f for f in fills if f["side"] == "sell"]), 3)

    def test_BAD_PRINT_never_executes_from_the_outlier(self):
        created, ref_price = self.open_perp(stop_pct=0.02)
        position_id = created["position_ref"].split(":", 1)[1]
        row = self.store._query("SELECT * FROM positions WHERE position_id=?", (position_id,))[0]
        entry, stop = row["entry_price"], row["stop_price"]
        opened = row["opened_at"]
        start = NOW.replace(second=0) + timedelta(minutes=1)
        history = [(entry, entry * 1.0005, entry * 0.9995, entry)] * 25
        self.perp_path["BTCUSDT"] = minute_bars(start - timedelta(minutes=25), history) + minute_bars(start, [
            (entry, entry * 1.0005, stop * 0.9, entry * 0.9999),
            (entry, entry * 1.0005, entry * 0.999, entry),
        ])
        self.clock.value = start + timedelta(minutes=3)
        events = self.runtime.monitor_once()
        self.assertEqual([e for e in events if e["event"] in {"stop", "liquidation"}], [])
        self.assertEqual(self.os.position(created["position_ref"])["status"], "open")
        self.assertIn("SUSPECT_PRINT", self.os.safety.counts(self.experiment_id))
        self.assertLess(opened, iso_utc(start))
        spot_mid = self.os.quote("fixture:spot:BTC_USDT")["mid_price"]
        PerpQuoteCatalog.overrides["fixture:perpetual:BTC_USDT"] = {"best_bid": spot_mid * 0.94, "best_ask": spot_mid * 0.9401}
        self.os._assess_cache.clear()
        with self.assertRaisesRegex(PaperTradingError, "EXECUTION_DEFERRED"):
            self.os.reduce_position(created["position_ref"], 0.25)

    def test_AI_HALLUCINATION_cannot_set_mechanics_or_impossible_size(self):
        with self.assertRaisesRegex(PaperTradingError, "derived server-side"):
            self.os.create_order({"instrument_id": "fixture:perpetual:BTC_USDT", "action": "long", "quantity": 1e9,
                                  "stop_price": 1, "targets": [2]}, source="AI")
        for bad in ({"action": "reduce", "reduce_fraction": 7.0}, {"action": "adjust", "stop_price": 1e12}):
            content = {"action": "hold", "stop_price": None, "target_prices": [], "reduce_fraction": None,
                       "thesis_status": "intact", "reason_codes": [], "summary": "", **bad}
            with self.assertRaises(AIProviderError):
                parse_replan(content, {"position": {"side": "long", "mark_price": 100.0}})
        order = self.os.create_order({"instrument_id": "fixture:spot:ETH_USDT", "action": "buy", "quote_amount": 1e9}, source="AI")
        self.assertFalse(order["accepted"])
        self.assertEqual(order["code"], "INSUFFICIENT_QUOTE")

    def test_DUPLICATE_RETRY_creates_one_fill(self):
        created, _ = self.open_perp()
        ref = created["position_ref"]
        first = self.os.reduce_position(ref, 0.25, request_id="retry-after-timeout-1")
        second = self.os.reduce_position(ref, 0.25, request_id="retry-after-timeout-1")
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(first["result"]["filled_quantity"], second["result"]["filled_quantity"])
        exits = [f for f in self.os.position(ref)["fills"] if f["side"] == "sell"]
        self.assertEqual(len(exits), 1)
        self.assertIn("DUPLICATE_PREVENTED", self.os.safety.counts(self.experiment_id))

    def test_DELAYED_ORDER_expired_decision_cannot_execute(self):
        preview = self.os.preview_order({"instrument_id": "fixture:spot:ETH_USDT", "action": "buy", "quote_amount": 10})
        self.assertIn("expires_at", preview)
        self.clock.value = NOW + timedelta(seconds=S["preview_ttl_seconds"] + 5)
        with self.assertRaisesRegex(PaperTradingError, "STALE_DECISION"):
            self.os.create_order({"instrument_id": "fixture:spot:ETH_USDT", "action": "buy", "quote_amount": 10,
                                  "preview_as_of": preview["as_of"]})
        self.assertEqual(self.os.spot_holdings(), [])
        self.assertIn("STALE_DECISION", self.os.safety.counts(self.experiment_id))

    def test_WRONG_SIDE_RETRY_cannot_reverse_or_oversell(self):
        created, _ = self.open_perp()
        ref = created["position_ref"]
        self.os.close_position(ref, confirm=True)
        self.clock.value = NOW + timedelta(seconds=3)
        with self.assertRaisesRegex(PaperTradingError, "POSITION_CLOSED"):
            self.os.close_position(ref, confirm=True)
        positions = [p for p in self.store.list_positions(self.experiment_id, cohort="primary")]
        self.assertTrue(all(p["status"] == "closed" for p in positions))
        buy = self.buy_spot(amount=20)
        holding_id = buy["position_ref"].split(":", 1)[1]
        with self.assertRaisesRegex(PaperTradingError, "INSUFFICIENT_BASE"):
            self.os._spot_market_sell(buy["position_ref"], holding_id, fraction=0, quantity=1e6, source="USER", exit_reason="x")
        self.assertIn("WRONG_SIDE_BLOCK", self.os.safety.counts(self.experiment_id))

    def test_RECONCILIATION_BREAK_escalates_kill_switch_and_blocks_automation(self):
        created, ref_price = self.open_perp()
        ref = created["position_ref"]
        self.os.set_management_mode(ref, "MANUAL_OVERRIDE")
        self.os.set_management_mode(ref, "AUTO_PAPER", confirm=True)
        self.assertTrue(self.os.reconcile()["ok"])
        self.store._db.execute("UPDATE wallets SET cash_balance=cash_balance+5 WHERE cohort='primary'")
        result = self.os.reconcile()
        self.assertFalse(result["ok"])
        self.assertEqual(self.os.kill_switch()["level"], "RISK_REDUCING_ONLY")
        with self.assertRaisesRegex(PaperTradingError, "AUTHORITY_DENIED"):
            self.os.update_protection(ref, stop_price=ref_price * 0.99, source="AI")
        with self.assertRaisesRegex(PaperTradingError, "KILL_SWITCH"):
            self.buy_spot(amount=10)
        attention = self.os.attention()["items"]
        self.assertTrue(any(item["kind"] == "kill_switch" and item["severity"] == "CRITICAL" for item in attention))
        with self.assertRaisesRegex(PaperTradingError, "RECONCILIATION_FAILURE"):
            self.os.set_kill_switch("NORMAL", confirm=True)
        self.store._db.execute("UPDATE wallets SET cash_balance=cash_balance-5 WHERE cohort='primary'")
        with self.assertRaises(ConfirmationRequired):
            self.os.set_kill_switch("NORMAL")
        self.assertEqual(self.os.set_kill_switch("NORMAL", confirm=True)["level"], "NORMAL")
        resolved = self.os.attention(include_resolved=True)["items"]
        self.assertTrue(any(item["kind"] == "kill_switch" and item["status"] == "resolved" for item in resolved))

    def test_LIQUIDATION_EMERGENCY_overrides_authority_and_delay(self):
        created, _ = self.open_perp()
        ref = created["position_ref"]
        self.os.set_management_mode(ref, "PAUSED")
        liq = self.os.position(ref)["liquidation_price"]
        instrument = "fixture:perpetual:BTC_USDT"
        self.os._live_prices = lambda: {instrument: liq * 1.01}
        before = self.os.position(ref)["quantity"]
        actions = self.os.liquidation_guard()
        self.assertEqual(len(actions), 1)
        after = self.os.position(ref)
        self.assertAlmostEqual(after["quantity"], before * 0.5, places=8)
        journal = [(e["action"], e["source"]) for e in self.os.management_events(ref)]
        self.assertIn(("REDUCE", "SYSTEM"), journal)
        self.assertEqual(self.os.liquidation_guard(), [], "cooldown prevents repeated emergency reductions")
        with self.assertRaisesRegex(PaperTradingError, "AUTHORITY_DENIED"):
            self.os.reduce_position(ref, 0.5, source="SYSTEM", reason="impatient")

    def test_kill_switch_levels_gate_cycles_and_entries(self):
        config = default_experiment_config()
        config["symbols"] = ["BTCUSDT"]
        self.store.save_experiment(config)
        self.runtime.start()
        self.os.set_kill_switch("NO_NEW_ENTRIES", reason="test")
        cycle = self.runtime.run_cycle("BTCUSDT", as_of=NOW, manual=True)
        self.assertEqual(cycle["risk"]["code"], "KILL_SWITCH_NO_NEW_ENTRIES")
        self.assertEqual(cycle["kill_switch"], "NO_NEW_ENTRIES")
        self.assertIn("market_safety", cycle)
        self.os.set_kill_switch("FULL_AUTOMATION_HALT", reason="test")
        self.clock.value = NOW + timedelta(minutes=15)
        with self.assertRaisesRegex(PaperTradingError, "HALT"):
            self.runtime.run_cycle("BTCUSDT", manual=True)
        overview = self.os.safety_overview()
        self.assertEqual(overview["kill_switch"]["level"], "FULL_AUTOMATION_HALT")
        self.assertEqual(overview["hierarchy"][0], "LIQUIDATION / ACCOUNTING SAFETY")

    def test_safety_export_files(self):
        self.buy_spot(amount=10)
        import io
        import json
        import zipfile
        body, _ = self.runtime.export_bundle()
        with zipfile.ZipFile(io.BytesIO(body)) as bundle:
            names = set(bundle.namelist())
            self.assertTrue({"safety-events.csv", "execution-plans.jsonl", "market-safety-states.csv", "kill-switch.json"} <= names)
            self.assertEqual(json.loads(bundle.read("kill-switch.json"))["level"], "NORMAL")


if __name__ == "__main__":
    unittest.main()
