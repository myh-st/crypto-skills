"""Prospective paired paper-evaluation runtime and immutable local records."""

from __future__ import annotations

import json
import math
import re
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .baselines import compare_model_runs
from .contracts import (
    FIXED_BASELINE_PARAMETERS,
    EvaluationError,
    digest,
    iso_utc,
    parse_timestamp,
    validate_dataset,
    validate_outcomes,
    validate_prediction_record,
)
from .dataset import build_dataset, validate_walk_forward
from .io import read_json, write_json_new
from .lifecycle import project_lifecycle
from .market_data import (
    BINANCE_KLINES_URL,
    BINANCE_PROVIDER_ID,
    INTERVAL_SECONDS,
    BinanceSpotKlinesProvider,
    MarketDataError,
    build_market_archive_record,
    load_market_archive,
    write_market_archive,
)
from .openai_runner import (
    DEFAULT_MODEL_ID,
    DEFAULT_REASONING_EFFORT,
    MissingOpenAICredentialsError,
    OpenAIResponsesConfig,
    OpenAIResponsesError,
    OpenAIResponsesRunner,
    PostTransport,
    PROMPT_TEMPLATE_VERSION,
    api_key_from_environment,
    load_skill_instructions,
)
from .runner import read_predictions, run_predictions, write_predictions
from .scoring import score_predictions


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / ".crypto-eval"
DEFAULT_SKILL_PATH = ROOT / "skills" / "crypto-market-trading-analysis" / "SKILL.md"
MAX_HISTORY_BARS = 1000
MAX_HORIZON_BARS = 10_000
HORIZON_SECONDS = {
    "scalp": 4 * 60 * 60,
    "intraday": 24 * 60 * 60,
    "swing": 7 * 24 * 60 * 60,
    "position": 30 * 24 * 60 * 60,
    "long_term": 180 * 24 * 60 * 60,
}
RUN_ID_PATTERN = re.compile(r"^fwd-[0-9a-f]{32}$")


class OutcomeNotReadyError(EvaluationError):
    """Raised when a forward outcome horizon has not fully closed."""


class ForwardRunNotFoundError(EvaluationError):
    """Raised when the requested immutable forward run does not exist."""


def _now_utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EvaluationError("runtime clock must return a timezone-aware timestamp")
    return value.astimezone(timezone.utc)


def _git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        raise EvaluationError("cannot determine the evaluated skill commit") from None
    commit = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise EvaluationError("cannot determine the evaluated skill commit")
    return commit


def _horizon_bars(horizon: str, interval_seconds: int) -> int:
    if horizon not in HORIZON_SECONDS:
        raise EvaluationError("horizon is unsupported")
    bars = math.ceil(HORIZON_SECONDS[horizon] / interval_seconds)
    if bars < 1 or bars > MAX_HORIZON_BARS:
        raise EvaluationError(
            f"horizon is too long for the configured interval (maximum {MAX_HORIZON_BARS} bars)"
        )
    return bars


def _build_forward_dataset(
    *,
    run_id: str,
    symbol: str,
    interval: str,
    horizon: str,
    candles: list[dict[str, Any]],
) -> dict[str, Any]:
    interval_seconds = INTERVAL_SECONDS[interval]
    if not candles:
        raise MarketDataError("forward analysis requires a non-empty closed-candle snapshot")
    cutoff = candles[-1]["close_time"]
    as_of = parse_timestamp(cutoff, "snapshot.data_cutoff")
    asset = symbol[:-4] if symbol.endswith("USDT") else symbol
    horizon_bars = _horizon_bars(horizon, interval_seconds)
    dataset_id = f"forward-{run_id}"
    spec = {
        "schema_version": "crypto-eval.spec.v1",
        "spec_version": "1.0.0",
        "dataset_id": dataset_id,
        "dataset_version": "1.0.0",
        "universe": [asset],
        "sampling": {
            "start": iso_utc(as_of),
            "end": iso_utc(as_of),
            "cadence": "one prospective forward-paper case per run",
            "include_all_eligible": True,
            "no_cherry_picking": True,
            "exclusion_rule_ids": ["market_data_unavailable"],
        },
        "coverage": {
            "require_all_assets": True,
            "minimum_cases_per_asset": 1,
            "required_market_regimes": [],
            "minimum_cases_per_regime": 0,
        },
        "walk_forward": {
            "strategy": "prospective_forward",
            "train_fraction": 0.6,
            "validation_fraction": 0.2,
            "test_fraction": 0.2,
            "shuffle": False,
        },
        "baselines": {**FIXED_BASELINE_PARAMETERS, "random_seed": 1729},
    }
    candidate = {
        "source_case_key": run_id,
        "asset": asset,
        "instrument": "spot",
        "venue": "Binance",
        "horizon": horizon,
        "as_of": iso_utc(as_of),
        "data_cutoff": iso_utc(as_of),
        "bar_interval_seconds": interval_seconds,
        "horizon_bars": horizon_bars,
        "snapshot": {"candles": candles},
    }
    bundle = {
        "schema_version": "crypto-eval.candidates.v1",
        "source_id": BINANCE_PROVIDER_ID,
        "data_origin": "forward_paper",
        "sampling_manifest": {
            "scheduled_case_count": 1,
            "included_case_count": 1,
            "excluded_cases": [],
        },
        "candidates": [candidate],
    }
    dataset = build_dataset(bundle, spec)
    validate_walk_forward(dataset)
    return dataset


