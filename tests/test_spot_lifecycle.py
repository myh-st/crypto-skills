"""Spot Cycle Lifecycle Manager: regime evidence, lifecycle policy, benchmarks, and PortfolioOS
integration. Fixture/synthetic data only; no network or paid calls."""

from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta, timezone

from crypto_eval.paper_contracts import PaperTradingError, iso_utc
from crypto_eval.portfolio_os import ConfirmationRequired
from crypto_eval.spot_benchmarks import ARMS, run_benchmarks
from crypto_eval.spot_lifecycle import (
    DEFAULT_LIFECYCLE_SETTINGS,
    TRANSITIONS,
    classify_regime,
    merge_recommendation,
    plan_lifecycle,
    regime_evidence,
    validate_lifecycle_settings,
    validate_transition,
)

from tests.test_portfolio_os import NOW, PortfolioCase

S = DEFAULT_LIFECYCLE_SETTINGS
END = NOW.replace(minute=0, second=0, microsecond=0) - timedelta(hours=NOW.hour % 4)


def bars(closes, volumes=None, end=END, hours=4):
    n = len(closes)
    out = []
    for i, close in enumerate(closes):
        close_time = end - timedelta(hours=hours * (n - 1 - i))
        out.append({"open_time": iso_utc(close_time - timedelta(hours=hours)), "close_time": iso_utc(close_time),
                    "open": close, "high": close * 1.004, "low": close * 0.996, "close": close,
                    "volume": 100.0 if volumes is None else volumes[i]})
    return out


def trend(n=120, rate=0.004, start=100.0):
    return [start * (1 + rate) ** i for i in range(n)]


FLAT = [100.0] * 120
STRONG = trend()
EARLY = [100.0] * 80 + [100 * 1.0012 ** i for i in range(1, 41)]
RANGE = [100 + 3 * math.sin(i / 3) for i in range(120)]
LATE = trend(112) + [trend(112)[-1] * (0.991 ** i) for i in range(1, 9)]
LATE_VOLUME = [100.0] * 108 + [40.0] * 12
BREAKDOWN = trend(80) + [trend(80)[-1] * (0.99 ** i) for i in range(1, 41)]
BASKET_WEAK = {k: bars([100 - 0.1 * i for i in range(120)]) for k in ("SOL", "BNB", "XRP")}
BASKET_STRONG = {k: bars(trend()) for k in ("SOL", "BNB", "XRP")}


def regime_of(closes, *, btc=FLAT, basket=None, volumes=None, safety=None, symbol="ETHUSDT"):
    evidence = regime_evidence(symbol=symbol, as_of=END, asset_bars=bars(closes, volumes), btc_bars=bars(btc),
                               basket=basket if basket is not None else BASKET_STRONG, safety=safety)
    return evidence, classify_regime(evidence)


def holding(quantity=1.0, core=0.5, avg=100.0, mark=None, allocation=0.3):
    return {"quantity": quantity, "core_quantity": core, "avg_cost": avg, "mark_price": mark or 100.0, "allocation_pct": allocation}


def plan(closes, *, state="HOLD_CORE", h=None, safety=None, lifecycle=None, **kw):
    evidence, regime = regime_of(closes, safety=safety, **kw)
    hold = h or holding(mark=closes[-1])
    return plan_lifecycle(position_ref="spot:test", holding=hold, lifecycle={"state": state, **(lifecycle or {})},
                          evidence=evidence, regime=regime, settings=S, safety=safety, max_allocation_pct=0.35, now=END)


