"""Holdings: manual entries + READ-ONLY Gate spot sync. Offline: fake signed transport, fake tickers."""

from __future__ import annotations

import contextlib
import io
import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crypto_eval.gate_account import (
    READ_ONLY_ENDPOINTS,
    LiveExecutionBlocked,
    ReadOnlyGateClient,
    normalize_spot_balance,
    normalize_spot_trade,
)
from crypto_eval.holdings import (
    MAX_FILLS,
    Holdings,
    alignment_for,
    moving_average_basis,
    reconcile_basis,
)
from crypto_eval.paper_contracts import PaperTradingError
from crypto_eval.paper_runtime import PaperRuntime, PaperScheduler, PaperStore
from crypto_eval.paper_server import PaperHTTPServer, PaperRequestHandler
from crypto_eval.secret_store import CredentialResolver, MemorySecretStore


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
KEY_SENTINEL = "HOLDINGS-KEY-SENTINEL-7f3e9a1c-NOT-REAL"
SECRET_SENTINEL = "HOLDINGS-SECRET-SENTINEL-c4b2d8e6-NOT-REAL"


def ts(days_ago: float) -> int:
    return int((NOW - timedelta(days=days_ago)).timestamp())


def trade(trade_id, side, amount, price, days_ago, *, fee="0", fee_currency="USDT", gt_fee="0", point_fee="0",
          pair="NEAR_USDT"):
    t = ts(days_ago)
    return {"id": str(trade_id), "order_id": f"o{trade_id}", "create_time": str(t), "create_time_ms": f"{t * 1000}.000",
            "currency_pair": pair, "side": side, "role": "taker", "amount": str(amount), "price": str(price),
            "fee": str(fee), "fee_currency": fee_currency, "gt_fee": str(gt_fee), "point_fee": str(point_fee)}


def fill(*args, **kwargs):
    return normalize_spot_trade(trade(*args, **kwargs))


class FakeGate:
    """Signed-transport fake for /spot/accounts and /spot/my_trades (honours from/to/page/limit)."""

    def __init__(self, balances, trades=None, *, fail=None, raise_exc=None):
        self.balances = balances
        self.trades = trades or {}
        self.fail = fail or {}
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, str, dict[str, str], dict[str, str]]] = []

    def __call__(self, method, url, headers, timeout):
        split = urllib.parse.urlsplit(url)
        path = split.path.replace("/api/v4", "")
        query = dict(urllib.parse.parse_qsl(split.query))
        self.calls.append((method, path, query, dict(headers)))
        if self.raise_exc is not None:
            raise self.raise_exc
        key = query.get("currency_pair", path)
        if key in self.fail or path in self.fail:
            failure = self.fail.get(key, self.fail.get(path))
            status, label = failure if isinstance(failure, tuple) else (failure, None)
            body = None
            if label is not None:  # Gate error body; the message echoes a secret to prove it never leaks
                body = io.BytesIO(json.dumps({"label": label, "message": f"bad key {KEY_SENTINEL}"}).encode())
            raise urllib.error.HTTPError(url, status, "error", None, body)
        if path == "/spot/accounts":
            return json.dumps(self.balances).encode()
        if path == "/spot/my_trades":
            start, end = int(query["from"]), int(query["to"])
            rows = [t for t in self.trades.get(query["currency_pair"], []) if start <= int(t["create_time"]) <= end]
            rows.sort(key=lambda t: -int(t["create_time"]))
            page, limit = int(query["page"]), int(query["limit"])
            return json.dumps(rows[(page - 1) * limit: page * limit]).encode()
        raise AssertionError(f"unexpected path {path}")


def fake_tickers(prices):
    calls = []

    def source():
        calls.append(1)
        return {f"{base}_USDT": {"currency_pair": f"{base}_USDT", "last": str(price)} for base, price in prices.items()}

    source.calls = calls
    return source


def resolver_with_gate_keys() -> CredentialResolver:
    store = MemorySecretStore()
    store.set("gate.gate-main.api-key", KEY_SENTINEL)
    store.set("gate.gate-main.api-secret", SECRET_SENTINEL)
    return CredentialResolver(store, environ={})


BALANCES = [
    {"currency": "NEAR", "available": "15", "locked": "5"},
    {"currency": "SEI", "available": "100", "locked": "0"},
    {"currency": "USDT", "available": "240", "locked": "10"},
    {"currency": "USDC", "available": "10", "locked": "0"},
    {"currency": "BTC", "available": "0", "locked": "0"},
]
TRADES = {"NEAR_USDT": [trade(1, "buy", 20, 4.0, 40)]}
PRICES = {"NEAR": 5.0, "SEI": 0.2, "ETH": 3300.0, "USDC": 1.0001}