def _format_price(value: float | int | None) -> str | None:
    if value is None:
        return None
    return f"{float(value):.10g} USDT"


def _entry_range(entry: dict[str, Any] | None) -> list[float] | None:
    if entry is None:
        return None
    if entry["kind"] == "pullback":
        return [float(entry["zone_low"]), float(entry["zone_high"])]
    if entry["kind"] == "breakout":
        level = float(entry["level"])
        return [level, level]
    if entry["kind"] == "immediate":
        reference = float(entry["reference_price"])
        return [reference, reference]
    return None


def _entry_label(entry: dict[str, Any] | None) -> str | None:
    if entry is None or entry.get("kind") == "none":
        return None
    if entry["kind"] == "pullback":
        return f"{_format_price(entry['zone_low'])} – {_format_price(entry['zone_high'])}"
    if entry["kind"] == "breakout":
        direction = "above" if entry["direction"] == "long" else "below"
        return f"Close {direction} {_format_price(entry['level'])}"
    if entry["kind"] == "immediate":
        return _format_price(entry["reference_price"])
    return None


def _analysis_output(
    case: dict[str, Any],
    prediction: dict[str, Any],
    *,
    interval: str,
) -> dict[str, Any]:
    decision = prediction["decision"]
    entry = decision["entry"]
    candles = case["snapshot"]["candles"]
    targets = [_format_price(value) for value in decision["targets"]]
    return {
        "asset": case["asset"],
        "state": decision["decision_state"],
        "bias": decision["bias"],
        "confidence": decision["confidence"],
        "preferred_entry": _entry_label(entry),
        "secondary_entry": None,
        "confirmation": entry.get("confirmation") if entry else None,
        "invalidation": _format_price(decision["invalidation"]),
        "targets": targets,
        "horizon": case["horizon"],
        "reasons": [decision["rationale"]],
        "risk": (
            "Research-only forward paper analysis. It does not place orders or model "
            "fills, fees, slippage, leverage, or portfolio PnL."
        ),
        "data_quality": (
            f"Binance public Spot OHLCV; {len(candles)} consecutive closed {interval} "
            f"candles through {case['data_cutoff']}. Derivatives, news, and on-chain "
            "data were not supplied."
        ),
        "scenario_map": [],
        "monitoring_conditions": [
            "Keep the prediction frozen; outcome scoring is enabled only after the full horizon closes.",
            "Reassess only from newly closed Spot candles; no order execution is available.",
        ],
    }


