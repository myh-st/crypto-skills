"""Deterministic tests for the AI Portfolio Trading OS (PAPER only, fixtures/fakes, no network)."""

from __future__ import annotations

import io
import json
import sqlite3
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crypto_eval.activity import derive_attention, filter_activity
from crypto_eval.gate_account import LiveExecutionBlocked, ReadOnlyGateClient
from crypto_eval.gate_market import normalize_contract
from crypto_eval.learning import build_review, strategy_tournament, update_hypotheses
from crypto_eval.market_catalog import (
    CatalogError,
    FixtureSpotMarketDataProvider,
    GateSpotMarketDataProvider,
    MarketCatalog,
    normalize_perpetual,
    normalize_spot_pair,
)
from crypto_eval.paper_ai import AIProviderError, JevAdapter, parse_replan
from crypto_eval.paper_contracts import PaperTradingError, default_experiment_config
from crypto_eval.paper_market import FixtureFuturesMarketDataProvider, MarketDataError
from crypto_eval.paper_runtime import PaperRuntime, PaperStore
from crypto_eval.paper_server import PaperHTTPServer, PaperRequestHandler
from crypto_eval.portfolio_brain import evaluate_entry, review_portfolio
from crypto_eval.portfolio_os import ConfirmationRequired, normalize_targets
from crypto_eval.portfolio_store import default_portfolio_settings, validate_portfolio_settings
from crypto_eval.secret_store import CredentialResolver, MemorySecretStore
from crypto_eval.spot_accounting import SpotHoldingState, SpotRiskEngine, apply_buy, apply_sell

