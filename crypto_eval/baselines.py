"""Fixed, deterministic comparators and paired evaluation summaries."""

from __future__ import annotations

import hashlib
from collections import Counter
from statistics import mean, median
from typing import Any

from .contracts import EvaluationError, digest, validate_dataset, validate_outcomes
from .dataset import validate_walk_forward
from .scoring import (
    _series_complete,
    binary_summary,
    paired_difference,
    scalar_summary,
    score_predictions,
)


BASELINE_NAMES = (
    "buy_and_hold",
    "btc_benchmark",
    "ema20_ema50",
    "fixed_rsi14",
    "naive_previous_candle",
    "seeded_random",
)


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    current = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        current = (value - current) * multiplier + current
    return current


def simple_rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    changes = [values[index] - values[index - 1] for index in range(len(values) - period, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_gain == 0 and average_loss == 0:
        return 50.0
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def _random_side(seed: int, case_id: str) -> str:
    value = hashlib.sha256(f"{seed}:{case_id}".encode("utf-8")).digest()[0]
    return "long" if value % 2 == 0 else "short"


def baseline_signals(case: dict[str, Any], seed: int = 1729) -> dict[str, dict[str, Any]]:
    candles = case["snapshot"]["candles"]
    closes = [float(candle["close"]) for candle in candles]
    last = candles[-1]
    values: dict[str, dict[str, Any]] = {
        "buy_and_hold": {"status": "signal", "side": "long", "market": "asset"},
    }
    benchmark = case["snapshot"].get("benchmark_candles", [])
    if case.get("asset") != "BTC" and case.get("benchmark_asset") == "BTC" and benchmark:
        values["btc_benchmark"] = {"status": "signal", "side": "long", "market": "benchmark"}
    else:
        values["btc_benchmark"] = {
            "status": "not_applicable" if case.get("asset") == "BTC" else "unavailable",
            "side": None,
            "market": "benchmark",
        }

    complete_history = case.get("data_availability", {}).get("candles", "available") == "available"
    fast = ema(closes, 20) if complete_history else None
    slow = ema(closes, 50) if complete_history else None
    if fast is None or slow is None:
        values["ema20_ema50"] = {
            "status": "unavailable",
            "side": None,
            "market": "asset",
            "reason": (
                "snapshot candle history is partial"
                if not complete_history
                else "requires at least 50 closed candles"
            ),
        }
    else:
        values["ema20_ema50"] = {
            "status": "signal",
            "side": "long" if fast > slow else "short",
            "market": "asset",
            "ema20": fast,
            "ema50": slow,
        }

    rsi = simple_rsi(closes, 14) if complete_history else None
    if rsi is None:
        values["fixed_rsi14"] = {
            "status": "unavailable",
            "side": None,
            "market": "asset",
            "reason": (
                "snapshot candle history is partial"
                if not complete_history
                else "requires at least 15 closed candles"
            ),
        }
    elif rsi < 30:
        values["fixed_rsi14"] = {"status": "signal", "side": "long", "market": "asset", "rsi14": rsi}
    elif rsi > 70:
        values["fixed_rsi14"] = {"status": "signal", "side": "short", "market": "asset", "rsi14": rsi}
    else:
        values["fixed_rsi14"] = {
            "status": "no_signal",
            "side": None,
            "market": "asset",
            "rsi14": rsi,
        }

    previous_candle = candles[-1]
    if float(previous_candle["close"]) > float(previous_candle["open"]):
        naive_side = "long"
    elif float(previous_candle["close"]) < float(previous_candle["open"]):
        naive_side = "short"
    else:
        naive_side = None
    values["naive_previous_candle"] = {
        "status": "signal" if naive_side else "no_signal",
        "side": naive_side,
        "market": "asset",
    }
    values["seeded_random"] = {
        "status": "signal",
        "side": _random_side(seed, case["case_id"]),
        "market": "asset",
        "seed": seed,
    }
    return values


def _baseline_return(
    case: dict[str, Any],
    outcome: dict[str, Any] | None,
    signal: dict[str, Any],
) -> tuple[float | None, float | None, str | None]:
    if outcome is None or outcome["status"] == "unavailable":
        return None, None, "outcome_unavailable"
    if signal["status"] != "signal" or signal["side"] is None:
        return None, None, signal.get("reason", signal["status"])
    market = signal["market"]
    if market == "benchmark":
        history = case["snapshot"].get("benchmark_candles", [])
        forward = outcome.get("benchmark_candles", [])
    else:
        history = case["snapshot"]["candles"]
        forward = outcome.get("candles", [])
    if not history or not forward:
        return None, None, "price_series_unavailable"
    is_complete, bars, _, reason = _series_complete(
        case, forward[: case["horizon_bars"]], outcome["status"]
    )
    if not is_complete:
        return None, None, reason or "incomplete_price_series"
    reference = float(history[-1]["close"])
    final_price = float(bars[-1]["close"])
    raw = final_price / reference - 1.0
    directional = raw if signal["side"] == "long" else -raw
    return directional, raw, None


def _baseline_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    available = [row for row in rows if row["strategy_return"] is not None]
    directional = [
        row["directional_correct"]
        for row in available
        if row["directional_correct"] is not None
    ]
    returns = [float(row["strategy_return"]) for row in available]
    ordered = sorted(available, key=lambda row: (row["as_of"], row["case_id"]))
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in ordered:
        cumulative += float(row["strategy_return"])
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    counts = Counter(row["status"] for row in rows)
    return {
        "eligible_cases": len(rows),
        "signal_count": sum(row["status"] == "signal" for row in rows),
        "available_return_count": len(available),
        "status_counts": dict(sorted(counts.items())),
        "directional_accuracy": binary_summary(directional),
        "mean_strategy_return": scalar_summary(returns),
        "median_strategy_return": {
            "n": len(returns),
            "value": round(median(returns), 10) if returns else None,
        },
        "mean_alpha_vs_btc": scalar_summary(
            [row["alpha_vs_btc"] for row in available if row["alpha_vs_btc"] is not None]
        ),
        "max_drawdown_proxy": {
            "value": round(max_drawdown, 10) if available else None,
            "n": len(available),
            "definition": (
                "maximum peak-to-trough decline in the cumulative equal-weighted "
                "decision-return sequence; not a portfolio PnL backtest"
            ),
        },
    }


def compare_baselines(
    dataset: dict[str, Any],
    outcomes: dict[str, Any],
    *,
    predictions: list[dict[str, Any]] | None = None,
    seed: int | None = None,
    fold: str = "test",
) -> dict[str, Any]:
    validate_dataset(dataset)
    validate_walk_forward(dataset)
    validate_outcomes(outcomes, dataset)
    if fold not in {"train", "validation", "test", "all"}:
        raise EvaluationError("fold must be train, validation, test, or all")
    declared_seed = dataset["spec"]["baselines"]["random_seed"]
    if seed is None:
        seed = declared_seed
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise EvaluationError("baseline seed must be an integer")
    if seed != declared_seed:
        raise EvaluationError("random baseline seed is fixed by the versioned dataset spec")
    outcome_map = {record["case_id"]: record for record in outcomes["records"]}
    rows_by_name: dict[str, list[dict[str, Any]]] = {name: [] for name in BASELINE_NAMES}
    for case in dataset["cases"]:
        if fold != "all" and case["fold"] != fold:
            continue
        signals = baseline_signals(case, seed)
        for name in BASELINE_NAMES:
            signal = signals[name]
            result, raw_return, reason = _baseline_return(
                case, outcome_map.get(case["case_id"]), signal
            )
            outcome = outcome_map.get(case["case_id"])
            benchmark_result: float | None = None
            if (
                case.get("benchmark_asset")
                and case["snapshot"].get("benchmark_candles")
                and outcome
                and outcome.get("benchmark_candles")
            ):
                benchmark_result, _, _ = _baseline_return(
                    case,
                    outcome,
                    {"status": "signal", "side": "long", "market": "benchmark"},
                )
            alpha = None
            if result is not None and benchmark_result is not None and signal["market"] != "benchmark":
                alpha = result - benchmark_result
            asset_raw = raw_return
            if signal["market"] == "benchmark":
                asset_raw = None
            correct = None
            if result is not None and signal["market"] != "benchmark" and asset_raw is not None:
                if asset_raw > 0:
                    correct = signal["side"] == "long"
                elif asset_raw < 0:
                    correct = signal["side"] == "short"
            row_status = signal["status"]
            if reason is not None and signal["status"] == "signal":
                row_status = "unavailable"
            rows_by_name[name].append(
                {
                    "case_id": case["case_id"],
                    "asset": case["asset"],
                    "fold": case["fold"],
                    "as_of": case["as_of"],
                    "status": row_status,
                    "side": signal["side"],
                    "strategy_return": result,
                    "raw_return": raw_return,
                    "alpha_vs_btc": alpha,
                    "directional_correct": correct,
                    "reason": reason,
                }
            )

    baseline_metrics = {
        name: _baseline_metrics(rows) for name, rows in rows_by_name.items()
    }
    paired: dict[str, Any] = {}
    model_scores = None
    if predictions is not None:
        model_scores = score_predictions(dataset, predictions, outcomes, fold=fold)
        model_returns = {
            row["case_id"]: float(row["entry_return"])
            for row in model_scores["cases"]
            if row["entry_return"] is not None
            and (fold == "all" or row["fold"] == fold)
        }
        for name, rows in rows_by_name.items():
            baseline_returns = {
                row["case_id"]: float(row["strategy_return"])
                for row in rows
                if row["strategy_return"] is not None
            }
            paired[name] = paired_difference(model_returns, baseline_returns)
    return {
        "schema_version": "crypto-eval.baselines.v1",
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["dataset_version"],
        "dataset_hash": dataset["dataset_hash"],
        "outcomes_hash": digest(outcomes),
        "sampling_audit": dataset["sampling_audit"],
        "fold_scope": fold,
        "seed": seed,
        "baseline_parameters": {
            "ema_fast": 20,
            "ema_slow": 50,
            "rsi_period": 14,
            "rsi_long_below": 30,
            "rsi_short_above": 70,
            "random_seed": seed,
        },
        "interpretation": (
            "Fixed diagnostic comparators only; no baseline was tuned to favor a model. "
            "Returns are per-decision normalized outcomes, not portfolio PnL."
        ),
        "model_runner": model_scores["runner"] if model_scores else None,
        "metrics": baseline_metrics,
        "paired_comparisons": paired,
        "cases": rows_by_name,
    }


def compare_model_runs(
    dataset: dict[str, Any],
    skill_predictions: list[dict[str, Any]],
    control_predictions: list[dict[str, Any]],
    outcomes: dict[str, Any],
    *,
    fold: str = "test",
) -> dict[str, Any]:
    if not skill_predictions or not control_predictions:
        raise EvaluationError("skill/control comparison requires both prediction runs")
    if not isinstance(skill_predictions[0], dict) or not isinstance(
        control_predictions[0], dict
    ):
        raise EvaluationError("skill/control prediction records must be objects")
    skill_runner = skill_predictions[0].get("runner")
    control_runner = control_predictions[0].get("runner")
    if not isinstance(skill_runner, dict) or not isinstance(control_runner, dict):
        raise EvaluationError("skill/control runner metadata is required")
    if skill_runner.get("variant") != "skill" or control_runner.get("variant") != "control":
        raise EvaluationError("prediction files must be marked with skill/control variants")
    if (
        skill_runner.get("execution_status") != "invoked"
        or control_runner.get("execution_status") != "invoked"
    ):
        raise EvaluationError("skill/control comparison requires actually invoked model runs")
    identity_fields = ("provider", "model_id", "inference_config_hash")
    mismatches = [
        field for field in identity_fields if skill_runner.get(field) != control_runner.get(field)
    ]
    if mismatches:
        raise EvaluationError(
            "same-model comparison requires identical "
            + ", ".join(mismatches)
        )
    if any(not isinstance(record, dict) for record in skill_predictions + control_predictions):
        raise EvaluationError("skill/control prediction records must be objects")
    skill_ids = {record.get("case_id") for record in skill_predictions}
    control_ids = {record.get("case_id") for record in control_predictions}
    if any(not isinstance(case_id, str) for case_id in skill_ids | control_ids):
        raise EvaluationError("skill/control prediction case IDs must be strings")
    if skill_ids != control_ids:
        raise EvaluationError("skill/control prediction runs must cover the same case IDs")

    skill_scores = score_predictions(dataset, skill_predictions, outcomes, fold=fold)
    control_scores = score_predictions(dataset, control_predictions, outcomes, fold=fold)
    skill_cases = {row["case_id"]: row for row in skill_scores["cases"]}
    control_cases = {row["case_id"]: row for row in control_scores["cases"]}
    selected_ids = [
        case_id
        for case_id in skill_ids
        if fold == "all" or skill_cases[case_id]["fold"] == fold
    ]
    paired_returns = paired_difference(
        {
            case_id: float(skill_cases[case_id]["entry_return"])
            for case_id in selected_ids
            if skill_cases[case_id]["entry_return"] is not None
        },
        {
            case_id: float(control_cases[case_id]["entry_return"])
            for case_id in selected_ids
            if control_cases[case_id]["entry_return"] is not None
        },
    )
    paired_direction = paired_difference(
        {
            case_id: float(skill_cases[case_id]["directional_outcome"] == "correct")
            for case_id in selected_ids
            if skill_cases[case_id]["directional_outcome"] in {"correct", "incorrect"}
        },
        {
            case_id: float(control_cases[case_id]["directional_outcome"] == "correct")
            for case_id in selected_ids
            if control_cases[case_id]["directional_outcome"] in {"correct", "incorrect"}
        },
    )
    return {
        "schema_version": "crypto-eval.paired-runs.v1",
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["dataset_version"],
        "dataset_hash": dataset["dataset_hash"],
        "outcomes_hash": digest(outcomes),
        "sampling_audit": dataset["sampling_audit"],
        "fold_scope": fold,
        "comparison_design": {
            "same_model": True,
            "provider": skill_runner.get("provider"),
            "model_id": skill_runner.get("model_id"),
            "inference_config_hash": skill_runner.get("inference_config_hash"),
            "skill_prompt_version": skill_runner.get("prompt_version"),
            "control_prompt_version": control_runner.get("prompt_version"),
            "skill_commit": skill_runner.get("skill_commit"),
            "control_commit": control_runner.get("skill_commit"),
            "skill_invocation_status": skill_runner.get("execution_status"),
            "control_invocation_status": control_runner.get("execution_status"),
        },
        "paired_return_difference": paired_returns,
        "paired_directional_accuracy_difference": paired_direction,
        "limitations": [
            "Paired differences are descriptive; the harness does not claim statistical significance.",
            "Model, inference configuration, dataset, and case IDs must match; only the evaluated skill/control prompt variant should differ.",
            "A real experiment requires frozen predictions made before outcome windows close and sufficient out-of-sample cases.",
            "These decision-quality measures are not portfolio PnL.",
        ],
    }
