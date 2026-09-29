"""Live Portfolio OS outputs conform to the versioned JSON Schema contracts in schemas/."""

from __future__ import annotations

import importlib.util
import json
import unittest
from datetime import timedelta
from pathlib import Path

from crypto_eval.paper_contracts import default_experiment_config

from tests.test_portfolio_os import NOW, PortfolioCase

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("validate_repo", ROOT / "scripts" / "validate_repo.py")
validate_repo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_repo)

CONTRACTS = (
    "market-instrument", "spot-wallet", "spot-holding", "unified-position-view", "paper-order-request",
    "paper-order-preview", "paper-order-view", "position-protection-update", "position-management-mode",
    "position-plan", "position-replan-request", "position-replan-proposal", "portfolio-state",
    "portfolio-brain-decision", "activity-event", "attention-event", "post-trade-review", "learning-tag",
)


class PortfolioSchemaTests(PortfolioCase):
    def assertConforms(self, value, name):
        path = ROOT / "schemas" / f"{name}.schema.json"
        schema = json.loads(path.read_text())
        errors: list[str] = []
        validate_repo.validate_schema(json.loads(json.dumps(value, default=str)), schema, path, schema, name, errors)
        self.assertEqual(errors, [], f"{name}: {errors[:5]}")

    def test_all_contract_files_exist_and_are_versioned(self):
        for name in CONTRACTS:
            schema = json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text())
            self.assertEqual(schema["$id"], f"{name}.schema.json")
            self.assertIn("title", schema)

    def test_runtime_outputs_conform(self):
        for market_type in ("spot", "perpetual"):
            for instrument in self.catalog.list(market_type)["instruments"]:
                self.assertConforms(instrument, "market-instrument")
        spot_request = {"client_request_id": "schema-req-0001", "instrument_id": "fixture:spot:ETH_USDT",
                        "action": "buy", "quote_amount": 25.0}
        self.assertConforms(spot_request, "paper-order-request")
        self.assertConforms(self.os.preview_order(spot_request), "paper-order-preview")
        spot = self.os.create_order(spot_request)
        quote = self.os.quote("fixture:perpetual:BTC_USDT")
        perp_request = {"instrument_id": "fixture:perpetual:BTC_USDT", "action": "long", "risk_pct": 0.01,
                        "stop_price": quote["best_ask"] * 0.98, "targets": [quote["best_ask"] * 1.02, quote["best_ask"] * 1.04],
                        "leverage": 3}
        self.assertConforms(perp_request, "paper-order-request")
        self.assertConforms(self.os.preview_order(perp_request), "paper-order-preview")
        perp = self.os.create_order(perp_request)
        self.assertConforms(self.os.spot_wallet(), "spot-wallet")
        for view in self.os.list_positions(status="all"):
            self.assertConforms(view, "unified-position-view")
            if view["market_type"] == "spot":
                self.assertConforms(view, "spot-holding")
        for order in self.os.list_orders():
            self.assertConforms(order, "paper-order-view")
        protection = self.os.update_protection(perp["position_ref"], stop_price=quote["best_ask"] * 0.99)
        self.assertConforms(protection, "position-protection-update")
        self.assertConforms({"mode": "MANUAL_OVERRIDE", "reason": "test"}, "position-management-mode")
        self.assertConforms({"intent": "tighten_risk", "use_ai": True}, "position-replan-request")
        proposal = self.os.request_replan(perp["position_ref"], intent="tighten_risk")
        self.assertConforms(proposal, "position-replan-proposal")
        for plan in self.os.export_files()["position_plans"]:
            self.assertConforms(plan, "position-plan")
        self.assertConforms(self.os.portfolio(), "portfolio-state")
        for event in self.os.activity(limit=200)["events"]:
            self.assertConforms(event, "activity-event")
        for item in self.os.attention(include_resolved=True)["items"]:
            self.assertConforms(item, "attention-event")
        self.clock.value = NOW + timedelta(seconds=5)
        self.os.close_position(perp["position_ref"], confirm=True)
        self.os.close_position(spot["position_ref"], confirm=True)
        self.os.sync_reviews()
        reviews = self.os.reviews()
        self.assertEqual(len(reviews), 2)
        for review in reviews:
            self.assertConforms(review, "post-trade-review")

    def test_cycle_brain_decision_conforms(self):
        config = default_experiment_config()
        config["symbols"] = ["BTCUSDT"]
        self.store.save_experiment(config)
        self.runtime.start()
        cycle = self.runtime.run_cycle("BTCUSDT", as_of=NOW, manual=True)
        self.assertConforms(cycle["portfolio_brain"], "portfolio-brain-decision")

    def test_validator_rejects_contract_violations(self):
        path = ROOT / "schemas" / "paper-order-request.schema.json"
        schema = json.loads(path.read_text())
        for bad in (
            {"instrument_id": "fixture:spot:ETH_USDT", "action": "hodl"},
            {"instrument_id": "fixture:spot:ETH_USDT", "action": "buy", "secret": "x"},
            {"action": "buy"},
        ):
            errors: list[str] = []
            validate_repo.validate_schema(bad, schema, path, schema, "request", errors)
            self.assertTrue(errors, bad)


if __name__ == "__main__":
    unittest.main()