# ---------------------------------------------------------------- allowlist
class SpotAllowlistTests(unittest.TestCase):
    def test_spot_read_endpoints_are_exact_and_get_only(self):
        self.assertEqual(READ_ONLY_ENDPOINTS["/spot/accounts"], frozenset({"currency"}))
        self.assertEqual(READ_ONLY_ENDPOINTS["/spot/my_trades"],
                         frozenset({"currency_pair", "limit", "page", "from", "to"}))
        for path in READ_ONLY_ENDPOINTS:
            self.assertNotRegex(path, r"transfer|withdraw|batch|cancel|price_orders|margin|leverage|/spot/orders")

    def test_non_get_and_order_transfer_paths_refused_before_transport(self):
        calls = []
        client = ReadOnlyGateClient(api_key=KEY_SENTINEL, api_secret=SECRET_SENTINEL,
                                    transport=lambda *args: calls.append(args) or b"[]")
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in ("/spot/accounts", "/spot/my_trades", "/spot/orders"):
                with self.assertRaises(LiveExecutionBlocked):
                    client.request(method, path)
        for path in ("/spot/orders", "/spot/batch_orders", "/spot/cancel_batch_orders", "/spot/price_orders",
                     "/spot/amend_batch_orders", "/wallet/transfers", "/withdrawals", "/wallet/sub_account_transfers",
                     "/spot/cross_liquidate_orders"):
            with self.assertRaises(LiveExecutionBlocked):
                client.get(path)
        with self.assertRaises(LiveExecutionBlocked):
            client.get("/spot/my_trades", {"currency_pair": "BTC_USDT", "order_id": "1"})
        with self.assertRaises(LiveExecutionBlocked):
            client.get("/spot/accounts", {"currency_pair": "BTC_USDT"})
        self.assertEqual(calls, [])
        client.get("/spot/accounts", {"currency": "BTC"})
        client.get("/spot/my_trades", {"currency_pair": "BTC_USDT", "limit": 10, "page": 1, "from": 1, "to": 2})
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[0] == "GET" for call in calls))

    def test_holdings_module_has_no_write_path(self):
        source = (ROOT / "crypto_eval" / "holdings.py").read_text()
        for forbidden in ('method="POST"', "method='POST'", '"POST"', '"DELETE"', '"PUT"', "urlopen(",
                          "/spot/orders", "/withdrawals", "/wallet/transfers", "place_order"):
            self.assertNotIn(forbidden, source)

    def test_normalizers(self):
        self.assertEqual(normalize_spot_balance({"currency": "near", "available": "1.5", "locked": "0.5"}),
                         {"currency": "NEAR", "available": 1.5, "locked": 0.5, "total": 2.0})
        for bad in (None, [], {"currency": "BTC"}, {"currency": "B-T", "available": "1", "locked": "0"},
                    {"currency": "BTC", "available": "nan", "locked": "0"},
                    {"currency": "BTC", "available": "-1", "locked": "0"}):
            self.assertIsNone(normalize_spot_balance(bad))
        parsed = normalize_spot_trade(trade(7, "buy", 2, 3.5, 1, fee="0.1", fee_currency="gt", gt_fee="0.1"))
        self.assertEqual((parsed["base"], parsed["quote"], parsed["side"], parsed["fee_currency"]),
                         ("NEAR", "USDT", "buy", "GT"))
        self.assertEqual(parsed["created_at_ms"], ts(1) * 1000)
        self.assertIsNone(normalize_spot_trade({**trade(8, "buy", 1, 1, 1), "side": "short"}))
        self.assertIsNone(normalize_spot_trade({**trade(9, "buy", 1, 1, 1), "price": "0"}))
        self.assertIsNone(normalize_spot_trade({**trade(9, "buy", 1, 1, 1), "currency_pair": "NEARUSDT"}))


