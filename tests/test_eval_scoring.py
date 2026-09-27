from __future__ import annotations

from copy import deepcopy
import unittest

from crypto_eval.contracts import EvaluationError
from crypto_eval.runner import run_predictions
from crypto_eval.scoring import score_predictions
from eval_test_support import (
    StaticRunner,
    breakout_decision,
    case_for_asset,
    demo_dataset,
    demo_outcomes,
    frozen_predictions,
    immediate_decision,
    outcome_for_asset,
    pullback_decision,
)


class TriggerAwareScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = demo_dataset()
        self.outcomes = demo_outcomes(self.dataset)

    def _predictions(self, case_id: str, decision: dict) -> list[dict]:
        defaults = {item["case_id"]: item for item in frozen_predictions(self.dataset)}
        decisions = {
            case["case_id"]: deepcopy(defaults[case["case_id"]]["decision"])
            for case in self.dataset["cases"]
        }
        decisions[case_id] = decision
        return run_predictions(self.dataset, StaticRunner(decisions))

    def _row(self, predictions: list[dict], case_id: str, outcomes=None) -> dict:
        result = score_predictions(
            self.dataset,
            predictions,
            outcomes or self.outcomes,
            fold="all",
        )
        return next(row for row in result["cases"] if row["case_id"] == case_id)

    def test_valid_wait_without_trigger_is_not_scored_as_a_failed_entry(self) -> None:
        case = case_for_asset(self.dataset, "BTC")
        prediction = self._predictions(
            case["case_id"],
            pullback_decision(low=90, high=95, stop=85, targets=[105, 110]),
        )
        row = self._row(prediction, case["case_id"])
        self.assertEqual(row["trigger"]["status"], "not_triggered")
        self.assertIsNone(row["entry_return"])
        self.assertIsNone(row["mfe"])
        self.assertIsNone(row["mae"])
        scores = score_predictions(
            self.dataset,
            [record for record in prediction if record["case_id"] == case["case_id"]],
            self.outcomes,
            fold="all",
        )
        self.assertEqual(scores["metrics"]["entry_trigger_rate"]["n"], 1)
        self.assertEqual(scores["metrics"]["entry_trigger_rate"]["rate"], 0)
        self.assertEqual(scores["metrics"]["target_hit_rate"]["n"], 0)

    def test_pullback_trigger_tracks_post_trigger_mfe_mae_and_target(self) -> None:
        case = case_for_asset(self.dataset, "SOL")
        predictions = self._predictions(
            case["case_id"],
            pullback_decision(
                low=24.5,
                high=24.75,
                stop=24,
                targets=[25.75, 26.5],
            ),
        )
        row = self._row(predictions, case["case_id"])
        self.assertEqual(row["trigger"]["status"], "triggered")
        self.assertEqual(row["trigger"]["entry_reference"], 24.75)
        self.assertEqual(row["path"]["first_event"], "target")
        self.assertEqual(row["path"]["target_index"], 0)
        self.assertAlmostEqual(row["mfe"], 26.2 / 24.75 - 1)
        self.assertLess(row["mae"], 0)
        self.assertGreater(row["entry_return"], 0)

    def test_breakout_wait_uses_close_confirmation_and_short_side(self) -> None:
        case = case_for_asset(self.dataset, "ETH")
        predictions = self._predictions(
            case["case_id"],
            breakout_decision(
                direction="short",
                level=48.755,
                stop=51,
                targets=[47.53, 46],
            ),
        )
        row = self._row(predictions, case["case_id"])
        self.assertEqual(row["trigger"]["status"], "triggered")
        self.assertEqual(row["trigger"]["entry_reference"], 48.755)
        self.assertEqual(row["path"]["first_event"], "target")
        self.assertTrue(row["path"]["target_before_invalidation"])
        self.assertGreater(row["entry_return"], 0)

    def test_same_candle_target_and_stop_assumes_invalidation_first(self) -> None:
        case = case_for_asset(self.dataset, "BTC")
        outcomes = deepcopy(self.outcomes)
        outcome = outcome_for_asset(self.dataset, outcomes, "BTC")
        outcome["candles"][0].update(open=100, high=106, low=94, close=101)
        predictions = self._predictions(
            case["case_id"],
            immediate_decision(case, stop=95, targets=[105, 110]),
        )
        row = self._row(predictions, case["case_id"], outcomes)
        self.assertEqual(row["path"]["first_event"], "invalidation")
        self.assertTrue(row["path"]["same_candle_ambiguous"])
        self.assertEqual(row["path"]["event_price"], 95)
        self.assertFalse(row["path"]["target_before_invalidation"])

    def test_stop_gap_uses_worse_opening_price(self) -> None:
        case = case_for_asset(self.dataset, "BTC")
        outcomes = deepcopy(self.outcomes)
        outcome = outcome_for_asset(self.dataset, outcomes, "BTC")
        outcome["candles"][0].update(open=90, high=92, low=89, close=91)
        predictions = self._predictions(
            case["case_id"],
            immediate_decision(case, stop=95, targets=[105]),
        )
        row = self._row(predictions, case["case_id"], outcomes)
        self.assertEqual(row["path"]["first_event"], "invalidation")
        self.assertEqual(row["path"]["event_price"], 90)

    def test_target_gap_records_the_favorable_opening_price(self) -> None:
        case = case_for_asset(self.dataset, "BTC")
        outcomes = deepcopy(self.outcomes)
        outcome = outcome_for_asset(self.dataset, outcomes, "BTC")
        outcome["candles"][0].update(open=110, high=112, low=100, close=111)
        predictions = self._predictions(
            case["case_id"],
            immediate_decision(case, stop=95, targets=[105]),
        )
        row = self._row(predictions, case["case_id"], outcomes)
        self.assertEqual(row["path"]["first_event"], "target")
        self.assertEqual(row["path"]["event_price"], 110)

    def test_multiple_targets_in_one_candle_count_nearest_target_first(self) -> None:
        case = case_for_asset(self.dataset, "BTC")
        outcomes = deepcopy(self.outcomes)
        outcome = outcome_for_asset(self.dataset, outcomes, "BTC")
        outcome["candles"][0].update(open=100, high=108, low=98, close=104)
        predictions = self._predictions(
            case["case_id"],
            immediate_decision(case, stop=95, targets=[102, 105, 107]),
        )
        row = self._row(predictions, case["case_id"], outcomes)
        self.assertEqual(row["path"]["first_event"], "target")
        self.assertEqual(row["path"]["target_index"], 0)
        self.assertEqual(row["path"]["event_price"], 102)

    def test_missing_intervals_make_horizon_and_unobserved_events_unknown(self) -> None:
        case = case_for_asset(self.dataset, "BTC")
        outcomes = deepcopy(self.outcomes)
        outcome = outcome_for_asset(self.dataset, outcomes, "BTC")
        outcome["candles"][1]["open_time"] = "2026-01-01T04:00:00Z"
        outcome["candles"][1]["close_time"] = "2026-01-01T05:00:00Z"
        outcome["candles"][2]["open_time"] = "2026-01-01T05:00:00Z"
        outcome["candles"][2]["close_time"] = "2026-01-01T06:00:00Z"
        outcome["known_at"] = "2026-01-01T07:00:00Z"
        predictions = self._predictions(
            case["case_id"],
            immediate_decision(case, stop=90, targets=[110]),
        )
        row = self._row(predictions, case["case_id"], outcomes)
        self.assertIsNone(row["forward_return"])
        self.assertEqual(row["path"]["status"], "unknown_missing_interval")
        self.assertIn("missing_interval", " ".join(row["data_notes"]))

    def test_partial_outcome_is_not_silently_zero_filled(self) -> None:
        case = case_for_asset(self.dataset, "ETH")
        outcomes = deepcopy(self.outcomes)
        outcome = outcome_for_asset(self.dataset, outcomes, "ETH")
        outcome["status"] = "partial"
        outcome["candles"] = outcome["candles"][:2]
        predictions = self._predictions(
            case["case_id"],
            breakout_decision(
                direction="short",
                level=48.755,
                stop=51,
                targets=[47.53],
            ),
        )
        row = self._row(predictions, case["case_id"], outcomes)
        self.assertEqual(row["score_status"], "scored_partial")
        self.assertIsNone(row["forward_return"])
        self.assertIsNotNone(row["path"]["target_hit"])
        self.assertIn("provider_marked_partial", " ".join(row["data_notes"]))

    def test_btc_alpha_is_only_computed_from_complete_benchmark_data(self) -> None:
        case = case_for_asset(self.dataset, "ETH")
        predictions = frozen_predictions(self.dataset)
        row = self._row(predictions, case["case_id"])
        self.assertAlmostEqual(row["forward_return"], -0.04 / 0.98)
        self.assertAlmostEqual(row["benchmark_return"], 0.01)
        self.assertAlmostEqual(row["alpha"], row["forward_return"] - 0.01)

        outcomes = deepcopy(self.outcomes)
        outcome_for_asset(self.dataset, outcomes, "ETH").pop("benchmark_candles")
        row = self._row(predictions, case["case_id"], outcomes)
        self.assertIsNone(row["benchmark_return"])
        self.assertIsNone(row["alpha"])

    def test_future_known_outcome_rejects_a_late_frozen_prediction(self) -> None:
        case = case_for_asset(self.dataset, "ETH")
        predictions = frozen_predictions(self.dataset)
        record = next(item for item in predictions if item["case_id"] == case["case_id"])
        record["frozen_at"] = "2026-01-02T06:00:00Z"
        from crypto_eval.contracts import digest

        record["prediction_hash"] = digest(
            {key: value for key, value in record.items() if key != "prediction_hash"}
        )
        with self.assertRaisesRegex(EvaluationError, "at/after outcome known_at"):
            score_predictions(self.dataset, predictions, self.outcomes, fold="all")

    def test_avoid_chasing_is_not_given_an_invented_entry(self) -> None:
        case = case_for_asset(self.dataset, "SOL")
        decision = {
            "decision_state": "AVOID_CHASING",
            "bias": "bullish",
            "confidence": "moderate",
            "entry": None,
            "invalidation": None,
            "targets": [],
            "leverage_stress": None,
            "rationale": "wait for better asymmetry",
        }
        predictions = self._predictions(case["case_id"], decision)
        row = self._row(predictions, case["case_id"])
        self.assertEqual(row["decision_state"], "AVOID_CHASING")
        self.assertIsNone(row["entry_return"])
        self.assertEqual(row["trigger"]["status"], "not_applicable")
        self.assertIsNotNone(row["directional_outcome"])


if __name__ == "__main__":
    unittest.main()