from tests.test_real_ai_live_paper import JEV_PROVIDER, jev_transport


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 28, 12, 1, 30, tzinfo=timezone.utc)
SENTINEL = "PORTFOLIO-SENTINEL-SECRET-4d3c2b1a-NOT-REAL"


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def minute_bars(start: datetime, prices: list[tuple[float, float, float, float]], volume: float = 5.0) -> list[dict]:
    bars = []
    for index, (o, h, l, c) in enumerate(prices):
        opened = start + timedelta(minutes=index)
        bars.append({
            "open_time": opened.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "close_time": (opened + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "open": o, "high": h, "low": l, "close": c, "volume": volume,
        })
    return bars


class PortfolioCase(unittest.TestCase):
    """Fresh in-memory runtime + fixture catalog per test."""

    def setUp(self) -> None:
        self.clock = Clock(NOW)
        self.store = PaperStore(":memory:")
        self.addCleanup(self.store.close)
        self.spot_path: dict[str, list[dict]] = {}
        self.perp_path: dict[str, list[dict]] = {}
        self.resolver = CredentialResolver(MemorySecretStore(), environ={})
        self.make_runtime()

    def make_runtime(self, **kwargs) -> None:
        self.catalog = kwargs.pop("catalog", None) or MarketCatalog(
            source="fixture",
            spot_provider=FixtureSpotMarketDataProvider(clock=self.clock, future_path=self.spot_path),
            clock=self.clock,
        )
        self.runtime = PaperRuntime(
            self.store,
            clock=self.clock,
            catalog=self.catalog,
            resolver=self.resolver,
            market_provider=FixtureFuturesMarketDataProvider(future_path=self.perp_path),
            **kwargs,
        )
        self.os = self.runtime.portfolio

    # helpers ------------------------------------------------------------
    def buy_spot(self, instrument="fixture:spot:ETH_USDT", amount=25.0, **extra):
        return self.os.create_order({"instrument_id": instrument, "action": "buy", "quote_amount": amount, **extra})

    def open_perp(self, instrument="fixture:perpetual:BTC_USDT", side="long", stop_pct=0.02, targets=(0.02, 0.04), **extra):
        quote = self.os.quote(instrument)
        ref = quote["best_ask"] if side == "long" else quote["best_bid"]
        sign = 1 if side == "long" else -1
        body = {
            "instrument_id": instrument,
            "action": side,
            "risk_pct": 0.01,
            "stop_price": ref * (1 - sign * stop_pct),
            "targets": [ref * (1 + sign * t) for t in targets],
            "leverage": 3,
            **extra,
        }
        return self.os.create_order(body), ref


# ---------------------------------------------------------------- catalog
class CatalogTests(PortfolioCase):
    def test_spot_pair_normalization_fields(self):
        raw = {
            "id": "ETH_USDT", "base": "ETH", "quote": "USDT", "fee": "0.2", "min_base_amount": "0.001",
            "min_quote_amount": "3", "max_base_amount": "10000", "max_quote_amount": "5000000",
            "amount_precision": 4, "precision": 2, "trade_status": "tradable",
        }
        ticker = {"last": "2690.12", "highest_bid": "2690.11", "lowest_ask": "2690.12",
                  "change_percentage": "1.5", "quote_volume": "360000000"}
        item = normalize_spot_pair(raw, ticker, refreshed_at="2026-09-28T12:00:00.000Z")
        self.assertEqual(item["instrument_id"], "gate:spot:ETH_USDT")
        self.assertEqual(item["display_symbol"], "ETH/USDT")
        self.assertAlmostEqual(item["price_tick"], 0.01)
        self.assertAlmostEqual(item["quantity_step"], 0.0001)
        self.assertEqual(item["min_notional"], 3.0)
        self.assertAlmostEqual(item["taker_fee_rate"], 0.002)
        self.assertAlmostEqual(item["change_24h"], 0.015)
        self.assertTrue(item["tradable"])
        self.assertIsNone(item["max_leverage"])
        self.assertIsNone(item["funding_rate"])
        self.assertEqual(item["liquidity"], "high")
        untradable = normalize_spot_pair({**raw, "trade_status": "untradable"}, None, refreshed_at="x")
        self.assertFalse(untradable["tradable"])
        delisting = normalize_spot_pair(
            {**raw, "delisting_time": int(NOW.timestamp()) + 3600}, None, refreshed_at="x", now=NOW
        )
        self.assertEqual(delisting["status"], "delisting")
        sell_only = normalize_spot_pair({**raw, "trade_status": "sellable"}, None, refreshed_at="x")
        self.assertEqual(sell_only["status"], "sell_only")

    def test_perpetual_normalization_and_delist(self):
        contract = normalize_contract({
            "name": "SOL_USDT", "quanto_multiplier": "1", "order_size_min": 1, "order_size_max": 100000,
            "order_price_round": "0.01", "leverage_min": "1", "leverage_max": "50", "maintenance_rate": "0.01",
            "taker_fee_rate": "0.00075", "maker_fee_rate": "-0.0001", "status": "trading", "in_delisting": False,
        })
        ticker = {"last_price": 150.0, "mark_price": 150.1, "index_price": 150.0, "funding_rate": 0.0001,
                  "change_24h_pct": -2.0, "volume_24h_quote": 1_000_000.0, "best_bid": 149.99, "best_ask": 150.01}
        item = normalize_perpetual(contract, ticker, refreshed_at="x")
        self.assertEqual(item["instrument_id"], "gate:perpetual:SOL_USDT")
        self.assertEqual(item["symbol"], "SOLUSDT")
        self.assertEqual(item["max_leverage"], 50.0)
        self.assertEqual(item["maintenance_rate"], 0.01)
        self.assertEqual(item["settle"], "USDT")
        self.assertTrue(item["tradable"])
        delisted = normalize_perpetual({**contract, "in_delisting": True}, ticker, refreshed_at="x")
        self.assertEqual(delisted["status"], "delisting")
        self.assertFalse(delisted["tradable"])

    def test_catalog_fails_closed_for_untradable_unknown_and_wrong_source(self):
        with self.assertRaisesRegex(CatalogError, "INSTRUMENT_NOT_TRADABLE"):
            self.catalog.require_tradable("fixture:spot:OLD_USDT")
        with self.assertRaisesRegex(CatalogError, "not in the exchange catalog"):
            self.catalog.require_tradable("fixture:spot:NOPE_USDT")
        with self.assertRaisesRegex(CatalogError, "different exchange"):
            self.catalog.get("gate:spot:ETH_USDT")
        with self.assertRaisesRegex(CatalogError, "invalid"):
            self.catalog.get("fixture:spot:eth_usdt")
        preview = None
        with self.assertRaisesRegex(PaperTradingError, "INSTRUMENT_NOT_TRADABLE"):
            preview = self.os.preview_order({"instrument_id": "fixture:perpetual:OLD_USDT", "action": "long",
                                             "stop_price": 1, "targets": [2]})
        self.assertIsNone(preview)

    def test_catalog_search_sort_and_freshness_with_stale_cache(self):
        listing = self.catalog.list("spot", query="eth")
        self.assertEqual(listing["instruments"][0]["base"], "ETH")
        self.assertFalse(listing["freshness"]["stale"])

        class FlakySpot(FixtureSpotMarketDataProvider):
            fail = False

            def currency_pairs(self, *, refresh=False):
                if self.fail:
                    raise MarketDataError("exchange unavailable")
                return super().currency_pairs(refresh=refresh)

        flaky = FlakySpot(clock=self.clock)
        catalog = MarketCatalog(source="fixture", spot_provider=flaky, clock=self.clock)
        self.assertTrue(catalog.require_tradable("fixture:spot:BTC_USDT")["tradable"])
        flaky.fail = True
        self.clock.value = NOW + timedelta(hours=7)
        listing = catalog.list("spot")
        self.assertTrue(listing["freshness"]["stale"])
        self.assertTrue(listing["freshness"]["fail_closed"])
        self.assertIn("unavailable", listing["freshness"]["last_error"])
        with self.assertRaisesRegex(CatalogError, "CATALOG_STALE"):
            catalog.require_tradable("fixture:spot:BTC_USDT")

    def test_gate_spot_adapter_is_get_only_on_approved_hosts_and_closed_candles(self):
        urls = []
        now_ts = int(NOW.timestamp())

        def transport(url, timeout):
            urls.append(url)
            path = urllib.parse.urlsplit(url).path.replace("/api/v4", "")
            if path == "/spot/candlesticks":
                start = now_ts - now_ts % 60 - 180
                rows = [[str(start + i * 60), "10", "101", "102", "99", "100", "1", "true"] for i in range(3)]
                rows.append([str(start + 180), "10", "101", "102", "99", "100", "1", "false"])
                return json.dumps(rows).encode()
            raise AssertionError(path)

        provider = GateSpotMarketDataProvider(transport=transport, clock=lambda: NOW)
        candles = provider.fetch_candles("BTC_USDT", "1m", bars=3, as_of=NOW)
        self.assertEqual(len(candles), 3)
        self.assertTrue(all(c["close_time"] <= NOW.strftime("%Y-%m-%dT%H:%M:%S.000Z") for c in candles))
        self.assertTrue(all(url.startswith("https://api.gateio.ws/api/v4/spot/") for url in urls))
        with self.assertRaises(MarketDataError):
            GateSpotMarketDataProvider(base_url="https://example.com/api/v4")


# ---------------------------------------------------------------- spot
class SpotAccountingTests(PortfolioCase):
    def test_average_cost_realized_unrealized_and_fees(self):
        state = SpotHoldingState()
        first = apply_buy(state, 1.0, 100.0, 0.001)
        second = apply_buy(first.holding, 1.0, 120.0, 0.001)
        self.assertAlmostEqual(second.holding.quantity, 2.0)
        self.assertAlmostEqual(second.holding.avg_cost, (100.1 + 120.12) / 2)
        self.assertAlmostEqual(second.holding.fees_paid, 0.1 + 0.12)
        sale = apply_sell(second.holding, 0.5, 130.0, 0.001)
        expected = 0.5 * 130 * (1 - 0.001) - 0.5 * second.holding.avg_cost
        self.assertAlmostEqual(sale.realized_pnl, expected)
        self.assertAlmostEqual(sale.holding.avg_cost, second.holding.avg_cost)
        self.assertAlmostEqual(sale.quote_delta, 0.5 * 130 * 0.999)
        closing = apply_sell(sale.holding, 1.5, 90.0, 0.001)
        self.assertEqual(closing.holding.quantity, 0.0)
        self.assertEqual(closing.holding.avg_cost, 0.0)
        with self.assertRaisesRegex(PaperTradingError, "INSUFFICIENT_BASE"):
            apply_sell(sale.holding, 5.0, 90.0, 0.001)

    def spot_risk(self, **overrides):
        instrument = self.catalog.get("fixture:spot:ETH_USDT")
        quote = self.os.quote("fixture:spot:ETH_USDT")
        base = dict(
            side="buy", order_type="market", instrument=instrument, quote=quote,
            settings=default_portfolio_settings(),
            wallet={"quote": "USDT", "cash_balance": 100.0, "reserved_quote": 0.0},
            holdings_value={}, holding_available=0.0, holding_quantity=0.0, quote_amount=20.0,
        )
        base.update(overrides)
        return SpotRiskEngine().evaluate(**base)

    def test_spot_risk_balances_allocation_reserve_minimum_and_staleness(self):
        self.assertTrue(self.spot_risk().allowed)
        self.assertEqual(self.spot_risk(quote_amount=150.0).code, "INSUFFICIENT_QUOTE")
        self.assertEqual(self.spot_risk(quote_amount=50.0).code, "ALLOCATION_LIMIT")
        reserve = self.spot_risk(quote_amount=30.0, settings={**default_portfolio_settings(), "spot_min_cash_reserve_pct": 0.75})
        self.assertEqual(reserve.code, "CASH_RESERVE")
        self.assertEqual(self.spot_risk(quote_amount=1.0).code, "BELOW_MIN_NOTIONAL")
        stale = dict(self.os.quote("fixture:spot:ETH_USDT"), fresh=False)
        self.assertEqual(self.spot_risk(quote=stale).code, "STALE_DATA")
        self.assertEqual(self.spot_risk(side="sell", quantity=1.0, quote_amount=None).code, "INSUFFICIENT_BASE")

    def test_spot_end_to_end_buy_partial_sell_close_and_export(self):
        buy = self.buy_spot(amount=30.0)
        self.assertEqual(buy["status"], "filled")
        ref = buy["position_ref"]
        wallet = self.os.spot_wallet()
        self.assertAlmostEqual(wallet["equity_usdt"], wallet["cash_balance_usdt"] + wallet["holdings_value_usdt"])
        self.assertLess(wallet["cash_balance_usdt"], 70.4)
        view = self.os.position(ref)
        self.assertEqual(view["market_type"], "spot")
        self.assertIsNone(view["leverage"])
        self.assertIsNone(view["liquidation_price"])
        self.assertIsNone(view["funding_usdt"])
        self.assertGreater(view["fees_usdt"], 0)
        reduced = self.os.reduce_position(ref, 0.5)
        self.assertFalse(reduced["closed"])
        holding = self.os.position(ref)
        self.assertAlmostEqual(holding["quantity"], buy["fill"]["quantity"] - reduced["result"]["filled_quantity"])
        with self.assertRaises(ConfirmationRequired) as ctx:
            self.os.close_position(ref)
        self.assertEqual(ctx.exception.code, "CONFIRM_CLOSE")
        closed = self.os.close_position(ref, confirm=True)
        self.assertTrue(closed["closed"])
        wallet = self.os.spot_wallet()
        self.assertAlmostEqual(wallet["holdings_value_usdt"], 0.0)
        self.assertAlmostEqual(
            wallet["cash_balance_usdt"], wallet["starting_balance_usdt"] + wallet["realized_pnl_usdt"], places=9
        )
        self.assertEqual(self.os.sync_reviews(), 1)
        body, _name = self.runtime.export_bundle()
        with zipfile.ZipFile(io.BytesIO(body)) as bundle:
            names = set(bundle.namelist())
            for name in ("spot-holdings.csv", "spot-fills.csv", "spot-orders.csv", "position-plans.jsonl",
                         "management-events.jsonl", "activity.csv", "post-trade-reviews.jsonl",
                         "strategy-tournament.csv", "portfolio-snapshots.csv"):
                self.assertIn(name, names)
            manifest = json.loads(bundle.read("manifest.json"))
            self.assertFalse(manifest["portfolio_os"]["spot_real_writes"])
            self.assertEqual(manifest["portfolio_os"]["row_counts"]["spot_fills"], 3)
            self.assertFalse(manifest["secret_scan"]["credential_values_found"])
            self.assertIn("spot-fills.csv", manifest["file_sha256"])

    def test_spot_limit_partial_fills_and_cancel_releases_reservation(self):
        quote = self.os.quote("fixture:spot:SOL_USDT")
        limit = round(quote["mid_price"] * 0.99, 2)
        order = self.os.create_order({"instrument_id": "fixture:spot:SOL_USDT", "action": "buy",
                                      "order_type": "limit", "limit_price": limit, "quantity": 0.2})
        self.assertEqual(order["status"], "pending")
        self.assertGreater(self.os.spot_wallet()["reserved_quote_usdt"], 0)
        start = NOW.replace(second=0) + timedelta(minutes=1)
        self.spot_path["SOLUSDT"] = minute_bars(start, [(limit * 1.01, limit * 1.01, limit * 0.999, limit)], volume=0.2)
        self.clock.value = start + timedelta(minutes=2)
        self.os.monitor_spot()
        view = next(o for o in self.os.list_orders() if o["order_ref"] == order["order_ref"])
        self.assertEqual(view["status"], "partially_filled")
        self.assertAlmostEqual(view["filled_quantity"], 0.05)
        self.os.cancel_order(order["order_ref"])
        self.assertAlmostEqual(self.os.spot_wallet()["reserved_quote_usdt"], 0.0)
        holding = self.os.list_positions()[0]
        self.assertAlmostEqual(holding["quantity"], 0.05)

    def test_spot_plan_stop_needs_confirmed_closes_and_never_sells_core(self):
        quote = self.os.quote("fixture:spot:ETH_USDT")
        mid = quote["mid_price"]
        buy = self.buy_spot(amount=20.0, stop_price=mid * 0.98, targets=[mid * 1.2])
        ref = buy["position_ref"]
        quantity = self.os.position(ref)["quantity"]
        self.os.set_core_quantity(ref, core_fraction=0.5)
        start = NOW.replace(second=0) + timedelta(minutes=1)
        # A wick through the stop that closes back above it never sells a Spot holding.
        self.spot_path["ETHUSDT"] = minute_bars(start, [
            (mid, mid * 1.001, mid * 0.90, mid),
            (mid, mid * 1.001, mid * 0.97, mid * 0.97),
            (mid * 0.97, mid * 0.975, mid * 0.96, mid * 0.965),
        ])
        self.clock.value = start + timedelta(minutes=1)
        self.os.monitor_spot()
        self.assertEqual(self.os.position(ref)["quantity"], quantity)
        self.clock.value = start + timedelta(minutes=4)
        self.os.monitor_spot()
        view = self.os.position(ref)
        self.assertEqual(view["status"], "open")
        self.assertAlmostEqual(view["quantity"], quantity * 0.5, places=9)
        actions = [e["action"] for e in self.os.management_events(ref)]
        self.assertIn("PLAN_STOP", actions)
        self.assertIn("CORE_ALLOCATION", actions)


# ---------------------------------------------------------------- perpetual orders/positions
class PerpetualOrderTests(PortfolioCase):
    def test_preview_is_server_authoritative(self):
        quote = self.os.quote("fixture:perpetual:BTC_USDT")
        preview = self.os.preview_order({
            "instrument_id": "fixture:perpetual:BTC_USDT", "action": "long", "risk_pct": 0.01,
            "stop_price": quote["best_ask"] * 0.98, "targets": [quote["best_ask"] * 1.03], "leverage": 3,
        })
        self.assertTrue(preview["allowed"])
        for field in ("quantity", "notional_usdt", "margin_usdt", "max_loss_usdt", "estimated_entry_fee_usdt",
                      "estimated_slippage_usdt", "liquidation_price", "liquidation_buffer_pct"):
            self.assertIsNotNone(preview[field], field)
        self.assertLessEqual(preview["max_loss_usdt"], 100 * 0.01 * 1.000001)
        self.assertTrue(preview["server_authoritative"])
        with self.assertRaisesRegex(PaperTradingError, "derived server-side"):
            self.os.preview_order({"instrument_id": "fixture:perpetual:BTC_USDT", "action": "long",
                                   "quantity": 5, "stop_price": 1, "targets": [2]})
        with self.assertRaisesRegex(PaperTradingError, "manual limit"):
            self.os.preview_order({"instrument_id": "fixture:perpetual:BTC_USDT", "action": "long", "risk_pct": 0.05,
                                   "stop_price": quote["best_ask"] * 0.98, "targets": [quote["best_ask"] * 1.03]})

    def test_risk_rejection_creates_no_order_or_fill(self):
        created, ref = self.open_perp(stop_pct=0.3)
        self.assertFalse(created["accepted"])
        self.assertEqual(created["code"], "STOP_DISTANCE")
        self.assertEqual(self.store._query("SELECT COUNT(*) AS n FROM orders")[0]["n"], 0)
        self.assertEqual(self.store._query("SELECT COUNT(*) AS n FROM fills")[0]["n"], 0)
        self.assertEqual(self.store.risk_events("EXP-001")[0]["code"], "STOP_DISTANCE")

    def test_manual_fill_uses_runtime_path_with_targets_and_user_authority(self):
        created, _ref = self.open_perp(targets=(0.02, 0.04, 0.06))
        self.assertEqual(created["status"], "filled")
        view = self.os.position(created["position_ref"])
        self.assertEqual(view["source"], "USER")
        self.assertEqual(view["source_arm"], "manual")
        self.assertEqual(view["management_mode"], "RECOMMEND_ONLY")
        self.assertEqual(len(view["targets"]), 3)
        self.assertEqual(sum(t["fraction"] for t in view["targets"]), 1.0)
        self.assertEqual(view["target_price"], view["targets"][-1]["price"])
        self.assertEqual(self.store._query("SELECT COUNT(*) AS n FROM fills")[0]["n"], 1)

    def test_limit_pending_amend_and_cancel(self):
        quote = self.os.quote("fixture:perpetual:ETH_USDT")
        limit = quote["best_bid"] * 0.995
        order = self.os.create_order({
            "instrument_id": "fixture:perpetual:ETH_USDT", "action": "long", "order_type": "limit",
            "limit_price": limit, "risk_pct": 0.01, "stop_price": limit * 0.98, "targets": [limit * 1.03],
        })
        self.assertEqual(order["status"], "pending")
        amended = self.os.amend_order(order["order_ref"], {"limit_price": limit * 0.99, "stop_price": limit * 0.97})
        self.assertEqual(amended["status"], "pending")
        self.assertEqual(amended["amend_mode"], "cancel_replace")
        statuses = {o["order_ref"]: o["status"] for o in self.os.list_orders()}
        self.assertEqual(statuses[order["order_ref"]], "cancelled")
        self.assertEqual(statuses[amended["order_ref"]], "pending")
        self.os.cancel_order(amended["order_ref"])
        with self.assertRaisesRegex(PaperTradingError, "use Close"):
            self.os.cancel_order(amended["order_ref"])

    def test_manual_pending_orders_survive_ai_pause(self):
        quote = self.os.quote("fixture:perpetual:ETH_USDT")
        limit = quote["best_bid"] * 0.995
        order = self.os.create_order({
            "instrument_id": "fixture:perpetual:ETH_USDT", "action": "long", "order_type": "limit",
            "limit_price": limit, "risk_pct": 0.01, "stop_price": limit * 0.98, "targets": [limit * 1.03],
        })
        self.store.set_status("running")
        self.store.set_status("paused")
        status = next(o for o in self.os.list_orders() if o["order_ref"] == order["order_ref"])["status"]
        self.assertEqual(status, "pending")

    def test_protection_tighten_fast_and_risk_increase_requires_confirmation(self):
        created, ref = self.open_perp()
        position = created["position_ref"]
        tightened = self.os.update_protection(position, stop_price=ref * 0.99)
        self.assertLess(tightened["risk_after_usdt"], tightened["risk_before_usdt"])
        self.assertIsInstance(tightened["audit_event_id"], int)
        with self.assertRaises(ConfirmationRequired) as ctx:
            self.os.update_protection(position, stop_price=ref * 0.975)
        self.assertEqual(ctx.exception.code, "CONFIRM_RISK_INCREASE")
        widened = self.os.update_protection(position, stop_price=ref * 0.975, confirm_risk_increase=True)
        self.assertTrue(widened["risk_increased"])
        targets = self.os.update_protection(position, targets=[ref * 1.05, ref * 1.07])
        self.assertEqual([round(t["price"], 6) for t in targets["new"]["targets"]], [round(ref * 1.05, 6), round(ref * 1.07, 6)])
        row = self.store._query("SELECT stop_price, target_price FROM positions")[0]
        self.assertAlmostEqual(row["stop_price"], ref * 0.975)
        self.assertAlmostEqual(row["target_price"], ref * 1.07)

    def test_protection_rejects_wrong_side_and_liquidation_crossing(self):
        created, ref = self.open_perp()
        position = created["position_ref"]
        with self.assertRaisesRegex(PaperTradingError, "loss side"):
            self.os.update_protection(position, stop_price=ref * 1.01)
        liq = self.os.position(position)["liquidation_price"]
        with self.assertRaisesRegex(PaperTradingError, "LIQUIDATION_BEFORE_STOP|STOP_DISTANCE"):
            self.os.update_protection(position, stop_price=liq * 0.999, confirm_risk_increase=True)
        with self.assertRaisesRegex(PaperTradingError, "profit side"):
            self.os.update_protection(position, targets=[ref * 0.9])

    def test_reduce_close_confirmation_and_idempotency(self):
        created, _ref = self.open_perp()
        position = created["position_ref"]
        quantity = self.os.position(position)["quantity"]
        first = self.os.reduce_position(position, 0.25, request_id="reduce-req-0001")
        replay = self.os.reduce_position(position, 0.25, request_id="reduce-req-0001")
        self.assertTrue(replay["idempotent_replay"])
        self.assertAlmostEqual(self.os.position(position)["quantity"], quantity * 0.75, places=8)
        with self.assertRaisesRegex(PaperTradingError, "different request"):
            self.os.reduce_position(position, 0.3, request_id="reduce-req-0001")
        with self.assertRaises(ConfirmationRequired) as ctx:
            self.os.reduce_position(position, 0.75)
        self.assertEqual(ctx.exception.code, "CONFIRM_LARGE_REDUCE")
        self.assertIn("instrument", ctx.exception.details)
        self.clock.value = NOW + timedelta(seconds=5)
        closed = self.os.close_position(position, confirm=True)
        self.assertTrue(closed["closed"])
        self.assertEqual(first["source"], "USER")
        with self.assertRaisesRegex(PaperTradingError, "POSITION_CLOSED"):
            self.os.close_position(position, confirm=True)

    def test_order_idempotency(self):
        quote = self.os.quote("fixture:spot:BTC_USDT")
        body = {"client_request_id": "order-req-0001", "instrument_id": "fixture:spot:BTC_USDT", "action": "buy", "quote_amount": 20}
        first = self.os.create_order(body)
        again = self.os.create_order(body)
        self.assertTrue(again["idempotent_replay"])
        self.assertEqual(first["order_ref"], again["order_ref"])
        self.assertEqual(len([o for o in self.os.list_orders() if o["market_type"] == "spot"]), 1)
        self.assertGreater(quote["mid_price"], 0)

    def test_partial_take_profit_then_stop_in_monitor(self):
        created, ref = self.open_perp(targets=(0.01, 0.03))
        position_id = created["position_ref"].split(":", 1)[1]
        opened = self.store._query("SELECT * FROM positions WHERE position_id=?", (position_id,))[0]
        start = datetime.fromisoformat(opened["opened_at"].replace("Z", "+00:00")).replace(second=0) + timedelta(minutes=1)
        entry = opened["entry_price"]
        stop = opened["stop_price"]
        self.perp_path["BTCUSDT"] = minute_bars(start, [
            (entry, entry * 1.012, entry * 0.999, entry * 1.005),
            (entry * 1.005, entry * 1.006, stop * 0.999, stop),
        ])
        self.clock.value = start + timedelta(minutes=3)
        events = self.runtime.monitor_once()
        self.assertEqual([e["event"] for e in events], ["target_partial", "stop"])
        view = self.os.position(created["position_ref"])
        self.assertEqual(view["status"], "closed")
        self.assertTrue(view["targets"][0]["hit"])
        actions = [e["action"] for e in self.os.management_events(created["position_ref"])]
        self.assertIn("TARGET_PARTIAL", actions)


# ---------------------------------------------------------------- authority
class AuthorityTests(PortfolioCase):
    def test_modes_gate_ai_mutations_and_ai_cannot_change_authority(self):
        created, ref = self.open_perp()
        position = created["position_ref"]
        for mode in ("RECOMMEND_ONLY", "MANUAL_OVERRIDE", "PAUSED"):
            if mode != "RECOMMEND_ONLY":
                self.os.set_management_mode(position, mode)
            with self.assertRaisesRegex(PaperTradingError, "AUTHORITY_DENIED"):
                self.os.update_protection(position, stop_price=ref * 0.99, source="AI")
            with self.assertRaisesRegex(PaperTradingError, "AUTHORITY_DENIED"):
                self.os.reduce_position(position, 0.25, source="AI")
        with self.assertRaisesRegex(PaperTradingError, "AUTHORITY_DENIED"):
            self.os.set_management_mode(position, "AUTO_PAPER", source="AI")
        self.os.set_management_mode(position, "MANUAL_OVERRIDE")
        with self.assertRaises(ConfirmationRequired) as ctx:
            self.os.set_management_mode(position, "AUTO_PAPER")
        self.assertEqual(ctx.exception.code, "CONFIRM_RETURN_TO_AI")
        self.os.set_management_mode(position, "AUTO_PAPER", confirm=True)
        tightened = self.os.update_protection(position, stop_price=ref * 0.99, source="AI")
        self.assertEqual(tightened["source"], "AI")
        with self.assertRaisesRegex(PaperTradingError, "AI_RISK_INCREASE_FORBIDDEN"):
            self.os.update_protection(position, stop_price=ref * 0.98, source="AI")
        self.os.set_automation({"ai_management_paused": True})
        with self.assertRaisesRegex(PaperTradingError, "AUTHORITY_DENIED"):
            self.os.update_protection(position, stop_price=ref * 0.995, source="AI")
        with self.assertRaises(ConfirmationRequired):
            self.os.set_automation({"ai_management_paused": False})
        self.os.set_automation({"ai_management_paused": False}, confirm=True)
        journal = [(e["action"], e["source"]) for e in self.os.management_events(position)]
        self.assertIn(("MODE_CHANGE", "USER"), journal)
        self.assertIn(("PROTECTION_UPDATE", "AI"), journal)

    def test_ai_positions_default_to_auto_paper(self):
        config = default_experiment_config()
        config["symbols"] = ["BTCUSDT"]
        config["evaluation_arms"] = ["quant"]
        config["primary_arm"] = "quant"
        self.store.save_experiment(config)
        self.runtime.start()
        self.runtime.run_cycle("BTCUSDT", as_of=NOW, manual=True)
        positions = [p for p in self.os.list_positions() if p["market_type"] == "perpetual"]
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["source"], "AI")
        self.assertEqual(positions[0]["management_mode"], "AUTO_PAPER")
        self.assertEqual(self.os.management_events(positions[0]["position_ref"])[0]["action"], "MODE_ASSIGNED")