class EvidenceTests(unittest.TestCase):
    def test_future_bars_are_excluded_point_in_time(self):
        series = bars(STRONG + [1.0] * 5, end=END + timedelta(hours=20))
        evidence = regime_evidence(symbol="ETHUSDT", as_of=END, asset_bars=series)
        self.assertEqual(evidence["bars"], 120)
        self.assertEqual(evidence["data_cutoff"], iso_utc(END))

    def test_missing_inputs_are_explicitly_unavailable_not_zero(self):
        evidence = regime_evidence(symbol="ETHUSDT", as_of=END, asset_bars=bars(STRONG))
        items = evidence["evidence"]
        for key in ("relative_strength_vs_btc", "btc_regime", "breadth_above_slow_ema", "funding_crowding", "eth_btc_trend"):
            self.assertFalse(items[key]["available"], key)
            self.assertIsNone(items[key]["value"], key)
        self.assertLess(evidence["coverage"], 0.5)
        short = regime_evidence(symbol="ETHUSDT", as_of=END, asset_bars=bars(STRONG[:30]))
        self.assertEqual(classify_regime(short)["regime"], "UNKNOWN")

    def test_regimes(self):
        self.assertEqual(regime_of(STRONG)[1]["regime"], "STRONG_TREND")
        self.assertEqual(regime_of(EARLY)[1]["regime"], "EARLY_BULL")
        self.assertEqual(regime_of(RANGE)[1]["regime"], "RANGE")
        self.assertEqual(regime_of(BREAKDOWN)[1]["regime"], "BREAKDOWN")
        late = regime_of(LATE, btc=trend(rate=0.006), basket=BASKET_WEAK, volumes=LATE_VOLUME)[1]
        self.assertEqual(late["regime"], "LATE_BULL")
        self.assertGreaterEqual(late["warning_signs"], 2)

    def test_no_alt_season_assumption_breadth_is_measured(self):
        weak = regime_of(STRONG, basket=BASKET_WEAK)[0]["evidence"]["breadth_above_slow_ema"]
        strong = regime_of(STRONG, basket=BASKET_STRONG)[0]["evidence"]["breadth_above_slow_ema"]
        self.assertEqual(weak["value"], 0.0)
        self.assertEqual(strong["value"], 1.0)