# ---------------------------------------------------------------- cost basis
class CostBasisTests(unittest.TestCase):
    def test_buys_sells_quote_and_base_fees(self):
        fills = [
            fill(1, "buy", 10, 4.0, 30, fee="0.04", fee_currency="USDT"),
            fill(2, "buy", 10, 5.0, 20, fee="0.01", fee_currency="NEAR"),
            fill(3, "sell", 5, 6.0, 10, fee="0.03", fee_currency="USDT"),
        ]
        basis = moving_average_basis(list(reversed(fills)), base="NEAR")  # order-independent input
        avg = 90.04 / 19.99
        self.assertAlmostEqual(basis["qty"], 14.99)
        self.assertAlmostEqual(basis["avg_price"], avg)  # a sell never changes the average
        self.assertAlmostEqual(basis["cost"], avg * 14.99)
        self.assertAlmostEqual(basis["realized"], (6.0 - avg) * 5 - 0.03)
        self.assertEqual(basis["trades_counted"], 3)
        self.assertEqual(basis["other_fees"], {})

    def test_partial_sells_accumulate_realized(self):
        fills = [fill(1, "buy", 10, 10.0, 30), fill(2, "sell", 3, 12.0, 20), fill(3, "sell", 2, 8.0, 10),
                 fill(4, "buy", 5, 11.0, 5)]
        basis = moving_average_basis(fills, base="NEAR")
        self.assertAlmostEqual(basis["realized"], 3 * 2.0 + 2 * -2.0)
        self.assertAlmostEqual(basis["qty"], 10.0)
        self.assertAlmostEqual(basis["avg_price"], (5 * 10.0 + 5 * 11.0) / 10)

    def test_gt_and_point_fees_are_noted_not_converted(self):
        fills = [fill(1, "buy", 10, 2.0, 3, fee="0.02", fee_currency="GT", gt_fee="0.02"),
                 fill(2, "buy", 10, 2.0, 2, fee="0", fee_currency="USDT", point_fee="0.5")]
        basis = moving_average_basis(fills, base="NEAR")
        self.assertAlmostEqual(basis["cost"], 40.0)  # nothing converted into USDT
        self.assertEqual(basis["other_fees"], {"GT": 0.02, "POINT": 0.5})
        result = reconcile_basis("NEAR", 20.0, basis)
        self.assertEqual(result["avg_source"], "gate_trades")
        self.assertIn("0.02 GT", result["cost_basis_note"])
        self.assertIn("0.5 POINT", result["cost_basis_note"])
        self.assertIn("not converted", result["cost_basis_note"])

    def test_sell_beyond_tracked_qty_realizes_only_matched_part(self):
        basis = moving_average_basis([fill(1, "buy", 2, 10.0, 5), fill(2, "sell", 5, 12.0, 1)], base="NEAR")
        self.assertAlmostEqual(basis["realized"], 4.0)
        self.assertEqual(basis["qty"], 0.0)
        self.assertAlmostEqual(basis["unmatched_sell_qty"], 3.0)

    def test_other_pairs_are_ignored(self):
        basis = moving_average_basis([fill(1, "buy", 2, 10.0, 5, pair="NEAR_BTC")], base="NEAR")
        self.assertEqual(basis["trades_counted"], 0)
        self.assertIsNone(basis["avg_price"])

    def test_unexplained_balance_is_unknown_never_invented(self):
        basis = moving_average_basis([fill(1, "buy", 10, 4.0, 5)], base="NEAR")
        deposit = reconcile_basis("NEAR", 25.0, basis)
        self.assertEqual(deposit["avg_source"], "unknown")
        self.assertIsNone(deposit["avg_price"])
        self.assertIn("deposits or transfers", deposit["cost_basis_note"])
        explained = reconcile_basis("NEAR", 10.0, basis)
        self.assertEqual((explained["avg_source"], explained["avg_price"], explained["cost_basis_note"]),
                         ("gate_trades", 4.0, None))
        withdrawn = reconcile_basis("NEAR", 6.0, basis)
        self.assertEqual(withdrawn["avg_source"], "gate_trades")
        self.assertIn("lower than the trades imply", withdrawn["cost_basis_note"])
        none = reconcile_basis("SEI", 100.0, moving_average_basis([], base="SEI"))
        self.assertEqual(none["avg_source"], "unknown")
        self.assertIsNone(none["avg_price"])

    def test_alignment_matrix(self):
        self.assertEqual(alignment_for("HOLD", True), "aligned")
        self.assertEqual(alignment_for("CASH", True), "holding_in_cash_state")
        self.assertEqual(alignment_for("WATCH", True), "holding_in_cash_state")
        self.assertEqual(alignment_for("HOLD", False), "not_held_in_hold_state")
        self.assertEqual(alignment_for("CASH", False), "aligned")
        self.assertIsNone(alignment_for(None, True))
        self.assertIsNone(alignment_for("HOLD", None))
        self.assertIsNone(alignment_for("MAYBE", True))


# ---------------------------------------------------------------- service
class HoldingsServiceCase(unittest.TestCase):
    def setUp(self):
        self.store = PaperStore(":memory:")
        self.resolver = resolver_with_gate_keys()
        self.runtime = PaperRuntime(self.store, resolver=self.resolver, clock=lambda: NOW)
        self.gate = FakeGate(BALANCES, TRADES)
        self.tickers = fake_tickers(PRICES)

    def tearDown(self):
        self.store.close()

    def holdings(self, **kwargs):
        options = {"ticker_source": self.tickers, "gate_transport": self.gate, "clock": lambda: NOW}
        options.update(kwargs)
        holdings = Holdings(self.runtime, **options)
        self.runtime.holdings = holdings
        return holdings