# ---------------------------------------------------------------- re-plan
class ReplanTests(PortfolioCase):
    def test_structured_replan_schema_validation(self):
        context = {"position": {"side": "long", "mark_price": 100.0}}
        ok = parse_replan({"action": "adjust", "stop_price": 98.0, "target_prices": [110.0, 105.0],
                           "reduce_fraction": None, "thesis_status": "intact",
                           "reason_codes": ["TIGHTEN_RISK"], "summary": "tighter"}, context)
        self.assertEqual(ok["target_prices"], [105.0, 110.0])
        bad = [
            {"action": "adjust", "stop_price": 101.0},
            {"action": "adjust", "target_prices": [99.0]},
            {"action": "reduce", "reduce_fraction": 1.5},
            {"action": "adjust", "reason_codes": ["MADE_UP"]},
            {"action": "close", "stop_price": 95.0},
        ]
        for patch in bad:
            content = {"action": "hold", "stop_price": None, "target_prices": [], "reduce_fraction": None,
                       "thesis_status": "intact", "reason_codes": [], "summary": "", **patch}
            with self.assertRaises(AIProviderError):
                parse_replan(content, context)

    def test_diff_apply_edit_reject(self):
        created, ref = self.open_perp()
        position = created["position_ref"]
        proposal = self.os.request_replan(position, intent="tighten_risk")
        self.assertEqual(proposal["status"], "proposed")
        self.assertLess(proposal["risk_after_usdt"], proposal["risk_before_usdt"])
        self.assertIn("stop", proposal["changes"])
        self.assertGreater(proposal["proposed"]["stop_price"], proposal["current"]["stop_price"])
        for key in ("targets", "remaining_pct", "exposure_pct", "risk_usdt"):
            self.assertIn(key, proposal["current"])
            self.assertIn(key, proposal["proposed"])
        self.assertIn("TIGHTEN_RISK", proposal["reason_codes"])
        self.assertNotIn("reasoning", json.dumps(proposal).lower().replace("reasoning_effort", ""))
        applied = self.os.apply_replan(proposal["proposal_id"])
        self.assertEqual(applied["status"], "applied")
        self.assertAlmostEqual(self.os.position(position)["stop_price"], proposal["proposed"]["stop_price"])
        second = self.os.request_replan(position, intent="reduce_exposure")
        self.assertEqual(self.os.reject_replan(second["proposal_id"])["status"], "rejected")
        third = self.os.request_replan(position, intent="reduce_exposure")
        edited = self.os.apply_replan(third["proposal_id"], edits={"reduce_fraction": 0.25})
        self.assertEqual(edited["status"], "applied")
        self.assertTrue(edited["decision"]["edited"])
        with self.assertRaisesRegex(PaperTradingError, "PROPOSAL_NOT_PENDING"):
            self.os.apply_replan(third["proposal_id"])

    def test_proposal_goes_stale_when_position_changes(self):
        created, ref = self.open_perp()
        position = created["position_ref"]
        proposal = self.os.request_replan(position, intent="tighten_risk")
        self.os.update_protection(position, stop_price=ref * 0.985)
        with self.assertRaisesRegex(PaperTradingError, "STALE_PROPOSAL"):
            self.os.apply_replan(proposal["proposal_id"])
        self.assertEqual(self.os.list_replans(position_ref=position)[0]["status"], "superseded")

    def test_stale_market_blocks_replan_without_mutation(self):
        created, ref = self.open_perp()

        class StaleCatalog(MarketCatalog):
            def quote(self, instrument):
                value = super().quote(instrument)
                value["fresh"] = False
                return value

        self.make_runtime(catalog=StaleCatalog(source="fixture", clock=self.clock))
        before = self.store._query("SELECT stop_price, quantity FROM positions")[0]
        proposal = self.os.request_replan(created["position_ref"], intent="tighten_risk")
        self.assertEqual(proposal["status"], "blocked")
        self.assertEqual(proposal["code"], "STALE_DATA")
        after = self.store._query("SELECT stop_price, quantity FROM positions")[0]
        self.assertEqual(dict(before), dict(after))
        preview = self.os.preview_order({"instrument_id": "fixture:perpetual:ETH_USDT", "action": "long",
                                         "stop_price": 1.0, "targets": [2.0]})
        self.assertFalse(preview["allowed"])
        self.assertEqual(preview["code"], "STALE_DATA")

    def test_budget_guard_blocks_paid_replan_calls_before_transport(self):
        created, _ref = self.open_perp()
        calls: list = []
        self.store.save_provider(JEV_PROVIDER)
        config = self.store.experiment()["config"]
        config["jev_provider_id"] = JEV_PROVIDER["provider_id"]
        config["gpt_escalation_enabled"] = False
        self.store.save_experiment(config)
        jev = JevAdapter(JEV_PROVIDER, resolver=self.resolver, transport=jev_transport(calls))
        self.make_runtime(jev_provider=jev)
        proposal = self.os.request_replan(created["position_ref"], intent="tighten_risk")
        self.assertEqual(calls, [])
        self.assertEqual(proposal["ai"]["jev"], "budget_blocked")
        self.assertIn("AI_BUDGET_BLOCK", proposal["reason_codes"])
        self.assertEqual(proposal["source"], "deterministic")

    def test_recommend_only_review_never_auto_applies(self):
        created, _ref = self.open_perp(side="short")
        self.store.set_status("running")
        self.store._db.execute("UPDATE position_meta SET next_ai_review_at='2020-01-01T00:00:00.000Z'")
        before = dict(self.store._query("SELECT stop_price, quantity, status FROM positions")[0])
        results = self.os.review_positions()
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["auto_applied"])
        self.assertEqual(dict(self.store._query("SELECT stop_price, quantity, status FROM positions")[0]), before)
        self.assertEqual(self.os.list_replans(status="proposed")[0]["position_ref"], created["position_ref"])

    def test_auto_paper_review_applies_permitted_change_and_journals(self):
        self.os.update_settings({"default_user_management_mode": "AUTO_PAPER"})
        created, _ref = self.open_perp(side="short")
        self.store.set_status("running")
        self.store._db.execute("UPDATE position_meta SET next_ai_review_at='2020-01-01T00:00:00.000Z'")
        results = self.os.review_positions()
        self.assertTrue(results[0]["auto_applied"])
        self.assertEqual(results[0]["triggers"], ["management_candle"])
        view = self.os.position(created["position_ref"])
        self.assertEqual(view["status"], "closed")
        actions = [(e["action"], e["source"]) for e in self.os.management_events(created["position_ref"])]
        self.assertIn(("CLOSE", "AI"), actions)
        self.assertIn(("REPLAN_APPLIED", "AI"), actions)
        self.assertEqual(self.os.review_positions(), [])

    def test_paused_positions_are_not_reviewed(self):
        created, _ref = self.open_perp(side="short")
        self.os.set_management_mode(created["position_ref"], "PAUSED")
        self.store.set_status("running")
        self.store._db.execute("UPDATE position_meta SET next_ai_review_at='2020-01-01T00:00:00.000Z'")
        self.assertEqual(self.os.review_positions(), [])