class PolicyTests(unittest.TestCase):
    def test_transitions_are_typed(self):
        self.assertTrue(validate_transition("HOLD_CORE", "DISTRIBUTE"))
        self.assertFalse(validate_transition("CASH_WAIT", "DISTRIBUTE"))
        self.assertFalse(validate_transition("EXIT", "HOLD_CORE"))
        self.assertEqual(TRANSITIONS["EXIT"], {"CASH_WAIT", "EXIT"})

    def test_early_bull_adds_only_on_qualified_pullback(self):
        result = plan(EARLY, state="HOLD_CORE")
        self.assertIn(result["action"], {"ADD", "HOLD"})
        self.assertEqual(result["sell_quantity"], 0.0)
        full = plan(EARLY, h=holding(mark=EARLY[-1], allocation=0.5))
        self.assertEqual(full["action"], "HOLD")  # no room under the allocation limit

    def test_strong_trend_holds_and_lets_winner_run_overweight(self):
        result = plan(STRONG, h=holding(mark=STRONG[-1], allocation=0.45))
        self.assertEqual((result["action"], result["state_after"]), ("HOLD", "TREND_EXPANSION"))
        self.assertIn("LET_WINNER_RUN", result["reasons"])

    def test_concentration_shock_trims_tactical_never_core(self):
        result = plan(STRONG, h=holding(mark=STRONG[-1], allocation=0.9, core=0.6))
        self.assertEqual(result["action"], "TAKE_PARTIAL_PROFIT")
        self.assertGreater(result["sell_quantity"], 0)
        self.assertFalse(result["sells_core"])

    def test_range_holds_without_churn(self):
        result = plan(RANGE)
        self.assertEqual((result["action"], result["sell_quantity"]), ("HOLD", 0.0))

    def test_late_bull_distributes_progressively(self):
        result = plan(LATE, btc=trend(rate=0.006), basket=BASKET_WEAK, volumes=LATE_VOLUME,
                      h=holding(quantity=1.0, core=0.4, avg=LATE[-1] * 0.99, mark=LATE[-1]))
        self.assertEqual(result["action"], "DISTRIBUTE")
        self.assertAlmostEqual(result["sell_quantity"], 0.6 * S["distribute_step_fraction"])
        self.assertFalse(result["sells_core"])
        cooling = plan(LATE, btc=trend(rate=0.006), basket=BASKET_WEAK, volumes=LATE_VOLUME, state="DISTRIBUTE",
                       lifecycle={"last_action_at": iso_utc(END - timedelta(minutes=30))},
                       h=holding(quantity=1.0, core=0.4, avg=LATE[-1] * 0.99, mark=LATE[-1]))
        self.assertEqual(cooling["action"], "HOLD")
        self.assertIn("ACTION_COOLDOWN", cooling["reasons"])

    def test_false_distribution_signal_single_warning_does_not_distribute(self):
        dip = trend(116) + [trend(116)[-1] * 0.985 ** i for i in range(1, 5)]
        evidence, regime = regime_of(dip)
        self.assertLess(regime["warning_signs"], S["late_bull_warning_signs"])
        self.assertNotEqual(regime["regime"], "LATE_BULL")
        result = plan(dip, h=holding(mark=dip[-1], avg=dip[-1] * 0.99))
        self.assertNotIn(result["action"], {"DISTRIBUTE", "EXIT", "REDUCE"})

    def test_flash_crash_recovery_never_sells_core_or_adds(self):
        crash = {"state": "CRASH_MODE", "structural_breakdown": False}
        result = plan(STRONG, safety=crash, h=holding(mark=STRONG[-1] * 0.7))
        self.assertEqual(result["action"], "HOLD")
        self.assertIn("CRASH_SAFETY_HOLD", result["reasons"])
        untrusted = plan(BREAKDOWN, safety={"state": "MARKET_DATA_UNTRUSTED", "structural_breakdown": False})
        self.assertEqual(untrusted["action"], "HOLD")

    def test_structural_breakdown_sheds_tactical_then_exits_after_confirmation(self):
        first = plan(BREAKDOWN, h=holding(quantity=1.0, core=0.5, mark=BREAKDOWN[-1]))
        self.assertEqual((first["action"], first["state_after"]), ("REDUCE", "REDUCE"))
        self.assertAlmostEqual(first["sell_quantity"], 0.5)
        self.assertFalse(first["sells_core"])
        second = plan(BREAKDOWN, state="REDUCE", lifecycle={"breakdown_streak": first["breakdown_streak"]},
                      h=holding(quantity=0.5, core=0.5, mark=BREAKDOWN[-1]))
        self.assertEqual((second["action"], second["state_after"]), ("EXIT", "EXIT"))
        self.assertTrue(second["sells_core"])
        structural = plan(BREAKDOWN, safety={"state": "CRASH_MODE", "structural_breakdown": True},
                          h=holding(quantity=0.5, core=0.5, mark=BREAKDOWN[-1]))
        self.assertEqual(structural["action"], "EXIT")

    def test_profit_giveback_protects_with_tactical(self):
        result = plan(RANGE, lifecycle={"peak_mark": 140.0}, h=holding(avg=100.0, mark=RANGE[-1], core=0.5))
        self.assertEqual(result["action"], "PROTECT_PROFIT")
        self.assertFalse(result["sells_core"])

    def test_recommendation_is_bounded_by_deterministic_plan(self):
        base = plan(LATE, btc=trend(rate=0.006), basket=BASKET_WEAK, volumes=LATE_VOLUME,
                    h=holding(quantity=1.0, core=0.4, avg=LATE[-1] * 0.99, mark=LATE[-1]))
        self.assertEqual(merge_recommendation(base, {"action": "EXIT"})["action"], "DISTRIBUTE")
        self.assertIn("RECOMMENDATION_CLAMPED", merge_recommendation(base, {"action": "EXIT"})["reasons"])
        held = merge_recommendation(base, {"action": "HOLD", "state_after": "HOLD_CORE"})
        self.assertEqual((held["action"], held["sell_quantity"]), ("HOLD", 0.0))
        smaller = merge_recommendation(base, {"action": "DISTRIBUTE", "sell_fraction": 0.05})
        self.assertAlmostEqual(smaller["sell_quantity"], 0.05)
        bigger = merge_recommendation(base, {"action": "DISTRIBUTE", "sell_fraction": 0.9})
        self.assertAlmostEqual(bigger["sell_quantity"], base["sell_quantity"])
        with self.assertRaises(PaperTradingError):
            merge_recommendation(base, {"action": "YOLO"})

    def test_settings_validation(self):
        self.assertEqual(validate_lifecycle_settings({})["slow_ema_bars"], 48)
        for bad in ({"fast_ema_bars": 60}, {"distribute_step_fraction": 2}, {"nope": 1}, {"enabled": "yes"}):
            with self.assertRaises(PaperTradingError):
                validate_lifecycle_settings(bad)


