from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any

from crypto_eval.baselines import compare_baselines
from crypto_eval.contracts import iso_utc, parse_timestamp
from crypto_eval.dataset import bind_outcomes, build_dataset
from crypto_eval.io import read_json
from crypto_eval.reporting import build_report
from crypto_eval.runner import FixtureRunner, run_predictions
from crypto_eval.scoring import score_predictions


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "crypto_eval" / "fixtures"


def fixture_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    spec = read_json(FIXTURE_DIR / "fixture-spec.json")
    bundle = read_json(FIXTURE_DIR / "candidates.json")
    raw_outcomes = read_json(FIXTURE_DIR / "outcomes.json")
    return spec, bundle, raw_outcomes


def demo_dataset() -> dict[str, Any]:
    spec, bundle, _ = fixture_inputs()
    return build_dataset(bundle, spec)


def demo_outcomes(dataset: dict[str, Any]) -> dict[str, Any]:
    _, _, raw_outcomes = fixture_inputs()
    return bind_outcomes(raw_outcomes, dataset)


class StaticRunner:
    def __init__(
        self,
        decisions: dict[str, dict[str, Any]],
        *,
        variant: str = "fixture",
        run_id: str = "unit-static-v1",
        execution_status: str = "not_invoked",
        provider: str = "unit-test",
        model_id: str = "deterministic-test-model",
        inference_config_hash: str = "fixed-test-config",
        prompt_version: str = "unit-test-prompt-v1",
    ) -> None:
        self.decisions = decisions
        self._metadata = {
            "name": "static_test_runner",
            "version": "1.0.0",
            "mode": "fixture" if execution_status == "not_invoked" else "external",
            "execution_status": execution_status,
            "provider": provider,
            "model_id": model_id,
            "inference_config_hash": inference_config_hash,
            "prompt_version": prompt_version,
            "skill_commit": "test-commit" if variant == "skill" else None,
            "variant": variant,
            "run_id": run_id,
        }

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self._metadata)

    def predict(self, case: dict[str, Any]) -> dict[str, Any]:
        decision = deepcopy(self.decisions[case["case_id"]])
        if self._metadata["execution_status"] == "invoked":
            decision["frozen_at"] = iso_utc(
                parse_timestamp(case["as_of"], "case.as_of") + timedelta(minutes=1)
            )
        return decision


def pullback_decision(
    *,
    low: float,
    high: float,
    stop: float,
    targets: list[float],
    confirmation: str = "touch",
) -> dict[str, Any]:
    return {
        "decision_state": "WAIT_FOR_PULLBACK",
        "bias": "bullish",
        "confidence": "moderate",
        "entry": {
            "kind": "pullback",
            "direction": "long",
            "zone_low": low,
            "zone_high": high,
            "confirmation": confirmation,
        },
        "invalidation": stop,
        "targets": list(targets),
        "leverage_stress": "elevated",
        "rationale": "test fixture",
    }


def breakout_decision(
    *,
    direction: str,
    level: float,
    stop: float,
    targets: list[float],
) -> dict[str, Any]:
    return {
        "decision_state": "WAIT_FOR_BREAKOUT_CONFIRMATION",
        "bias": "bullish" if direction == "long" else "bearish",
        "confidence": "low",
        "entry": {
            "kind": "breakout",
            "direction": direction,
            "level": level,
            "confirmation": "close",
        },
        "invalidation": stop,
        "targets": list(targets),
        "leverage_stress": None,
        "rationale": "test fixture",
    }


def immediate_decision(
    case: dict[str, Any],
    *,
    direction: str = "long",
    stop: float,
    targets: list[float],
) -> dict[str, Any]:
    reference = float(case["snapshot"]["candles"][-1]["close"])
    return {
        "decision_state": "ENTER_LONG" if direction == "long" else "ENTER_SHORT",
        "bias": "bullish" if direction == "long" else "bearish",
        "confidence": "moderate",
        "entry": {
            "kind": "immediate",
            "direction": direction,
            "reference_price": reference,
        },
        "invalidation": stop,
        "targets": list(targets),
        "leverage_stress": None,
        "rationale": "test fixture",
    }


def frozen_predictions(
    dataset: dict[str, Any], overrides: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    default = FixtureRunner()
    decisions = {
        case["case_id"]: (overrides or {}).get(case["case_id"], default.predict(case))
        for case in dataset["cases"]
    }
    return run_predictions(dataset, StaticRunner(decisions))


def case_for_asset(dataset: dict[str, Any], asset: str) -> dict[str, Any]:
    return next(case for case in dataset["cases"] if case["asset"] == asset)


def outcome_for_asset(dataset: dict[str, Any], outcomes: dict[str, Any], asset: str) -> dict[str, Any]:
    case = case_for_asset(dataset, asset)
    return next(record for record in outcomes["records"] if record["case_id"] == case["case_id"])


def score_demo(
    dataset: dict[str, Any],
    predictions: list[dict[str, Any]],
    outcomes: dict[str, Any],
    fold: str = "all",
) -> dict[str, Any]:
    scores = score_predictions(dataset, predictions, outcomes, fold=fold)
    scores["data_origin"] = dataset["data_origin"]
    return scores


def report_demo(
    dataset: dict[str, Any],
    predictions: list[dict[str, Any]],
    outcomes: dict[str, Any],
) -> dict[str, Any]:
    scores = score_demo(dataset, predictions, outcomes)
    baselines = compare_baselines(dataset, outcomes, predictions=predictions, fold="all")
    return build_report(scores, baselines)


def schema_errors(value: Any, schema_name: str) -> list[str]:
    from scripts.validate_repo import ROOT as VALIDATOR_ROOT
    from scripts.validate_repo import load_json, validate_schema

    schema_path = VALIDATOR_ROOT / "schemas" / schema_name
    schema = load_json(schema_path)
    errors: list[str] = []
    validate_schema(value, schema, schema_path, schema, "fixture", errors)
    return errors