# ---------------------------------------------------------------- portfolio brain
class PortfolioBrainTests(unittest.TestCase):
    settings = default_portfolio_settings()["brain"]

    def state(self, exposures, **extra):
        return {"equity_usdt": 100.0, "drawdown": 0.0, "max_drawdown_stop": 0.15,
                "spot_cash_usdt": 80.0, "spot_equity_usdt": 100.0, "exposures": exposures, **extra}

    def test_concentration_block(self):
        decision = evaluate_entry(
            {"base": "SOL", "side": "long", "market_type": "perpetual", "risk_usdt": 1.0, "notional_usdt": 50},
            self.state([{"base": "SOL", "side": "long", "risk_usdt": 2.9, "notional_usdt": 100}]), self.settings,
        )
        self.assertEqual(decision["action"], "BLOCK_CONCENTRATION")
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["size_multiplier"], 0.0)

    def test_correlated_alt_exposure_blocks_good_avax_long_and_resizes(self):
        exposures = [
            {"base": "SOL", "side": "long", "risk_usdt": 1.5, "notional_usdt": 100},
            {"base": "SUI", "side": "long", "risk_usdt": 1.5, "notional_usdt": 100},
            {"base": "SEI", "side": "long", "risk_usdt": 0.9, "notional_usdt": 100},
        ]
        blocked = evaluate_entry({"base": "AVAX", "side": "long", "market_type": "perpetual", "risk_usdt": 1.0,
                                  "notional_usdt": 50}, self.state(exposures), self.settings)
        self.assertEqual(blocked["action"], "BLOCK_CORRELATED_EXPOSURE")
        resized = evaluate_entry({"base": "AVAX", "side": "long", "market_type": "perpetual", "risk_usdt": 1.5,
                                  "notional_usdt": 50}, self.state(exposures[:2]), self.settings)
        self.assertEqual(resized["action"], "RESIZE")
        self.assertAlmostEqual(resized["size_multiplier"], 1.0 / 1.5, places=5)
        self.assertIn("CORRELATED_ALT_LONG_RESIZE", resized["reason_codes"])
        btc = evaluate_entry({"base": "BTC", "side": "long", "market_type": "perpetual", "risk_usdt": 1.0,
                              "notional_usdt": 50}, self.state(exposures[:2]), self.settings)
        self.assertEqual(btc["action"], "ALLOW")
        short = evaluate_entry({"base": "AVAX", "side": "short", "market_type": "perpetual", "risk_usdt": 1.0,
                                "notional_usdt": 50}, self.state(exposures), self.settings)
        self.assertTrue(short["allowed"])

    def test_drawdown_de_risk_hold_cash_and_spot_cash_reserve(self):
        candidate = {"base": "BTC", "side": "long", "market_type": "perpetual", "risk_usdt": 1.0, "notional_usdt": 50}
        de_risk = evaluate_entry(candidate, self.state([], drawdown=0.08), self.settings)
        self.assertEqual(de_risk["action"], "DE_RISK")
        self.assertEqual(de_risk["size_multiplier"], 0.5)
        hold = evaluate_entry(candidate, self.state([], drawdown=0.14), self.settings)
        self.assertEqual(hold["action"], "HOLD_CASH")
        spot = evaluate_entry({"base": "ETH", "side": "long", "market_type": "spot", "risk_usdt": 2.0,
                               "notional_usdt": 75}, self.state([]), self.settings)
        self.assertEqual(spot["action"], "HOLD_CASH")
        prefer = evaluate_entry({**candidate, "funding_rate": 0.001}, self.state([]), self.settings)
        self.assertIn("PREFER_SPOT", prefer["advisories"])

    def test_portfolio_review_is_typed_and_non_mutating(self):
        review = review_portfolio(
            [{"position_ref": "perp:x", "symbol": "SOLUSDT", "market_type": "perpetual", "side": "long",
              "r_multiple": 2.0, "stop_price": 95.0, "entry_price": 100.0}],
            self.state([{"base": "SOL", "side": "long", "risk_usdt": 5.0, "notional_usdt": 100}]), self.settings,
        )
        actions = {item["action"] for item in review["actions"]}
        self.assertIn("PROTECT_PROFIT", actions)
        self.assertIn("REDUCE_CONCENTRATION", actions)
        self.assertFalse(review["mutates_accounts"])


