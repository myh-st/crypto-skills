"""Command-line interface for building and evaluating crypto decision datasets."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .baselines import compare_baselines, compare_model_runs
from .contracts import EvaluationError, parse_timestamp
from .dataset import bind_outcomes, build_dataset, validate_walk_forward
from .forward import DEFAULT_DATA_DIR, HORIZON_SECONDS, ForwardRuntimeService
from .io import read_json, write_json_new, write_text_new
from .lifecycle import project_lifecycle, summarize_lifecycle
from .market_data import (
    INTERVAL_SECONDS,
    BinanceSpotKlinesProvider,
    build_market_archive_record,
    write_market_archive,
)
from .providers import JsonArchiveProvider
from .reporting import build_report, render_markdown
from .runner import FixtureRunner, read_predictions, run_predictions, write_predictions
from .scoring import score_predictions
from .server import serve_runtime


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crypto-eval",
        description="Point-in-time crypto evaluation, forward research, and local PAPER futures; no real-money order submission.",
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

    archive = commands.add_parser(
        "archive-binance",
        help="archive a read-only Binance Spot OHLCV range with source and content metadata",
    )
    archive.add_argument("--symbol", required=True, help="Binance Spot symbol, e.g. BTCUSDT")
    archive.add_argument(
        "--interval",
        choices=sorted(INTERVAL_SECONDS),
        required=True,
        help="fixed Binance candle interval",
    )
    archive.add_argument("--start", required=True, help="timezone-aware interval-aligned ISO-8601")
    archive.add_argument("--end", required=True, help="timezone-aware as-of ISO-8601")
    archive.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_DATA_DIR / "archives",
        help="local archive directory (default: .crypto-eval/archives)",
    )

    forward_create = commands.add_parser(
        "forward-create",
        help="freeze a paired skill/control prediction over one live Spot snapshot",
    )
    forward_create.add_argument("--symbol", required=True, help="Binance Spot pair, e.g. BTCUSDT")
    forward_create.add_argument("--instrument", choices=("spot",), default="spot")
    forward_create.add_argument("--horizon", choices=sorted(HORIZON_SECONDS), required=True)
    forward_create.add_argument(
        "--interval",
        choices=sorted(INTERVAL_SECONDS),
        default="1h",
    )
    forward_create.add_argument("--history-bars", type=int, default=200)
    forward_create.add_argument("--question", default="")
    forward_create.add_argument(
        "--risk-style",
        choices=("aggressive", "neutral", "conservative"),
        default="neutral",
    )
    forward_create.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)

    forward_score = commands.add_parser(
        "forward-score",
        help="fetch and score a separate outcome after its configured horizon closes",
    )
    forward_score.add_argument("--run-id", required=True)
    forward_score.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)

    forward_status = commands.add_parser(
        "forward-status",
        help="list local prospective paper-evaluation cases and lifecycle states",
    )
    forward_status.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)

    serve = commands.add_parser(
        "serve",
        help="serve the frontend and local same-origin analysis API on loopback",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--interval", choices=sorted(INTERVAL_SECONDS), default="1h")
    serve.add_argument("--history-bars", type=int, default=200)
    serve.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)

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
    paper.add_argument(
        "--no-live-stream",
        action="store_true",
        help="do not connect the backend-owned Gate public futures WebSocket",
    )

    real = commands.add_parser(
        "real-integration-check",
        help="REAL local acceptance: Gate REST/WS, TypeSafe Jev, Azure GPT-6 Luna; never fixtures",
    )
    real.add_argument("--symbol", default="BTCUSDT")
    real.add_argument("--database", type=Path, help="isolated SQLite path for the check")
    real.add_argument(
        "--no-fallback-prices",
        action="store_true",
        help="use only the configured price book (paid calls fail closed if prices are unknown)",
    )
    real.add_argument("--ws-timeout", type=float, default=45.0)
    real.add_argument("--out", type=Path, help="summary JSON path (default: reports/<check-id>.json)")

    portfolio_real = commands.add_parser(
        "portfolio-real-check",
        help="REAL local acceptance of Portfolio OS AI paths: live Gate data, real Jev/Luna re-plans, budget block",
    )
    portfolio_real.add_argument("--database", type=Path, help="isolated SQLite path for the check")
    portfolio_real.add_argument("--out", type=Path, help="summary JSON path")
    portfolio_real.add_argument(
        "--full-loop", action="store_true",
        help="scan a live liquid perp universe with the real decision stack and drive the full human/AI loop",
    )
    portfolio_real.add_argument("--max-candles", type=int, default=1, help="closed 15m candles to scan (full loop)")

    setup = commands.add_parser(
        "paper-setup-real",
        help="store .env credentials in the OS credential store and configure real Jev/Foundry providers",
    )
    setup.add_argument("--database", type=Path)
    setup.add_argument("--prices-file", type=Path, help="JSON price-book entries (contract pricing)")
    setup.add_argument("--overwrite-secrets", action="store_true")
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

    if args.command == "archive-binance":
        provider = BinanceSpotKlinesProvider()
        start = parse_timestamp(args.start, "archive.start")
        end = parse_timestamp(args.end, "archive.end")
        candles = provider.fetch_range(
            args.symbol,
            args.interval,
            start,
            end,
        )
        record = build_market_archive_record(
            provider.provider_id,
            args.symbol,
            args.interval,
            start,
            end,
            candles,
            datetime.now(timezone.utc),
        )
        path = write_market_archive(record, args.out_dir)
        print(
            f"Archived {record['candle_count']} closed {args.interval} candle(s) for "
            f"{record['symbol']} through {record['data_cutoff']}."
        )
        print(f"Archive: {path}")
        print(f"Content SHA-256: {record['content_sha256']}")
        return 0

    if args.command == "forward-create":
        runtime = ForwardRuntimeService(
            data_dir=args.data_dir,
            interval=args.interval,
            history_bars=args.history_bars,
        )
        response = runtime.create(
            {
                "symbol": args.symbol,
                "instrument": args.instrument,
                "horizon": args.horizon,
                "question": args.question,
                "risk_style": args.risk_style,
            }
        )
        print(f"Frozen forward run: {response['run']['id']}")
        print(f"Prediction: {response['prediction']['prediction_id']}")
        print(f"Dataset SHA-256: {response['run']['datasetHash']}")
        print(f"Lifecycle: {response['run']['evaluationStatus']}")
        print("Skill/control calls used the same point-in-time Spot snapshot.")
        return 0

    if args.command == "forward-score":
        runtime = ForwardRuntimeService(data_dir=args.data_dir)
        response = runtime.score(args.run_id)
        print(f"Forward run: {response['run']['id']}")
        print(f"Lifecycle: {response['run']['evaluationStatus']}")
        if "evaluation" in response:
            paired = response["evaluation"]["paired_comparison"]
            print(f"Paired cases: {paired['paired_return_difference']['n_paired']}")
            print("Paired differences are descriptive, not a performance claim.")
        return 0

    if args.command == "forward-status":
        runtime = ForwardRuntimeService(data_dir=args.data_dir)
        print(json.dumps({"runs": runtime.list_runs()}, sort_keys=True, indent=2))
        return 0

    if args.command == "serve":
        runtime = ForwardRuntimeService(
            data_dir=args.data_dir,
            interval=args.interval,
            history_bars=args.history_bars,
        )
        serve_runtime(runtime, host=args.host, port=args.port)
        return 0

    if args.command == "demo":
        return _run_demo(args)
    if args.command == "paper-server":
        from .paper_server import serve

        return serve(
            host=args.host,
            port=args.port,
            database=args.database,
            live_stream=not args.no_live_stream,
        )
    if args.command == "real-integration-check":
        from .paper_server import default_database_path
        from .real_integration import run_real_integration_check

        return run_real_integration_check(
            symbol=args.symbol.strip().upper(),
            database=args.database,
            app_database=default_database_path(),
            use_fallback_prices=not args.no_fallback_prices,
            ws_timeout=args.ws_timeout,
            out=args.out,
        )
    if args.command == "portfolio-real-check":
        from .portfolio_acceptance import run_full_loop_check, run_portfolio_real_check

        if args.full_loop:
            return run_full_loop_check(database=args.database, out=args.out, max_candles=max(1, min(args.max_candles, 96)))
        return run_portfolio_real_check(database=args.database, out=args.out)
    if args.command == "paper-setup-real":
        from .real_integration import setup_real

        return setup_real(
            prices_file=args.prices_file,
            database=args.database,
            overwrite_secrets=args.overwrite_secrets,
        )
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
