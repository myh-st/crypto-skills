from __future__ import annotations

import unittest

from crypto_eval.baselines import (
    BASELINE_NAMES,
    baseline_signals,
    compare_baselines,
    compare_model_runs,
)
from crypto_eval.contracts import EvaluationError, iso_utc
from crypto_eval.lifecycle import project_lifecycle, summarize_lifecycle
from crypto_eval.reporting import build_report, render_markdown
from crypto_eval.runner import FixtureRunner, run_predictions
from crypto_eval.scoring import binary_summary, paired_difference
from eval_test_support import (
    StaticRunner,
    case_for_asset,
    demo_dataset,
    demo_outcomes,
    immediate_decision,
)


class BaselineAndReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = demo_dataset()
        self.outcomes = demo_outcomes(self.dataset)

    def test_fixed_baselines_are_deterministic_and_explicitly_unavailable(self) -> None:
        case = {
            "case_id": "synthetic-rising-case",
            "asset": "ETH",
            "benchmark_asset": None,
            "snapshot": {
                "candles": [
                    {
                        "open_time": "2026-01-01T00:00:00Z",
                        "close_time": "2026-01-01T01:00:00Z",
                        "open": float(index + 99),
                        "high": float(index + 102),
                        "low": float(index + 98),
                        "close": float(index + 100),
                        "volume": 1,
                    }
                    for index in range(60)
                ]
            },
        }
        signals = baseline_signals(case, seed=1729)
        repeated = baseline_signals(case, seed=1729)
        self.assertEqual(signals, repeated)
        self.assertEqual(signals["buy_and_hold"]["side"], "long")
        self.assertEqual(signals["ema20_ema50"]["side"], "long")
        self.assertEqual(signals["fixed_rsi14"]["side"], "short")
        self.assertIn(signals["seeded_random"]["side"], {"long", "short"})
        self.assertEqual(tuple(sorted(signals)), tuple(sorted(BASELINE_NAMES)))

        short_history = dict(case)
        short_history["snapshot"] = {"candles": case["snapshot"]["candles"][:2]}
        short_signals = baseline_signals(short_history)
        self.assertEqual(short_signals["ema20_ema50"]["status"], "unavailable")
        self.assertEqual(short_signals["fixed_rsi14"]["status"], "unavailable")

    def test_baseline_comparison_keeps_missing_inputs_out_of_the_denominator(self) -> None:
        result = compare_baselines(
            self.dataset,
            self.outcomes,
            fold="test",
            seed=1729,
        )
        self.assertEqual(set(result["metrics"]), set(BASELINE_NAMES))
        self.assertEqual(result["metrics"]["ema20_ema50"]["available_return_count"], 0)
        self.assertEqual(result["metrics"]["ema20_ema50"]["mean_strategy_return"]["mean"], None)
        self.assertGreater(
            result["metrics"]["btc_benchmark"]["available_return_count"], 0
        )
        self.assertIn("not portfolio PnL", result["interpretation"])
        with self.assertRaisesRegex(EvaluationError, "fixed by the versioned dataset spec"):
            compare_baselines(
                self.dataset,
                self.outcomes,
                fold="test",
                seed=1,
            )

    def test_paired_comparison_reports_paired_differences_without_significance_claims(self) -> None:
        skill_decisions = {}
        control_decisions = {}
        for case in self.dataset["cases"]:
            skill_decisions[case["case_id"]] = immediate_decision(
                case,
                direction="long",
                stop=float(case["snapshot"]["candles"][-1]["close"]) * 0.5,
                targets=[float(case["snapshot"]["candles"][-1]["close"]) * 1.2],
            )
            direction = "short" if case["asset"] == "ETH" else "long"
            control_decisions[case["case_id"]] = immediate_decision(
                case,
                direction=direction,
                stop=(
                    float(case["snapshot"]["candles"][-1]["close"]) * 1.5
                    if direction == "short"
                    else float(case["snapshot"]["candles"][-1]["close"]) * 0.5
                ),
                targets=[
                    (
                        float(case["snapshot"]["candles"][-1]["close"]) * 0.8
                        if direction == "short"
                        else float(case["snapshot"]["candles"][-1]["close"]) * 1.2
                    )
                ],
            )
        skill = run_predictions(
            self.dataset,
            StaticRunner(
                skill_decisions,
                variant="skill",
                run_id="skill-run",
                execution_status="invoked",
                prompt_version="skill-prompt-v1",
            ),
        )
        control = run_predictions(
            self.dataset,
            StaticRunner(
                control_decisions,
                variant="control",
                run_id="control-run",
                execution_status="invoked",
                prompt_version="control-prompt-v1",
            ),
        )
        result = compare_model_runs(
            self.dataset,
            skill,
            control,
            self.outcomes,
            fold="all",
        )
        self.assertTrue(result["comparison_design"]["same_model"])
        self.assertEqual(result["paired_return_difference"]["n_paired"], 3)
        self.assertEqual(
            result["comparison_design"]["skill_prompt_version"], "skill-prompt-v1"
        )
        self.assertTrue(
            any("descriptive" in item for item in result["limitations"])
        )

    def test_paired_comparison_rejects_different_inference_configurations(self) -> None:
        decisions = {}
        for case in self.dataset["cases"]:
            reference = float(case["snapshot"]["candles"][-1]["close"])
            decisions[case["case_id"]] = immediate_decision(
                case,
                stop=reference * 0.5,
                targets=[reference * 2],
            )
        skill = run_predictions(
            self.dataset,
            StaticRunner(
                decisions,
                variant="skill",
                run_id="skill",
                execution_status="invoked",
            ),
        )
        control = run_predictions(
            self.dataset,
            StaticRunner(
                decisions,
                variant="control",
                run_id="control",
                execution_status="invoked",
                inference_config_hash="different-config",
            ),
        )
        with self.assertRaisesRegex(EvaluationError, "same-model comparison"):
            compare_model_runs(self.dataset, skill, control, self.outcomes, fold="all")

    def test_reports_state_fixture_limitation_and_do_not_claim_accuracy(self) -> None:
        predictions = run_predictions(self.dataset, FixtureRunner())
        result = compare_baselines(
            self.dataset,
            self.outcomes,
            predictions=predictions,
            fold="all",
        )
        from crypto_eval.scoring import score_predictions

        scores = score_predictions(self.dataset, predictions, self.outcomes, fold="all")
        scores["data_origin"] = self.dataset["data_origin"]
        report = build_report(scores, result)
        markdown = render_markdown(report)
        self.assertEqual(report["classification"], "DEMO / HARNESS VALIDATION")
        self.assertIn("NOT MARKET PERFORMANCE EVIDENCE", markdown)
        self.assertEqual(report["accuracy_claim"], "none")
        self.assertEqual(report["portfolio_pnl_claim"], "none")
        self.assertIn("not probabilities", str(report["metrics"]["confidence_reliability"]))

    def test_lifecycle_projects_pending_waiting_ready_and_scored_states(self) -> None:
        predictions = run_predictions(self.dataset, FixtureRunner())
        case = self.dataset["cases"][0]
        one_prediction = [
            record for record in predictions if record["case_id"] == case["case_id"]
        ]
        no_prediction = project_lifecycle(self.dataset)
        self.assertEqual(no_prediction[0]["status"], "pending")
        waiting = project_lifecycle(self.dataset, one_prediction)
        self.assertEqual(waiting[0]["status"], "waiting_for_outcome")
        ready = project_lifecycle(self.dataset, one_prediction, self.outcomes)
        self.assertEqual(ready[0]["status"], "ready_to_score")
        scores = {
            "dataset_id": self.dataset["dataset_id"],
            "dataset_version": self.dataset["dataset_version"],
            "dataset_hash": self.dataset["dataset_hash"],
            "cases": [
                {
                    "case_id": case["case_id"],
                    "prediction_id": one_prediction[0]["prediction_id"],
                    "score_status": "scored",
                }
            ]
        }
        scored = project_lifecycle(self.dataset, one_prediction, self.outcomes, scores)
        self.assertEqual(scored[0]["status"], "scored")
        self.assertEqual(summarize_lifecycle(scored)["scored"], 1)

    def test_intervals_and_paired_statistics_are_explicitly_descriptive(self) -> None:
        one = binary_summary([True])
        self.assertEqual(one["n"], 1)
        self.assertIsNotNone(one["interval_95_wilson"])
        comparison = paired_difference({"a": 0.1, "b": 0.2}, {"a": 0.0, "b": 0.1})
        self.assertEqual(comparison["n_paired"], 2)
        self.assertAlmostEqual(comparison["mean_difference"], 0.1)
        self.assertIn("not a significance test", comparison["definition"])


if __name__ == "__main__":
    unittest.main()