class BrainIntegrationTests(PortfolioCase):
    def test_brain_never_bypasses_risk_engine_and_cycle_records_hybrid_brain_arm(self):
        created, _ref = self.open_perp(stop_pct=0.3)
        self.assertEqual(created["code"], "STOP_DISTANCE")
        self.runtime.start()
        cycle = self.runtime.run_cycle("BTCUSDT", as_of=NOW, manual=True)
        self.assertIn("hybrid_brain", cycle["arms"])
        self.assertIsNotNone(cycle["portfolio_brain"])
        self.assertIn(cycle["portfolio_brain"]["action"], {"ALLOW", "RESIZE", "DE_RISK"})
        self.assertTrue(cycle["portfolio_brain"]["risk_engine_authoritative"])
        cohorts = {row["cohort"] for row in self.store._query("SELECT cohort FROM positions")}
        self.assertIn("arm-hybrid_brain", cohorts)
        tournament = self.os.tournament()
        self.assertIn("hybrid_brain", [row["arm"] for row in tournament["arms"]])
        self.assertIn("promotion_rule", tournament)
        self.assertFalse(tournament["causal_claims"])

    def test_brain_block_reaches_primary_risk_summary(self):
        self.os.update_settings({"brain": {"max_asset_risk_pct": 0.001}})
        config = default_experiment_config()
        config["symbols"] = ["BTCUSDT"]
        self.store.save_experiment(config)
        self.runtime.start()
        cycle = self.runtime.run_cycle("BTCUSDT", as_of=NOW, manual=True)
        self.assertEqual(cycle["risk"]["code"], "PORTFOLIO_BRAIN_BLOCK")
        primary = self.store._query("SELECT COUNT(*) AS n FROM positions WHERE cohort='primary'")[0]["n"]
        self.assertEqual(primary, 0)
        arms = self.store._query("SELECT COUNT(*) AS n FROM positions WHERE cohort='arm-quant'")[0]["n"]
        self.assertEqual(arms, 1)

    def test_automation_new_entry_pause_and_emergency_stop(self):
        self.os.set_automation({"new_entries_paused": True})
        config = default_experiment_config()
        config["symbols"] = ["BTCUSDT"]
        self.store.save_experiment(config)
        self.runtime.start()
        cycle = self.runtime.run_cycle("BTCUSDT", as_of=NOW, manual=True)
        self.assertEqual(cycle["risk"]["code"], "AUTOMATION_NEW_ENTRIES_PAUSED")
        created, _ref = self.open_perp()
        self.assertEqual(created["status"], "filled")
        self.os.set_automation({"emergency_stop": True})
        with self.assertRaisesRegex(PaperTradingError, "EMERGENCY_STOP"):
            self.buy_spot()
        reduced = self.os.reduce_position(created["position_ref"], 0.25)
        self.assertFalse(reduced["closed"])


