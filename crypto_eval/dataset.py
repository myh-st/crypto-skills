"""Reproducible dataset construction and chronological split assignment."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from .contracts import (
    EvaluationError,
    chronological_fold_map,
    canonical_json,
    digest,
    parse_timestamp,
    validate_candidate_bundle,
    validate_dataset,
    validate_spec,
)


def stable_case_id(dataset_id: str, dataset_version: str, source_case_key: str) -> str:
    identity = {
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "source_case_key": source_case_key,
    }
    return f"case-{digest(identity)[:20]}"


def _assign_folds(cases: list[dict[str, Any]], spec: dict[str, Any]) -> None:
    timestamps = [
        parse_timestamp(case["as_of"], f"{case['case_id']}.as_of") for case in cases
    ]
    time_to_fold = chronological_fold_map(timestamps, spec["walk_forward"])
    for case in cases:
        timestamp = parse_timestamp(case["as_of"], f"{case['case_id']}.as_of")
        case["fold"] = time_to_fold[timestamp]


def _availability(case: dict[str, Any]) -> dict[str, str]:
    snapshot = case["snapshot"]
    provided = dict(snapshot.get("data_availability", {}))
    feature_arrays = {
        "funding": "funding",
        "open_interest": "open_interest",
        "news": "news",
        "options": "options",
        "on_chain": "on_chain",
        "macro": "macro",
    }
    for name, field in feature_arrays.items():
        if name not in provided:
            available = bool(snapshot.get(field)) or (
                name == "open_interest" and bool(snapshot.get("oi"))
            )
            provided[name] = "available" if available else "unavailable"
    if "benchmark" not in provided:
        if case.get("benchmark_asset") and snapshot.get("benchmark_candles"):
            provided["benchmark"] = "available"
        else:
            provided["benchmark"] = "not_covered"
    provided.setdefault("candles", "available")
    return provided


def build_dataset(bundle: Any, raw_spec: Any) -> dict[str, Any]:
    spec = validate_spec(raw_spec)
    candidates, source_id, data_origin, sampling_audit = validate_candidate_bundle(
        bundle, spec
    )
    case_ids: set[str] = set()
    cases: list[dict[str, Any]] = []
    for candidate in candidates:
        case_id = stable_case_id(
            spec["dataset_id"],
            spec["dataset_version"],
            candidate["source_case_key"],
        )
        if case_id in case_ids:
            raise EvaluationError(f"case ID collision for {candidate['source_case_key']!r}")
        case_ids.add(case_id)
        case = dict(candidate)
        case["case_id"] = case_id
        case["snapshot"] = dict(candidate["snapshot"])
        case["data_availability"] = _availability(case)
        case["snapshot"].pop("data_availability", None)
        cases.append(case)
    cases.sort(
        key=lambda case: (
            parse_timestamp(case["as_of"], f"{case['case_id']}.as_of"),
            case["case_id"],
        )
    )
    _assign_folds(cases, spec)

    coverage = spec["coverage"]
    counts_by_asset = Counter(case["asset"] for case in cases)
    counts_by_regime = Counter(case.get("market_regime") for case in cases)
    missing_assets = [
        asset.upper() for asset in spec["universe"] if counts_by_asset[asset.upper()] == 0
    ]
    minimum_per_asset = coverage["minimum_cases_per_asset"]
    underrepresented = {
        asset: count
        for asset, count in counts_by_asset.items()
        if count < minimum_per_asset
    }
    required_regimes = coverage.get("required_market_regimes", [])
    missing_regimes = [regime for regime in required_regimes if counts_by_regime[regime] == 0]
    minimum_per_regime = coverage.get("minimum_cases_per_regime", 0)
    underrepresented_regimes = {
        regime: counts_by_regime[regime]
        for regime in required_regimes
        if counts_by_regime[regime] < minimum_per_regime
    }
    if coverage["require_all_assets"] and missing_assets:
        raise EvaluationError(f"dataset is missing required assets: {', '.join(missing_assets)}")
    if underrepresented:
        summary = ", ".join(f"{asset}={count}" for asset, count in sorted(underrepresented.items()))
        raise EvaluationError(
            f"dataset does not meet minimum_cases_per_asset={minimum_per_asset}: {summary}"
        )
    if missing_regimes:
        raise EvaluationError(
            f"dataset is missing required market regimes: {', '.join(missing_regimes)}"
        )
    if underrepresented_regimes:
        summary = ", ".join(
            f"{regime}={count}" for regime, count in sorted(underrepresented_regimes.items())
        )
        raise EvaluationError(
            "dataset does not meet minimum_cases_per_regime="
            f"{minimum_per_regime}: {summary}"
        )

    dataset: dict[str, Any] = {
        "schema_version": "crypto-eval.dataset.v1",
        "dataset_id": spec["dataset_id"],
        "dataset_version": spec["dataset_version"],
        "spec_version": spec["spec_version"],
        "spec_hash": digest(spec),
        "source_id": source_id,
        "data_origin": data_origin,
        "spec": spec,
        "coverage_summary": {
            "cases": len(cases),
            "scheduled_cases": sampling_audit["scheduled_case_count"],
            "included_cases": sampling_audit["included_case_count"],
            "excluded_cases": len(sampling_audit["excluded_cases"]),
            "assets": dict(sorted(counts_by_asset.items())),
            "market_regimes": {
                str(regime): count for regime, count in sorted(counts_by_regime.items(), key=lambda item: str(item[0]))
            },
            "missing_target_assets": missing_assets,
        },
        "sampling_audit": sampling_audit,
        "cases": cases,
    }
    dataset["dataset_hash"] = digest(dataset)
    validate_dataset(dataset)
    return dataset


def validate_walk_forward(dataset: dict[str, Any]) -> None:
    """Fail if chronological folds are missing, shuffled, or overlap in time."""

    validate_dataset(dataset)
    folds = {"train": [], "validation": [], "test": []}
    for case in dataset["cases"]:
        folds[case["fold"]].append(
            parse_timestamp(case["as_of"], f"{case['case_id']}.as_of")
        )
    for name, values in folds.items():
        if not values:
            raise EvaluationError(f"walk-forward dataset has no {name} cases")
    train_end = max(folds["train"])
    validation_start = min(folds["validation"])
    validation_end = max(folds["validation"])
    test_start = min(folds["test"])
    if train_end >= validation_start or validation_end >= test_start:
        raise EvaluationError("walk-forward folds must be strictly chronological and non-overlapping")


def dataset_fingerprint(dataset: dict[str, Any]) -> str:
    """Return the content-addressed fingerprint used by the dataset contract."""

    payload = dict(dataset)
    payload.pop("dataset_hash", None)
    return digest(payload)


def bind_outcomes(bundle: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
    """Resolve stable source keys to built case IDs without editing predictions."""

    if not isinstance(bundle, dict) or not isinstance(bundle.get("records"), list):
        raise EvaluationError("outcomes input must contain a records array")
    if (
        bundle.get("dataset_id") != dataset["dataset_id"]
        or bundle.get("dataset_version") != dataset["dataset_version"]
        or bundle.get("dataset_hash") != dataset["dataset_hash"]
    ):
        raise EvaluationError("outcome dataset identity/hash does not match the dataset")
    by_source_key = {case["source_case_key"]: case["case_id"] for case in dataset["cases"]}
    result = dict(bundle)
    records: list[dict[str, Any]] = []
    for record in bundle["records"]:
        if not isinstance(record, dict):
            raise EvaluationError("outcome records must be objects")
        normalized = dict(record)
        if "case_id" not in normalized:
            source_key = normalized.pop("source_case_key", None)
            if not isinstance(source_key, str) or source_key not in by_source_key:
                raise EvaluationError(f"unknown outcome source_case_key: {source_key!r}")
            normalized["case_id"] = by_source_key[source_key]
        elif "source_case_key" in normalized:
            matching_case = next(
                (case for case in dataset["cases"] if case["case_id"] == normalized["case_id"]),
                None,
            )
            if matching_case is None or matching_case["source_case_key"] != normalized["source_case_key"]:
                raise EvaluationError("outcome case_id and source_case_key do not match")
        records.append(normalized)
    result["records"] = records
    return result


def pretty_spec(spec: dict[str, Any]) -> str:
    """A deterministic JSON representation suitable for human inspection."""

    return canonical_json(spec)