class BenchmarkTests(unittest.TestCase):
    def test_all_arms_aligned_and_honest(self):
        series = trend(200) + [trend(200)[-1] * 0.995 ** i for i in range(1, 120)]
        report = run_benchmarks(bars(series), symbol="ETHUSDT", btc_bars=bars(trend(319, 0.001)))
        names = [arm["arm"] for arm in report["arms"]]
        self.assertEqual(sorted(names), sorted(ARMS))
        self.assertTrue(report["claim"].startswith("NOT_EVIDENCE_OF_SUPERIORITY"))
        for arm in report["arms"]:
            if arm.get("status") == "NOT_APPLICABLE":
                continue
            for key in ("total_return", "max_drawdown", "turnover", "fees_usdt", "upside_capture", "downside_capture",
                        "peak_capture_ratio", "profit_giveback", "time_in_cash", "premature_exits", "avoided_drawdowns"):
                self.assertIn(key, arm)
        lifecycle = next(arm for arm in report["arms"] if arm["arm"] == "ai_lifecycle")
        self.assertIsNone(lifecycle["ai_cost_usdt"])  # unavailable, never zero-filled
        self.assertIsNone(lifecycle["net_economic_return"])
        bh = next(arm for arm in report["arms"] if arm["arm"] == "buy_hold")
        self.assertEqual(bh["ai_cost_usdt"], 0.0)

    def test_grid_only_in_range_and_too_short_series_refused(self):
        ranged = run_benchmarks(bars([100 + 3 * math.sin(i / 3) for i in range(160)]), symbol="ETHUSDT")
        self.assertNotEqual(next(a for a in ranged["arms"] if a["arm"] == "grid").get("status"), "NOT_APPLICABLE")
        with self.assertRaises(PaperTradingError):
            run_benchmarks(bars(STRONG[:40]), symbol="ETHUSDT")