class AiSpotEntryTests(PortfolioCase):
    def start_cycle(self):
        config = default_experiment_config()
        config["symbols"] = ["ETHUSDT"]
        self.store.save_experiment(config)
        self.runtime.start()
        return self.runtime.run_cycle("ETHUSDT", as_of=NOW, manual=True)

    def test_disabled_by_default_and_prefer_spot_trigger_needs_advisory(self):
        cycle = self.start_cycle()
        self.assertNotIn("spot_execution", cycle)
        self.assertEqual(self.os.spot_holdings(), [])

    def test_ai_spot_entry_is_ai_owned_brain_gated_and_idempotent(self):
        self.os.update_settings({"ai_spot": {"enabled": True, "trigger": "all_long", "allocation_pct": 0.1}})
        cycle = self.start_cycle()
        self.assertTrue(cycle["spot_execution"]["accepted"], cycle["spot_execution"])
        holdings = self.os.list_positions()
        spot = [p for p in holdings if p["market_type"] == "spot"]
        self.assertEqual(len(spot), 1)
        self.assertEqual(spot[0]["source"], "AI")
        self.assertEqual(spot[0]["management_mode"], "AUTO_PAPER")
        self.assertIsNotNone(spot[0]["stop_price"])
        self.assertAlmostEqual(spot[0]["current_value_usdt"], 10.0, delta=0.5)
        again = self.runtime.run_cycle("ETHUSDT", as_of=NOW, manual=True)
        self.assertTrue(again["duplicate"])
        self.assertEqual(len(self.os.spot_holdings()), 1)
        orders = [o for o in self.os.list_orders() if o["market_type"] == "spot"]
        self.assertEqual([o["source"] for o in orders], ["AI"])

    def test_ai_spot_entry_respects_new_entry_pause_and_brain(self):
        self.os.update_settings({"ai_spot": {"enabled": True, "trigger": "all_long", "allocation_pct": 0.3},
                                 "brain": {"hold_cash_min_pct": 0.8}})
        cycle = self.start_cycle()
        self.assertFalse(cycle["spot_execution"]["accepted"])
        self.assertEqual(cycle["spot_execution"]["code"], "PORTFOLIO_BRAIN_BLOCK")
        self.assertEqual(self.os.spot_holdings(), [])
        with self.assertRaisesRegex(PaperTradingError, "trigger"):
            self.os.update_settings({"ai_spot": {"trigger": "always"}})


