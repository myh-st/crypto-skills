"""Trend sleeves engine (EXP-002): signal functions, trailing stops, long/short, safety gates,
idempotency, and sub-account accounting through the real runtime. Synthetic data only."""

from __future__ import annotations

import bisect
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crypto_eval.paper_contracts import PaperTradingError, default_experiment_config, iso_utc, validate_experiment_config
from crypto_eval.paper_market import MarketDataError
from crypto_eval.paper_runtime import PaperRuntime, PaperScheduler, PaperStore
from crypto_eval.sleeves import (
    COHORTS,
    DEFAULT_SLEEVES,
    capital_cohorts,
    daily_closes,
    stream_symbols,
    donchian_signal,
    trail_stop,
    tsmom_weights,
    validate_sleeves,
    xsmom_weights,
)

H4 = 4 * 3600
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def lane(prices, start=START):
    out = []
    for i, p in enumerate(prices):
        t = start + timedelta(hours=4 * i)
        out.append({"open_time": iso_utc(t), "close_time": iso_utc(t + timedelta(hours=4)), "open": p, "high": p * 1.002,
                    "low": p * 0.998, "close": p, "volume": 1.0})
    return out


class SignalTests(unittest.TestCase):
    def test_donchian_long_short_and_none(self):
        flat = [100.0] * 60
        self.assertIsNone(donchian_signal(lane(flat), DEFAULT_SLEEVES["donchian"]))
        up = donchian_signal(lane(flat + [110.0]), DEFAULT_SLEEVES["donchian"])
        self.assertEqual(up["side"], "long")
        self.assertAlmostEqual(up["stop_dist"], 2.0 * up["atr"])
        self.assertAlmostEqual(up["trail_dist"], 6.0 * up["atr"])
        down = donchian_signal(lane(flat + [90.0]), DEFAULT_SLEEVES["donchian"])
        self.assertEqual(down["side"], "short")
        self.assertIsNone(donchian_signal(lane([100.0] * 20 + [120.0]), DEFAULT_SLEEVES["donchian"]))  # not enough history

    def test_trailing_stop_only_reduces_risk(self):
        best, stop = trail_stop("long", 90.0, 100.0, 120.0, 2.0, 6.0)
        self.assertEqual((best, stop), (120.0, 108.0))
        best, stop = trail_stop("long", stop, best, 110.0, 2.0, 6.0)  # pullback: stop never loosens
        self.assertEqual((best, stop), (120.0, 108.0))
        best, stop = trail_stop("short", 110.0, 100.0, 80.0, 2.0, 6.0)
        self.assertEqual((best, stop), (80.0, 92.0))
        self.assertEqual(trail_stop("short", stop, best, 90.0, 2.0, 6.0), (80.0, 92.0))

    def test_momentum_weights_take_both_sides(self):
        days = list(range(100))
        up = {d: 100 * 1.01 ** d for d in days}
        down = {d: 100 * 0.99 ** d for d in days}
        ts = tsmom_weights({"AUSDT": up, "BUSDT": down}, 99, DEFAULT_SLEEVES["tsmom"], 3.0)
        self.assertGreater(ts["AUSDT"], 0)
        self.assertLess(ts["BUSDT"], 0)
        closes = {f"S{i}USDT": {d: 100 * (1 + 0.002 * (i - 3)) ** d * (1 + 0.01 * math.sin(d + i)) for d in days} for i in range(7)}
        xs = xsmom_weights(closes, 99, DEFAULT_SLEEVES["xsmom"], 3.0)
        self.assertEqual(sum(1 for w in xs.values() if w > 0), 2)
        self.assertEqual(sum(1 for w in xs.values() if w < 0), 2)
        self.assertGreater(xs["S6USDT"], 0)
        self.assertLess(xs["S0USDT"], 0)

    def test_margin_aware_gross_cap_keeps_a_long_short_book_balanced(self):
        from crypto_eval.sleeves import effective_gross_cap

        self.assertAlmostEqual(effective_gross_cap(validate_sleeves({}), "xsmom"), 3.0)            # off by default: unchanged
        with self.assertRaises(PaperTradingError):
            validate_sleeves({"margin_scaling": "yes"})
        sleeves = validate_sleeves({"xsmom": {"vol_target": 0.057}, "margin_scaling": True})    # the 2x experiment
        self.assertAlmostEqual(effective_gross_cap(sleeves, "xsmom"), 1.98)   # 0.99 x leverage 2
        self.assertAlmostEqual(effective_gross_cap(validate_sleeves({"xsmom": {"leverage": 5}, "margin_scaling": True}), "xsmom"), 3.0)
        days = list(range(100))
        closes = {f"S{i}USDT": {d: 100 * (1 + 0.002 * (i - 3)) ** d * (1 + 0.01 * math.sin(d + i)) for d in days} for i in range(7)}
        w = xsmom_weights(closes, 99, sleeves["xsmom"], effective_gross_cap(sleeves, "xsmom"))
        self.assertLessEqual(sum(abs(v) for v in w.values()), 1.98 + 1e-9)
        longs, shorts = [v for v in w.values() if v > 0], [v for v in w.values() if v < 0]
        self.assertEqual((len(longs), len(shorts)), (2, 2))                  # scaled evenly, never one-sided

    def test_daily_closes_use_the_bar_closing_at_midnight(self):
        bars = lane([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])  # 00:00 open → the 6th bar closes at next 00:00
        self.assertEqual(daily_closes(bars), {int(START.timestamp()) // 86400: 6.0})

    def test_config_validation(self):
        self.assertEqual(validate_sleeves({})["universe"], DEFAULT_SLEEVES["universe"])
        for bad in ({"universe": ["BTCUSDT"]}, {"donchian": {"risk": 0.5}}, {"xsmom": {"k": 5}}, {"nope": 1}, {"leverage": 3},
                    {"tsmom": {"leverage": 9}}, {"donchian": {"nope": 1}}):
            with self.assertRaises(PaperTradingError):
                validate_sleeves(bad)
        config = validate_experiment_config({**default_experiment_config(), "strategy_engine": "sleeves_v1"})
        self.assertEqual(config["sleeves"]["tsmom"]["look_days"], 60)
        self.assertEqual(capital_cohorts(config), ["sleeve-don", "sleeve-ts", "sleeve-xs"])
        self.assertEqual(capital_cohorts(default_experiment_config()), ["primary"])
        self.assertEqual(validate_experiment_config({**default_experiment_config(), "label": "EXP-002"})["label"], "EXP-002")
        with self.assertRaises(PaperTradingError):
            validate_experiment_config({**default_experiment_config(), "label": "<script>"})


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class SyntheticProvider:
    provider_id = "synthetic-4h"
    data_origin = "FIXTURE"

    def __init__(self, series: dict[str, list[float]]):
        self.bars = {s: lane(p, START - timedelta(hours=4 * 400)) for s, p in series.items()}
        self.close_ts = {s: [int(datetime.fromisoformat(b["close_time"].replace("Z", "+00:00")).timestamp()) for b in lane_]
                         for s, lane_ in self.bars.items()}

    def validate_symbols(self, symbols):
        return {"supported": list(symbols), "excluded": []}

    def fetch_snapshot(self, symbol, as_of):
        raise MarketDataError("no snapshot")

    def fetch_history(self, symbol, interval, *, bars, as_of):
        i = bisect.bisect_right(self.close_ts[symbol], int(as_of.timestamp()))
        return self.bars[symbol][max(0, i - bars):i]

    def fetch_monitor_bars(self, symbol, after, as_of):
        ts = self.close_ts[symbol]
        return self.bars[symbol][bisect.bisect_right(ts, int(after.timestamp())):bisect.bisect_right(ts, int(as_of.timestamp()))]

    def fetch_funding_rate(self, symbol, as_of):
        return 0.0001


class SleevesFixture(unittest.TestCase):
    """A running sleeves experiment on synthetic 4h data (shared by the engine and day-view tests)."""

    def setUp(self):
        universe = ["AUSDT", "BUSDT", "CUSDT", "DUSDT"]
        n = 800
        series = {
            "AUSDT": [100 * 1.004 ** i for i in range(n)],          # strong uptrend
            "BUSDT": [100 * 0.996 ** i for i in range(n)],          # strong downtrend
            "CUSDT": [100 + 5 * math.sin(i / 7) for i in range(n)],  # range
            "DUSDT": [100 * 1.001 ** i for i in range(n)],          # mild uptrend
        }
        self.provider = SyntheticProvider(series)
        self.clock = Clock(START)
        self.store = PaperStore(Path(tempfile.mkdtemp()) / "s.sqlite3")
        self.addCleanup(self.store.close)
        self.runtime = PaperRuntime(self.store, clock=self.clock, market_provider=self.provider)
        config = dict(self.store.experiment()["config"])
        config.update({"strategy_engine": "sleeves_v1", "starting_balance_usdt": 300.0, "evaluation_arms": ["quant"], "primary_arm": "quant",
                       "jev_enabled": False, "gpt_escalation_enabled": False,
                       "sleeves": {"universe": universe, "xsmom": {"k": 1}, "tsmom": {"look_days": 20}}})
        config["sleeves"]["xsmom"]["look_days"] = 20
        self.store.save_experiment(config)
        self.runtime.start()
        self.scheduler = PaperScheduler(self.runtime)

    def run_until(self, days: int):
        t = START
        while t < START + timedelta(days=days):
            self.clock.value = t + timedelta(seconds=61)
            self.runtime.monitor_once(as_of=self.clock.value)
            self.scheduler.cycle_tick(self.clock.value)
            t += timedelta(hours=4)


class EngineTests(SleevesFixture):
    def test_sleeves_open_long_and_short_in_separate_subaccounts(self):
        self.run_until(9)
        positions = self.store.open_positions("EXP-001")
        by = {(p["cohort"], p["symbol"]): p["side"] for p in positions}
        self.assertEqual(by.get(("sleeve-ts", "AUSDT")), "long")
        self.assertEqual(by.get(("sleeve-ts", "BUSDT")), "short")
        self.assertEqual(by.get(("sleeve-xs", "AUSDT")), "long")
        self.assertEqual(by.get(("sleeve-xs", "BUSDT")), "short")
        self.assertIn(("sleeve-don", "AUSDT"), by)
        wallets = {c: float(self.store.wallet_summary("EXP-001", c)["starting_balance"]) for c in COHORTS.values()}
        self.assertEqual(wallets, {c: 100.0 for c in COHORTS.values()})
        self.assertTrue(self.runtime.portfolio.reconcile(escalate=False)["ok"])

    def test_trailing_stop_ratchets_on_open_donchian_position(self):
        self.run_until(4)
        pos = [p for p in self.store.open_positions("EXP-001") if p["cohort"] == "sleeve-don" and p["symbol"] == "AUSDT"][0]
        first = float(pos["stop_price"])
        self.run_until(8)
        again = [p for p in self.store.open_positions("EXP-001") if p["position_id"] == pos["position_id"]]
        self.assertTrue(again)
        self.assertGreater(float(again[0]["stop_price"]), first)

    def test_tick_is_idempotent_per_boundary(self):
        self.run_until(3)
        count = len(self.store.open_positions("EXP-001"))
        now = self.clock.value
        self.assertEqual(self.runtime.sleeves.tick(now)["status"], "already_done")
        self.assertEqual(len(self.store.open_positions("EXP-001")), count)

    def test_kill_switch_blocks_new_entries_but_not_trailing(self):
        self.runtime.portfolio.set_kill_switch("NO_NEW_ENTRIES")
        self.run_until(3)
        self.assertEqual(self.store.open_positions("EXP-001"), [])
        blocked = self.store._query("SELECT summary_json FROM sleeve_ticks ORDER BY boundary DESC LIMIT 1")[0][0]
        self.assertIn("KILL_SWITCH_NO_NEW_ENTRIES", blocked)

    def test_trail_distance_is_fixed_at_entry(self):
        self.run_until(4)
        pos = [p for p in self.store.open_positions("EXP-001") if p["cohort"] == "sleeve-don" and p["symbol"] == "AUSDT"][0]
        first = self.store._query("SELECT trail_dist FROM sleeve_trails WHERE position_id=?", (pos["position_id"],))[0][0]
        self.run_until(8)
        again = self.store._query("SELECT trail_dist FROM sleeve_trails WHERE position_id=?", (pos["position_id"],))[0][0]
        self.assertEqual(first, again)

    def test_capital_rebalance_moves_free_cash_and_reconciles(self):
        self.run_until(9)
        config = self.store.experiment()["config"]
        before = {c: self.store.wallet_summary("EXP-001", c) for c in COHORTS.values()}
        total = sum(float(w["equity"]) for w in before.values())
        target = total / 3
        summary = {}
        boundary = iso_utc(self.clock.value)
        self.runtime.sleeves._rebalance_capital("EXP-001", config, boundary, self.clock.value, summary)
        self.runtime.sleeves._rebalance_capital("EXP-001", config, boundary, self.clock.value, summary)  # once per boundary
        after = {c: self.store.wallet_summary("EXP-001", c) for c in COHORTS.values()}
        self.assertAlmostEqual(sum(float(w["equity"]) for w in after.values()), total, places=6)
        transfers = self.store._query("SELECT from_cohort, amount FROM wallet_transfers")
        self.assertTrue(transfers)
        for row in transfers:  # only free cash moves; open positions and their margin stay put
            donor = before[row["from_cohort"]]
            self.assertLessEqual(float(row["amount"]), float(donor["equity"]) - float(donor["margin_used"]) + 1e-6)
        self.assertLess(max(abs(float(w["equity"]) - target) for w in after.values()),
                        max(abs(float(w["equity"]) - target) for w in before.values()))
        self.assertTrue(self.runtime.portfolio.reconcile(escalate=False)["ok"])
        with self.store.transaction() as db:  # an unledgered cash change is caught
            db.execute("UPDATE wallets SET cash_balance=cash_balance+1 WHERE cohort='sleeve-xs'")
        self.assertFalse(self.runtime.portfolio.reconcile(escalate=False)["ok"])

    def test_campaign_summary_shows_each_sleeve_book(self):
        self.run_until(9)
        view = self.runtime.governance.campaign_summary()
        self.assertEqual(view["engine"], "sleeves_v1")
        rows = {r["sleeve"]: r for r in view["sleeves"]["sleeves"]}
        self.assertEqual(set(rows), {"donchian", "tsmom", "xsmom"})
        self.assertIn("AUSDT", rows["tsmom"]["long"])
        self.assertIn("BUSDT", rows["tsmom"]["short"])
        self.assertEqual((rows["donchian"]["leverage"], rows["tsmom"]["leverage"]), (3, 2))
        total = sum(float(self.store.wallet_summary("EXP-001", c)["equity"]) for c in COHORTS.values())
        self.assertAlmostEqual(sum(r["equity_usdt"] for r in rows.values()), total, places=3)
        self.assertIsNotNone(view["sleeves"]["last_tick"])

    def test_ai_position_review_never_touches_sleeve_positions(self):
        self.run_until(5)
        self.assertTrue(self.store.open_positions("EXP-001"))
        self.assertEqual(self.runtime.portfolio.review_positions(force=True), [])
        self.assertEqual(self.store._query("SELECT COUNT(*) FROM ai_calls")[0][0], 0)

    def test_first_tick_bootstraps_momentum_without_waiting_for_the_weekly_slot(self):
        self.clock.value = START + timedelta(hours=8, seconds=61)  # 08:00 UTC: not a daily boundary
        self.runtime.monitor_once(as_of=self.clock.value)
        self.scheduler.cycle_tick(self.clock.value)
        cohorts = {p["cohort"] for p in self.store.open_positions("EXP-001")}
        self.assertTrue({"sleeve-ts", "sleeve-xs"} <= cohorts)
        self.clock.value = START + timedelta(hours=12, seconds=61)  # next 4h tick: no momentum re-run off-slot
        before = self.store._query("SELECT COUNT(*) FROM sleeve_decisions WHERE sleeve IN ('tsmom','xsmom')")[0][0]
        self.scheduler.cycle_tick(self.clock.value)
        self.assertEqual(self.store._query("SELECT COUNT(*) FROM sleeve_decisions WHERE sleeve IN ('tsmom','xsmom')")[0][0], before)

    def test_blocked_momentum_entries_retry_on_the_next_tick_with_the_slot_weights(self):
        self.runtime.portfolio.set_kill_switch("NO_NEW_ENTRIES")
        self.clock.value = START + timedelta(hours=8, seconds=61)
        self.scheduler.cycle_tick(self.clock.value)
        self.assertEqual(self.store.open_positions("EXP-001"), [])
        pending = json.loads(self.store._query("SELECT summary_json FROM sleeve_ticks ORDER BY boundary DESC LIMIT 1")[0][0])["pending"]
        self.assertIn("AUSDT", pending["tsmom"])
        self.runtime.portfolio.set_kill_switch("NORMAL", confirm=True)
        self.clock.value = START + timedelta(hours=12, seconds=61)  # not a momentum slot
        self.runtime.monitor_once(as_of=self.clock.value)
        self.scheduler.cycle_tick(self.clock.value)
        cohorts = {(p["cohort"], p["symbol"]) for p in self.store.open_positions("EXP-001")}
        self.assertIn(("sleeve-ts", "AUSDT"), cohorts)
        self.assertIn(("sleeve-xs", "AUSDT"), cohorts)

    def test_live_feed_covers_the_sleeve_universe(self):
        config = self.store.experiment()["config"]
        symbols = stream_symbols(config)
        self.assertTrue(set(config["symbols"]) <= set(symbols))
        self.assertTrue(set(config["sleeves"]["universe"]) <= set(symbols))
        self.assertEqual(stream_symbols(default_experiment_config()), default_experiment_config()["symbols"])

    def test_a_sleeve_wallet_read_before_the_first_tick_gets_its_share_not_the_whole_book(self):
        # Regression: the monitor's snapshot read the sleeve wallets before ensure_wallets ran, and
        # lazy creation seeded each with the full starting balance (3x the capital).
        perp = self.runtime.portfolio._perp_wallet()
        self.assertAlmostEqual(float(perp["starting_balance"]), 300.0)
        for cohort in COHORTS.values():
            self.assertAlmostEqual(float(self.store.wallet_summary("EXP-001", cohort)["starting_balance"]), 100.0)
        self.run_until(2)
        self.assertAlmostEqual(sum(float(self.store.wallet_summary("EXP-001", c)["starting_balance"]) for c in COHORTS.values()), 300.0)

    def test_starting_balance_edit_before_any_trade_resets_every_untouched_sleeve_wallet(self):
        self.store.wallet_summary("EXP-001", "sleeve-ts")  # created at 100 (300 / 3)
        self.runtime.stop()
        config = dict(self.store.experiment()["config"])
        config["starting_balance_usdt"] = 600.0
        self.store.save_experiment(config)
        self.assertAlmostEqual(float(self.store.wallet_summary("EXP-001", "sleeve-ts")["starting_balance"]), 200.0)
        self.assertAlmostEqual(float(self.store.wallet_summary("EXP-001", "primary")["starting_balance"]), 600.0)


class Exp002SetupTests(unittest.TestCase):
    """The committed EXP-002 setup helper produces a config and settings the server accepts."""

    def test_setup_helper_builds_a_valid_sleeves_experiment(self):
        import importlib.util

        from crypto_eval.portfolio_store import default_portfolio_settings, validate_portfolio_settings

        path = Path(__file__).resolve().parents[1] / "scripts" / "paper_exp002_setup.py"
        spec = importlib.util.spec_from_file_location("paper_exp002_setup", path)
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        config = validate_experiment_config(setup.experiment_config(default_experiment_config(), recorded_at=iso_utc(START)))
        self.assertEqual((config["strategy_engine"], config["label"], config["starting_balance_usdt"]), ("sleeves_v1", "EXP-002", 500.0))
        self.assertEqual(config["sleeves"]["universe"], DEFAULT_SLEEVES["universe"])
        self.assertFalse(config["jev_enabled"] or config["gpt_escalation_enabled"])
        self.assertEqual(config["ai_budget"]["limit_action"], "BLOCK_PAID_AI")
        self.assertEqual(capital_cohorts(config), list(COHORTS.values()))
        settings = validate_portfolio_settings(setup.portfolio_settings(default_portfolio_settings()))
        self.assertFalse(settings["ai_spot"]["enabled"] or settings["review"]["enabled"])
        self.assertEqual(settings["promotion"]["min_completed_trades"], 80)


class StopDistanceCapTests(unittest.TestCase):
    def test_only_an_engine_risk_config_widens_the_stop_cap_and_never_past_45_percent(self):
        from crypto_eval.paper_contracts import INTENT_SCHEMA_VERSION, TradingIntent
        from crypto_eval.paper_runtime import RiskEngine

        config = {**default_experiment_config(), "symbols": ["BTCUSDT"], "risk_per_trade": 0.05}
        now = START

        def decide(stop_pct, cap=None):
            intent = TradingIntent.from_dict({
                "schema_version": INTENT_SCHEMA_VERSION, "action": "open", "symbol": "BTCUSDT", "side": "long", "order_type": "market",
                "entry_price": 100.0, "stop_price": 100.0 * (1 - stop_pct), "target_price": 2000.0, "reduce_only": False,
                "reduce_fraction": None, "position_id": None, "reason": "t", "source_arm": "quant", "as_of": iso_utc(now)})
            cfg = config if cap is None else {**config, "max_stop_distance_pct": cap}
            return RiskEngine().evaluate(intent, cfg, equity=1000.0, margin_used=0.0, open_positions=[], data_cutoff=iso_utc(now),
                                         decision_as_of=now, leverage=2).code

        self.assertEqual(decide(0.30), "STOP_DISTANCE")
        self.assertEqual(decide(0.30, cap=0.4), "APPROVED")
        self.assertEqual(decide(0.46, cap=0.9), "STOP_DISTANCE")
        # A catastrophe stop capped at exactly max_stop_distance_pct must not be lost to float rounding.
        for entry in (0.2537, 0.2539, 4.7409, 84011.799, 1.141):
            intent = TradingIntent.from_dict({
                "schema_version": INTENT_SCHEMA_VERSION, "action": "open", "symbol": "BTCUSDT", "side": "long", "order_type": "market",
                "entry_price": entry, "stop_price": entry - entry * 0.4, "target_price": entry * 20, "reduce_only": False,
                "reduce_fraction": None, "position_id": None, "reason": "t", "source_arm": "quant", "as_of": iso_utc(now)})
            code = RiskEngine().evaluate(intent, {**config, "max_stop_distance_pct": 0.4}, equity=1000.0, margin_used=0.0, open_positions=[],
                                         data_cutoff=iso_utc(now), decision_as_of=now, leverage=2).code
            self.assertEqual(code, "APPROVED", entry)


if __name__ == "__main__":
    unittest.main()