class LifecycleIntegrationTests(PortfolioCase):
    def setUp(self):
        super().setUp()
        self.series = {"ETH": STRONG, "BTC": FLAT}
        self.os._regime_bars = lambda base, now, n=120: bars(self.series.get(base, trend()), end=END)

    def spot(self, amount=25.0, mode=None):
        self.buy_spot(amount=amount)
        ref = next(v["position_ref"] for v in self.os.list_positions() if v["market_type"] == "spot")
        if mode:
            self.os.set_management_mode(ref, mode, confirm=True)
        return ref

    def test_recommend_only_holding_gets_a_proposal_then_user_applies(self):
        ref = self.spot()
        self.series["ETH"] = BREAKDOWN
        review = self.os.lifecycle_review(ref)
        self.assertEqual((review["status"], review["plan"]["action"]), ("PROPOSED", "REDUCE"))
        before = self.os.position(ref)["quantity"]
        self.assertEqual(before, self.os.position(ref)["quantity"])  # nothing executed
        self.assertIsNotNone(self.os.lifecycle_view(ref)["lifecycle"]["pending_plan"])
        with self.assertRaises(ConfirmationRequired):
            self.os.apply_lifecycle(ref)
        applied = self.os.apply_lifecycle(ref, confirm=True)
        self.assertEqual(applied["status"], "APPLIED")
        self.assertTrue(self.os.reconcile()["ok"])
        self.assertIsNone(self.os.lifecycle_view(ref)["lifecycle"]["pending_plan"])

    def test_auto_paper_executes_as_ai_through_safety_and_kill_switch_blocks(self):
        ref = self.spot(mode="AUTO_PAPER")
        self.series["ETH"] = BREAKDOWN
        self.os.set_kill_switch("AI_MANAGEMENT_PAUSED")
        blocked = self.os.lifecycle_review(ref)
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIn("AUTHORITY_DENIED", blocked["result"]["reason"])
        self.os.set_kill_switch("NORMAL", confirm=True)
        applied = self.os.lifecycle_review(ref, now=self.clock() + timedelta(minutes=1))
        self.assertEqual(applied["status"], "APPLIED")
        self.assertEqual(self.os.lifecycle_events(ref)[0]["source"], "AI")
        journal = self.os.management_events(ref)
        self.assertTrue(any(e["source"] == "AI" for e in journal))
        self.assertTrue(self.os.reconcile()["ok"])

    def test_crash_mode_holds_core_even_in_auto_paper(self):
        ref = self.spot(mode="AUTO_PAPER")
        self.series["ETH"] = BREAKDOWN
        crash = dict(self.os.assess("fixture:spot:ETH_USDT", force=True), state="CRASH_MODE", structural_breakdown=False)
        self.os.assess = lambda instrument_id, force=False: crash
        review = self.os.lifecycle_review(ref)
        self.assertEqual((review["status"], review["plan"]["action"]), ("HOLD", "HOLD"))
        self.assertIn("CRASH_SAFETY_HOLD", review["plan"]["reasons"])

    def test_scheduler_reviews_only_when_due_and_hold_is_recorded(self):
        ref = self.spot()
        first = self.os.lifecycle_due()
        self.assertEqual([r["position_ref"] for r in first], [ref])
        self.assertEqual(first[0]["action"], "HOLD")
        self.assertEqual(self.os.lifecycle_due(), [])  # not due again for review_interval_minutes
        later = self.clock() + timedelta(minutes=S["review_interval_minutes"] + 1)
        self.assertEqual(len(self.os.lifecycle_due(now=later)), 1)
        view = self.os.position(ref)
        self.assertEqual(view["lifecycle_state"], "TREND_EXPANSION")
        self.assertEqual(view["lifecycle"]["events"][0]["status"], "HOLD")

    def test_stale_proposal_is_refused(self):
        ref = self.spot()
        self.series["ETH"] = BREAKDOWN
        self.os.lifecycle_review(ref, now=self.clock() - timedelta(minutes=S["review_interval_minutes"] + 5))
        with self.assertRaisesRegex(PaperTradingError, "STALE_DECISION"):
            self.os.apply_lifecycle(ref, confirm=True)

    def test_benchmark_and_export(self):
        self.series["ETH"] = trend(360)
        self.os._regime_bars = lambda base, now, n=120: bars(self.series.get(base, trend(n))[-n:], end=END)
        report = self.os.lifecycle_benchmark("fixture:spot:ETH_USDT", bars=360)
        self.assertEqual(report["data_origin"], self.catalog.data_origin)
        self.assertEqual(len(self.os.lifecycle_benchmarks()), 1)
        ref = self.spot()
        self.os.lifecycle_review(ref)
        files = self.os.export_files()
        self.assertEqual(len(files["lifecycle_events"]), 1)
        self.assertEqual(len(files["lifecycle_benchmarks"]), 1)
        self.assertEqual(files["lifecycle_states"][0]["position_ref"], ref)

    def test_lifecycle_settings_round_trip(self):
        settings = self.os.update_settings({"lifecycle": {"review_interval_minutes": 60}})
        self.assertEqual(settings["lifecycle"]["review_interval_minutes"], 60.0)
        with self.assertRaises(PaperTradingError):
            self.os.update_settings({"lifecycle": {"max_tactical_step_fraction": 5}})


if __name__ == "__main__":
    unittest.main()