def _ui_run(
    manifest: dict[str, Any],
    dataset: dict[str, Any],
    prediction: dict[str, Any],
    lifecycle: dict[str, Any],
    question: str = "",
) -> dict[str, Any]:
    case = next(case for case in dataset["cases"] if case["case_id"] == manifest["case_id"])
    decision = prediction["decision"]
    candles = case["snapshot"]["candles"]
    entry_zone = _entry_range(decision["entry"])
    report = _analysis_output(case, prediction, interval=manifest["interval"])
    price_values = [float(candle["close"]) for candle in candles]
    window_high = max(float(candle["high"]) for candle in candles)
    window_low = min(float(candle["low"]) for candle in candles)
    if price_values[-1] > price_values[0]:
        trend = "Higher over sampled window"
    elif price_values[-1] < price_values[0]:
        trend = "Lower over sampled window"
    else:
        trend = "Range-bound over sampled window"
    retrieved_at = manifest["retrieved_at"]
    freshness = max(
        0.0,
        (
            parse_timestamp(retrieved_at, "run.retrieved_at")
            - parse_timestamp(case["data_cutoff"], "case.data_cutoff")
        ).total_seconds(),
    )
    last = candles[-1]
    evidence = {
        "evidence_ledger": [
            {
                "claim": (
                    f"Latest closed Binance Spot candle closed at "
                    f"{_format_price(last['close'])}."
                ),
                "metric": "spot_close",
                "value": float(last["close"]),
                "unit": "USDT",
                "venue": "Binance",
                "instrument": "spot",
                "observed_at": last["close_time"],
                "retrieved_at": retrieved_at,
                "freshness_seconds": freshness,
                "source": {
                    "provider": "Binance Spot public klines",
                    "type": "primary",
                    "uri": BINANCE_KLINES_URL,
                },
                "evidence_type": "price_structure",
                "quality": "high",
                "supports": "neutral",
                "interpretation": {
                    "supports": "neutral",
                    "confidence": "low",
                    "notes": "A price observation is not itself a directional forecast.",
                },
                "notes": "Spot OHLCV only; no derivatives or external research was supplied.",
            }
        ]
    }
    return {
        "id": manifest["run_id"],
        "createdAt": prediction["frozen_at"],
        "asset": case["asset"],
        "symbol": manifest["symbol"],
        "analysisType": "spot",
        "instrument": "spot",
        "horizon": case["horizon"],
        "entryKind": decision["entry"]["kind"] if decision["entry"] else "none",
        "question": question,
        "riskStyle": manifest["risk_style"],
        "capital": None,
        "runtimeMode": "live",
        "dataOrigin": "forward_paper",
        "evaluationStatus": lifecycle["status"],
        "horizonClosesAt": manifest["horizon_closes_at"],
        "caseId": case["case_id"],
        "datasetHash": dataset["dataset_hash"],
        "archiveId": manifest["archive_id"],
        "archiveContentSha256": manifest["archive_content_sha256"],
        "predictionId": prediction["prediction_id"],
        "controlPredictionId": manifest["control_prediction_id"],
        "requestSettings": {
            "venue": "Binance",
            "instrument": "spot",
            "model": prediction["runner"]["model_id"],
            "interval": manifest["interval"],
            "barIntervalSeconds": case["bar_interval_seconds"],
            "dataAsOf": case["data_cutoff"],
            "dataProviders": ["Binance Spot public klines"],
            "referenceCurrency": "USDT",
            "inferenceConfigHash": prediction["runner"]["inference_config_hash"],
            "datasetHash": dataset["dataset_hash"],
            "archiveId": manifest["archive_id"],
        },
        "report": report,
        "marketStructure": {
            "trend": trend,
            "notes": (
                f"Computed from the {len(candles)} closed Spot candles in this snapshot; "
                "it does not include external or derivative data."
            ),
            "keyLevels": [
                {"label": "Window low", "price": window_low},
                {"label": "Window high", "price": window_high},
            ],
        },
        "leverage": {
            "state": "not_covered",
            "fundingRate": None,
            "openInterestTrend": "not_covered",
            "notes": "Binance Spot klines do not contain funding or open-interest observations.",
        },
        "priceLevels": {
            "current": float(last["close"]),
            "entryZone": entry_zone,
            "secondaryEntry": None,
            "invalidation": decision["invalidation"],
            "targets": decision["targets"],
        },
        "marketCandles": candles,
        "evidence": evidence,
    }


