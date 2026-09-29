"""scripts/paper_campaign_status.py: read-only status levels from faked API responses (no network)."""

from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "scripts" / "paper_campaign_status.py"
spec = importlib.util.spec_from_file_location("paper_campaign_status", PATH)
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)


def fake(campaign_extra=None, reconcile=True, overall="OK", incidents=()):
    campaign = {"experiment_id": "EXP-001", "label": "EXP-002", "engine": "sleeves_v1", "status": "running", "elapsed_days": 1,
                "completed_trades": 3, "target_trades": 80, "manifest_version": 1, "drift": False, "risk_incidents": [],
                "ai_budget": {"remaining_experiment_usd": 1.0, "exhausted": False},
                "sleeves": {"last_tick": {"boundary": "2026-09-29T08:00:00.000Z", "blocked": [], "combined_drawdown": 0.01},
                            "sleeves": [{"sleeve": "tsmom", "equity_usdt": 166.0, "pnl_usdt": -0.6, "long": ["BTCUSDT"], "short": []}]}}
    campaign.update(campaign_extra or {})
    answers = {"/api/health": {"real_money_execution": False},
               "/api/runtime-health": {"overall": overall, "components": {"db": {"status": "OK"}}, "open_incidents": list(incidents)},
               "/api/campaign": campaign,
               "/api/safety": {"kill_switch": {"level": "NORMAL"}, "reconciliation": {"ok": reconcile}}}
    return lambda base, path: answers[path]


class CampaignStatusScriptTests(unittest.TestCase):
    def run_check(self, getter):
        original = status._get
        status._get = getter
        try:
            return status.check_server("EXP-002", "http://127.0.0.1:8768", NOW)
        finally:
            status._get = original

    def test_healthy_server_is_ok(self):
        r = self.run_check(fake())
        self.assertEqual(r["level"], "OK")
        self.assertEqual(r["last_tick_age_hours"], 1.0)

    def test_failures_and_warnings(self):
        self.assertEqual(self.run_check(fake(reconcile=False))["level"], "FAIL")
        stale = fake({"sleeves": {"last_tick": {"boundary": "2026-09-28T20:00:00.000Z", "blocked": []}, "sleeves": []}})
        self.assertEqual(self.run_check(stale)["level"], "FAIL")
        blocked = fake({"sleeves": {"last_tick": {"boundary": "2026-09-29T08:00:00.000Z", "blocked": ["tsmom ENAUSDT STALE_MARKET_FEED"]},
                                    "sleeves": []}})
        self.assertEqual(self.run_check(blocked)["level"], "WARN")
        self.assertEqual(self.run_check(fake({"status": "stopped"}))["level"], "FAIL")

    def test_unreachable_server_fails_without_raising(self):
        def down(base, path):
            raise ConnectionRefusedError()
        r = self.run_check(down)
        self.assertEqual(r["level"], "FAIL")
        self.assertIn("unreachable", r["problems"][0])


if __name__ == "__main__":
    unittest.main()