# ---------------------------------------------------------------- activity / attention
class ActivityAttentionTests(PortfolioCase):
    def test_activity_is_human_readable_filterable_and_not_tick_spam(self):
        config = default_experiment_config()
        config["symbols"] = ["BTCUSDT"]
        self.store.save_experiment(config)
        self.runtime.start()
        self.runtime.run_cycle("BTCUSDT", as_of=NOW, manual=True)
        self.buy_spot()
        events = self.os.activity(limit=200)["events"]
        sources = {e["source"] for e in events}
        categories = {e["category"] for e in events}
        self.assertTrue({"AI", "USER", "SYSTEM"} <= sources)
        self.assertTrue({"SIGNAL", "DECISION", "RISK", "FILL", "ORDER"} <= categories)
        self.assertTrue(all(e["schema_version"] == "activity-event.v1" for e in events))
        self.assertFalse(any("usage" in e["title"].lower() or "token" in e["title"].lower() for e in events))
        spot_only = self.os.activity(market_type="spot")["events"]
        self.assertTrue(spot_only and all(e["market_type"] == "spot" for e in spot_only))
        user_only = self.os.activity(source="USER")["events"]
        self.assertTrue(all(e["source"] == "USER" for e in user_only))
        self.assertEqual(len(filter_activity(events, symbol="BTCUSDT", category="FILL")), 1)

    def test_attention_dedupes_updates_and_resolves(self):
        created, ref = self.open_perp()
        position = created["position_ref"]
        self.os.update_protection(position, stop_price=ref * 0.996)
        first = self.os.attention()
        near = [item for item in first["items"] if item["kind"] == "near_stop"]
        self.assertEqual(len(near), 1)
        self.assertEqual(near[0]["severity"], "ACTION")
        second = self.os.attention()
        near_again = [item for item in second["items"] if item["kind"] == "near_stop"]
        self.assertEqual(len(near_again), 1)
        self.assertEqual(near_again[0]["attention_id"], near[0]["attention_id"])
        # An unchanged condition re-evaluated is not a new occurrence ("seen N×" counts changes).
        self.assertEqual(near_again[0]["occurrences"], near[0]["occurrences"])
        self.os.update_protection(position, stop_price=ref * 0.985, confirm_risk_increase=True)
        third = self.os.attention(include_resolved=True)
        resolved = [item for item in third["items"] if item["attention_id"] == near[0]["attention_id"]][0]
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["resolution"], "condition_cleared")
        info = [item for item in third["items"] if item["kind"] == "info" and item["status"] == "open"]
        if info:
            self.assertEqual(self.os.acknowledge_attention(info[0]["attention_id"])["status"], "resolved")

    def test_attention_budget_emergency_and_pending_proposal(self):
        settings = default_portfolio_settings()
        items = derive_attention(
            {
                "positions": [], "experiment": {"status": "stopped", "config": {"market_data_mode": "fixture"}},
                "pending_proposals": [{"proposal_id": "rp-1", "position_ref": "perp:x", "symbol": "SOLUSDT",
                                       "headline": "Tighten stop", "reason_codes": ["TIGHTEN_RISK"]}],
                "pending_orders": [], "budget_status": {"utilization_today": 0.85, "exhausted": False},
                "concentration": [], "recent_info": [],
            },
            {**settings, "automation": {**settings["automation"], "emergency_stop": True}},
            NOW,
        )
        kinds = {(item["kind"], item["severity"]) for item in items}
        self.assertIn(("budget", "ACTION"), kinds)
        self.assertIn(("automation", "ACTION"), kinds)
        self.assertIn(("replan", "ACTION"), kinds)
        stale = derive_attention(
            {"positions": [{"position_ref": "perp:x", "market_type": "perpetual", "symbol": "BTCUSDT"}],
             "experiment": {"status": "running", "config": {"market_data_mode": "gate_usdt"}},
             "market_stream": {"state": "STALE"}},
            settings, NOW,
        )
        self.assertEqual(stale[0]["severity"], "CRITICAL")


# ---------------------------------------------------------------- learning
class LearningTests(unittest.TestCase):
    base = {
        "position_ref": "perp:x", "market_type": "perpetual", "symbol": "SOLUSDT", "side": "long",
        "entry_price": 100.0, "opened_quantity": 1.0, "initial_risk_usdt": 2.0,
    }

    def test_review_tags_outcome_and_hypothesis(self):
        tight = build_review({**self.base, "realized_pnl": -2.0, "exit_reason": "stop",
                              "path_bars": [{"high": 101.5, "low": 97.9, "close": 98.0}]})
        self.assertEqual(tight["outcome"], "LOSS")
        self.assertIn("STOP_TOO_TIGHT", tight["tags"])
        self.assertIsNotNone(tight["hypothesis"])
        self.assertFalse(tight["auto_applied_to_strategy"])
        bad = build_review({**self.base, "realized_pnl": -2.0, "exit_reason": "stop",
                            "path_bars": [{"high": 100.2, "low": 97.9, "close": 98.0}], "funding_paid": 0.5})
        self.assertIn("BAD_DIRECTION", bad["tags"])
        self.assertIn("FUNDING_DRAG", bad["tags"])
        helped = build_review({**self.base, "realized_pnl": 1.0, "realized_gross": 1.2, "exit_reason": "user_close",
                               "counterfactual_gross_pnl": -2.0, "override_source": "USER"})
        self.assertIn("HUMAN_OVERRIDE_HELPED", helped["tags"])
        hurt = build_review({**self.base, "realized_pnl": 0.5, "realized_gross": 0.6, "exit_reason": "ai_close",
                             "counterfactual_gross_pnl": 4.0, "override_source": "AI"})
        self.assertIn("AI_OVERRIDE_HURT", hurt["tags"])
        unavailable = build_review({**self.base, "realized_pnl": 0.0, "exit_reason": "stop"})
        self.assertEqual(unavailable["path_status"], "unavailable")
        self.assertIsNone(unavailable["mfe_r"])
        hypotheses = update_hypotheses([tight, tight, tight, bad])
        self.assertEqual(hypotheses[0]["tag"], "STOP_TOO_TIGHT")
        self.assertEqual(hypotheses[0]["status"], "candidate")
        self.assertFalse(hypotheses[0]["auto_applied"])

    def test_tournament_includes_ai_cost_sample_counts_and_never_promotes_on_pnl(self):
        positions = [
            {"cohort": "arm-quant", "status": "closed", "closed_pnl_recorded": 1, "realized_pnl": -1.0,
             "closed_at": "2026-09-28T13:00:00.000Z", "opened_at": "2026-09-28T12:00:00.000Z", "symbol": "BTCUSDT"},
            {"cohort": "arm-hybrid", "status": "closed", "closed_pnl_recorded": 1, "realized_pnl": 3.0,
             "closed_at": "2026-09-28T13:00:00.000Z", "opened_at": "2026-09-28T12:00:00.000Z", "symbol": "BTCUSDT"},
        ]
        usage = [{"arm": "shared:jev", "status": "ok", "estimated_cost_usd": 0.01, "cost_status": "exact"},
                 {"arm": "hybrid", "status": "ok", "estimated_cost_usd": 0.20, "cost_status": "exact"}]
        result = strategy_tournament(positions=positions, usage_events=usage, cycles=[], arms=["quant", "hybrid"],
                                     starting_balance=100.0, fx={"mode": "manual", "usdt_per_usd": 1.0})
        hybrid = next(row for row in result["arms"] if row["arm"] == "hybrid")
        self.assertAlmostEqual(hybrid["ai_cost_usd"], 0.21)
        self.assertAlmostEqual(hybrid["economic_pnl_usdt"], 3.0 - 0.21)
        self.assertEqual(hybrid["promotion_status"], "INSUFFICIENT_SAMPLE")
        self.assertAlmostEqual(hybrid["incremental_vs_quant"]["economic_value_usdt"], 4.0 - 0.21)
        no_fx = strategy_tournament(positions=positions, usage_events=usage, cycles=[], arms=["quant", "hybrid"],
                                    starting_balance=100.0, fx=None)
        self.assertIsNone(next(r for r in no_fx["arms"] if r["arm"] == "hybrid")["economic_pnl_usdt"])


