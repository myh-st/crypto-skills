"""Command-line interface for building and evaluating crypto decision datasets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .baselines import compare_baselines, compare_model_runs
from .contracts import EvaluationError
from .dataset import bind_outcomes, build_dataset, validate_walk_forward
from .io import read_json, write_json_new, write_text_new
from .lifecycle import project_lifecycle, summarize_lifecycle
from .providers import JsonArchiveProvider
from .reporting import build_report, render_markdown
from .runner import FixtureRunner, read_predictions, run_predictions, write_predictions
from .scoring import score_predictions


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crypto-eval",
        description="Point-in-time crypto evaluation and local PAPER research; no real-money order submission.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build-dataset", help="validate and build a versioned dataset")
    build.add_argument("--source", type=Path, required=True, help="normalized candidate JSON")
    build.add_argument("--spec", type=Path, required=True, help="versioned JSON dataset spec")
    build.add_argument("--out", type=Path, required=True, help="new dataset JSON output")

    runner = commands.add_parser("run-fixture", help="freeze deterministic fixture predictions")
    runner.add_argument("--dataset", type=Path, required=True)
    runner.add_argument("--out", type=Path, required=True, help="new append-only JSONL output")
    runner.add_argument(
        "--append",
        action="store_true",
        help="append new, non-duplicate cases to an existing immutable prediction log",
    )
    runner.add_argument(
        "--case-id",
        action="append",
        help="freeze only this case ID; repeat when appending new forward cases",
    )

    score = commands.add_parser("score", help="score frozen predictions against separate outcomes")
    score.add_argument("--dataset", type=Path, required=True)
    score.add_argument("--predictions", type=Path, required=True)
    score.add_argument("--outcomes", type=Path, required=True)
    score.add_argument("--fold", choices=("train", "validation", "test", "all"), default="test")
    score.add_argument("--out", type=Path, required=True)

    baselines = commands.add_parser(
        "compare-baselines", help="compare fixed deterministic baselines"
    )
    baselines.add_argument("--dataset", type=Path, required=True)
    baselines.add_argument("--outcomes", type=Path, required=True)
    baselines.add_argument("--predictions", type=Path)
    baselines.add_argument("--fold", choices=("train", "validation", "test", "all"), default="test")
    baselines.add_argument("--out", type=Path, required=True)

    paired = commands.add_parser(
        "compare-runs", help="compare paired skill/control runs using the same model setup"
    )
    paired.add_argument("--dataset", type=Path, required=True)
    paired.add_argument("--skill-predictions", type=Path, required=True)
    paired.add_argument("--control-predictions", type=Path, required=True)
    paired.add_argument("--outcomes", type=Path, required=True)
    paired.add_argument("--fold", choices=("train", "validation", "test", "all"), default="test")
    paired.add_argument("--out", type=Path, required=True)

    report = commands.add_parser("report", help="render honest JSON and Markdown reports")
    report.add_argument("--scores", type=Path, required=True)
    report.add_argument("--baselines", type=Path, required=True)
    report.add_argument("--out-json", type=Path, required=True)
    report.add_argument("--out-md", type=Path, required=True)

    status = commands.add_parser("status", help="show the paper-evaluation lifecycle")
    status.add_argument("--dataset", type=Path, required=True)
    status.add_argument("--predictions", type=Path)
    status.add_argument("--outcomes", type=Path)
    status.add_argument("--scores", type=Path)
    status.add_argument("--format", choices=("human", "json"), default="human")

    demo = commands.add_parser(
        "demo",
        help="run the complete deterministic synthetic dataset-to-report fixture flow",
    )
    demo.add_argument("--out-dir", type=Path, required=True)
    demo.add_argument("--fold", choices=("train", "validation", "test", "all"), default="test")

    paper = commands.add_parser(
        "paper-server",
        help="run the loopback-only PAPER futures research console",
    )
    paper.add_argument("--host", choices=("127.0.0.1", "::1"), default="127.0.0.1")
    paper.add_argument("--port", type=int, default=8765)
    paper.add_argument(
        "--database",
        type=Path,
        help="SQLite state path (default: the user's local application-data directory)",
    )
    return parser


def _load_dataset(path: Path) -> dict[str, Any]:
    dataset = read_json(path)
    validate_walk_forward(dataset)
    return dataset


def _load_outcomes(path: Path, dataset: dict[str, Any]) -> dict[str, Any]:
    raw = read_json(path)
    return bind_outcomes(raw, dataset)


def _load_optional(path: Path | None) -> Any:
    return read_json(path) if path else None


def _run_demo(args: argparse.Namespace) -> int:
    out_dir: Path = args.out_dir
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise EvaluationError(
            f"demo output directory already exists: {out_dir}; choose a new path"
        ) from exc
    except OSError as exc:
        raise EvaluationError(f"cannot create demo output directory {out_dir}: {exc}") from exc

    spec = read_json(FIXTURE_DIR / "fixture-spec.json")
    source = JsonArchiveProvider(FIXTURE_DIR / "candidates.json").load_candidates()
    dataset = build_dataset(source, spec)
    write_json_new(out_dir / "dataset.json", dataset)

    predictions = run_predictions(dataset, FixtureRunner())
    write_predictions(out_dir / "predictions.jsonl", predictions)

    outcomes = _load_outcomes(FIXTURE_DIR / "outcomes.json", dataset)
    scores = score_predictions(dataset, predictions, outcomes, fold=args.fold)
    scores["data_origin"] = dataset["data_origin"]
    write_json_new(out_dir / "scores.json", scores)

    baseline_report = compare_baselines(
        dataset,
        outcomes,
        predictions=predictions,
        fold=args.fold,
    )
    write_json_new(out_dir / "baselines.json", baseline_report)

    report = build_report(scores, baseline_report)
    write_json_new(out_dir / "report.json", report)
    write_text_new(out_dir / "report.md", render_markdown(report))
    print("DEMO / HARNESS VALIDATION")
    print("NOT MARKET PERFORMANCE EVIDENCE")
    print(f"Dataset: {dataset['dataset_id']} ({len(dataset['cases'])} cases)")
    print(f"Fold: {args.fold}")
    print(f"Outputs: {out_dir}")
    print("Model invocation: not performed; the fixture runner is deterministic.")
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "build-dataset":
        provider = JsonArchiveProvider(args.source)
        dataset = build_dataset(provider.load_candidates(), read_json(args.spec))
        write_json_new(args.out, dataset)
        print(
            f"Built {dataset['dataset_id']}: {len(dataset['cases'])} cases, "
            f"sha256 {dataset['dataset_hash']}"
        )
        return 0

    if args.command == "run-fixture":
        dataset = _load_dataset(args.dataset)
        if args.append and not args.case_id:
            raise EvaluationError("--append requires one or more --case-id values")
        predictions = run_predictions(dataset, FixtureRunner(), case_ids=args.case_id)
        write_predictions(args.out, predictions, append=args.append)
        print(
            f"Froze {len(predictions)} deterministic fixture prediction(s) in {args.out}; "
            "no model was invoked."
        )
        return 0

    if args.command == "score":
        dataset = _load_dataset(args.dataset)
        predictions = read_predictions(args.predictions)
        outcomes = _load_outcomes(args.outcomes, dataset)
        scores = score_predictions(dataset, predictions, outcomes, fold=args.fold)
        scores["data_origin"] = dataset["data_origin"]
        write_json_new(args.out, scores)
        print(
            f"Scored fold={args.fold}: "
            f"{scores['metrics']['complete_price_path_count']} complete forward path(s); "
            "missing data was not zero-filled."
        )
        return 0

    if args.command == "compare-baselines":
        dataset = _load_dataset(args.dataset)
        outcomes = _load_outcomes(args.outcomes, dataset)
        predictions = read_predictions(args.predictions) if args.predictions else None
        result = compare_baselines(
            dataset,
            outcomes,
            predictions=predictions,
            fold=args.fold,
        )
        write_json_new(args.out, result)
        print(f"Compared {len(result['metrics'])} fixed baselines on fold={args.fold}.")
        return 0

    if args.command == "compare-runs":
        dataset = _load_dataset(args.dataset)
        skill_predictions = read_predictions(args.skill_predictions)
        control_predictions = read_predictions(args.control_predictions)
        outcomes = _load_outcomes(args.outcomes, dataset)
        result = compare_model_runs(
            dataset,
            skill_predictions,
            control_predictions,
            outcomes,
            fold=args.fold,
        )
        write_json_new(args.out, result)
        print(
            f"Compared paired predictions on fold={args.fold}: "
            f"n={result['paired_return_difference']['n_paired']}."
        )
        return 0

    if args.command == "report":
        if args.out_json.resolve() == args.out_md.resolve():
            raise EvaluationError("report JSON and Markdown outputs must use different paths")
        report = build_report(read_json(args.scores), read_json(args.baselines))
        json_path: Path = args.out_json
        md_path: Path = args.out_md
        if json_path.exists() or md_path.exists():
            raise EvaluationError("refusing to overwrite existing report output(s)")
        write_json_new(json_path, report)
        write_text_new(md_path, render_markdown(report))
        print(report["evidence_disclaimer"])
        print(f"Reports: {json_path} and {md_path}")
        return 0

    if args.command == "status":
        dataset = _load_dataset(args.dataset)
        predictions = read_predictions(args.predictions) if args.predictions else []
        outcomes = _load_outcomes(args.outcomes, dataset) if args.outcomes else None
        scores = _load_optional(args.scores)
        projection = project_lifecycle(dataset, predictions, outcomes, scores)
        if args.format == "json":
            print(json.dumps(projection, sort_keys=True, indent=2))
        else:
            counts = summarize_lifecycle(projection)
            print("Paper-evaluation lifecycle")
            for status, count in counts.items():
                print(f"- {status}: {count}")
            for item in projection:
                print(f"- {item['case_id']} [{item['fold']}]: {item['status']}")
        return 0

    if args.command == "demo":
        return _run_demo(args)
    if args.command == "paper-server":
        from .paper_server import serve

        return serve(host=args.host, port=args.port, database=args.database)
    raise EvaluationError(f"unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except EvaluationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON: {exc}", file=sys.stderr)
        return 2
