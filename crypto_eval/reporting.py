"""Honest machine-readable and concise human-readable report generation."""

from __future__ import annotations

from typing import Any

from .contracts import EvaluationError


def _percentage(value: Any) -> str:
    if value is None:
        return "Unavailable"
    return f"{100 * float(value):.1f}%"


def _scalar(value: dict[str, Any]) -> str:
    count = value.get("n", 0)
    mean_value = value.get("mean")
    if mean_value is None:
        return f"Unavailable (n={count})"
    return f"{100 * float(mean_value):.2f}% (n={count})"


def build_report(scores: dict[str, Any], baselines: dict[str, Any]) -> dict[str, Any]:
    if scores.get("dataset_id") != baselines.get("dataset_id"):
        raise EvaluationError("scores and baselines refer to different datasets")
    if scores.get("dataset_version") != baselines.get("dataset_version"):
        raise EvaluationError("scores and baselines refer to different dataset versions")
    if scores.get("dataset_hash") != baselines.get("dataset_hash"):
        raise EvaluationError("scores and baselines have different dataset fingerprints")
    if scores.get("outcomes_hash") != baselines.get("outcomes_hash"):
        raise EvaluationError("scores and baselines use different outcome data")
    runner = scores.get("runner") or {}
    fixture_only = (
        runner.get("mode") == "fixture"
        or runner.get("execution_status") != "invoked"
    )
    synthetic = scores.get("data_origin") == "synthetic_fixture"
    is_demo = fixture_only or synthetic
    disclaimer = (
        "DEMO / HARNESS VALIDATION — NOT MARKET PERFORMANCE EVIDENCE"
        if is_demo
        else "Historical/forward decision-quality summary; not proof of skill improvement or portfolio performance."
    )
    return {
        "schema_version": "crypto-eval.report.v1",
        "classification": "DEMO / HARNESS VALIDATION" if is_demo else "EVALUATION SUMMARY",
        "evidence_disclaimer": disclaimer,
        "accuracy_claim": "none",
        "portfolio_pnl_claim": "none",
        "dataset": {
            "dataset_id": scores["dataset_id"],
            "dataset_version": scores["dataset_version"],
            "dataset_hash": scores["dataset_hash"],
            "data_origin": scores.get("data_origin", "unknown"),
            "fold_scope": scores.get("fold_scope"),
        },
        "outcomes_hash": scores.get("outcomes_hash"),
        "sampling_audit": scores.get("sampling_audit", {}),
        "runner": runner,
        "runtime_limitations": list(scores.get("runtime_limitations", []))
        + [
            "The fixture runner is not an invocation of the production analysis skill.",
            "Same-model skill/control comparisons require archived predictions from the same model and inference configuration.",
            "Unsupported funding, open-interest, news, benchmark, and other feature lanes remain unavailable; missing values are not zero-filled.",
        ],
        "metrics": scores.get("metrics", {}),
        "metrics_by_fold": scores.get("metrics_by_fold", {}),
        "breakdowns": scores.get("breakdowns", {}),
        "baseline_comparison": {
            "interpretation": baselines.get("interpretation"),
            "parameters": baselines.get("baseline_parameters"),
            "metrics": baselines.get("metrics", {}),
            "paired_comparisons": baselines.get("paired_comparisons", {}),
        },
        "sample_counts": {
            "prediction_case_count": len(scores.get("cases", [])),
            "scored_case_count": sum(
                row.get("score_status") in {"scored", "scored_partial"}
                for row in scores.get("cases", [])
            ),
            "complete_forward_return_count": scores.get("metrics", {}).get(
                "complete_price_path_count", 0
            ),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report.get("metrics", {})
    baseline_metrics = report.get("baseline_comparison", {}).get("metrics", {})
    runner = report.get("runner") or {}
    lines = [
        "# Crypto Evaluation Report",
        "",
        f"> **{report['evidence_disclaimer']}**",
        "",
        "This report validates the evaluation harness and/or summarizes decision-quality outcomes. "
        "It does not claim that the skill is accurate, profitable, or better than a control.",
        "",
        "## Evaluation scope",
        "",
        f"- Dataset: `{report['dataset']['dataset_id']}` "
        f"v{report['dataset']['dataset_version']} "
        f"(`{report['dataset']['dataset_hash'][:16]}`)",
        f"- Data origin: `{report['dataset']['data_origin']}`",
        f"- Fold: `{report['dataset']['fold_scope']}`",
        f"- Sampling: {report['sampling_audit'].get('included_case_count', 0)} included / "
        f"{report['sampling_audit'].get('scheduled_case_count', 0)} scheduled; "
        f"{len(report['sampling_audit'].get('excluded_cases', []))} excluded with recorded reasons",
        f"- Runner: `{runner.get('name', 'unavailable')}` / "
        f"`{runner.get('mode', 'unavailable')}`; model invocation: "
        f"`{runner.get('execution_status', 'not available')}`",
        "",
        "## Decision-quality metrics",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Directional accuracy | {_percentage(metrics.get('directional_accuracy', {}).get('rate'))} "
        f"(n={metrics.get('directional_accuracy', {}).get('n', 0)}) |",
        f"| Mean forward return | {_scalar(metrics.get('mean_forward_return', {}))} |",
        f"| Mean BTC benchmark return | {_scalar(metrics.get('mean_benchmark_return', {}))} |",
        f"| Mean alpha vs. BTC | {_scalar(metrics.get('mean_alpha', {}))} |",
        f"| Mean MFE | {_scalar(metrics.get('mean_mfe', {}))} |",
        f"| Mean MAE | {_scalar(metrics.get('mean_mae', {}))} |",
        f"| Entry trigger rate | {_percentage(metrics.get('entry_trigger_rate', {}).get('rate'))} "
        f"(n={metrics.get('entry_trigger_rate', {}).get('n', 0)}) |",
        f"| Invalidation hit rate | {_percentage(metrics.get('invalidation_hit_rate', {}).get('rate'))} "
        f"(n={metrics.get('invalidation_hit_rate', {}).get('n', 0)}) |",
        f"| Target hit rate | {_percentage(metrics.get('target_hit_rate', {}).get('rate'))} "
        f"(n={metrics.get('target_hit_rate', {}).get('n', 0)}) |",
        f"| Target before invalidation | "
        f"{_percentage(metrics.get('target_before_invalidation_rate', {}).get('rate'))} "
        f"(n={metrics.get('target_before_invalidation_rate', {}).get('n', 0)}) |",
        f"| Max drawdown proxy | {_percentage(metrics.get('max_drawdown_proxy', {}).get('value'))} "
        f"(n={metrics.get('max_drawdown_proxy', {}).get('n', 0)}) |",
        "",
        "Binary rates include Wilson 95% intervals in the JSON report. Return intervals use an "
        "approximate normal interval when n ≥ 2. Small samples are descriptive and are not a "
        "significance test. Qualitative confidence labels are reported as observed hit-rate "
        "breakdowns, not calibrated probabilities.",
        "",
        "## Fixed baselines",
        "",
        "| Baseline | Mean strategy return | Directional accuracy | Available returns |",
        "|---|---:|---:|---:|",
    ]
    for name, baseline in sorted(baseline_metrics.items()):
        lines.append(
            f"| `{name}` | {_scalar(baseline.get('mean_strategy_return', {}))} | "
            f"{_percentage(baseline.get('directional_accuracy', {}).get('rate'))} | "
            f"{baseline.get('available_return_count', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Runtime limitations",
            "",
        ]
    )
    lines.extend(f"- {limitation}" for limitation in report.get("runtime_limitations", []))
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            "- Validation is not accuracy evaluation.",
            "- A decision-quality review is not portfolio PnL; sizing, cash, fills, fees, slippage, "
            "and applicable funding are outside this harness.",
            "- Only point-in-time archived data and predictions frozen before outcome availability "
            "can support a historical predictive evaluation.",
            "- A skill/control claim requires a pre-registered, same-model, same-configuration, "
            "chronological out-of-sample paired experiment with enough cases and archived outputs.",
            "- Fixture output is deterministic synthetic harness validation only and must not be "
            "presented as real market performance.",
            "",
        ]
    )
    return "\n".join(lines)
