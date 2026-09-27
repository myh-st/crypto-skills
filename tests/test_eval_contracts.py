from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

from crypto_eval.contracts import (
    EvaluationError,
    validate_dataset,
    validate_prediction_record,
)
from crypto_eval.dataset import build_dataset
from crypto_eval.runner import FixtureRunner, run_predictions, write_predictions
from eval_test_support import (
    FIXTURE_DIR,
    demo_dataset,
    demo_outcomes,
    fixture_inputs,
    report_demo,
    schema_errors,
)


class EvaluationContractTests(unittest.TestCase):
    def test_dataset_ids_and_chronological_folds_are_reproducible(self) -> None:
        first = demo_dataset()
        second = demo_dataset()
        self.assertEqual(first["dataset_hash"], second["dataset_hash"])
        self.assertEqual(
            [case["case_id"] for case in first["cases"]],
            [case["case_id"] for case in second["cases"]],
        )
        self.assertEqual(
            [case["fold"] for case in first["cases"]],
            ["train", "validation", "test"],
        )
        self.assertEqual(set(first["coverage_summary"]["assets"]), {"BTC", "ETH", "SOL"})

    def test_sampling_audit_reconciles_exclusions_and_requires_declared_rules(self) -> None:
        spec, bundle, _ = fixture_inputs()
        bundle["sampling_manifest"] = {
            "scheduled_case_count": 4,
            "included_case_count": 3,
            "excluded_cases": [
                {
                    "source_case_key": "btc-20260104-0200",
                    "asset": "BTC",
                    "scheduled_at": "2026-01-04T02:00:00Z",
                    "reason": "fixture archive intentionally omits this observation",
                    "rule_id": "fixture_input_unavailable",
                }
            ],
        }
        dataset = build_dataset(bundle, spec)
        self.assertEqual(dataset["sampling_audit"]["scheduled_case_count"], 4)
        self.assertEqual(dataset["coverage_summary"]["excluded_cases"], 1)
        bundle["sampling_manifest"]["excluded_cases"][0]["rule_id"] = "manual_skip"
        with self.assertRaisesRegex(EvaluationError, "not allowed by the versioned dataset spec"):
            build_dataset(bundle, spec)

    def test_sampling_manifest_must_reconcile_every_scheduled_case(self) -> None:
        spec, bundle, _ = fixture_inputs()
        bundle["sampling_manifest"]["scheduled_case_count"] = 4
        with self.assertRaisesRegex(EvaluationError, "every scheduled case"):
            build_dataset(bundle, spec)

    def test_declared_cadence_requires_every_asset_time_slot(self) -> None:
        spec, bundle, _ = fixture_inputs()
        spec["sampling"].update(
            {
                "start": "2026-01-01T02:00:00Z",
                "end": "2026-01-03T02:00:00Z",
                "cadence_seconds": 86400,
                "instrument": "spot",
                "horizon": "intraday",
            }
        )
        excluded = []
        for date, missing_assets in (
            ("2026-01-01", ("ETH", "SOL")),
            ("2026-01-02", ("BTC", "SOL")),
            ("2026-01-03", ("BTC", "ETH")),
        ):
            for asset in missing_assets:
                excluded.append(
                    {
                        "source_case_key": f"excluded-{asset.lower()}-{date}",
                        "asset": asset,
                        "scheduled_at": f"{date}T02:00:00Z",
                        "reason": "synthetic archive omits this scheduled slot",
                        "rule_id": "fixture_input_unavailable",
                    }
                )
        bundle["sampling_manifest"] = {
            "scheduled_case_count": 9,
            "included_case_count": 3,
            "excluded_cases": excluded,
        }
        dataset = build_dataset(bundle, spec)
        self.assertEqual(dataset["coverage_summary"]["scheduled_cases"], 9)
        bundle["candidates"][0]["as_of"] = "2026-01-01T03:00:00Z"
        with self.assertRaisesRegex(EvaluationError, "UTC sampling cadence"):
            build_dataset(bundle, spec)

    def test_walk_forward_fold_cannot_be_reassigned_out_of_order(self) -> None:
        dataset = demo_dataset()
        dataset["cases"][2]["fold"] = "validation"
        with self.assertRaisesRegex(EvaluationError, "fold.*chronological spec"):
            validate_dataset(dataset)

    def test_case_rejects_missing_point_in_time_coordinates(self) -> None:
        spec, bundle, _ = fixture_inputs()
        del bundle["candidates"][0]["data_cutoff"]
        with self.assertRaisesRegex(EvaluationError, "data_cutoff"):
            build_dataset(bundle, spec)

    def test_fixed_baseline_parameters_cannot_be_tuned_in_the_dataset_spec(self) -> None:
        spec, bundle, _ = fixture_inputs()
        spec["baselines"]["ema_fast"] = 9
        with self.assertRaisesRegex(EvaluationError, "fixed at 20"):
            build_dataset(bundle, spec)

    def test_cutoff_and_timezone_are_explicit_and_normalized(self) -> None:
        spec, bundle, _ = fixture_inputs()
        bundle["candidates"][0]["data_cutoff"] = "2026-01-01T02:00:00"
        with self.assertRaisesRegex(EvaluationError, "timezone"):
            build_dataset(bundle, spec)

        spec, bundle, _ = fixture_inputs()
        bundle["candidates"][0]["as_of"] = "2026-01-01T09:00:00+07:00"
        bundle["candidates"][0]["data_cutoff"] = "2026-01-01T09:00:00+07:00"
        bundle["candidates"][0]["snapshot"]["candles"][-1]["close_time"] = (
            "2026-01-01T09:00:00+07:00"
        )
        dataset = build_dataset(bundle, spec)
        self.assertEqual(dataset["cases"][0]["as_of"], "2026-01-01T09:00:00+07:00")

    def test_rejects_future_candles_and_observation_families(self) -> None:
        mutations = (
            (
                "future asset candle",
                lambda case: case["snapshot"]["candles"][-1].update(
                    close_time="2026-01-01T02:00:01Z"
                ),
            ),
            (
                "future funding",
                lambda case: case["snapshot"].update(
                    funding=[{"observed_at": "2026-01-01T03:00:00Z", "rate": 0.001}]
                ),
            ),
            (
                "future open interest",
                lambda case: case["snapshot"].update(
                    open_interest=[
                        {"observed_at": "2026-01-01T03:00:00Z", "value": 100.0}
                    ]
                ),
            ),
            (
                "future benchmark candle",
                lambda case: case["snapshot"]["benchmark_candles"][-1].update(
                    close_time="2026-01-02T02:00:01Z"
                ),
            ),
            (
                "future news",
                lambda case: case["snapshot"].update(
                    news=[
                        {
                            "published_at": "2026-01-02T03:00:00Z",
                            "available_at": "2026-01-02T03:00:00Z",
                            "archive_id": "test-archive",
                        }
                    ]
                ),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                spec, bundle, _ = fixture_inputs()
                candidate = bundle["candidates"][0]
                if name == "future benchmark candle":
                    candidate = bundle["candidates"][1]
                mutate(candidate)
                with self.assertRaises(EvaluationError):
                    build_dataset(bundle, spec)

    def test_current_search_news_without_archive_availability_is_rejected(self) -> None:
        spec, bundle, _ = fixture_inputs()
        bundle["candidates"][0]["snapshot"]["news"] = [
            {"published_at": "2026-01-01T01:30:00Z", "title": "not archived"}
        ]
        with self.assertRaisesRegex(EvaluationError, "available_at"):
            build_dataset(bundle, spec)

    def test_future_labels_and_reflections_are_rejected(self) -> None:
        for field in ("label", "outcome", "reflection", "outcome_known_at"):
            with self.subTest(field=field):
                spec, bundle, _ = fixture_inputs()
                bundle["candidates"][0]["snapshot"][field] = "leaked"
                with self.assertRaisesRegex(EvaluationError, "outcome/label data"):
                    build_dataset(bundle, spec)

    def test_unknown_snapshot_fields_and_nested_payloads_fail_closed(self) -> None:
        mutations = (
            (
                "unknown future label",
                lambda snapshot: snapshot.update(future_label="bullish"),
            ),
            (
                "unknown timestamped future field",
                lambda snapshot: snapshot.update(
                    future_label_at="2026-01-02T00:00:00Z"
                ),
            ),
            (
                "opaque nested payload",
                lambda snapshot: snapshot.update(
                    vendor_payload={"labels": {"future_label": "bullish"}}
                ),
            ),
            (
                "nested observation label",
                lambda snapshot: snapshot.update(
                    funding=[
                        {
                            "observed_at": "2026-01-01T01:00:00Z",
                            "rate": 0.001,
                            "future_label": "bullish",
                        }
                    ]
                ),
            ),
            (
                "candle label extension",
                lambda snapshot: snapshot["candles"][0].update(
                    future_label="bullish"
                ),
            ),
            (
                "unknown availability lane",
                lambda snapshot: snapshot.update(
                    data_availability={"future_metric": "available"}
                ),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                spec, bundle, _ = fixture_inputs()
                mutate(bundle["candidates"][0]["snapshot"])
                with self.assertRaises(EvaluationError):
                    build_dataset(bundle, spec)

        tampered_dataset = demo_dataset()
        tampered_dataset["cases"][0]["snapshot"]["opaque"] = {"payload": "unknown"}
        with self.assertRaisesRegex(EvaluationError, "unsupported fields"):
            run_predictions(tampered_dataset, FixtureRunner())

    def test_candidate_schema_rejects_unknown_snapshot_fields(self) -> None:
        _, bundle, _ = fixture_inputs()
        bundle["candidates"][0]["snapshot"]["future_label_at"] = (
            "2026-01-02T00:00:00Z"
        )
        self.assertTrue(schema_errors(bundle, "eval-candidates.schema.json"))

        dataset = demo_dataset()
        dataset["cases"][0]["snapshot"]["future_label_at"] = (
            "2026-01-02T00:00:00Z"
        )
        self.assertTrue(schema_errors(dataset, "eval-dataset.schema.json"))

    def test_invalid_ohlc_prices_are_rejected(self) -> None:
        spec, bundle, _ = fixture_inputs()
        bundle["candidates"][0]["snapshot"]["candles"][-1]["high"] = 98.0
        with self.assertRaisesRegex(EvaluationError, "inconsistent OHLC"):
            build_dataset(bundle, spec)

    def test_snapshot_missing_intervals_are_marked_partial(self) -> None:
        spec, bundle, _ = fixture_inputs()
        btc = bundle["candidates"][0]
        btc["as_of"] = "2026-01-01T02:01:00Z"
        btc["data_cutoff"] = "2026-01-01T02:01:00Z"
        btc["snapshot"]["candles"][-1]["open_time"] = "2026-01-01T01:01:00Z"
        btc["snapshot"]["candles"][-1]["close_time"] = "2026-01-01T02:01:00Z"
        dataset = build_dataset(bundle, spec)
        btc_case = next(case for case in dataset["cases"] if case["asset"] == "BTC")
        self.assertEqual(btc_case["data_availability"]["candles"], "partial")

    def test_prediction_hash_detects_mutation_and_never_contains_outcomes(self) -> None:
        dataset = demo_dataset()
        prediction = run_predictions(dataset, FixtureRunner())[0]
        case = dataset["cases"][0]
        validate_prediction_record(
            prediction,
            case,
            dataset["dataset_id"],
            dataset["dataset_version"],
            dataset["dataset_hash"],
        )
        modified = deepcopy(prediction)
        modified["decision"]["confidence"] = "high"
        with self.assertRaisesRegex(EvaluationError, "prediction_hash"):
            validate_prediction_record(
                modified,
                case,
                dataset["dataset_id"],
                dataset["dataset_version"],
                dataset["dataset_hash"],
            )
        modified = deepcopy(prediction)
        modified["outcome"] = {"raw_return": 0.1}
        with self.assertRaisesRegex(EvaluationError, "outcome/label data"):
            validate_prediction_record(
                modified,
                case,
                dataset["dataset_id"],
                dataset["dataset_version"],
                dataset["dataset_hash"],
            )

    def test_prediction_writer_refuses_to_overwrite_any_existing_file(self) -> None:
        dataset = demo_dataset()
        predictions = run_predictions(dataset, FixtureRunner())
        path = FIXTURE_DIR / "outcomes.json"
        original = path.read_bytes()
        with self.assertRaisesRegex(EvaluationError, "immutable"):
            write_predictions(path, predictions)
        self.assertEqual(path.read_bytes(), original)

    def test_eval_schemas_validate_fixtures_and_generated_records(self) -> None:
        spec, bundle, raw_outcomes = fixture_inputs()
        dataset = build_dataset(bundle, spec)
        predictions = run_predictions(dataset, FixtureRunner())
        outcomes = demo_outcomes(dataset)
        report = report_demo(dataset, predictions, outcomes)
        self.assertEqual(schema_errors(spec, "eval-spec.schema.json"), [])
        self.assertEqual(schema_errors(bundle, "eval-candidates.schema.json"), [])
        self.assertEqual(schema_errors(raw_outcomes, "eval-outcomes.schema.json"), [])
        self.assertEqual(schema_errors(dataset, "eval-dataset.schema.json"), [])
        self.assertEqual(schema_errors(predictions[0], "eval-prediction.schema.json"), [])
        self.assertEqual(schema_errors(report, "eval-report.schema.json"), [])


if __name__ == "__main__":
    unittest.main()
