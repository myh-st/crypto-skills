"""Forward paper-evaluation lifecycle projection."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .contracts import (
    EvaluationError,
    parse_timestamp,
    validate_dataset,
    validate_outcomes,
    validate_prediction_record,
)


def project_lifecycle(
    dataset: dict[str, Any],
    predictions: list[dict[str, Any]] | None = None,
    outcomes: dict[str, Any] | None = None,
    scores: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    validate_dataset(dataset)
    prediction_by_case: dict[str, dict[str, Any]] = {}
    case_map = {case["case_id"]: case for case in dataset["cases"]}
    for prediction in predictions or []:
        case_id = prediction.get("case_id") if isinstance(prediction, dict) else None
        if not isinstance(case_id, str) or case_id not in case_map:
            raise EvaluationError(f"unknown prediction case in lifecycle: {case_id!r}")
        if case_id in prediction_by_case:
            raise EvaluationError(f"multiple predictions prevent lifecycle projection: {case_id}")
        validate_prediction_record(
            prediction,
            case_map[case_id],
            dataset["dataset_id"],
            dataset["dataset_version"],
            dataset["dataset_hash"],
        )
        prediction_by_case[case_id] = prediction
    if outcomes is not None:
        validate_outcomes(outcomes, dataset)
    if scores is not None and (
        scores.get("dataset_id") != dataset["dataset_id"]
        or scores.get("dataset_version") != dataset["dataset_version"]
        or scores.get("dataset_hash") != dataset["dataset_hash"]
    ):
        raise EvaluationError("scores do not match the lifecycle dataset identity")
    outcome_by_case = {
        record["case_id"]: record for record in (outcomes or {}).get("records", [])
    }
    score_records = (scores or {}).get("cases", [])
    if not isinstance(score_records, list):
        raise EvaluationError("scores cases must be an array")
    for row in score_records:
        if not isinstance(row, dict):
            raise EvaluationError("score case records must be objects")
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or case_id not in case_map:
            raise EvaluationError(f"score contains an unknown case_id: {case_id!r}")
        prediction = prediction_by_case.get(case_id)
        if row.get("prediction_id") is not None and (
            prediction is None or row["prediction_id"] != prediction["prediction_id"]
        ):
            raise EvaluationError(f"score prediction_id does not match frozen prediction for {case_id}")
    scored_ids = {
        record.get("case_id")
        for record in score_records
        if record.get("prediction_id") is not None
        and record.get("score_status") in {"scored", "scored_partial"}
    }

    projection: list[dict[str, Any]] = []
    for case in dataset["cases"]:
        case_id = case["case_id"]
        prediction = prediction_by_case.get(case_id)
        outcome = outcome_by_case.get(case_id)
        events = ["pending"]
        status = "pending"
        if prediction:
            events.append("prediction_frozen")
            status = "prediction_frozen"
            known_at = None
            outcome_ready = False
            if outcome and outcome.get("status") in {"complete", "partial"}:
                known_at = parse_timestamp(outcome["known_at"], f"{case_id}.outcome.known_at")
                frozen_at = parse_timestamp(prediction["frozen_at"], f"{case_id}.frozen_at")
                if frozen_at >= known_at:
                    raise EvaluationError(
                        f"prediction for {case_id} was frozen after its outcome was known"
                    )
                outcome_ready = True
            if outcome_ready:
                events.append("ready_to_score")
                status = "ready_to_score"
                if case_id in scored_ids:
                    events.append("scored")
                    status = "scored"
            else:
                events.append("waiting_for_outcome")
                status = "waiting_for_outcome"
        projection.append(
            {
                "case_id": case_id,
                "fold": case["fold"],
                "status": status,
                "events": events,
            }
        )
    return projection


def summarize_lifecycle(projection: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in projection:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return dict(sorted(counts.items()))
