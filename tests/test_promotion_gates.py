"""Experiment Promotion Gates: manifests, drift, checkpoint reports, gate outcomes (campaign
rehearsal), and the guarantee that nothing enables live execution. Fixture data only."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import timedelta

from crypto_eval.paper_contracts import PaperTradingError, iso_utc
from crypto_eval.portfolio_os import ConfirmationRequired
from crypto_eval.promotion import (
    DEFAULT_PROMOTION_CRITERIA,
    STATUSES,
    checkpoint_label,
    evaluate_gate,
    validate_criteria,
)

from tests.test_portfolio_os import NOW, PortfolioCase


def report(**overrides):
    base = {
        "identity": {"experiment_id": "EXP-001", "manifest_version": 1, "manifest_drift": [], "elapsed_days": 95.0, "checkpoint": "DAY_90"},
        "safety": {"live_execution_enabled": False, "reconciliation_ok": True, "duplicate_cycles": [], "secret_findings": [],
                   "open_critical_incidents": 0, "database_ok": True},
        "reliability": {"skipped_slot_ratio": 0.01},
        "economics": {"completed_trades": 240, "net_economic_pnl_usdt": 42.0, "profit_factor": 1.4, "max_drawdown": 0.08,
                      "top_trade_share": 0.12},
        "coverage": {"regimes_observed": 3},
    }
    for path, value in overrides.items():
        section, key = path.split("__")
        base[section][key] = value
    return base


CRITERIA = validate_criteria({})


class GateEngineTests(unittest.TestCase):
    """Campaign rehearsal: every status is reachable and precedence is deterministic."""

    def test_pass_is_only_a_review_eligibility_never_live(self):
        gate = evaluate_gate(report(), CRITERIA)
        self.assertEqual(gate["status"], "PASS")
        self.assertFalse(gate["live_execution_enabled"])
        self.assertIn("nothing is enabled", gate["next_step"])

    def test_continue_collecting_when_sample_or_time_is_short(self):
        self.assertEqual(evaluate_gate(report(identity__elapsed_days=30.0, identity__checkpoint="DAY_30"), CRITERIA)["status"],
                         "CONTINUE_COLLECTING_DATA")
        short = evaluate_gate(report(economics__completed_trades=120), CRITERIA)
        self.assertEqual(short["status"], "CONTINUE_COLLECTING_DATA")
        self.assertIn("120 of 200 completed trades", short["reasons"][0])
        narrow = evaluate_gate(report(coverage__regimes_observed=1), CRITERIA)
        self.assertEqual(narrow["status"], "CONTINUE_COLLECTING_DATA")

    def test_day_seven_never_judges_profitability(self):
        rich = report(identity__elapsed_days=7.5, identity__checkpoint="DAY_7")
        lenient = validate_criteria({"min_days": 7, "min_completed_trades": 10})
        gate = evaluate_gate(rich, lenient)
        self.assertEqual(gate["status"], "CONTINUE_COLLECTING_DATA")
        self.assertTrue(any("day-7" in reason for reason in gate["reasons"]))

    def test_fail_strategy_after_full_sample(self):
        self.assertEqual(evaluate_gate(report(economics__net_economic_pnl_usdt=-5.0), CRITERIA)["status"], "FAIL_STRATEGY")
        self.assertEqual(evaluate_gate(report(economics__profit_factor=0.9), CRITERIA)["status"], "FAIL_STRATEGY")
        self.assertEqual(evaluate_gate(report(economics__max_drawdown=0.35), CRITERIA)["status"], "FAIL_STRATEGY")
        single = evaluate_gate(report(economics__top_trade_share=0.8), CRITERIA)
        self.assertEqual(single["status"], "FAIL_STRATEGY")
        self.assertIn("one trade contributes 80%", single["reasons"][0])
        unavailable = evaluate_gate(report(economics__net_economic_pnl_usdt=None), CRITERIA)
        self.assertEqual(unavailable["status"], "FAIL_STRATEGY")
        self.assertIn("unavailable", unavailable["reasons"][0])

    def test_safety_blocks_regardless_of_pnl(self):
        for override, blocker in (
            ({"safety__reconciliation_ok": False}, "RECONCILIATION_FAILURE"),
            ({"safety__duplicate_cycles": [{"symbol": "BTCUSDT"}]}, "DUPLICATE_LOGICAL_EXECUTION"),
            ({"safety__secret_findings": ["x.y"]}, "SECRET_LEAK"),
            ({"safety__open_critical_incidents": 1}, "OPEN_CRITICAL_INCIDENT"),
            ({"safety__database_ok": False}, "DATABASE_INTEGRITY"),
            ({"safety__live_execution_enabled": True}, "LIVE_EXECUTION_ENABLED"),
        ):
            gate = evaluate_gate(report(economics__net_economic_pnl_usdt=10_000.0, **override), CRITERIA)
            self.assertEqual(gate["status"], "FAIL_SAFETY", override)
            self.assertIn(blocker, gate["blockers"])

    def test_reliability_and_invalid_precedence(self):
        self.assertEqual(evaluate_gate(report(reliability__skipped_slot_ratio=0.2), CRITERIA)["status"], "FAIL_RELIABILITY")
        self.assertEqual(evaluate_gate(report(identity__manifest_version=None), CRITERIA)["status"], "INVALID_EXPERIMENT")
        drift = evaluate_gate(report(identity__manifest_drift=["providers"], safety__reconciliation_ok=False), CRITERIA)
        self.assertEqual(drift["status"], "INVALID_EXPERIMENT")  # invalid beats every other finding
        self.assertEqual(set(STATUSES), {"PASS", "CONTINUE_COLLECTING_DATA", "FAIL_STRATEGY", "FAIL_SAFETY", "FAIL_RELIABILITY", "INVALID_EXPERIMENT"})

    def test_checkpoints_and_criteria_validation(self):
        self.assertEqual([checkpoint_label(d) for d in (1, 7, 29, 30, 61, 90, 200)],
                         ["PRE_DAY_7", "DAY_7", "DAY_7", "DAY_30", "DAY_60", "DAY_90", "DAY_90"])
        self.assertEqual(validate_criteria({})["min_completed_trades"], DEFAULT_PROMOTION_CRITERIA["min_completed_trades"])
        for bad in ({"min_days": 1}, {"nope": 1}, {"require_positive_net_economic_pnl": "yes"}, {"max_drawdown": 2}):
            with self.assertRaises(PaperTradingError):
                validate_criteria(bad)


class GovernanceTests(PortfolioCase):
    def setUp(self):
        super().setUp()
        self.runtime.start()
        self.gov = self.runtime.governance

    def test_manifest_frozen_on_start_without_secrets(self):
        status = self.gov.status()
        self.assertTrue(status["frozen"])
        self.assertEqual(status["version"], 1)
        self.assertEqual(status["drift"], [])
        material = self.gov.frozen()["manifest"]["material"]
        for key in ("experiment_config", "portfolio_policy", "providers", "skill_sha256", "policy_versions", "price_book_version", "benchmark_arms"):
            self.assertIn(key, material)
        text = json.dumps(material)
        for forbidden in ("credential_secret", "api_key", "secret_value"):
            self.assertNotIn(forbidden, text)
        self.runtime.pause()
        self.runtime.resume()
        self.assertEqual(self.gov.status()["version"], 1)  # idempotent

    def test_running_experiment_without_manifest_is_frozen_at_restart(self):
        with self.store.transaction() as db:
            db.execute("DELETE FROM experiment_manifests")
        self.assertIsNone(self.gov.frozen())
        actions = self.runtime.recover_on_startup(disk_usage=lambda _p: __import__("collections").namedtuple("U", "total used free")(2**40, 0, 2**40))
        self.assertTrue(actions["manifest_frozen_at_restart"])
        self.assertIn("predates experiment manifests", self.gov.frozen()["reason"])

    def test_material_settings_change_needs_confirmation_and_versions(self):
        with self.assertRaises(ConfirmationRequired) as raised:
            self.os.update_settings({"brain": {"max_asset_risk_pct": 0.02}})
        self.assertIn("brain.max_asset_risk_pct", raised.exception.details["fields"])
        self.os.update_settings({"brain": {"max_asset_risk_pct": 0.02}}, confirm=True)
        self.assertEqual(self.gov.status()["version"], 2)
        self.assertEqual(self.gov.status()["drift"], [])
        self.os.update_settings({"attention": {"near_stop_pct": 0.02}})  # operational: no new version
        self.assertEqual(self.gov.status()["version"], 2)

    def test_promotion_criteria_cannot_move_without_a_new_version(self):
        self.assertIn("promotion", self.gov.frozen()["manifest"]["material"]["portfolio_policy"])
        with self.assertRaises(ConfirmationRequired) as raised:
            self.os.update_settings({"promotion": {"min_completed_trades": 50}})
        self.assertIn("promotion.min_completed_trades", raised.exception.details["fields"])
        self.assertEqual(self.gov.status()["version"], 1)
        self.os.update_settings({"promotion": {"min_completed_trades": 50}}, confirm=True)
        self.assertEqual(self.gov.status()["version"], 2)
        self.assertIn("promotion.min_completed_trades", self.gov.frozen()["reason"])

    def test_unversioned_provider_change_invalidates_until_recorded(self):
        provider = self.store.provider(self.store.experiment()["config"]["gpt_provider_id"])
        changed = dict(provider, model=f"{provider.get('model', 'm')}-other")
        with self.store.transaction() as db:
            db.execute("UPDATE providers SET config_json=? WHERE provider_id=?", (json.dumps(changed), provider["provider_id"]))
        self.assertTrue(any(path.startswith("providers") for path in self.gov.drift()))
        review = self.gov.review()
        self.assertEqual(review["gate"]["status"], "INVALID_EXPERIMENT")
        self.assertIn("UNVERSIONED_MATERIAL_CHANGE", review["gate"]["blockers"])
        self.gov.freeze(reason="user: switched model deliberately")
        self.assertEqual(self.gov.drift(), [])
        self.assertEqual(self.gov.status()["version"], 2)

    def test_checkpoint_report_is_reproducible_and_counts_real_trades(self):
        self.buy_spot(amount=25.0)
        ref = next(v["position_ref"] for v in self.os.list_positions() if v["market_type"] == "spot")
        self.os.close_position(ref, confirm=True)
        as_of = self.clock() + timedelta(minutes=1)
        first = self.gov.checkpoint_report(as_of=as_of)
        second = self.gov.checkpoint_report(as_of=as_of)
        self.assertEqual(first["report_sha256"], second["report_sha256"])
        economics = first["economics"]
        self.assertEqual((economics["completed_trades"], economics["spot_round_trips"], economics["denominator"]), (1, 1, 1))
        self.assertLess(economics["net_trading_pnl_usdt"], 0)  # fees + slippage on an immediate round trip
        self.assertFalse(first["safety"]["live_execution_enabled"])
        self.assertTrue(first["safety"]["reconciliation_ok"])
        self.assertEqual(first["safety"]["secret_findings"], [])
        self.assertEqual(first["identity"]["checkpoint"], "PRE_DAY_7")
        for section in ("identity", "safety", "reliability", "economics", "ai_incremental_value", "spot_lifecycle", "coverage"):
            self.assertIn(section, first)

    def test_review_is_persisted_and_early_campaign_continues(self):
        review = self.gov.review()
        self.assertEqual(review["gate"]["status"], "CONTINUE_COLLECTING_DATA")
        listed = self.gov.reviews()
        self.assertEqual(listed[0]["review_id"], review["review_id"])
        self.assertEqual(listed[0]["report_sha256"], review["report"]["report_sha256"])

    def test_unpriced_ai_cost_makes_net_economic_unavailable(self):
        with self.store.transaction() as db:
            db.execute("INSERT INTO ai_usage_events(experiment_id, provider_id, provider_kind, model, call_type, started_at, "
                       "status, estimated_cost_usd, cost_status, real_external_call, created_at) VALUES('EXP-001', 'p', 'responses', 'm', "
                       "'gpt_decision', ?, 'ok', 0.4, 'unknown', 1, ?)", (iso_utc(self.clock()), iso_utc(self.clock())))
        economics = self.gov.checkpoint_report(as_of=self.clock() + timedelta(seconds=1))["economics"]
        self.assertIsNone(economics["net_economic_pnl_usdt"])
        self.assertEqual(economics["ai_unpriced_calls"], 1)


if __name__ == "__main__":
    unittest.main()


class SchemaEvolutionDriftTests(unittest.TestCase):
    """A config field added by a later release, at its default value, is not drift from an older manifest."""

    def test_new_field_at_default_is_not_drift_but_any_real_change_is(self):
        from crypto_eval.promotion import diff_material, material_defaults

        frozen = {"experiment_config": {"symbols": ["BTCUSDT"], "max_daily_loss": 0.05}}
        current = {"experiment_config": {"symbols": ["BTCUSDT"], "max_daily_loss": 0.05, "strategy_engine": "breakout_15m", "sleeves": None}}
        defaults = material_defaults()
        self.assertEqual(diff_material(frozen, current, added_defaults=defaults), [])
        engine_changed = {"experiment_config": {**current["experiment_config"], "strategy_engine": "sleeves_v1"}}
        self.assertEqual(diff_material(frozen, engine_changed, added_defaults=defaults), ["experiment_config.strategy_engine"])
        risk_changed = {"experiment_config": {**current["experiment_config"], "max_daily_loss": 0.08}}
        self.assertEqual(diff_material(frozen, risk_changed, added_defaults=defaults), ["experiment_config.max_daily_loss"])
        # a field REMOVED from the current manifest is always a change
        self.assertEqual(diff_material(frozen, {"experiment_config": {"symbols": ["BTCUSDT"]}}, added_defaults=defaults),
                         ["experiment_config.max_daily_loss"])
        # without defaults (e.g. listing what a new version changes) every difference is reported
        self.assertEqual(diff_material(frozen, current), ["experiment_config.strategy_engine"])