class HoldingsServiceTests(HoldingsServiceCase):
    def test_not_configured_without_account_and_no_network(self):
        holdings = self.holdings()
        payload = holdings.payload()
        self.assertEqual(payload["sources"]["gate"]["status"], "NOT_CONFIGURED")
        self.assertEqual(payload["holdings"], [])
        self.assertIsNone(payload["totals"]["cash_usdt"])
        self.assertEqual(payload["share_holdings_with_ai"], False)
        self.assertEqual(self.tickers.calls, [])  # nothing to price → no ticker call
        self.assertEqual(holdings.sync()["sources"]["gate"]["status"], "NOT_CONFIGURED")
        self.assertEqual(self.gate.calls, [])
        self.assertIsNone(holdings.held("NEAR"))

    def test_account_without_keys_is_not_configured(self):
        self.store.save_exchange_account({"account_id": "gate-other"})
        payload = self.holdings().sync()
        self.assertEqual(payload["sources"]["gate"]["status"], "NOT_CONFIGURED")
        self.assertIn("not stored", payload["sources"]["gate"]["error"])
        self.assertEqual(self.gate.calls, [])

    def test_sync_totals_cash_and_unknown_deposit(self):
        self.store.save_exchange_account({"account_id": "gate-main"})
        holdings = self.holdings()
        self.assertEqual(holdings.payload()["sources"]["gate"]["status"], "NOT_CONFIGURED")  # never synced
        holdings.save_manual({"base": "ETH", "qty": 0.5, "avg_price": 3000})
        payload = holdings.sync()
        self.assertTrue(all(method == "GET" for method, *_ in self.gate.calls))
        self.assertEqual({path for _, path, _, _ in self.gate.calls}, {"/spot/accounts", "/spot/my_trades"})
        gate = payload["sources"]["gate"]
        self.assertEqual((gate["account_id"], gate["status"], gate["error"]), ("gate-main", "OK", None))
        self.assertIsNotNone(gate["synced_at"])
        self.assertEqual(payload["sources"]["manual_count"], 1)
        rows = {row["base"]: row for row in payload["holdings"]}
        self.assertEqual(set(rows), {"NEAR", "SEI", "ETH"})  # BTC zero balance, stables are cash
        near = rows["NEAR"]
        self.assertEqual((near["qty"], near["qty_source"], near["avg_price"], near["avg_source"]),
                         (20.0, "gate", 4.0, "gate_trades"))
        self.assertEqual((near["value_usdt"], near["cost_usdt"], near["unrealized_usdt"], near["unrealized_pct"]),
                         (100.0, 80.0, 20.0, 0.25))
        self.assertEqual((near["trades_counted"], near["realized_usdt"], near["manual_id"]), (1, 0.0, None))
        sei = rows["SEI"]
        self.assertEqual((sei["avg_source"], sei["avg_price"], sei["cost_usdt"], sei["unrealized_usdt"]),
                         ("unknown", None, None, None))
        self.assertEqual(sei["value_usdt"], 20.0)
        self.assertIn("unknown", sei["cost_basis_note"])
        eth = rows["ETH"]
        self.assertEqual((eth["qty_source"], eth["avg_source"], eth["value_usdt"], eth["realized_usdt"]),
                         ("manual", "manual", 1650.0, None))
        totals = payload["totals"]
        self.assertEqual(totals["value_usdt"], 1770.0)
        self.assertEqual(totals["cost_usdt"], 1580.0)
        self.assertEqual(totals["unrealized_usdt"], 170.0)
        self.assertAlmostEqual(totals["unrealized_pct"], round(170 / 1580, 6))
        self.assertEqual(totals["realized_usdt"], 0.0)
        self.assertAlmostEqual(totals["cash_usdt"], 250 + 10 * 1.0001)
        self.assertEqual(payload["cash"], [{"currency": "USDC", "qty": 10.0}, {"currency": "USDT", "qty": 250.0}])
        self.assertAlmostEqual(near["allocation"], round(100 / 1770, 6))
        self.assertAlmostEqual(sum(row["allocation"] for row in payload["holdings"]), 1.0, places=5)
        self.assertEqual([row["base"] for row in payload["holdings"]], ["ETH", "NEAR", "SEI"])
        for row in payload["holdings"]:
            self.assertIsNone(row["rule_state"])
            self.assertIsNone(row["alignment"])
        self.assertEqual(set(near), {
            "base", "qty", "qty_source", "avg_price", "avg_source", "price", "value_usdt", "cost_usdt",
            "unrealized_usdt", "unrealized_pct", "realized_usdt", "allocation", "rule_state", "alignment",
            "trades_counted", "cost_basis_note", "manual_id"})

    def test_trade_walk_is_windowed_and_bounded(self):
        self.store.save_exchange_account({"account_id": "gate-main"})
        old = [trade(i, "buy", 1, 1.0, 380 + i) for i in range(3)]  # older than 365 days: never fetched
        recent = [trade(100 + i, "buy", 1, 2.0, 200 + i) for i in range(5)]
        self.gate.balances = [{"currency": "NEAR", "available": "8", "locked": "0"}]
        self.gate.trades = {"NEAR_USDT": old + recent}
        row = self.holdings().sync()["holdings"][0]
        self.assertEqual(row["trades_counted"], 5)
        self.assertEqual(row["avg_source"], "unknown")  # 3 NEAR are older than the look-back
        trade_calls = [q for _, path, q, _ in self.gate.calls if path == "/spot/my_trades"]
        self.assertTrue(all(int(q["to"]) - int(q["from"]) <= 30 * 86400 for q in trade_calls))
        self.assertGreaterEqual(min(int(q["from"]) for q in trade_calls), ts(365))
        self.assertTrue(all(q["currency_pair"] == "NEAR_USDT" for q in trade_calls))

    def test_fill_cap_keeps_latest_2000(self):
        self.store.save_exchange_account({"account_id": "gate-main"})
        fills = [trade(i, "buy", 1, 1.0 + (i >= 100), 10 - i / 1000) for i in range(MAX_FILLS + 100)]
        self.gate.balances = [{"currency": "NEAR", "available": str(MAX_FILLS), "locked": "0"}]
        self.gate.trades = {"NEAR_USDT": fills}
        row = self.holdings().sync()["holdings"][0]
        self.assertEqual(row["trades_counted"], MAX_FILLS)
        self.assertEqual(row["avg_price"], 2.0)  # the 100 oldest (price 1.0) fell outside the cap
        self.assertIn(f"latest {MAX_FILLS} fills", row["cost_basis_note"])

    def test_manual_override_precedence_validation_and_delete(self):
        self.store.save_exchange_account({"account_id": "gate-main"})
        holdings = self.holdings()
        holdings.sync()
        entry = holdings.save_manual({"base": "NEAR", "qty": 1, "avg_price": 3.0, "opened_at": "2026-01-02",
                                      "note": "bought on another exchange"})
        self.assertTrue(entry["id"].startswith("hm-"))
        near = next(r for r in holdings.payload()["holdings"] if r["base"] == "NEAR")
        self.assertEqual((near["qty"], near["qty_source"], near["avg_price"], near["avg_source"], near["manual_id"]),
                         (20.0, "gate", 3.0, "manual", entry["id"]))
        self.assertEqual(near["cost_usdt"], 60.0)
        self.assertEqual(near["realized_usdt"], 0.0)  # realized still comes from Gate fills
        # Adding the same coin again edits the one entry; editing by id works.
        again = holdings.save_manual({"base": "NEAR", "qty": 2, "avg_price": 3.5})
        self.assertEqual(again["id"], entry["id"])
        edited = holdings.save_manual({"id": entry["id"], "base": "NEAR", "qty": 2, "avg_price": 3.25})
        self.assertEqual(edited["avg_price"], 3.25)
        self.assertEqual(len(holdings.manual_entries()), 1)
        other = holdings.save_manual({"base": "ENA", "qty": 100, "avg_price": 0.5})
        bad_bodies = [
            {"base": "near", "qty": 1, "avg_price": 1},
            {"base": "NE-AR", "qty": 1, "avg_price": 1},
            {"base": "NEAR", "qty": 0, "avg_price": 1},
            {"base": "NEAR", "qty": 1, "avg_price": -1},
            {"base": "NEAR", "qty": True, "avg_price": 1},
            {"base": "NEAR", "qty": "1", "avg_price": 1},
            {"base": "NEAR", "qty": float("inf"), "avg_price": 1},
            {"base": "USDT", "qty": 1, "avg_price": 1},
            {"base": "NEAR", "qty": 1, "avg_price": 1, "api_key": "x"},
            {"base": "NEAR", "qty": 1, "avg_price": 1, "note": "x" * 501},
            {"base": "NEAR", "qty": 1, "avg_price": 1, "opened_at": "yesterday"},
            {"id": "hm-missing", "base": "NEAR", "qty": 1, "avg_price": 1},
            {"id": other["id"], "base": "NEAR", "qty": 1, "avg_price": 1},
        ]
        for body in bad_bodies:
            with self.assertRaises(PaperTradingError, msg=str(body)):
                holdings.save_manual(body)
        self.assertEqual(holdings.delete_manual(entry["id"]), {"deleted": True})
        with self.assertRaises(PaperTradingError):
            holdings.delete_manual(entry["id"])
        near = next(r for r in holdings.payload()["holdings"] if r["base"] == "NEAR")
        self.assertEqual((near["avg_source"], near["avg_price"]), ("gate_trades", 4.0))

    def test_alignment_via_injected_rule_provider(self):
        self.store.save_exchange_account({"account_id": "gate-main"})
        states = {"NEAR": "HOLD", "SEI": "CASH", "ETH": "WATCH"}
        holdings = self.holdings(rule_state_provider=states.get)
        holdings.save_manual({"base": "ETH", "qty": 0.5, "avg_price": 3000})
        rows = {row["base"]: row for row in holdings.sync()["holdings"]}
        self.assertEqual((rows["NEAR"]["rule_state"], rows["NEAR"]["alignment"]), ("HOLD", "aligned"))
        self.assertEqual((rows["SEI"]["rule_state"], rows["SEI"]["alignment"]), ("CASH", "holding_in_cash_state"))
        self.assertEqual((rows["ETH"]["rule_state"], rows["ETH"]["alignment"]), ("WATCH", "holding_in_cash_state"))

        def broken(_base):
            raise RuntimeError("co-trader not ready")

        holdings.rule_state_provider = broken
        self.assertTrue(all(r["rule_state"] is None and r["alignment"] is None for r in holdings.payload()["holdings"]))
        holdings.rule_state_provider = lambda base: "BUY"  # not a rule state
        self.assertTrue(all(r["rule_state"] is None for r in holdings.payload()["holdings"]))

    def test_holding_for_ai_respects_setting(self):
        self.store.save_exchange_account({"account_id": "gate-main"})
        holdings = self.holdings()
        self.assertIsNone(holdings.holding_for_ai("NEAR"))
        holdings.update_settings({"share_holdings_with_ai": True})
        self.assertIsNone(holdings.holding_for_ai("NEAR"))  # holdings unknown until synced
        holdings.sync()
        self.assertEqual(holdings.holding_for_ai("NEAR"),
                         {"held": True, "qty": 20.0, "avg_price": 4.0, "unrealized_pct": 0.25})
        self.assertEqual(holdings.holding_for_ai("BTC"),
                         {"held": False, "qty": 0.0, "avg_price": None, "unrealized_pct": None})
        self.assertEqual(holdings.holding_for_ai("SEI")["avg_price"], None)
        self.assertIsNone(holdings.holding_for_ai("bad-base"))
        shared = json.dumps(holdings.holding_for_ai("NEAR"))
        self.assertNotIn("gate-main", shared)
        self.assertNotIn("SEI", shared)
        holdings.update_settings({"share_holdings_with_ai": False})
        self.assertIsNone(holdings.holding_for_ai("NEAR"))
        for bad in ({"share_holdings_with_ai": "yes"}, {}, {"share_holdings_with_ai": True, "x": 1}):
            with self.assertRaises(PaperTradingError):
                holdings.update_settings(bad)
        self.assertTrue(holdings.held("NEAR"))
        self.assertFalse(holdings.held("BTC"))

    def test_error_keeps_last_good_snapshot_and_never_leaks(self):
        self.store.save_exchange_account({"account_id": "gate-main"})
        holdings = self.holdings()
        first = holdings.sync()
        self.gate.fail = {"/spot/accounts": 401}
        failed = holdings.sync()
        gate = failed["sources"]["gate"]
        self.assertEqual(gate["status"], "ERROR")
        self.assertIn("HTTP 401", gate["error"])
        self.assertEqual(gate["synced_at"], first["sources"]["gate"]["synced_at"])
        self.assertEqual(len(failed["holdings"]), 2)  # last good snapshot is still shown
        self.gate.raise_exc = RuntimeError(f"boom {KEY_SENTINEL} {SECRET_SENTINEL}")
        leaked = holdings.sync()
        self.assertEqual(leaked["sources"]["gate"]["error"], "Gate spot sync failed safely")
        self.gate.raise_exc = OSError(f"network {SECRET_SENTINEL}")
        self.assertEqual(holdings.sync()["sources"]["gate"]["error"], "Gate read-only request failed")
        dump = "\n".join(self.store._db.iterdump())
        for sentinel in (KEY_SENTINEL, SECRET_SENTINEL):
            self.assertNotIn(sentinel, dump)
            self.assertNotIn(sentinel, json.dumps(leaked))

    def test_trade_history_failure_marks_coin_unknown(self):
        self.store.save_exchange_account({"account_id": "gate-main"})
        self.gate.fail = {"NEAR_USDT": 403}
        payload = self.holdings().sync()
        self.assertEqual(payload["sources"]["gate"]["status"], "OK")
        near = next(r for r in payload["holdings"] if r["base"] == "NEAR")
        self.assertEqual((near["avg_source"], near["avg_price"]), ("unknown", None))
        self.assertIn("HTTP 403", near["cost_basis_note"])

    def test_ticker_failure_leaves_prices_null(self):
        def broken():
            raise OSError("down")

        holdings = self.holdings(ticker_source=broken)
        holdings.save_manual({"base": "NEAR", "qty": 2, "avg_price": 4})
        payload = holdings.payload()
        row = payload["holdings"][0]
        self.assertEqual((row["price"], row["value_usdt"], row["unrealized_usdt"], row["allocation"]),
                         (None, None, None, None))
        self.assertEqual(row["cost_usdt"], 8.0)
        self.assertEqual(payload["sources"]["prices"]["status"], "ERROR")
        self.assertEqual(payload["totals"]["value_usdt"], 0.0)
        self.assertIsNone(payload["totals"]["unrealized_pct"])

    def test_sync_if_due(self):
        holdings = self.holdings()
        self.assertIsNone(holdings.sync_if_due())
        self.store.save_exchange_account({"account_id": "gate-main"})
        self.assertIsNotNone(holdings.sync_if_due())
        calls = len(self.gate.calls)
        self.assertIsNone(holdings.sync_if_due())
        self.assertEqual(len(self.gate.calls), calls)

    def test_explicit_account_errors(self):
        holdings = self.holdings()
        with self.assertRaises(PaperTradingError):
            holdings.sync("missing")
        with self.assertRaises(PaperTradingError):
            holdings.sync("Bad Id")
        self.store.save_exchange_account({"account_id": "gate-main", "enabled": False})
        with self.assertRaises(PaperTradingError):
            holdings.sync("gate-main")