class ForwardRuntimeService:
    """Create and score immutable one-case prospective skill/control pairs."""

    def __init__(
        self,
        *,
        data_dir: Path = DEFAULT_DATA_DIR,
        interval: str = "1h",
        history_bars: int = 200,
        market_provider: BinanceSpotKlinesProvider | None = None,
        openai_config: OpenAIResponsesConfig | None = None,
        api_key_provider: Callable[[], str] = api_key_from_environment,
        openai_transport: PostTransport | None = None,
        skill_path: Path = DEFAULT_SKILL_PATH,
        skill_commit_provider: Callable[[], str] = _git_head,
        clock: Callable[[], datetime] | None = None,
        run_id_provider: Callable[[], str] | None = None,
    ) -> None:
        if interval not in INTERVAL_SECONDS:
            raise EvaluationError("unsupported Binance Spot interval")
        if (
            isinstance(history_bars, bool)
            or not isinstance(history_bars, int)
            or not 1 <= history_bars <= MAX_HISTORY_BARS
        ):
            raise EvaluationError(f"history_bars must be between 1 and {MAX_HISTORY_BARS}")
        self.data_dir = Path(data_dir)
        self.interval = interval
        self.history_bars = history_bars
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.market_provider = market_provider or BinanceSpotKlinesProvider(clock=self._clock)
        self._openai_config = openai_config
        self._api_key_provider = api_key_provider
        self._openai_transport = openai_transport
        self.skill_path = Path(skill_path)
        self._skill_commit_provider = skill_commit_provider
        self._run_id_provider = run_id_provider or (lambda: f"fwd-{uuid.uuid4().hex}")
        self._lock = threading.RLock()

    def _model_config(self) -> OpenAIResponsesConfig:
        return self._openai_config or OpenAIResponsesConfig.from_environment()

    def status(self) -> dict[str, Any]:
        try:
            config = self._model_config()
            configuration_error = None
        except OpenAIResponsesError as exc:
            config = None
            configuration_error = str(exc)
        try:
            api_key_configured = bool(self._api_key_provider())
        except MissingOpenAICredentialsError:
            api_key_configured = False
        except Exception:
            api_key_configured = False
        return {
            "schema_version": "crypto-live-runtime.status.v1",
            "mode": "live",
            "market_data_provider": BINANCE_PROVIDER_ID,
            "instrument": "spot",
            "interval": self.interval,
            "model_id": config.model_id if config else DEFAULT_MODEL_ID,
            "reasoning_effort": (
                config.reasoning_effort if config else DEFAULT_REASONING_EFFORT
            ),
            "model_configured": api_key_configured and config is not None,
            "configuration_error": configuration_error,
            "capabilities": ["analyze", "forward-evaluation", "score-matured-outcomes"],
        }

    def create(self, request: Any) -> dict[str, Any]:
        with self._lock:
            symbol, horizon, question, risk_style = self._validate_request(request)
            run_id = self._run_id_provider()
            if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
                raise EvaluationError("runtime generated an invalid run ID")

            config = self._model_config()
            api_key = self._api_key_provider()
            if not isinstance(api_key, str) or not api_key.strip():
                raise MissingOpenAICredentialsError(
                    "OPENAI_API_KEY is not configured in the server environment; no model call was made"
                )
            skill_text = load_skill_instructions(self.skill_path)
            skill_commit = self._skill_commit_provider()
            if not isinstance(skill_commit, str) or not skill_commit.strip():
                raise EvaluationError("evaluated skill commit is unavailable")

            skill_runner = OpenAIResponsesRunner(
                config,
                api_key=api_key,
                variant="skill",
                run_id=run_id,
                skill_text=skill_text,
                skill_commit=skill_commit,
                question=question,
                risk_style=risk_style,
                transport=self._openai_transport,
                clock=self._clock,
            )
            control_runner = OpenAIResponsesRunner(
                config,
                api_key=api_key,
                variant="control",
                run_id=run_id,
                skill_text=skill_text,
                skill_commit=None,
                question=question,
                risk_style=risk_style,
                transport=self._openai_transport,
                clock=self._clock,
            )

            candles = self.market_provider.fetch_history(
                symbol,
                self.interval,
                bars=self.history_bars,
            )
            now = _now_utc(self._clock)
            cutoff = candles[-1]["close_time"]
            interval_seconds = INTERVAL_SECONDS[self.interval]
            due_at = parse_timestamp(cutoff, "snapshot.data_cutoff") + timedelta(
                seconds=_horizon_bars(horizon, interval_seconds) * interval_seconds
            )
            if due_at <= now:
                raise EvaluationError(
                    "requested horizon has already closed; forward analysis requires a pending outcome window"
                )

            archive = build_market_archive_record(
                BINANCE_PROVIDER_ID,
                symbol,
                self.interval,
                candles[0]["open_time"],
                cutoff,
                candles,
                now,
            )
            archive_path = write_market_archive(archive, self.data_dir / "archives")
            dataset = _build_forward_dataset(
                run_id=run_id,
                symbol=symbol,
                interval=self.interval,
                horizon=horizon,
                candles=candles,
            )
            case = dataset["cases"][0]
            skill_predictions = run_predictions(
                dataset,
                skill_runner,
                case_ids=[case["case_id"]],
            )
            control_predictions = run_predictions(
                dataset,
                control_runner,
                case_ids=[case["case_id"]],
            )
            skill_prediction = skill_predictions[0]
            control_prediction = control_predictions[0]
            self._validate_pair(skill_prediction, control_prediction)
            if any(
                parse_timestamp(prediction["frozen_at"], "prediction.frozen_at") >= due_at
                for prediction in (skill_prediction, control_prediction)
            ):
                raise EvaluationError(
                    "paired predictions completed after the outcome horizon closed"
                )

            manifest = {
                "schema_version": "crypto-eval.forward-run.v1",
                "run_id": run_id,
                "created_at": skill_prediction["frozen_at"],
                "symbol": symbol,
                "asset": case["asset"],
                "instrument": "spot",
                "horizon": horizon,
                "risk_style": risk_style,
                "question_sha256": digest(question),
                "interval": self.interval,
                "horizon_bars": case["horizon_bars"],
                "data_cutoff": case["data_cutoff"],
                "horizon_closes_at": iso_utc(due_at),
                "retrieved_at": archive["retrieved_at"],
                "provider_id": archive["provider_id"],
                "archive_id": archive["archive_id"],
                "archive_content_sha256": archive["content_sha256"],
                "archive_path": str(archive_path.relative_to(self.data_dir)),
                "dataset_id": dataset["dataset_id"],
                "dataset_version": dataset["dataset_version"],
                "dataset_hash": dataset["dataset_hash"],
                "case_id": case["case_id"],
                "model_id": skill_prediction["runner"]["model_id"],
                "inference_config_hash": skill_prediction["runner"][
                    "inference_config_hash"
                ],
                "prompt_version": skill_prediction["runner"]["prompt_version"],
                "control_prompt_version": control_prediction["runner"]["prompt_version"],
                "skill_commit": skill_prediction["runner"]["skill_commit"],
                "skill_prediction_id": skill_prediction["prediction_id"],
                "control_prediction_id": control_prediction["prediction_id"],
            }

            run_dir = self.data_dir / "forward" / run_id
            run_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                run_dir.mkdir(mode=0o700, exist_ok=False)
                write_json_new(run_dir / "dataset.json", dataset)
                write_predictions(run_dir / "skill-predictions.jsonl", skill_predictions)
                write_predictions(run_dir / "control-predictions.jsonl", control_predictions)
                write_json_new(run_dir / "run.json", manifest)
            except Exception:
                if run_dir.exists():
                    import shutil

                    shutil.rmtree(run_dir, ignore_errors=True)
                raise
            return self.get(run_id, question=question)

    def score(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            self.get(run_id)
            manifest, run_dir = self._read_manifest(run_id)
            due_at = parse_timestamp(manifest["horizon_closes_at"], "run.horizon_closes_at")
            if _now_utc(self._clock) < due_at:
                raise OutcomeNotReadyError(
                    f"outcome horizon is still open until {manifest['horizon_closes_at']}"
                )

            if not (run_dir / "outcomes.json").exists():
                dataset = validate_dataset(read_json(run_dir / "dataset.json"))
                case = next(
                    item for item in dataset["cases"] if item["case_id"] == manifest["case_id"]
                )
                candles = self.market_provider.fetch_range(
                    manifest["symbol"],
                    manifest["interval"],
                    case["data_cutoff"],
                    manifest["horizon_closes_at"],
                )
                if len(candles) != manifest["horizon_bars"]:
                    raise MarketDataError(
                        "forward outcome does not contain the complete configured horizon"
                    )
                outcome_archive = build_market_archive_record(
                    manifest["provider_id"],
                    manifest["symbol"],
                    manifest["interval"],
                    case["data_cutoff"],
                    manifest["horizon_closes_at"],
                    candles,
                    _now_utc(self._clock),
                )
                write_market_archive(
                    outcome_archive,
                    self.data_dir / "archives",
                )
                outcomes = {
                    "schema_version": "crypto-eval.outcomes.v1",
                    "dataset_id": dataset["dataset_id"],
                    "dataset_version": dataset["dataset_version"],
                    "dataset_hash": dataset["dataset_hash"],
                    "records": [
                        {
                            "case_id": case["case_id"],
                            "source_case_key": case["source_case_key"],
                            "status": "complete",
                            "known_at": manifest["horizon_closes_at"],
                            "candles": candles,
                        }
                    ],
                }
                validate_outcomes(outcomes, dataset)
                write_json_new(run_dir / "outcomes.json", outcomes)

            dataset = validate_dataset(read_json(run_dir / "dataset.json"))
            outcomes = read_json(run_dir / "outcomes.json")
            validate_outcomes(outcomes, dataset)
            skill_predictions = read_predictions(run_dir / "skill-predictions.jsonl")
            control_predictions = read_predictions(run_dir / "control-predictions.jsonl")
            existing_scores_path = run_dir / "scores.json"
            if existing_scores_path.exists():
                scores = read_json(existing_scores_path)
            else:
                skill_scores = score_predictions(
                    dataset,
                    skill_predictions,
                    outcomes,
                    fold="all",
                )
                control_scores = score_predictions(
                    dataset,
                    control_predictions,
                    outcomes,
                    fold="all",
                )
                paired = compare_model_runs(
                    dataset,
                    skill_predictions,
                    control_predictions,
                    outcomes,
                    fold="all",
                )
                scores = {
                    "schema_version": "crypto-eval.forward-scores.v1",
                    "dataset_id": dataset["dataset_id"],
                    "dataset_version": dataset["dataset_version"],
                    "dataset_hash": dataset["dataset_hash"],
                    "outcomes_hash": digest(outcomes),
                    "skill_scores": skill_scores,
                    "control_scores": control_scores,
                    "paired_comparison": paired,
                }
                write_json_new(existing_scores_path, scores)
            return self.get(run_id)

    def get(self, run_id: str, *, question: str = "") -> dict[str, Any]:
        manifest, run_dir = self._read_manifest(run_id)
        dataset = validate_dataset(read_json(run_dir / "dataset.json"))
        validate_walk_forward(dataset)
        if (
            manifest.get("dataset_id") != dataset["dataset_id"]
            or manifest.get("dataset_version") != dataset["dataset_version"]
            or manifest.get("dataset_hash") != dataset["dataset_hash"]
        ):
            raise EvaluationError("forward run manifest does not match its frozen dataset")
        skill_predictions = read_predictions(run_dir / "skill-predictions.jsonl")
        control_predictions = read_predictions(run_dir / "control-predictions.jsonl")
        skill_prediction = skill_predictions[0]
        control_prediction = control_predictions[0]
        if len(skill_predictions) != 1 or len(control_predictions) != 1:
            raise EvaluationError("forward run must contain exactly one skill/control pair")
        if manifest.get("case_id") != dataset["cases"][0]["case_id"]:
            raise EvaluationError("forward run case does not match its frozen dataset")
        case = dataset["cases"][0]
        expected_due_at = parse_timestamp(
            case["data_cutoff"], "case.data_cutoff"
        ) + timedelta(
            seconds=case["horizon_bars"] * case["bar_interval_seconds"]
        )
        manifest_interval = manifest.get("interval")
        if (
            manifest.get("horizon_bars") != case["horizon_bars"]
            or manifest.get("horizon_closes_at") != iso_utc(expected_due_at)
            or manifest.get("symbol") != f"{case['asset']}USDT"
            or not isinstance(manifest_interval, str)
            or manifest_interval not in INTERVAL_SECONDS
            or INTERVAL_SECONDS[manifest_interval] != case["bar_interval_seconds"]
            or manifest.get("horizon") != case["horizon"]
            or manifest.get("instrument") != "spot"
        ):
            raise EvaluationError("forward run manifest does not match its case horizon")
        self._validate_pair(skill_prediction, control_prediction)
        if (
            skill_prediction["prediction_id"] != manifest.get("skill_prediction_id")
            or control_prediction["prediction_id"] != manifest.get("control_prediction_id")
            or skill_prediction["case_id"] != manifest.get("case_id")
            or skill_prediction["dataset_hash"] != manifest.get("dataset_hash")
        ):
            raise EvaluationError("forward run manifest does not match its frozen predictions")
        runner = skill_prediction["runner"]
        if any(
            manifest.get(manifest_field) != runner.get(runner_field)
            for manifest_field, runner_field in (
                ("model_id", "model_id"),
                ("inference_config_hash", "inference_config_hash"),
                ("prompt_version", "prompt_version"),
                ("skill_commit", "skill_commit"),
            )
        ):
            raise EvaluationError("forward run manifest does not match its runner configuration")
        if manifest.get("control_prompt_version") != control_prediction["runner"][
            "prompt_version"
        ]:
            raise EvaluationError("forward run manifest does not match its control prompt")
        archive_relative_path = Path(str(manifest.get("archive_path", "")))
        if archive_relative_path.is_absolute() or ".." in archive_relative_path.parts:
            raise EvaluationError("forward run archive path is invalid")
        archive_path = (self.data_dir / archive_relative_path).resolve()
        archive_root = (self.data_dir / "archives").resolve()
        if archive_path.parent != archive_root:
            raise EvaluationError("forward run archive path is outside the archive directory")
        archive = load_market_archive(archive_path)
        if (
            archive["archive_id"] != manifest.get("archive_id")
            or archive["content_sha256"] != manifest.get("archive_content_sha256")
            or archive["provider_id"] != manifest.get("provider_id")
            or archive["symbol"] != manifest.get("symbol")
            or archive["interval"] != manifest.get("interval")
            or archive["data_cutoff"] != manifest.get("data_cutoff")
            or archive["candles"] != case["snapshot"]["candles"]
        ):
            raise EvaluationError("forward run market archive does not match its frozen case")
        validate_prediction_record(
            skill_prediction,
            case,
            dataset["dataset_id"],
            dataset["dataset_version"],
            dataset["dataset_hash"],
        )
        validate_prediction_record(
            control_prediction,
            case,
            dataset["dataset_id"],
            dataset["dataset_version"],
            dataset["dataset_hash"],
        )
        outcomes_path = run_dir / "outcomes.json"
        outcomes = read_json(outcomes_path) if outcomes_path.exists() else None
        if outcomes is not None:
            validate_outcomes(outcomes, dataset)
        scores_path = run_dir / "scores.json"
        scores = read_json(scores_path) if scores_path.exists() else None
        if scores is not None and (
            outcomes is None
            or scores.get("dataset_hash") != dataset["dataset_hash"]
            or scores.get("outcomes_hash") != digest(outcomes)
        ):
            raise EvaluationError("forward scores do not match their immutable outcomes")
        skill_scores = scores.get("skill_scores") if scores else None
        lifecycle_records = project_lifecycle(
            dataset,
            skill_predictions,
            outcomes=outcomes,
            scores=skill_scores,
        )
        lifecycle = next(
            item for item in lifecycle_records if item["case_id"] == manifest["case_id"]
        )
        if (
            lifecycle["status"] == "waiting_for_outcome"
            and _now_utc(self._clock)
            >= parse_timestamp(manifest["horizon_closes_at"], "run.horizon_closes_at")
        ):
            lifecycle = {
                **lifecycle,
                "status": "ready_to_score",
                "events": [*lifecycle["events"], "horizon_closed"],
            }
        ui_run = _ui_run(
            manifest,
            dataset,
            skill_prediction,
            lifecycle,
            question=question,
        )
        response = {
            "schema_version": "crypto-eval.forward-response.v1",
            "run": ui_run,
            "analysis": ui_run["report"],
            "prediction": skill_prediction,
            "pair": {
                "same_case": True,
                "control_prediction_id": control_prediction["prediction_id"],
                "control_inference_config_hash": control_prediction["runner"][
                    "inference_config_hash"
                ],
                "control_prompt_version": control_prediction["runner"]["prompt_version"],
            },
            "lifecycle": lifecycle,
        }
        if scores:
            response["evaluation"] = {
                "outcomes_hash": scores["outcomes_hash"],
                "skill_metrics": scores["skill_scores"]["metrics"],
                "control_metrics": scores["control_scores"]["metrics"],
                "paired_comparison": scores["paired_comparison"],
            }
        return response

    def list_runs(self) -> list[dict[str, Any]]:
        forward_dir = self.data_dir / "forward"
        if not forward_dir.is_dir():
            return []
        summaries = []
        for path in forward_dir.glob("*/run.json"):
            try:
                manifest, _ = self._read_manifest(path.parent.name)
                result = self.get(manifest["run_id"])
            except (EvaluationError, OSError, json.JSONDecodeError):
                continue
            run = result["run"]
            summaries.append(
                {
                    "run_id": run["id"],
                    "asset": run["asset"],
                    "symbol": run["symbol"],
                    "instrument": run["instrument"],
                    "horizon": run["horizon"],
                    "created_at": run["createdAt"],
                    "data_cutoff": run["requestSettings"]["dataAsOf"],
                    "horizon_closes_at": run["horizonClosesAt"],
                    "dataset_hash": run["datasetHash"],
                    "prediction_id": run["predictionId"],
                    "status": run["evaluationStatus"],
                }
            )
        return sorted(summaries, key=lambda item: item["created_at"], reverse=True)

    def _validate_request(self, request: Any) -> tuple[str, str, str, str]:
        allowed = {"symbol", "instrument", "horizon", "question", "risk_style"}
        if not isinstance(request, dict):
            raise EvaluationError("analysis request must be a JSON object")
        unexpected = set(request) - allowed
        if unexpected:
            raise EvaluationError(
                "analysis request has unsupported fields: " + ", ".join(sorted(unexpected))
            )
        if request.get("instrument") != "spot":
            raise EvaluationError("only read-only Binance Spot analysis is supported")
        symbol = request.get("symbol")
        if (
            not isinstance(symbol, str)
            or not re.fullmatch(r"[A-Za-z0-9]{5,20}", symbol.strip())
            or not symbol.strip().upper().endswith("USDT")
        ):
            raise EvaluationError("symbol must be a Binance USDT Spot pair such as BTCUSDT")
        symbol = symbol.strip().upper()
        horizon = request.get("horizon")
        if not isinstance(horizon, str) or horizon not in HORIZON_SECONDS:
            raise EvaluationError("horizon is unsupported")
        question = request.get("question", "")
        if not isinstance(question, str) or len(question) > 1200:
            raise EvaluationError("question must be a string of at most 1200 characters")
        risk_style = request.get("risk_style", "neutral")
        if not isinstance(risk_style, str) or risk_style not in {
            "aggressive",
            "neutral",
            "conservative",
        }:
            raise EvaluationError("risk_style must be aggressive, neutral, or conservative")
        return symbol, horizon, question, risk_style

    @staticmethod
    def _validate_pair(
        skill_prediction: dict[str, Any],
        control_prediction: dict[str, Any],
    ) -> None:
        skill_runner = skill_prediction["runner"]
        control_runner = control_prediction["runner"]
        if skill_prediction["case_id"] != control_prediction["case_id"]:
            raise EvaluationError("skill and control predictions must use the same case")
        if skill_prediction["dataset_hash"] != control_prediction["dataset_hash"]:
            raise EvaluationError("skill and control predictions must use the same dataset snapshot")
        for field in ("provider", "model_id", "inference_config_hash", "run_id"):
            if skill_runner.get(field) != control_runner.get(field):
                raise EvaluationError(
                    f"skill and control runners have different {field} configuration"
                )
        if skill_runner.get("variant") != "skill" or control_runner.get("variant") != "control":
            raise EvaluationError("paired predictions must be marked skill and control")
        if (
            not isinstance(skill_runner.get("prompt_version"), str)
            or not skill_runner["prompt_version"].startswith(
                f"{PROMPT_TEMPLATE_VERSION}:skill-sha256-"
            )
            or control_runner.get("prompt_version")
            != f"{PROMPT_TEMPLATE_VERSION}:control-no-skill"
        ):
            raise EvaluationError("paired predictions must identify their skill/control prompts")

    def _read_manifest(self, run_id: str) -> tuple[dict[str, Any], Path]:
        if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
            raise ForwardRunNotFoundError("forward run was not found")
        run_dir = self.data_dir / "forward" / run_id
        try:
            manifest = read_json(run_dir / "run.json")
        except EvaluationError:
            raise ForwardRunNotFoundError("forward run was not found") from None
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != "crypto-eval.forward-run.v1"
            or manifest.get("run_id") != run_id
        ):
            raise EvaluationError("forward run manifest is invalid")
        return manifest, run_dir
