"""Trigger-aware scoring, point-in-time outcomes, and evidence-safe metrics."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import mean, median, stdev
from typing import Any

from .contracts import (
    EvaluationError,
    digest,
    parse_timestamp,
    validate_dataset,
    validate_outcomes,
    validate_prediction_record,
)
from .dataset import validate_walk_forward
from .runner import read_predictions


WAIT_STATES = {"WAIT_FOR_PULLBACK", "WAIT_FOR_BREAKOUT_CONFIRMATION"}
NON_ENTRY_STATES = {
    "NO_TRADE",
    "AVOID_CHASING",
    "HOLD",
    "REDUCE",
    "TAKE_PARTIAL_PROFIT",
    "HEDGE_DE_RISK",
    "EXIT",
}
BREAKDOWN_FIELDS = (
    "decision_state",
    "asset",
    "horizon",
    "market_regime",
    "btc_regime",
    "leverage_stress",
    "confidence",
    "liquidity_tier",
)


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 10)


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> dict[str, float] | None:
    if total <= 0:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * ((proportion * (1 - proportion) / total + z * z / (4 * total * total)) ** 0.5)
        / denominator
    )
    return {"lower": _round(max(0.0, center - margin)), "upper": _round(min(1.0, center + margin))}


def binary_summary(values: list[bool]) -> dict[str, Any]:
    total = len(values)
    successes = sum(values)
    return {
        "n": total,
        "successes": successes,
        "rate": _round(successes / total) if total else None,
        "interval_95_wilson": wilson_interval(successes, total),
    }


def scalar_summary(values: list[float]) -> dict[str, Any]:
    clean = [float(value) for value in values]
    total = len(clean)
    if not total:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "interval_95_normal_approx": None,
        }
    interval = None
    if total >= 2:
        margin = 1.959963984540054 * stdev(clean) / (total**0.5)
        center = mean(clean)
        interval = {"lower": _round(center - margin), "upper": _round(center + margin)}
    return {
        "n": total,
        "mean": _round(mean(clean)),
        "median": _round(median(clean)),
        "interval_95_normal_approx": interval,
    }


def paired_difference(left: dict[str, float], right: dict[str, float]) -> dict[str, Any]:
    shared = sorted(set(left) & set(right))
    differences = [left[case_id] - right[case_id] for case_id in shared]
    summary = scalar_summary(differences)
    return {
        "n_paired": summary["n"],
        "mean_difference": summary["mean"],
        "interval_95_normal_approx": summary["interval_95_normal_approx"],
        "definition": "left minus right; descriptive paired comparison, not a significance test",
    }


def _entry_direction(decision: dict[str, Any]) -> str | None:
    entry = decision.get("entry")
    if isinstance(entry, dict) and entry.get("kind") not in {None, "none"}:
        return entry.get("direction")
    bias = decision.get("bias")
    return {"bullish": "long", "bearish": "short"}.get(bias)


def _side_return(price: float, reference: float, direction: str) -> float:
    if direction == "long":
        return price / reference - 1.0
    return reference / price - 1.0


def _gap_before(
    case: dict[str, Any], bars: list[dict[str, Any]]
) -> tuple[list[bool], bool]:
    expected_open = parse_timestamp(
        case["snapshot"]["candles"][-1]["close_time"], f"{case['case_id']}.last_snapshot_close"
    )
    gaps: list[bool] = []
    for index, bar in enumerate(bars):
        open_time = parse_timestamp(bar["open_time"], f"{case['case_id']}.outcome[{index}].open_time")
        gap = abs((open_time - expected_open).total_seconds()) > 1.0
        gaps.append(gap)
        expected_open = parse_timestamp(
            bar["close_time"], f"{case['case_id']}.outcome[{index}].close_time"
        )
    return gaps, any(gaps)


def _series_complete(
    case: dict[str, Any],
    bars: list[dict[str, Any]],
    outcome_status: str,
) -> tuple[bool, list[dict[str, Any]], list[bool], str | None]:
    horizon = case["horizon_bars"]
    window = bars[:horizon]
    gaps, has_gap = _gap_before(case, window)
    if outcome_status == "unavailable":
        return False, window, gaps, "provider_unavailable"
    if len(window) < horizon:
        return False, window, gaps, "fewer_than_horizon_bars"
    if has_gap:
        return False, window, gaps, "missing_interval"
    if outcome_status == "partial":
        return False, window, gaps, "provider_marked_partial"
    return True, window, gaps, None


def _find_trigger(
    case: dict[str, Any],
    decision: dict[str, Any],
    bars: list[dict[str, Any]],
    gaps: list[bool],
    complete: bool,
) -> dict[str, Any]:
    entry = decision.get("entry")
    state = decision["decision_state"]
    if state not in WAIT_STATES:
        if isinstance(entry, dict) and entry.get("kind") == "immediate":
            return {
                "status": "immediate",
                "trigger_index": -1,
                "triggered_at": case["as_of"],
                "entry_reference": float(entry["reference_price"]),
                "bars_to_trigger": 0,
                "elapsed_hours": 0.0,
            }
        return {
            "status": "not_applicable",
            "trigger_index": None,
            "triggered_at": None,
            "entry_reference": None,
            "bars_to_trigger": None,
            "elapsed_hours": None,
        }

    if not isinstance(entry, dict):
        return {
            "status": "unknown_missing_trigger",
            "trigger_index": None,
            "triggered_at": None,
            "entry_reference": None,
            "bars_to_trigger": None,
            "elapsed_hours": None,
        }
    direction = entry["direction"]
    for index, bar in enumerate(bars):
        if index >= len(gaps) or gaps[index]:
            return {
                "status": "unknown_missing_interval",
                "trigger_index": None,
                "triggered_at": None,
                "entry_reference": None,
                "bars_to_trigger": None,
                "elapsed_hours": None,
            }
        open_price = float(bar["open"])
        close_price = float(bar["close"])
        low = float(bar["low"])
        high = float(bar["high"])
        kind = entry["kind"]

        if kind == "pullback":
            zone_low = float(entry["zone_low"])
            zone_high = float(entry["zone_high"])
            gap_through = (direction == "long" and open_price < zone_low) or (
                direction == "short" and open_price > zone_high
            )
            if gap_through:
                continue
            touched = low <= zone_high and high >= zone_low
            confirmation = entry.get("confirmation", "touch")
            if confirmation == "close_inside_zone":
                touched = touched and zone_low <= close_price <= zone_high
            elif confirmation == "close_beyond_zone":
                touched = touched and (
                    close_price >= zone_high if direction == "long" else close_price <= zone_low
                )
            if not touched:
                continue
            if zone_low <= open_price <= zone_high:
                entry_reference = open_price
            else:
                entry_reference = zone_high if direction == "long" else zone_low
        elif kind == "breakout":
            level = float(entry["level"])
            confirmed = close_price >= level if direction == "long" else close_price <= level
            if not confirmed:
                continue
            if direction == "long":
                entry_reference = open_price if open_price > level else level
            else:
                entry_reference = open_price if open_price < level else level
        else:
            continue

        triggered_at = parse_timestamp(
            bar["close_time"], f"{case['case_id']}.outcome[{index}].close_time"
        )
        as_of = parse_timestamp(case["as_of"], f"{case['case_id']}.as_of")
        return {
            "status": "triggered",
            "trigger_index": index,
            "triggered_at": bar["close_time"],
            "entry_reference": _round(entry_reference),
            "bars_to_trigger": index + 1,
            "elapsed_hours": _round((triggered_at - as_of).total_seconds() / 3600),
        }
    if complete:
        return {
            "status": "not_triggered",
            "trigger_index": None,
            "triggered_at": None,
            "entry_reference": None,
            "bars_to_trigger": None,
            "elapsed_hours": None,
        }
    return {
        "status": "unknown_missing_interval",
        "trigger_index": None,
        "triggered_at": None,
        "entry_reference": None,
        "bars_to_trigger": None,
        "elapsed_hours": None,
    }


def _first_path_event(
    decision: dict[str, Any],
    direction: str,
    bars: list[dict[str, Any]],
    gaps: list[bool],
    complete: bool,
    trigger_index: int,
) -> dict[str, Any]:
    targets = [float(value) for value in decision.get("targets", [])]
    invalidation = decision.get("invalidation")
    stop = float(invalidation) if invalidation is not None else None
    start_index = max(trigger_index, 0)
    for index in range(start_index, len(bars)):
        if index > start_index and (index >= len(gaps) or gaps[index]):
            return {
                "status": "unknown_missing_interval",
                "first_event": None,
                "target_index": None,
                "bar_index": None,
                "event_at": None,
                "event_price": None,
                "same_candle_ambiguous": False,
            }
        if index == start_index and trigger_index < 0 and (index >= len(gaps) or gaps[index]):
            return {
                "status": "unknown_missing_interval",
                "first_event": None,
                "target_index": None,
                "bar_index": None,
                "event_at": None,
                "event_price": None,
                "same_candle_ambiguous": False,
            }

        bar = bars[index]
        open_price = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        stop_hit = stop is not None and (
            (direction == "long" and (low <= stop or open_price <= stop))
            or (direction == "short" and (high >= stop or open_price >= stop))
        )
        target_hits = [
            target_index
            for target_index, target in enumerate(targets)
            if (direction == "long" and (high >= target or open_price >= target))
            or (direction == "short" and (low <= target or open_price <= target))
        ]
        same_candle = bool(stop_hit and target_hits)
        event_at = bar["close_time"]
        if stop_hit:
            event_price = (
                open_price
                if (direction == "long" and open_price <= stop)
                or (direction == "short" and open_price >= stop)
                else stop
            )
            return {
                "status": "event",
                "first_event": "invalidation",
                "target_index": None,
                "bar_index": index,
                "event_at": event_at,
                "event_price": _round(event_price),
                "same_candle_ambiguous": same_candle,
            }
        if index == start_index and trigger_index >= 0:
            continue
        if target_hits:
            target_index = min(target_hits)
            target = targets[target_index]
            event_price = (
                open_price
                if (direction == "long" and open_price >= target)
                or (direction == "short" and open_price <= target)
                else target
            )
            return {
                "status": "event",
                "first_event": "target",
                "target_index": target_index,
                "bar_index": index,
                "event_at": event_at,
                "event_price": _round(event_price),
                "same_candle_ambiguous": False,
            }
    if complete:
        return {
            "status": "no_event",
            "first_event": None,
            "target_index": None,
            "bar_index": None,
            "event_at": None,
            "event_price": None,
            "same_candle_ambiguous": False,
        }
    return {
        "status": "unknown_missing_interval",
        "first_event": None,
        "target_index": None,
        "bar_index": None,
        "event_at": None,
        "event_price": None,
        "same_candle_ambiguous": False,
    }


def _excursions(
    direction: str,
    entry_reference: float,
    bars: list[dict[str, Any]],
    trigger_index: int,
    complete: bool,
) -> tuple[float | None, float | None]:
    if not complete or not bars:
        return None, None
    favorable: list[float] = []
    adverse: list[float] = []
    for index, bar in enumerate(bars):
        if trigger_index >= 0 and index < trigger_index:
            continue
        if trigger_index >= 0 and index == trigger_index:
            high = low = float(bar["close"])
        else:
            high = float(bar["high"])
            low = float(bar["low"])
        if direction == "long":
            favorable.append(max(0.0, high / entry_reference - 1.0))
            adverse.append(min(0.0, low / entry_reference - 1.0))
        else:
            favorable.append(max(0.0, entry_reference / low - 1.0))
            adverse.append(min(0.0, entry_reference / high - 1.0))
    return _round(max(favorable)), _round(min(adverse))


def _single_case_score(
    case: dict[str, Any],
    prediction: dict[str, Any] | None,
    outcome: dict[str, Any] | None,
) -> dict[str, Any]:
    decision = prediction["decision"] if prediction else None
    state = decision.get("decision_state") if decision else None
    base: dict[str, Any] = {
        "case_id": case["case_id"],
        "asset": case["asset"],
        "horizon": case["horizon"],
        "fold": case["fold"],
        "as_of": case["as_of"],
        "decision_state": state,
        "confidence": decision.get("confidence") if decision else None,
        "market_regime": case.get("market_regime"),
        "btc_regime": case.get("btc_regime"),
        "leverage_stress": (
            case.get("leverage_stress")
            if case.get("leverage_stress") not in (None, "unavailable", "not_covered")
            else (decision.get("leverage_stress") if decision else None)
        ),
        "liquidity_tier": case.get("liquidity_tier"),
        "prediction_id": prediction.get("prediction_id") if prediction else None,
        "outcome_status": "missing" if outcome is None else outcome["status"],
        "forward_return": None,
        "benchmark_return": None,
        "alpha": None,
        "directional_outcome": None,
        "directional_return": None,
        "trigger": {
            "status": "pending_prediction" if prediction is None else "unavailable_outcome",
            "triggered_at": None,
            "entry_reference": None,
            "bars_to_trigger": None,
            "elapsed_hours": None,
        },
        "path": {
            "status": "unavailable",
            "first_event": None,
            "target_index": None,
            "event_at": None,
            "event_price": None,
            "same_candle_ambiguous": False,
            "target_before_invalidation": None,
            "invalidation_hit": None,
            "target_hit": None,
            "time_to_target_hours": None,
        },
        "entry_return": None,
        "mfe": None,
        "mae": None,
        "score_status": "pending_prediction" if prediction is None else "waiting_for_outcome",
        "data_notes": [],
    }
    if outcome is None or outcome["status"] == "unavailable":
        base["data_notes"].append("outcome data unavailable; metrics are omitted, not zero-filled")
        return base
    if prediction is None:
        base["score_status"] = "outcome_available_without_prediction"
        return base

    frozen_at = parse_timestamp(prediction["frozen_at"], f"{case['case_id']}.frozen_at")
    known_at = parse_timestamp(outcome["known_at"], f"{case['case_id']}.outcome.known_at")
    if frozen_at >= known_at:
        raise EvaluationError(
            f"prediction for {case['case_id']} was frozen at/after outcome known_at"
        )
    base["score_status"] = "scored_partial"

    bars = outcome.get("candles", [])[: case["horizon_bars"]]
    complete, bars, gaps, incomplete_reason = _series_complete(case, bars, outcome["status"])
    if incomplete_reason:
        base["data_notes"].append(
            f"asset price path is incomplete ({incomplete_reason}); horizon metrics are unavailable"
        )
    if complete:
        reference = float(case["snapshot"]["candles"][-1]["close"])
        final_close = float(bars[-1]["close"])
        raw_return = final_close / reference - 1.0
        base["forward_return"] = _round(raw_return)
        base["score_status"] = "scored"

    benchmark_candles = outcome.get("benchmark_candles", [])[: case["horizon_bars"]]
    if case.get("benchmark_asset") and case["snapshot"].get("benchmark_candles") and benchmark_candles:
        benchmark_complete, benchmark_bars, _, benchmark_reason = _series_complete(
            case, benchmark_candles, outcome["status"]
        )
        if benchmark_complete:
            benchmark_reference = float(case["snapshot"]["benchmark_candles"][-1]["close"])
            benchmark_close = float(benchmark_bars[-1]["close"])
            base["benchmark_return"] = _round(benchmark_close / benchmark_reference - 1.0)
            if base["forward_return"] is not None:
                base["alpha"] = _round(base["forward_return"] - base["benchmark_return"])
        else:
            base["data_notes"].append(
                f"benchmark return unavailable ({benchmark_reason or 'incomplete benchmark data'})"
            )
    else:
        base["data_notes"].append("benchmark return unavailable; no point-in-time benchmark series")

    if decision is None:
        return base
    direction = _entry_direction(decision)
    if direction and base["forward_return"] is not None:
        directional_return = base["forward_return"] * (1 if direction == "long" else -1)
        base["directional_return"] = _round(directional_return)
        if directional_return > 0:
            base["directional_outcome"] = "correct"
        elif directional_return < 0:
            base["directional_outcome"] = "incorrect"
        else:
            base["directional_outcome"] = "flat"

    trigger = _find_trigger(case, decision, bars, gaps, complete)
    base["trigger"] = {
        key: trigger[key]
        for key in (
            "status",
            "triggered_at",
            "entry_reference",
            "bars_to_trigger",
            "elapsed_hours",
        )
    }
    trigger_index = trigger["trigger_index"]
    entry_reference = trigger["entry_reference"]
    if entry_reference is None or direction is None:
        if state in WAIT_STATES and trigger["status"] == "not_triggered":
            base["data_notes"].append(
                "valid wait was not triggered within the evaluation window; no entry return or excursion is scored"
            )
        return base

    if complete:
        end_price = float(bars[-1]["close"])
        base["entry_return"] = _round(_side_return(end_price, float(entry_reference), direction))
        base["mfe"], base["mae"] = _excursions(
            direction, float(entry_reference), bars, trigger_index, complete
        )

    path = _first_path_event(
        decision,
        direction,
        bars,
        gaps,
        complete,
        trigger_index if trigger_index is not None else -1,
    )
    invalidation_configured = decision.get("invalidation") is not None
    targets_configured = bool(decision.get("targets"))
    path_result = {
        "status": path["status"],
        "first_event": path["first_event"],
        "target_index": path["target_index"],
        "event_at": path["event_at"],
        "event_price": path["event_price"],
        "same_candle_ambiguous": path["same_candle_ambiguous"],
        "target_before_invalidation": None,
        "invalidation_hit": None,
        "target_hit": None,
        "time_to_target_hours": None,
    }
    if path["status"] in {"event", "no_event"}:
        if invalidation_configured:
            path_result["invalidation_hit"] = path["first_event"] == "invalidation"
        if targets_configured:
            path_result["target_hit"] = path["first_event"] == "target"
        if invalidation_configured and targets_configured:
            path_result["target_before_invalidation"] = path["first_event"] == "target"
        if path["first_event"] == "target":
            entry_time = (
                parse_timestamp(trigger["triggered_at"], f"{case['case_id']}.triggered_at")
                if trigger["triggered_at"]
                else parse_timestamp(case["as_of"], f"{case['case_id']}.as_of")
            )
            target_time = parse_timestamp(path["event_at"], f"{case['case_id']}.target_at")
            path_result["time_to_target_hours"] = _round(
                (target_time - entry_time).total_seconds() / 3600
            )
    base["path"] = path_result
    return base


def _summarize_metrics(scores: list[dict[str, Any]]) -> dict[str, Any]:
    directionals = [
        row["directional_outcome"] == "correct"
        for row in scores
        if row["directional_outcome"] in {"correct", "incorrect"}
    ]
    trigger_rows = [
        row
        for row in scores
        if row["decision_state"] in WAIT_STATES
        and row["trigger"]["status"] in {"triggered", "not_triggered"}
    ]
    invalidation_values = [
        row["path"]["invalidation_hit"]
        for row in scores
        if row["path"]["invalidation_hit"] is not None
    ]
    target_values = [
        row["path"]["target_hit"]
        for row in scores
        if row["path"]["target_hit"] is not None
    ]
    target_before_values = [
        row["path"]["target_before_invalidation"]
        for row in scores
        if row["path"]["target_before_invalidation"] is not None
    ]
    decisions_with_entry = [
        row for row in scores if row["entry_return"] is not None
    ]
    decision_returns = {
        row["case_id"]: float(row["entry_return"]) for row in decisions_with_entry
    }
    ordered_returns = [
        float(row["entry_return"])
        for row in sorted(
            decisions_with_entry,
            key=lambda item: (item["as_of"], item["case_id"]),
        )
    ]
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for result in ordered_returns:
        cumulative += result
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    confidence_bins: dict[str, Any] = {}
    for label in ("low", "moderate", "high"):
        values = [
            row["directional_outcome"] == "correct"
            for row in scores
            if row["confidence"] == label
            and row["directional_outcome"] in {"correct", "incorrect"}
        ]
        confidence_bins[label] = binary_summary(values)
    has_all_confidence_samples = all(
        confidence_bins[label]["n"] >= 30 for label in ("low", "moderate", "high")
    )
    observed_rates = [
        confidence_bins[label]["rate"]
        for label in ("low", "moderate", "high")
        if confidence_bins[label]["rate"] is not None
    ]
    ordered = (
        all(left <= right for left, right in zip(observed_rates, observed_rates[1:]))
        if len(observed_rates) == 3
        else None
    )
    return {
        "case_count": len(scores),
        "outcome_available_count": sum(row["outcome_status"] != "missing" for row in scores),
        "complete_price_path_count": sum(row["forward_return"] is not None for row in scores),
        "directional_accuracy": binary_summary(directionals),
        "mean_forward_return": scalar_summary(
            [row["forward_return"] for row in scores if row["forward_return"] is not None]
        ),
        "median_forward_return": _round(
            median([row["forward_return"] for row in scores if row["forward_return"] is not None])
        )
        if any(row["forward_return"] is not None for row in scores)
        else None,
        "mean_benchmark_return": scalar_summary(
            [row["benchmark_return"] for row in scores if row["benchmark_return"] is not None]
        ),
        "mean_alpha": scalar_summary(
            [row["alpha"] for row in scores if row["alpha"] is not None]
        ),
        "mean_mfe": scalar_summary([row["mfe"] for row in scores if row["mfe"] is not None]),
        "mean_mae": scalar_summary([row["mae"] for row in scores if row["mae"] is not None]),
        "entry_trigger_rate": binary_summary(
            [row["trigger"]["status"] == "triggered" for row in trigger_rows]
        ),
        "invalidation_hit_rate": binary_summary(invalidation_values),
        "target_hit_rate": binary_summary(target_values),
        "target_before_invalidation_rate": binary_summary(target_before_values),
        "mean_time_to_trigger_hours": scalar_summary(
            [
                row["trigger"]["elapsed_hours"]
                for row in scores
                if row["trigger"]["elapsed_hours"] is not None
            ]
        ),
        "mean_time_to_target_hours": scalar_summary(
            [
                row["path"]["time_to_target_hours"]
                for row in scores
                if row["path"]["time_to_target_hours"] is not None
            ]
        ),
        "max_drawdown_proxy": {
            "value": _round(max_drawdown) if decisions_with_entry else None,
            "n": len(decision_returns),
            "definition": (
                "maximum peak-to-trough decline in the cumulative equal-weighted "
                "decision-return sequence; not a portfolio PnL backtest"
            ),
        },
        "confidence_reliability": {
            "interpretation": (
                "Observed directional hit rates by qualitative confidence label; "
                "not probabilities or probability calibration."
            ),
            "bins": confidence_bins,
            "rank_ordered_observed_rates": ordered,
            "sample_status": "minimum_30_per_label" if has_all_confidence_samples else "descriptive_small_sample",
        },
    }


def _breakdowns(scores: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for dimension in BREAKDOWN_FIELDS:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in scores:
            value = row.get(dimension)
            groups[str(value) if value is not None else "unavailable"].append(row)
        output[dimension] = {
            name: _summarize_metrics(rows)
            for name, rows in sorted(groups.items())
        }
    return output


def score_predictions(
    dataset: dict[str, Any],
    predictions: list[dict[str, Any]],
    outcomes: dict[str, Any],
    *,
    fold: str = "test",
) -> dict[str, Any]:
    validate_dataset(dataset)
    validate_walk_forward(dataset)
    validate_outcomes(outcomes, dataset)
    if not isinstance(predictions, list):
        raise EvaluationError("predictions must be an array of frozen records")
    if fold not in {"train", "validation", "test", "all"}:
        raise EvaluationError("fold must be train, validation, test, or all")
    case_map = {case["case_id"]: case for case in dataset["cases"]}
    prediction_map: dict[str, dict[str, Any]] = {}
    runner_signatures: set[str] = set()
    for record in predictions:
        case_id = record.get("case_id") if isinstance(record, dict) else None
        if not isinstance(case_id, str) or case_id not in case_map:
            raise EvaluationError(f"prediction references unknown case_id: {case_id!r}")
        if case_id in prediction_map:
            raise EvaluationError(f"multiple predictions supplied for case_id {case_id}")
        validate_prediction_record(
            record,
            case_map[case_id],
            dataset["dataset_id"],
            dataset["dataset_version"],
            dataset["dataset_hash"],
        )
        runner_signatures.add(digest(record["runner"]))
        prediction_map[case_id] = record
    if len(runner_signatures) > 1:
        raise EvaluationError("score accepts one immutable prediction run at a time")
    outcome_map = {record["case_id"]: record for record in outcomes["records"]}

    case_scores = [
        _single_case_score(case, prediction_map.get(case["case_id"]), outcome_map.get(case["case_id"]))
        for case in dataset["cases"]
    ]
    selected = [
        row for row in case_scores if fold == "all" or row["fold"] == fold
    ]
    metrics_by_fold = {
        name: _summarize_metrics([row for row in case_scores if row["fold"] == name])
        for name in ("train", "validation", "test")
    }
    breakdown_scores = selected
    runner = predictions[0]["runner"] if predictions else None
    return {
        "schema_version": "crypto-eval.scores.v1",
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["dataset_version"],
        "dataset_hash": dataset["dataset_hash"],
        "outcomes_hash": digest(outcomes),
        "sampling_audit": dataset["sampling_audit"],
        "data_origin": dataset.get("data_origin", "unknown"),
        "fold_scope": fold,
        "runner": runner,
        "runtime_limitations": [
            "The harness does not invoke a model unless an external PredictionRunner implementation does so.",
            "FixtureRunner is a deterministic mechanics test, not the crypto-market-trading-analysis skill.",
            "Decision-quality metrics are not portfolio PnL; sizing, fills, fees, slippage, funding, and cash are not modeled.",
        ],
        "metrics": _summarize_metrics(selected),
        "metrics_by_fold": metrics_by_fold,
        "breakdowns": _breakdowns(breakdown_scores),
        "cases": case_scores,
    }


def score_prediction_file(
    dataset: dict[str, Any],
    prediction_path: Any,
    outcomes: dict[str, Any],
    *,
    fold: str = "test",
) -> dict[str, Any]:
    return score_predictions(dataset, read_predictions(prediction_path), outcomes, fold=fold)