class GateValidationTests(HoldingsServiceCase):
    def test_no_key_makes_no_network_call(self):
        holdings = self.holdings()
        result = holdings.validate_gate()  # no account at all
        self.assertEqual((result["ok"], result["reason"]), (False, "no_key"))
        self.assertEqual(result["checks"], {"credentials_present": False, "auth": "skipped", "spot_read": "skipped"})
        self.store.save_exchange_account({"account_id": "gate-other"})  # account without stored keys
        result = holdings.validate_gate()
        self.assertEqual(result["reason"], "no_key")
        self.assertIsNone(result["key_hint"])
        self.assertEqual(self.gate.calls, [])
        self.assertIsNone(holdings.last_validation())

    def test_ok_is_exactly_one_get_to_spot_accounts(self):
        self.store.save_exchange_account({"account_id": "gate-main"})
        holdings = self.holdings()
        result = holdings.validate_gate()
        self.assertEqual([(method, path) for method, path, _, _ in self.gate.calls], [("GET", "/spot/accounts")])
        self.assertTrue(result["ok"])
        self.assertNotIn("reason", result)
        self.assertNotIn("error_code", result)
        self.assertEqual(result["checks"], {"credentials_present": True, "auth": "ok", "spot_read": "ok"})
        self.assertEqual(result["balances_nonzero"], 4)  # NEAR, SEI, USDT, USDC; BTC is zero
        self.assertEqual(result["key_hint"]["account_id"], "gate-main")
        self.assertIn("read-only", result["read_only_note"])
        self.assertEqual(set(result), {"ok", "checks", "balances_nonzero", "key_hint", "checked_at", "read_only_note"})
        text = json.dumps(result)
        for leaked in (KEY_SENTINEL, SECRET_SENTINEL, "NEAR", "240"):
            self.assertNotIn(leaked, text)
        self.assertEqual(holdings.last_validation(),
                         {"ok": True, "checked_at": result["checked_at"], "error_code": None, "account_id": "gate-main"})
        # Validation never syncs.
        self.assertEqual(self.store._query("SELECT COUNT(*) AS n FROM holdings_gate_syncs")[0]["n"], 0)
        self.assertEqual(holdings.payload()["sources"]["gate"]["last_validation"]["ok"], True)

    def test_auth_failure_reports_label(self):
        self.store.save_exchange_account({"account_id": "gate-main"})
        holdings = self.holdings()
        self.gate.fail = {"/spot/accounts": (401, "INVALID_KEY")}
        result = holdings.validate_gate()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "INVALID_KEY")
        self.assertEqual(result["checks"], {"credentials_present": True, "auth": "failed", "spot_read": "skipped"})
        self.assertNotIn(KEY_SENTINEL, json.dumps(result))
        self.assertEqual(holdings.last_validation()["error_code"], "INVALID_KEY")
        self.gate.fail = {"/spot/accounts": (403, "IP_NOT_ALLOWED")}
        result = holdings.validate_gate()
        self.assertEqual((result["error_code"], result["checks"]["auth"]), ("IP_NOT_ALLOWED", "failed"))
        self.gate.fail = {"/spot/accounts": (403, "FORBIDDEN")}
        result = holdings.validate_gate()
        self.assertEqual((result["error_code"], result["checks"]["auth"], result["checks"]["spot_read"]),
                         ("FORBIDDEN", "ok", "failed"))
        self.gate.fail = {"/spot/accounts": 401}
        self.assertEqual(holdings.validate_gate()["error_code"], "HTTP_401")
        self.assertEqual({path for _, path, _, _ in self.gate.calls}, {"/spot/accounts"})
        dump = "\n".join(self.store._db.iterdump())
        self.assertNotIn(KEY_SENTINEL, dump)
        self.assertNotIn(SECRET_SENTINEL, dump)

    def test_network_error(self):
        self.store.save_exchange_account({"account_id": "gate-main"})
        holdings = self.holdings()
        self.gate.raise_exc = OSError(f"connection reset {SECRET_SENTINEL}")
        result = holdings.validate_gate()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "NETWORK_ERROR")
        self.assertEqual(result["checks"], {"credentials_present": True, "auth": "skipped", "spot_read": "failed"})
        self.assertNotIn(SECRET_SENTINEL, json.dumps(result))
        self.assertEqual(len(self.gate.calls), 1)
        self.gate.raise_exc = TimeoutError()
        self.assertEqual(holdings.validate_gate()["error_code"], "TIMEOUT")