# ---------------------------------------------------------------- safety
class SafetyTests(PortfolioCase):
    def test_no_exchange_write_path_in_portfolio_modules(self):
        for name in ("market_catalog.py", "portfolio_os.py", "spot_accounting.py", "portfolio_brain.py",
                     "activity.py", "learning.py", "portfolio_store.py"):
            source = (ROOT / "crypto_eval" / name).read_text()
            for forbidden in ("method=\"POST\"", "method='POST'", "method=\"DELETE\"", "method=\"PUT\"",
                              "urlopen(", "ReadOnlyGateClient", "create_order_real", "/spot/orders", "/futures/usdt/orders"):
                self.assertNotIn(forbidden, source, f"{name} must not contain {forbidden}")
        client = ReadOnlyGateClient(api_key="k" * 8, api_secret="s" * 8, transport=lambda *a: b"[]")
        for method, path in (("POST", "/spot/orders"), ("DELETE", "/futures/usdt/orders"), ("PUT", "/futures/usdt/positions")):
            with self.assertRaises(LiveExecutionBlocked):
                client.request(method, path)

    def test_real_mirror_is_separate_from_paper_equity(self):
        self.store.save_exchange_account({"account_id": "gate-main"})
        self.store.record_account_sync("gate-main", {"source": "GATE_LIVE_READONLY", "status": "passed",
                                                    "balance": {"equity": 5000.0}, "positions": []})
        portfolio = self.os.portfolio()
        self.assertFalse(portfolio["real_mirror"]["merged_with_paper"])
        self.assertTrue(portfolio["real_mirror"]["read_only"])
        self.assertEqual(portfolio["real_mirror"]["write_execution"], "BLOCKED_BY_DESIGN")
        self.assertAlmostEqual(portfolio["paper"]["total_equity_usdt"], 200.0)

    def test_secrets_never_reach_sqlite_views_or_export(self):
        self.store.save_exchange_account({"account_id": "gate-main"})
        self.runtime.save_account_secrets("gate-main", SENTINEL, SENTINEL + "-2")
        self.buy_spot()
        created, _ref = self.open_perp()
        self.os.request_replan(created["position_ref"], intent="tighten_risk")
        dump = "\n".join(self.store._db.iterdump())
        self.assertNotIn(SENTINEL, dump)
        payloads = json.dumps([self.os.portfolio(), self.os.list_orders(), self.os.activity(), self.os.attention()], default=str)
        self.assertNotIn(SENTINEL, payloads)
        body, _ = self.runtime.export_bundle()
        with zipfile.ZipFile(io.BytesIO(body)) as bundle:
            for name in bundle.namelist():
                self.assertNotIn(SENTINEL.encode(), bundle.read(name))
            manifest = json.loads(bundle.read("manifest.json"))
        self.assertEqual(manifest["secret_scan"]["configured_credentials_checked"], 2)

    def test_settings_validation_is_typed_and_bounded(self):
        with self.assertRaisesRegex(PaperTradingError, "not supported"):
            validate_portfolio_settings({"unknown": 1})
        with self.assertRaisesRegex(PaperTradingError, "between"):
            validate_portfolio_settings({"spot_max_allocation_pct": 5})
        with self.assertRaisesRegex(PaperTradingError, "boolean"):
            validate_portfolio_settings({"automation": {"emergency_stop": "yes"}})
        with self.assertRaisesRegex(PaperTradingError, "sum to 1"):
            normalize_targets([1, 2], [0.5, 0.6], side="long", quantity=1)
        self.buy_spot()
        with self.assertRaisesRegex(PaperTradingError, "frozen"):
            self.os.update_settings({"spot_starting_balance_usdt": 500})

    def test_replan_features_are_point_in_time(self):
        created, _ref = self.open_perp()
        proposal = self.os.request_replan(created["position_ref"], intent="reassess", use_ai=False)
        cutoff = proposal["evidence"]["data_cutoff"]
        self.assertLessEqual(cutoff, NOW.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")


# ---------------------------------------------------------------- HTTP
class HttpRouteTests(PortfolioCase):
    def setUp(self):
        super().setUp()
        self.server = PaperHTTPServer(("127.0.0.1", 0), PaperRequestHandler, self.runtime, None)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_portfolio_order_position_routes(self):
        status, markets = self.call("GET", "/api/markets?market_type=spot&q=eth")
        self.assertEqual(status, 200)
        self.assertEqual(markets["instruments"][0]["instrument_id"], "fixture:spot:ETH_USDT")
        status, perps = self.call("GET", "/api/markets?market_type=perpetual&tradable=true")
        self.assertTrue(all(item["tradable"] for item in perps["instruments"]))
        status, preview = self.call("POST", "/api/orders/preview", {"instrument_id": "fixture:spot:ETH_USDT", "action": "buy", "quote_amount": 20})
        self.assertEqual(status, 200)
        self.assertTrue(preview["preview"]["allowed"])
        status, order = self.call("POST", "/api/orders", {"instrument_id": "fixture:spot:ETH_USDT", "action": "buy", "quote_amount": 20})
        self.assertEqual(order["order"]["status"], "filled")
        ref = urllib.parse.quote(order["order"]["position_ref"], safe="")
        status, positions = self.call("GET", "/api/positions")
        self.assertEqual(len(positions["positions"]), 1)
        status, detail = self.call("GET", f"/api/positions/{ref}")
        self.assertEqual(detail["position"]["market_type"], "spot")
        mid = detail["position"]["mark_price"]
        status, protection = self.call("PATCH", f"/api/positions/{ref}/protection", {"stop_price": mid * 0.95})
        self.assertEqual(status, 200, protection)
        status, conflict = self.call("POST", f"/api/positions/{ref}/close", {})
        self.assertEqual(status, 409)
        self.assertTrue(conflict["confirmation_required"])
        self.assertEqual(conflict["code"], "CONFIRM_CLOSE")
        status, mode = self.call("POST", f"/api/positions/{ref}/management-mode", {"mode": "MANUAL_OVERRIDE"})
        self.assertEqual(mode["result"]["management_mode"], "MANUAL_OVERRIDE")
        status, proposal = self.call("POST", f"/api/positions/{ref}/replan", {"intent": "reduce_exposure"})
        self.assertEqual(proposal["proposal"]["status"], "proposed")
        pid = proposal["proposal"]["proposal_id"]
        status, rejected = self.call("POST", f"/api/replans/{pid}/reject", {})
        self.assertEqual(rejected["proposal"]["status"], "rejected")
        status, closed = self.call("POST", f"/api/positions/{ref}/close", {"confirm": True})
        self.assertTrue(closed["result"]["closed"])
        quote = self.os.quote("fixture:spot:SOL_USDT")
        status, pending = self.call("POST", "/api/orders", {"instrument_id": "fixture:spot:SOL_USDT", "action": "buy",
                                                           "order_type": "limit", "limit_price": quote["mid_price"] * 0.95, "quantity": 0.1})
        order_ref = urllib.parse.quote(pending["order"]["order_ref"], safe="")
        status, amended = self.call("PATCH", f"/api/orders/{order_ref}", {"limit_price": quote["mid_price"] * 0.96})
        self.assertEqual(amended["order"]["status"], "pending")
        new_ref = urllib.parse.quote(amended["order"]["order_ref"], safe="")
        status, cancelled = self.call("DELETE", f"/api/orders/{new_ref}")
        self.assertEqual(cancelled["order"]["status"], "cancelled")
        for path in ("/api/portfolio", "/api/orders", "/api/attention", "/api/activity", "/api/tournament",
                     "/api/reviews", "/api/replans", "/api/portfolio/settings"):
            status, payload = self.call("GET", path)
            self.assertEqual(status, 200, path)
        status, review = self.call("POST", "/api/portfolio/review", {})
        self.assertEqual(review["review"]["schema_version"], "portfolio-review.v1")
        status, validation = self.call("POST", "/api/experiment/validate", {"experiment_id": "EXP-001", "risk_per_trade": 9})
        self.assertFalse(validation["valid"])
        status, automation = self.call("POST", "/api/automation", {"new_entries_paused": True})
        self.assertTrue(automation["settings"]["automation"]["new_entries_paused"])
        status, activity = self.call("GET", "/api/activity?source=USER&market_type=spot")
        self.assertTrue(activity["events"])
        self.assertIn("activity", activity)


if __name__ == "__main__":
    unittest.main()
