"""Prediction runner interface and append-only frozen prediction records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from .contracts import (
    EvaluationError,
    digest,
    iso_utc,
    parse_timestamp,
    validate_dataset,
    validate_prediction_record,
)


class PredictionRunner(Protocol):
    """Pluggable interface for fixture or externally implemented model runners."""

    @property
    def metadata(self) -> dict[str, Any]:
        """Return reproducibility metadata for this runner and inference setup."""

    def predict(self, case: dict[str, Any]) -> dict[str, Any]:
        """Return a decision using only the supplied point-in-time case."""


class FixtureRunner:
    """Small deterministic rule runner used only to validate harness mechanics."""

    def __init__(self) -> None:
        self._metadata = {
            "name": "deterministic_fixture_rule",
            "version": "1.0.0",
            "mode": "fixture",
            "execution_status": "not_invoked",
            "provider": "none",
            "model_id": "deterministic-price-rule",
            "inference_config_hash": digest(
                {
                    "rule": "compare-last-two-closed-candles",
                    "pullback_fraction": 0.01,
                    "invalidation_fraction": 0.04,
                    "target_fractions": [0.03, 0.06],
                }
            ),
            "prompt_version": "not_applicable_fixture",
            "skill_commit": None,
            "variant": "fixture",
            "run_id": "fixture-rule-v1",
        }

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self._metadata)

    def predict(self, case: dict[str, Any]) -> dict[str, Any]:
        candles = case["snapshot"]["candles"]
        last = candles[-1]
        previous_close = candles[-2]["close"] if len(candles) > 1 else last["open"]
        reference_price = float(last["close"])
        direction = "long" if float(last["close"]) >= float(previous_close) else "short"

        if direction == "long":
            low = reference_price * 0.98
            high = reference_price * 0.99
            return {
                "decision_state": "WAIT_FOR_PULLBACK",
                "bias": "bullish",
                "confidence": "low" if len(candles) < 20 else "moderate",
                "entry": {
                    "kind": "pullback",
                    "direction": "long",
                    "zone_low": round(low, 10),
                    "zone_high": round(high, 10),
                    "confirmation": "touch",
                },
                "invalidation": round(reference_price * 0.96, 10),
                "targets": [
                    round(reference_price * 1.03, 10),
                    round(reference_price * 1.06, 10),
                ],
                "leverage_stress": None,
                "rationale": "Deterministic fixture rule; not a market recommendation.",
            }

        return {
            "decision_state": "WAIT_FOR_BREAKOUT_CONFIRMATION",
            "bias": "bearish",
            "confidence": "low" if len(candles) < 20 else "moderate",
            "entry": {
                "kind": "breakout",
                "direction": "short",
                "level": round(reference_price * 0.995, 10),
                "confirmation": "close",
            },
            "invalidation": round(reference_price * 1.04, 10),
            "targets": [
                round(reference_price * 0.97, 10),
                round(reference_price * 0.94, 10),
            ],
            "leverage_stress": None,
            "rationale": "Deterministic fixture rule; not a market recommendation.",
        }


def freeze_prediction(
    dataset: dict[str, Any],
    case: dict[str, Any],
    runner: PredictionRunner,
) -> dict[str, Any]:
    metadata = runner.metadata
    decision = runner.predict(case)
    if not isinstance(decision, dict):
        raise EvaluationError("runner.predict must return a decision object")
    if metadata.get("execution_status") == "not_invoked":
        frozen_at = case["as_of"]
    else:
        frozen_at = decision.pop("frozen_at", None)
        if frozen_at is None:
            raise EvaluationError(
                "invoked runners must provide frozen_at before any outcome is known"
            )
    frozen_time = parse_timestamp(frozen_at, "prediction.frozen_at")
    if frozen_time < parse_timestamp(case["as_of"], "case.as_of"):
        raise EvaluationError("prediction cannot be frozen before as_of")
    run_identity = {
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["dataset_version"],
        "dataset_hash": dataset["dataset_hash"],
        "case_id": case["case_id"],
        "runner": metadata,
    }
    record: dict[str, Any] = {
        "schema_version": "crypto-eval.prediction.v1",
        "prediction_id": f"pred-{digest(run_identity)[:24]}",
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["dataset_version"],
        "dataset_hash": dataset["dataset_hash"],
        "case_id": case["case_id"],
        "frozen_at": iso_utc(frozen_time),
        "runner": metadata,
        "decision": decision,
    }
    record["prediction_hash"] = digest(record)
    validate_prediction_record(
        record,
        case,
        dataset["dataset_id"],
        dataset["dataset_version"],
        dataset["dataset_hash"],
    )
    return record


def run_predictions(
    dataset: dict[str, Any],
    runner: PredictionRunner,
    case_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    validate_dataset(dataset)
    if case_ids is None:
        cases = dataset["cases"]
    else:
        if len(case_ids) != len(set(case_ids)):
            raise EvaluationError("case_ids cannot contain duplicates")
        case_map = {case["case_id"]: case for case in dataset["cases"]}
        unknown = [case_id for case_id in case_ids if case_id not in case_map]
        if unknown:
            raise EvaluationError(f"unknown case IDs: {', '.join(unknown)}")
        cases = [case_map[case_id] for case_id in case_ids]
        if not cases:
            raise EvaluationError("at least one case_id is required")
    return [freeze_prediction(dataset, case, runner) for case in cases]


def read_predictions(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvaluationError(f"cannot read predictions {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            raise EvaluationError(f"{path}:{line_number} is a blank JSONL record")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"{path}:{line_number} is invalid JSON: {exc}") from exc
        records.append(record)
    if not records:
        raise EvaluationError(f"prediction file is empty: {path}")
    return records


def write_predictions(
    path: Path, records: list[dict[str, Any]], *, append: bool = False
) -> None:
    if not records:
        raise EvaluationError("refusing to write an empty prediction file")
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if append:
        if path.exists():
            existing = read_predictions(path)
        existing_ids = {record.get("prediction_id") for record in existing}
        existing_runs = {
            (
                record.get("dataset_id"),
                record.get("case_id"),
                (record.get("runner") or {}).get("run_id"),
            )
            for record in existing
        }
        for record in records:
            identity = (
                record.get("dataset_id"),
                record.get("case_id"),
                (record.get("runner") or {}).get("run_id"),
            )
            if record.get("prediction_id") in existing_ids or identity in existing_runs:
                raise EvaluationError(
                    f"prediction already frozen for case/run: {record.get('case_id')}"
                )
            existing_ids.add(record.get("prediction_id"))
            existing_runs.add(identity)
        mode = "a" if path.exists() else "x"
    else:
        mode = "x"

    try:
        with path.open(mode, encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False))
                stream.write("\n")
    except FileExistsError as exc:
        raise EvaluationError(
            f"prediction output already exists and is immutable: {path}; choose a new run file"
        ) from exc
    except OSError as exc:
        raise EvaluationError(f"cannot write predictions {path}: {exc}") from exc