# ---------------------------------------------------------------- loopback endpoints
class HoldingsEndpointTests(HoldingsServiceCase):
    def setUp(self):
        super().setUp()
        self.store.save_exchange_account({"account_id": "gate-main"})
        self.holdings(rule_state_provider={"NEAR": "HOLD", "SEI": "CASH"}.get)
        scheduler = PaperScheduler(self.runtime)
        server = PaperHTTPServer(("127.0.0.1", 0), PaperRequestHandler, self.runtime, scheduler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(scheduler.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 5)
        self.addCleanup(server.shutdown)
        self.base_url = f"http://127.0.0.1:{server.server_address[1]}"
        self.bodies: list[str] = []

    def call(self, path, body=None, *, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method="GET" if body is None else "POST",
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                status, text = response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            status, text = exc.code, exc.read().decode()
        self.bodies.append(text)
        return status, json.loads(text)

    def test_endpoints_contract_and_secret_scan(self):
        logs = io.StringIO()
        with contextlib.redirect_stdout(logs), contextlib.redirect_stderr(logs):
            status, empty = self.call("/api/holdings")
            self.assertEqual(status, 200)
            self.assertEqual(empty["sources"]["gate"]["status"], "NOT_CONFIGURED")
            status, synced = self.call("/api/holdings/sync", {})
            self.assertEqual(status, 200)
            self.assertEqual(synced["sources"]["gate"]["status"], "OK")
            self.assertEqual(set(synced), {"as_of", "sources", "share_holdings_with_ai", "totals", "holdings", "cash"})
            self.assertEqual(set(synced["totals"]), {"value_usdt", "cost_usdt", "unrealized_usdt", "unrealized_pct",
                                                     "realized_usdt", "cash_usdt"})
            rows = {row["base"]: row for row in synced["holdings"]}
            self.assertEqual(rows["NEAR"]["alignment"], "aligned")
            self.assertEqual(rows["SEI"]["alignment"], "holding_in_cash_state")
            status, explicit = self.call("/api/holdings/sync", {"account_id": "gate-main"})
            self.assertEqual(status, 200)
            status, created = self.call("/api/holdings/manual", {"base": "ETH", "qty": 0.5, "avg_price": 3000,
                                                                 "note": "cold wallet"})
            self.assertEqual(status, 200)
            entry = created["entry"]
            self.assertEqual((entry["base"], entry["qty"], entry["avg_price"], entry["note"]),
                             ("ETH", 0.5, 3000.0, "cold wallet"))
            status, listed = self.call("/api/holdings")
            self.assertEqual(listed["sources"]["manual_count"], 1)
            self.assertEqual(next(r for r in listed["holdings"] if r["base"] == "ETH")["manual_id"], entry["id"])
            status, error = self.call("/api/holdings/manual", {"base": "eth", "qty": 1, "avg_price": 1})
            self.assertEqual(status, 400)
            self.assertIn("uppercase", error["error"])
            status, error = self.call("/api/holdings/manual", {"base": "ETH", "qty": 0, "avg_price": 1})
            self.assertEqual(status, 400)
            status, deleted = self.call(f"/api/holdings/manual/{entry['id']}/delete", {})
            self.assertEqual((status, deleted), (200, {"deleted": True}))
            status, _ = self.call(f"/api/holdings/manual/{entry['id']}/delete", {})
            self.assertEqual(status, 400)
            status, settings = self.call("/api/holdings/settings", {"share_holdings_with_ai": True})
            self.assertEqual((status, settings), (200, {"share_holdings_with_ai": True}))
            status, _ = self.call("/api/holdings/settings", {"share_holdings_with_ai": 1})
            self.assertEqual(status, 400)
            status, _ = self.call("/api/holdings/sync", {"api_key": KEY_SENTINEL})
            self.assertEqual(status, 400)
            status, listed = self.call("/api/holdings")
            self.assertTrue(listed["share_holdings_with_ai"])
            status, _ = self.call("/api/holdings/nope", {})
            self.assertEqual(status, 404)
            # Origin validation still guards every POST.
            status, _ = self.call("/api/holdings/settings", {"share_holdings_with_ai": False},
                                  headers={"Origin": "http://evil.example"})
            self.assertEqual(status, 400)
            status, _ = self.call("/api/holdings/manual", {"base": "BTC", "qty": 1, "avg_price": 1},
                                  headers={"Sec-Fetch-Site": "cross-site"})
            self.assertEqual(status, 400)
            before = len(self.gate.calls)
            status, validated = self.call("/api/holdings/gate/validate", {})
            self.assertEqual((status, validated["ok"], validated["balances_nonzero"]), (200, True, 4))
            self.assertEqual([c[1] for c in self.gate.calls[before:]], ["/spot/accounts"])
            self.gate.fail = {"/spot/accounts": (401, "INVALID_SIGNATURE")}
            status, validated = self.call("/api/holdings/gate/validate", {})
            self.assertEqual((status, validated["ok"], validated["error_code"]), (200, False, "INVALID_SIGNATURE"))
            status, listed = self.call("/api/holdings")
            self.assertEqual(listed["sources"]["gate"]["last_validation"]["error_code"], "INVALID_SIGNATURE")
            status, _ = self.call("/api/holdings/gate/validate", {}, headers={"Origin": "http://evil.example"})
            self.assertEqual(status, 400)
            status, failed = self.call("/api/holdings/sync", {})
            self.assertEqual(status, 200)
            self.assertEqual(failed["sources"]["gate"]["status"], "ERROR")
        self.assertEqual(self.runtime.holdings.manual_entries(), [])
        # The signed transport saw the key only as a header, and only on GET.
        self.assertTrue(all(method == "GET" for method, *_ in self.gate.calls))
        self.assertTrue(all(headers["KEY"] == KEY_SENTINEL for *_, headers in self.gate.calls))
        dump = "\n".join(self.store._db.iterdump())
        for sentinel in (KEY_SENTINEL, SECRET_SENTINEL):
            for text in self.bodies:
                self.assertNotIn(sentinel, text)
            self.assertNotIn(sentinel, dump)
            self.assertNotIn(sentinel, logs.getvalue())


if __name__ == "__main__":
    unittest.main()
