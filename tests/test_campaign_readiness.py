"""Campaign-readiness fixes found by the 88-day Gate replay: the loss-streak pause expires,
risk stops are visible incidents/attention, and a pre-start capital edit leaves no stale
seed equity point. Fixture data only."""

from __future__ import annotations

import unittest
from datetime import timedelta

from crypto_eval.paper_contracts import PaperTradingError, default_experiment_config, iso_utc, validate_experiment_config

from tests.test_portfolio_os import NOW, PortfolioCase


class LossStreakPauseTests(PortfolioCase):
    def close_trade(self, pnl: float, closed_at, cohort: str = "primary") -> None:
        stamp = iso_utc(closed_at)
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO positions(position_id, experiment_id, cycle_id, symbol, cohort, is_shadow, source_arm, side, quantity, "
                "opened_quantity, entry_price, mark_price, stop_price, target_price, leverage, margin, liquidation_price, entry_fee, "
                "entry_fee_remaining, status, opened_at, closed_at, realized_pnl, closed_pnl_recorded, last_funding_slot) "
                "VALUES(?, 'EXP-001', ?, 'BTCUSDT', ?, 0, 'quant', 'long', 0, 1, 100, 100, 99, 101, 3, 1, 70, 0, 0, 'closed', ?, ?, ?, 1, ?)",
                (f"p-{closed_at.timestamp()}-{cohort}", f"c-{closed_at.timestamp()}", cohort, stamp, stamp, pnl, stamp),
            )

    def config(self, **overrides):
        return {**self.store.experiment()["config"], **overrides}

    def test_pause_blocks_then_expires_and_resets(self):
        config = self.config(max_consecutive_losses=3, loss_streak_pause_minutes=60)
        start = NOW - timedelta(hours=3)
        for i in range(3):
            self.close_trade(-1.0, start + timedelta(minutes=10 * i))
        self.clock.value = start + timedelta(minutes=30)
        streak, until = self.runtime._loss_streak("EXP-001", "primary", config)
        self.assertEqual(streak, 3)
        self.assertEqual(until, start + timedelta(minutes=80))
        self.clock.value = start + timedelta(minutes=81)
        self.assertEqual(self.runtime._loss_streak("EXP-001", "primary", config), (0, None))

    def test_one_loss_after_expiry_does_not_rearm_the_old_streak(self):
        config = self.config(max_consecutive_losses=3, loss_streak_pause_minutes=60)
        start = NOW - timedelta(hours=5)
        for i in range(3):
            self.close_trade(-1.0, start + timedelta(minutes=10 * i))
        self.close_trade(-1.0, start + timedelta(minutes=200))  # after the pause expired
        self.clock.value = start + timedelta(minutes=210)
        self.assertEqual(self.runtime._loss_streak("EXP-001", "primary", config)[0], 1)

    def test_win_resets_and_cohorts_are_independent(self):
        config = self.config(max_consecutive_losses=2, loss_streak_pause_minutes=600)
        start = NOW - timedelta(hours=2)
        self.close_trade(-1.0, start)
        self.close_trade(2.0, start + timedelta(minutes=5))
        self.close_trade(-1.0, start + timedelta(minutes=10))
        self.close_trade(-1.0, start, cohort="arm-quant")
        self.close_trade(-1.0, start + timedelta(minutes=1), cohort="arm-quant")
        self.clock.value = start + timedelta(minutes=20)
        self.assertEqual(self.runtime._loss_streak("EXP-001", "primary", config)[0], 1)
        self.assertEqual(self.runtime._loss_streak("EXP-001", "arm-quant", config)[0], 2)

    def test_pause_minutes_validation(self):
        base = default_experiment_config()
        self.assertEqual(validate_experiment_config(base)["loss_streak_pause_minutes"], 1440)
        for bad in (0, 10, 100_000, "x"):
            with self.assertRaises(PaperTradingError):
                validate_experiment_config({**base, "loss_streak_pause_minutes": bad})


class RiskVisibilityTests(PortfolioCase):
    def test_drawdown_halt_is_critical_and_needs_attention(self):
        config = self.store.experiment()["config"]
        self.runtime._record_risk_pause("EXP-001", "DRAWDOWN_LIMIT", config)
        self.runtime._record_risk_pause("EXP-001", "DRAWDOWN_LIMIT", config)  # deduplicated
        halts = [i for i in self.runtime.resilience.incidents(status="OPEN") if i["kind"] == "RISK_HALT"]
        self.assertEqual(len(halts), 1)
        self.assertEqual(halts[0]["severity"], "CRITICAL")
        items = [i for i in self.os.attention()["items"] if i["kind"] == "incident"]
        self.assertEqual(items[0]["severity"], "CRITICAL")
        self.runtime._record_risk_pause("EXP-001", "APPROVED", config)
        self.assertTrue([i for i in self.runtime.resilience.incidents(status="OPEN") if i["kind"] == "RISK_HALT"])  # needs review

    def test_loss_streak_pause_resolves_when_entries_resume(self):
        config = self.store.experiment()["config"]
        self.runtime._record_risk_pause("EXP-001", "LOSS_STREAK_LIMIT", config)
        self.assertTrue([i for i in self.runtime.resilience.incidents(status="OPEN") if i["kind"] == "RISK_PAUSE"])
        self.runtime._record_risk_pause("EXP-001", "APPROVED", config)
        self.assertFalse([i for i in self.runtime.resilience.incidents(status="OPEN") if i["kind"] == "RISK_PAUSE"])


class SeedEquityTests(PortfolioCase):
    def test_capital_edit_before_start_replaces_the_seed_point(self):
        config = dict(self.store.experiment()["config"], starting_balance_usdt=300.0)
        self.store.save_experiment(config)
        rows = self.store._query("SELECT equity FROM equity WHERE experiment_id='EXP-001' AND cohort='primary'")
        self.assertEqual([float(r["equity"]) for r in rows], [300.0])
        self.runtime.start()
        daily_loss, drawdown, streak = self.runtime._risk_statistics("EXP-001", "primary")
        self.assertEqual((daily_loss, drawdown, streak), (0.0, 0.0, 0))


if __name__ == "__main__":
    unittest.main()


class FeatureHistoryBoundTests(unittest.TestCase):
    def test_bounded_history_gives_the_same_features(self):
        from crypto_eval.paper_market import FixtureFuturesMarketDataProvider
        from crypto_eval.paper_runtime import FEATURE_HISTORY_BARS, compute_features

        provider = FixtureFuturesMarketDataProvider()
        snapshot = provider.fetch_snapshot("BTCUSDT", NOW)
        full = {interval: provider.fetch_history("BTCUSDT", interval, bars=bars, as_of=NOW)
                for interval, bars in (("15m", 3000), ("1h", 1500), ("4h", 800))}
        bounded = {interval: lane[-FEATURE_HISTORY_BARS[interval]:] for interval, lane in full.items()}
        a = compute_features(snapshot, historical_bars=full)
        b = compute_features(snapshot, historical_bars=bounded)
        for key in ("ema12", "ema26", "ema12_1h", "ema26_1h", "ema12_4h", "ema26_4h", "atr", "rsi14", "signal_strength"):
            self.assertAlmostEqual(a[key], b[key], delta=abs(a[key]) * 1e-9 + 1e-12, msg=key)
        for key in ("quant_direction", "quant_regime", "signal_trigger", "gate_eligible", "previous_20_bar_high", "previous_20_bar_low"):
            self.assertEqual(a[key], b[key], key)
