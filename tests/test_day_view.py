"""Day-trading read-only views: today's P&L, next decision time, peers aggregator, strategy-search files."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from crypto_eval import day_view
from crypto_eval.sleeves import COHORTS
from tests.test_sleeves import START, SleevesFixture


class NextDecisionTests(unittest.TestCase):
    def test_engine_cadence(self):
        now = datetime(2026, 9, 29, 8, 30, tzinfo=timezone.utc)
        self.assertEqual(day_view.next_decision_at({"strategy_engine": "sleeves_v1", "schedule_delay_seconds": 60}, now), "2026-09-29T12:01:00.000Z")
        self.assertEqual(day_view.next_decision_at({"schedule_delay_seconds": 60}, now), "2026-09-29T08:31:00.000Z")
        self.assertEqual(day_view.next_decision_at({"schedule_delay_seconds": 60}, now + timedelta(minutes=2)), "2026-09-29T08:46:00.000Z")


class TodayViewTests(SleevesFixture):
    def test_today_matches_the_wallets_and_flags_no_ai(self):
        t = START
        while t < START + timedelta(days=3):
            self.clock.value = t + timedelta(seconds=61)
            self.runtime.monitor_once(as_of=self.clock.value)
            self.scheduler.cycle_tick(self.clock.value)
            self.runtime.portfolio.record_snapshot(force=True)
            t += timedelta(hours=4)
        with self.store.transaction() as db:  # a set-up snapshot from before the experiment was frozen
            db.execute("INSERT INTO portfolio_snapshots(experiment_id, as_of, total_equity, perp_equity, spot_equity, open_risk, "
                       "gross_exposure, payload_json) VALUES('EXP-001', ?, 1.0, 1.0, 0, 0, 0, '{}')",
                       ((self.clock.value - timedelta(hours=1, seconds=7)).isoformat().replace("+00:00", "Z"),))
        self.runtime.governance.frozen = lambda: {"frozen_at": (self.clock.value - timedelta(hours=1)).isoformat().replace("+00:00", "Z")}
        view = day_view.today(self.runtime)
        self.assertGreater(min(v for _, v in view["intraday"]), 100)  # the set-up point is excluded
        del self.runtime.governance.frozen
        view = day_view.today(self.runtime)
        wallets = sum(float(self.store.wallet_summary("EXP-001", c)["equity"]) for c in COHORTS.values())
        spot = float(self.runtime.portfolio.spot_wallet()["equity_usdt"])
        self.assertAlmostEqual(view["equity_usdt"], round(wallets + spot, 4), places=3)
        self.assertEqual(len(view["calendar"]), 42)
        self.assertEqual(view["engine"], "sleeves_v1")
        self.assertFalse(view["ai"]["enabled"])
        self.assertEqual(view["ai"]["calls_total"], 0)
        self.assertGreater(view["open_positions"], 0)
        self.assertTrue(view["intraday"])
        self.assertEqual(view["trading_day_utc"], self.clock.value.date().isoformat())
        filled = [c for c in view["calendar"] if c["pnl_usdt"] is not None]
        self.assertAlmostEqual(sum(c["pnl_usdt"] for c in filled), view["pnl_total_usdt"], places=2)
        # the correlated-risk attention (Portfolio Brain limit) is not raised for a sleeves book
        items = self.runtime.portfolio.attention(evaluate=True)
        titles = [i.get("title", "") for i in (items.get("items") if isinstance(items, dict) else items)]
        self.assertFalse(any("correlated" in (i.get("summary") or "") for i in (items.get("items") if isinstance(items, dict) else items)), titles)
        self.assertIs(self.runtime.portfolio.portfolio()["paper"]["ai_budget"]["ai_in_use"], False)


class StubPeer(BaseHTTPRequestHandler):
    answers: dict = {}

    def do_GET(self):
        body = self.answers.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body or {"error": "no"}).encode())

    def log_message(self, *args):
        pass


class PeersTests(SleevesFixture):
    def serve(self, answers):
        handler = type("H", (StubPeer,), {"answers": answers})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_peers_are_read_only_loopback_and_non_paper_servers_are_skipped(self):
        paper = self.serve({"/api/health": {"service": "paper-futures"}, "/api/campaign": {"experiment_id": "EXP-001", "label": "EXP-009",
                                                                                            "status": "running", "engine": "breakout_15m"}})
        other = self.serve({"/api/health": {"hello": "world"}})
        with mock.patch.dict(os.environ, {"PAPER_PEER_PORTS": f"{paper},{other},1"}):
            view = day_view.experiments(self.runtime, 9)
        labels = [r.get("label") for r in view["experiments"]]
        self.assertIn("EXP-009", labels)            # a peer without /api/today still gets a row
        self.assertEqual(len(view["experiments"]), 2)  # self + the paper peer; the other server is ignored
        self.assertTrue(any(r["self"] for r in view["experiments"]))


class StrategySearchFilesTests(unittest.TestCase):
    def test_missing_then_present_research_files(self):
        root = Path(tempfile.mkdtemp())
        self.assertFalse(day_view.strategy_search(root)["available"])
        base = root / "reports" / "day-trade"
        base.mkdir(parents=True)
        (base / "status.json").write_text(json.dumps({"stage": "search", "coins_downloaded": 3, "secret": "never"}))
        out = day_view.strategy_search(root)
        self.assertEqual(out["status"]["coins_downloaded"], 3)
        self.assertNotIn("secret", out["status"])      # only whitelisted status fields are exposed
        (base / "analysis.json").write_text(json.dumps({"n_configs": 2, "n_coins": 1, "n_runs": 2, "coins": [], "families": [],
                                                         "is_selection": {"selected": 0, "top": []},
                                                         "wfo": {"K20_sharpe": {"oos_sharpe": 1.0, "quarters": [{"quarter": "2025-Q1", "picked": []}]}}}))
        (base / "deep.json").write_text("{not json")
        out = day_view.strategy_search(root)
        self.assertTrue(out["available"])
        self.assertEqual(out["wfo"]["K20_sharpe"]["oos_sharpe"], 1.0)
        self.assertNotIn("quarters", out["wfo"]["K20_sharpe"])
        self.assertNotIn("deep", out)                   # an unreadable file is skipped, never an error


if __name__ == "__main__":
    unittest.main()
