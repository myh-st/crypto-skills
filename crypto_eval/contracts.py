"""Input contracts and point-in-time validation for the evaluation harness."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DECISION_STATES = frozenset(
    json.loads((ROOT / "schemas" / "decision-state.schema.json").read_text(encoding="utf-8"))[
        "enum"
    ]
)
HORIZONS = frozenset({"scalp", "intraday", "swing", "position", "long_term"})
INSTRUMENTS = frozenset({"spot", "perpetual", "futures", "options"})
AVAILABILITY_STATES = frozenset(
    {"available", "partial", "stale", "unavailable", "not_covered"}
)
FIXED_BASELINE_PARAMETERS = {
    "ema_fast": 20,
    "ema_slow": 50,
    "rsi_period": 14,
    "rsi_long_below": 30,
    "rsi_short_above": 70,
}
CONFIDENCE_LEVELS = frozenset({"low", "moderate", "high"})
BIAS_DIRECTIONS = frozenset({"bullish", "bearish", "neutral"})
OUTCOME_STATUSES = frozenset({"complete", "partial", "unavailable"})
WAIT_STATES_FOR_CONTRACTS = frozenset(
    {"WAIT_FOR_PULLBACK", "WAIT_FOR_BREAKOUT_CONFIRMATION"}
)

_LABEL_FIELDS = frozenset(
    {
        "alpha",
        "benchmark_return",
        "decision_return",
        "directional_outcome",
        "drawdown",
        "first_event",
        "forward_return",
        "invalidation_hit",
        "invalidation_hit_first",
        "label",
        "labels",
        "mae",
        "mfe",
        "max_adverse_excursion",
        "max_favorable_excursion",
        "outcome",
        "outcome_known_at",
        "outcomes",
        "post_trigger_mae",
        "post_trigger_mfe",
        "post_trigger_return",
        "raw_return",
        "reflection",
        "score",
        "scored_at",
        "strategy_return",
        "target_hit",
        "target_hit_first",
        "thesis_result",
        "model_output",
        "prediction",
        "predictions",
    }
)
_TIMESTAMP_KEYS = frozenset(
    {
        "analysis_time",
        "available_at",
        "close_time",
        "data_cutoff",
        "effective_at",
        "known_at",
        "open_time",
        "observed_at",
        "published_at",
        "release_at",
        "timestamp",
        "time",
    }
)
_NEWS_FIELDS = frozenset({"news", "headlines", "articles"})
_OBSERVATION_FIELDS = frozenset(
    {
        "funding",
        "open_interest",
        "oi",
        "derivatives",
        "options",
        "on_chain",
        "macro",
        "benchmarks",
    }
)
_CANDLE_FIELDS = frozenset(
    {"open_time", "close_time", "open", "high", "low", "close", "volume"}
)
_SNAPSHOT_FIELDS = (
    frozenset({"candles", "benchmark_candles", "data_availability"})
    | _NEWS_FIELDS
    | _OBSERVATION_FIELDS
)
_DATA_AVAILABILITY_FIELDS = frozenset(
    {
        "candles",
        "funding",
        "open_interest",
        "news",
        "options",
        "on_chain",
        "macro",
        "benchmark",
    }
)
_OBSERVATION_NUMBER_FIELDS = frozenset(
    {
        "value",
        "rate",
        "funding_rate",
        "open_interest",
        "price",
        "volume",
        "notional",
        "quantity",
        "ratio",
        "long_short_ratio",
        "implied_volatility",
        "delta",
        "gamma",
        "theta",
        "vega",
        "bid",
        "ask",
    }
)
_OBSERVATION_STRING_FIELDS = frozenset(
    {"asset", "symbol", "instrument", "venue", "unit", "period", "source"}
)
_OBSERVATION_RECORD_FIELDS = (
    frozenset({"observed_at"})
    | _OBSERVATION_NUMBER_FIELDS
    | _OBSERVATION_STRING_FIELDS
)
_NEWS_STRING_FIELDS = frozenset(
    {"archive_id", "title", "url", "source", "language", "summary", "author"}
)
_NEWS_RECORD_FIELDS = frozenset({"published_at", "available_at"}) | _NEWS_STRING_FIELDS


class EvaluationError(ValueError):
    """Raised when evaluation input violates a contract."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_timestamp(value: Any, location: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{location} must be a timezone-aware ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvaluationError(f"{location} is not a valid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvaluationError(f"{location} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _number(value: Any, location: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationError(f"{location} must be a finite number")
    if not math.isfinite(value):
        raise EvaluationError(f"{location} must be a finite number")
    if minimum is not None and value < minimum:
        raise EvaluationError(f"{location} must be at least {minimum}")
    return float(value)


def _reject_unknown_fields(
    value: dict[str, Any], allowed_fields: frozenset[str], location: str
) -> None:
    if any(not isinstance(key, str) for key in value):
        raise EvaluationError(f"{location} field names must be strings")
    unexpected = set(value) - allowed_fields
    if unexpected:
        raise EvaluationError(
            f"{location} has unsupported fields: {', '.join(sorted(unexpected))}"
        )


def _reject_labels(value: Any, location: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _LABEL_FIELDS:
                raise EvaluationError(
                    f"{location}.{key} is outcome/label data and cannot appear in a frozen case"
                )
            _reject_labels(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_labels(child, f"{location}[{index}]")


def _validate_point_in_time(
    value: Any,
    cutoff: datetime,
    location: str,
    *,
    skip_retrieved_at: bool = True,
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_name = str(key).lower()
            is_timestamp = (
                key_name in _TIMESTAMP_KEYS
                or key_name.endswith("_at")
                or key_name.endswith("_time")
            )
            if is_timestamp and not (skip_retrieved_at and key_name == "retrieved_at"):
                observed = parse_timestamp(child, f"{location}.{key}")
                if observed > cutoff:
                    raise EvaluationError(
                        f"{location}.{key} ({child}) is after data_cutoff"
                    )
            else:
                _validate_point_in_time(
                    child,
                    cutoff,
                    f"{location}.{key}",
                    skip_retrieved_at=skip_retrieved_at,
                )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_point_in_time(
                child,
                cutoff,
                f"{location}[{index}]",
                skip_retrieved_at=skip_retrieved_at,
            )


def _validate_candles(
    candles: Any,
    location: str,
    *,
    cutoff: datetime | None = None,
    interval_seconds: int | None = None,
    allow_future: bool = False,
    minimum_count: int = 1,
) -> list[dict[str, Any]]:
    if not isinstance(candles, list) or len(candles) < minimum_count:
        raise EvaluationError(
            f"{location} must contain at least {minimum_count} OHLCV candle(s)"
        )
    normalized: list[dict[str, Any]] = []
    previous_open: datetime | None = None
    previous_close: datetime | None = None
    for index, raw in enumerate(candles):
        item_location = f"{location}[{index}]"
        if not isinstance(raw, dict):
            raise EvaluationError(f"{item_location} must be an object")
        _reject_unknown_fields(raw, _CANDLE_FIELDS, item_location)
        missing = {"open_time", "close_time", "open", "high", "low", "close", "volume"} - set(
            raw
        )
        if missing:
            raise EvaluationError(f"{item_location} is missing {', '.join(sorted(missing))}")
        open_time = parse_timestamp(raw["open_time"], f"{item_location}.open_time")
        close_time = parse_timestamp(raw["close_time"], f"{item_location}.close_time")
        if close_time <= open_time:
            raise EvaluationError(f"{item_location} close_time must be after open_time")
        if not allow_future and cutoff is not None and close_time > cutoff:
            raise EvaluationError(f"{item_location} closes after data_cutoff")
        if previous_open is not None and open_time <= previous_open:
            raise EvaluationError(f"{location} must be strictly chronological without duplicates")
        if previous_close is not None and open_time < previous_close:
            raise EvaluationError(f"{item_location} overlaps the previous candle")

        open_price = _number(raw["open"], f"{item_location}.open", minimum=0)
        high = _number(raw["high"], f"{item_location}.high", minimum=0)
        low = _number(raw["low"], f"{item_location}.low", minimum=0)
        close = _number(raw["close"], f"{item_location}.close", minimum=0)
        _number(raw["volume"], f"{item_location}.volume", minimum=0)
        if min(open_price, high, low, close) <= 0:
            raise EvaluationError(f"{item_location} prices must be greater than zero")
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            raise EvaluationError(f"{item_location} has inconsistent OHLC prices")
        if interval_seconds and abs(
            (close_time - open_time).total_seconds() - interval_seconds
        ) > 2.0:
            raise EvaluationError(
                f"{item_location} does not match the declared candle interval"
            )

        normalized.append(dict(raw))
        previous_open = open_time
        previous_close = close_time
    return normalized


def _has_candle_gaps(candles: list[dict[str, Any]]) -> bool:
    for previous, current in zip(candles, candles[1:]):
        previous_close = parse_timestamp(previous["close_time"], "candle.close_time")
        current_open = parse_timestamp(current["open_time"], "candle.open_time")
        if abs((current_open - previous_close).total_seconds()) > 1.0:
            return True
    return False


def validate_spec(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise EvaluationError("dataset spec must be a JSON object")
    required = {
        "spec_version",
        "dataset_id",
        "dataset_version",
        "universe",
        "sampling",
        "coverage",
        "walk_forward",
        "baselines",
    }
    missing = required - set(spec)
    if missing:
        raise EvaluationError(f"dataset spec is missing {', '.join(sorted(missing))}")
    for key in ("spec_version", "dataset_id", "dataset_version"):
        if not isinstance(spec[key], str) or not spec[key].strip():
            raise EvaluationError(f"dataset spec {key} must be a non-empty string")
    universe = spec["universe"]
    if (
        not isinstance(universe, list)
        or not universe
        or any(not isinstance(asset, str) or not asset.strip() for asset in universe)
    ):
        raise EvaluationError("dataset spec universe must be a non-empty string array")
    if len({asset.upper() for asset in universe}) != len(universe):
        raise EvaluationError("dataset spec universe contains duplicate assets")

    sampling = spec["sampling"]
    if not isinstance(sampling, dict):
        raise EvaluationError("dataset spec sampling must be an object")
    start = parse_timestamp(sampling.get("start"), "sampling.start")
    end = parse_timestamp(sampling.get("end"), "sampling.end")
    if start > end:
        raise EvaluationError("sampling.start must not be after sampling.end")
    if sampling.get("include_all_eligible") is not True:
        raise EvaluationError("sampling.include_all_eligible must be true to limit cherry-picking")
    if sampling.get("no_cherry_picking") is not True:
        raise EvaluationError("sampling.no_cherry_picking must be true")
    if not isinstance(sampling.get("cadence"), str) or not sampling["cadence"]:
        raise EvaluationError("sampling.cadence must be a non-empty string")
    cadence_seconds = sampling.get("cadence_seconds")
    if cadence_seconds is not None and (
        isinstance(cadence_seconds, bool)
        or not isinstance(cadence_seconds, int)
        or cadence_seconds <= 0
    ):
        raise EvaluationError("sampling.cadence_seconds must be a positive integer")
    if cadence_seconds is not None:
        if sampling.get("instrument") not in INSTRUMENTS:
            raise EvaluationError("a fixed supported sampling.instrument is required with cadence")
        if sampling.get("horizon") not in HORIZONS:
            raise EvaluationError("a fixed supported sampling.horizon is required with cadence")
    exclusion_rule_ids = sampling.get("exclusion_rule_ids")
    if (
        not isinstance(exclusion_rule_ids, list)
        or not exclusion_rule_ids
        or any(not isinstance(rule, str) or not rule for rule in exclusion_rule_ids)
    ):
        raise EvaluationError("sampling.exclusion_rule_ids must be a non-empty string array")
    if len(exclusion_rule_ids) != len(set(exclusion_rule_ids)):
        raise EvaluationError("sampling.exclusion_rule_ids contains duplicates")

    coverage = spec["coverage"]
    if not isinstance(coverage, dict):
        raise EvaluationError("dataset spec coverage must be an object")
    if not isinstance(coverage.get("require_all_assets"), bool):
        raise EvaluationError("coverage.require_all_assets must be boolean")
    minimum_per_asset = coverage.get("minimum_cases_per_asset")
    if isinstance(minimum_per_asset, bool) or not isinstance(minimum_per_asset, int):
        raise EvaluationError("coverage.minimum_cases_per_asset must be an integer")
    if minimum_per_asset < 0:
        raise EvaluationError("coverage.minimum_cases_per_asset cannot be negative")
    required_regimes = coverage.get("required_market_regimes", [])
    if not isinstance(required_regimes, list) or any(
        not isinstance(regime, str) or not regime for regime in required_regimes
    ):
        raise EvaluationError("coverage.required_market_regimes must be a string array")
    if len(required_regimes) != len(set(required_regimes)):
        raise EvaluationError("coverage.required_market_regimes contains duplicates")
    minimum_per_regime = coverage.get("minimum_cases_per_regime", 0)
    if isinstance(minimum_per_regime, bool) or not isinstance(minimum_per_regime, int):
        raise EvaluationError("coverage.minimum_cases_per_regime must be an integer")
    if minimum_per_regime < 0:
        raise EvaluationError("coverage.minimum_cases_per_regime cannot be negative")

    walk_forward = spec["walk_forward"]
    if not isinstance(walk_forward, dict):
        raise EvaluationError("dataset spec walk_forward must be an object")
    if walk_forward.get("strategy") != "expanding_chronological":
        raise EvaluationError("walk_forward.strategy must be expanding_chronological")
    if walk_forward.get("shuffle") is not False:
        raise EvaluationError("walk_forward.shuffle must be false")
    fractions = [
        _number(walk_forward.get(key), f"walk_forward.{key}", minimum=0)
        for key in ("train_fraction", "validation_fraction", "test_fraction")
    ]
    if any(value == 0 for value in fractions) or abs(sum(fractions) - 1.0) > 1e-9:
        raise EvaluationError("walk-forward fractions must be positive and sum to 1")
    if not isinstance(spec["baselines"], dict):
        raise EvaluationError("dataset spec baselines must be an object")
    for name, expected in FIXED_BASELINE_PARAMETERS.items():
        actual = spec["baselines"].get(name)
        if isinstance(actual, bool) or not isinstance(actual, int) or actual != expected:
            raise EvaluationError(f"baseline {name} is fixed at {expected} and cannot be tuned")
    seed = spec["baselines"].get("random_seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise EvaluationError("baseline random_seed must be a pre-declared integer")
    if seed < 0:
        raise EvaluationError("baseline random_seed cannot be negative")
    if spec.get("schema_version", "crypto-eval.spec.v1") != "crypto-eval.spec.v1":
        raise EvaluationError("unsupported dataset spec schema_version")
    return dict(spec)


def validate_candidate_bundle(
    bundle: Any, spec: dict[str, Any]
) -> tuple[list[dict[str, Any]], str, str, dict[str, Any]]:
    if not isinstance(bundle, dict) or bundle.get("schema_version") != "crypto-eval.candidates.v1":
        raise EvaluationError("candidate input must use schema_version crypto-eval.candidates.v1")
    unexpected_top_level = set(bundle) - {
        "schema_version",
        "source_id",
        "data_origin",
        "sampling_manifest",
        "candidates",
    }
    if unexpected_top_level:
        raise EvaluationError(
            "candidate input has unsupported fields: "
            + ", ".join(sorted(unexpected_top_level))
        )
    source_id = bundle.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise EvaluationError("candidate input source_id must be a non-empty string")
    if "data_origin" not in bundle:
        raise EvaluationError("candidate input must explicitly declare data_origin")
    data_origin = bundle["data_origin"]
    if not isinstance(data_origin, str) or data_origin not in {
        "historical_archive",
        "forward_paper",
        "synthetic_fixture",
    }:
        raise EvaluationError(
            "data_origin must be historical_archive, forward_paper, or synthetic_fixture"
        )
    candidates = bundle.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise EvaluationError("candidate input must contain a non-empty candidates array")
    manifest = bundle.get("sampling_manifest")
    if not isinstance(manifest, dict):
        raise EvaluationError("candidate input must include a sampling_manifest")
    if set(manifest) != {
        "scheduled_case_count",
        "included_case_count",
        "excluded_cases",
    }:
        raise EvaluationError(
            "sampling_manifest requires scheduled_case_count, included_case_count, and excluded_cases"
        )
    scheduled_count = manifest["scheduled_case_count"]
    included_count = manifest["included_case_count"]
    exclusions = manifest["excluded_cases"]
    if (
        isinstance(scheduled_count, bool)
        or not isinstance(scheduled_count, int)
        or scheduled_count < 1
    ):
        raise EvaluationError("sampling_manifest.scheduled_case_count must be a positive integer")
    if (
        isinstance(included_count, bool)
        or not isinstance(included_count, int)
        or included_count < 1
    ):
        raise EvaluationError("sampling_manifest.included_case_count must be a positive integer")
    if not isinstance(exclusions, list):
        raise EvaluationError("sampling_manifest.excluded_cases must be an array")
    if included_count != len(candidates):
        raise EvaluationError(
            "sampling_manifest.included_case_count does not match the candidate rows"
        )
    if scheduled_count != included_count + len(exclusions):
        raise EvaluationError(
            "every scheduled case must be included or have a recorded exclusion"
        )

    allowed_horizons = HORIZONS
    allowed_instruments = INSTRUMENTS
    expected_assets = {asset.upper() for asset in spec["universe"]}
    start = parse_timestamp(spec["sampling"]["start"], "sampling.start")
    end = parse_timestamp(spec["sampling"]["end"], "sampling.end")
    cadence_seconds = spec["sampling"].get("cadence_seconds")
    schedule_keys: set[tuple[str, int]] = set()
    expected_schedule_count: int | None = None
    if cadence_seconds is not None:
        schedule_slots = int((end - start).total_seconds() // cadence_seconds) + 1
        expected_schedule_count = schedule_slots * len(expected_assets)
        if scheduled_count != expected_schedule_count:
            raise EvaluationError(
                "sampling_manifest.scheduled_case_count does not match the versioned cadence, window, and universe"
            )

    def record_schedule(asset: str, timestamp: datetime, location: str) -> None:
        if cadence_seconds is None:
            return
        offset = (timestamp - start).total_seconds()
        slot = round(offset / cadence_seconds)
        if (
            slot < 0
            or abs(offset - slot * cadence_seconds) > 1.0
            or timestamp > end
        ):
            raise EvaluationError(f"{location} is not on the declared UTC sampling cadence")
        key = (asset, slot)
        if key in schedule_keys:
            raise EvaluationError(f"{location} duplicates an asset/time sampling slot")
        schedule_keys.add(key)

    keys: set[str] = set()
    normalized_exclusions: list[dict[str, str]] = []
    for index, exclusion in enumerate(exclusions):
        location = f"sampling_manifest.excluded_cases[{index}]"
        if not isinstance(exclusion, dict):
            raise EvaluationError(f"{location} must be an object")
        _reject_labels(exclusion, location)
        if set(exclusion) != {"source_case_key", "asset", "scheduled_at", "reason", "rule_id"}:
            raise EvaluationError(
                f"{location} requires source_case_key, asset, scheduled_at, reason, and rule_id"
            )
        source_key = exclusion["source_case_key"]
        if not isinstance(source_key, str) or not source_key.strip() or source_key in keys:
            raise EvaluationError(f"{location}.source_case_key must be unique and non-empty")
        keys.add(source_key)
        asset = exclusion["asset"]
        if not isinstance(asset, str) or asset.upper() not in expected_assets:
            raise EvaluationError(f"{location}.asset is outside the versioned dataset universe")
        scheduled_at = parse_timestamp(exclusion["scheduled_at"], f"{location}.scheduled_at")
        if not start <= scheduled_at <= end:
            raise EvaluationError(f"{location}.scheduled_at is outside the sampling window")
        record_schedule(asset.upper(), scheduled_at, location)
        for field in ("reason", "rule_id"):
            if not isinstance(exclusion[field], str) or not exclusion[field].strip():
                raise EvaluationError(f"{location}.{field} must be a non-empty string")
        if exclusion["rule_id"] not in spec["sampling"]["exclusion_rule_ids"]:
            raise EvaluationError(
                f"{location}.rule_id is not allowed by the versioned dataset spec"
            )
        normalized_exclusions.append(
            {
                "source_case_key": source_key,
                "asset": asset.upper(),
                "scheduled_at": exclusion["scheduled_at"],
                "reason": exclusion["reason"].strip(),
                "rule_id": exclusion["rule_id"].strip(),
            }
        )

    validated: list[dict[str, Any]] = []

    required_fields = {
        "source_case_key",
        "asset",
        "instrument",
        "venue",
        "horizon",
        "as_of",
        "data_cutoff",
        "bar_interval_seconds",
        "horizon_bars",
        "snapshot",
    }
    allowed_fields = required_fields | {
        "market_regime",
        "btc_regime",
        "leverage_stress",
        "liquidity_tier",
        "benchmark_asset",
    }

    for index, candidate in enumerate(candidates):
        location = f"candidates[{index}]"
        if not isinstance(candidate, dict):
            raise EvaluationError(f"{location} must be an object")
        missing = required_fields - set(candidate)
        if missing:
            raise EvaluationError(f"{location} is missing {', '.join(sorted(missing))}")
        extra = set(candidate) - allowed_fields
        if extra:
            raise EvaluationError(f"{location} has unsupported fields: {', '.join(sorted(extra))}")

        source_case_key = candidate["source_case_key"]
        if not isinstance(source_case_key, str) or not source_case_key.strip():
            raise EvaluationError(f"{location}.source_case_key must be a non-empty string")
        if source_case_key in keys:
            raise EvaluationError(f"duplicate source_case_key: {source_case_key}")
        keys.add(source_case_key)

        asset = candidate["asset"]
        if not isinstance(asset, str) or not asset.strip():
            raise EvaluationError(f"{location}.asset must be a non-empty string")
        asset = asset.upper()
        if asset not in expected_assets:
            raise EvaluationError(
                f"{location}.asset {asset!r} is outside the versioned dataset universe"
            )
        instrument = candidate["instrument"]
        if not isinstance(instrument, str) or instrument not in allowed_instruments:
            raise EvaluationError(f"{location}.instrument is unsupported: {instrument!r}")
        horizon = candidate["horizon"]
        if not isinstance(horizon, str) or horizon not in allowed_horizons:
            raise EvaluationError(f"{location}.horizon is unsupported: {horizon!r}")
        if cadence_seconds is not None and (
            instrument != spec["sampling"]["instrument"]
            or horizon != spec["sampling"]["horizon"]
        ):
            raise EvaluationError(
                f"{location} does not match the versioned instrument/horizon sampling schedule"
            )
        venue = candidate["venue"]
        if not isinstance(venue, str) or not venue.strip():
            raise EvaluationError(f"{location}.venue must be a non-empty string")
        for optional_field in (
            "market_regime",
            "btc_regime",
            "liquidity_tier",
        ):
            value = candidate.get(optional_field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise EvaluationError(
                    f"{location}.{optional_field} must be a non-empty string or null"
                )
        declared_regimes = set(spec["coverage"].get("required_market_regimes", []))
        if (
            candidate.get("market_regime") is not None
            and declared_regimes
            and candidate["market_regime"] not in declared_regimes
        ):
            raise EvaluationError(
                f"{location}.market_regime is not in the versioned regime taxonomy"
            )
        leverage_stress = candidate.get("leverage_stress")
        if leverage_stress is not None and (
            not isinstance(leverage_stress, str)
            or leverage_stress
            not in {
                "low",
                "elevated",
                "high",
                "extreme",
                "unavailable",
                "not_covered",
            }
        ):
            raise EvaluationError(f"{location}.leverage_stress is invalid")

        as_of = parse_timestamp(candidate["as_of"], f"{location}.as_of")
        cutoff = parse_timestamp(candidate["data_cutoff"], f"{location}.data_cutoff")
        if cutoff > as_of:
            raise EvaluationError(f"{location}.data_cutoff must not be after as_of")
        if not start <= as_of <= end:
            raise EvaluationError(f"{location}.as_of is outside the declared sampling window")
        record_schedule(asset, as_of, location)
        interval_seconds = candidate["bar_interval_seconds"]
        horizon_bars = candidate["horizon_bars"]
        if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or interval_seconds <= 0:
            raise EvaluationError(f"{location}.bar_interval_seconds must be a positive integer")
        if isinstance(horizon_bars, bool) or not isinstance(horizon_bars, int) or horizon_bars <= 0:
            raise EvaluationError(f"{location}.horizon_bars must be a positive integer")

        snapshot = candidate["snapshot"]
        if not isinstance(snapshot, dict):
            raise EvaluationError(f"{location}.snapshot must be an object")
        _reject_labels(snapshot, f"{location}.snapshot")
        _reject_unknown_fields(snapshot, _SNAPSHOT_FIELDS, f"{location}.snapshot")
        candles = _validate_candles(
            snapshot.get("candles"),
            f"{location}.snapshot.candles",
            cutoff=cutoff,
            interval_seconds=interval_seconds,
        )
        if parse_timestamp(candles[-1]["close_time"], f"{location}.snapshot.candles[-1].close_time") > cutoff:
            raise EvaluationError(f"{location} last candle closes after data_cutoff")
        _validate_point_in_time(snapshot, cutoff, f"{location}.snapshot")
        for name in _OBSERVATION_FIELDS:
            observations = snapshot.get(name, [])
            if not isinstance(observations, list):
                raise EvaluationError(f"{location}.snapshot.{name} must be an array")
            for observation_index, observation in enumerate(observations):
                item_location = f"{location}.snapshot.{name}[{observation_index}]"
                if not isinstance(observation, dict):
                    raise EvaluationError(f"{item_location} must be an object")
                _reject_unknown_fields(
                    observation, _OBSERVATION_RECORD_FIELDS, item_location
                )
                if "observed_at" not in observation:
                    raise EvaluationError(f"{item_location}.observed_at is required")
                for field in _OBSERVATION_NUMBER_FIELDS & observation.keys():
                    _number(observation[field], f"{item_location}.{field}")
                for field in _OBSERVATION_STRING_FIELDS & observation.keys():
                    if not isinstance(observation[field], str) or not observation[field].strip():
                        raise EvaluationError(
                            f"{item_location}.{field} must be a non-empty string"
                        )
        for name in _NEWS_FIELDS:
            articles = snapshot.get(name, [])
            if not isinstance(articles, list):
                raise EvaluationError(f"{location}.snapshot.{name} must be an array")
            for article_index, article in enumerate(articles):
                item_location = f"{location}.snapshot.{name}[{article_index}]"
                if not isinstance(article, dict):
                    raise EvaluationError(f"{item_location} must be an object")
                _reject_unknown_fields(article, _NEWS_RECORD_FIELDS, item_location)
                if "published_at" not in article or "available_at" not in article:
                    raise EvaluationError(
                        f"{item_location} requires published_at and archived available_at"
                    )
                if not isinstance(article.get("archive_id"), str) or not article["archive_id"]:
                    raise EvaluationError(f"{item_location}.archive_id is required")
                for field in (_NEWS_STRING_FIELDS - {"archive_id"}) & article.keys():
                    if not isinstance(article[field], str) or not article[field].strip():
                        raise EvaluationError(
                            f"{item_location}.{field} must be a non-empty string"
                        )
                if parse_timestamp(article["published_at"], f"{item_location}.published_at") > cutoff:
                    raise EvaluationError(f"{item_location} was published after data_cutoff")
                if parse_timestamp(article["available_at"], f"{item_location}.available_at") > cutoff:
                    raise EvaluationError(
                        f"{item_location} was not archived by data_cutoff; current search is not valid"
                    )

        benchmark_asset = candidate.get("benchmark_asset")
        if benchmark_asset is not None and (
            not isinstance(benchmark_asset, str) or not benchmark_asset.strip()
        ):
            raise EvaluationError(f"{location}.benchmark_asset must be a string or null")
        benchmark_candles = snapshot.get("benchmark_candles", [])
        if benchmark_candles:
            _validate_candles(
                benchmark_candles,
                f"{location}.snapshot.benchmark_candles",
                cutoff=cutoff,
                interval_seconds=interval_seconds,
            )
            if not benchmark_asset:
                raise EvaluationError(
                    f"{location}.benchmark_asset is required when benchmark_candles are supplied"
                )
        elif benchmark_asset and benchmark_asset.upper() != "BTC":
            raise EvaluationError(
                f"{location} only supports the BTC benchmark until another benchmark is supplied"
            )

        availability = snapshot.get("data_availability", {})
        if not isinstance(availability, dict):
            raise EvaluationError(f"{location}.snapshot.data_availability must be an object")
        _reject_unknown_fields(
            availability,
            _DATA_AVAILABILITY_FIELDS,
            f"{location}.snapshot.data_availability",
        )
        for data_name, status in availability.items():
            if not isinstance(data_name, str) or not data_name:
                raise EvaluationError(f"{location}.snapshot.data_availability has an invalid key")
            if not isinstance(status, str) or status not in AVAILABILITY_STATES:
                raise EvaluationError(
                    f"{location}.snapshot.data_availability.{data_name} has an invalid status"
                )
        availability_sources = {
            "funding": "funding",
            "open_interest": "open_interest",
            "news": "news",
            "options": "options",
            "on_chain": "on_chain",
            "macro": "macro",
        }
        for availability_name, source_name in availability_sources.items():
            status = availability.get(availability_name)
            present = bool(snapshot.get(source_name)) or (
                source_name == "open_interest" and bool(snapshot.get("oi"))
            )
            if status == "available" and not present:
                raise EvaluationError(
                    f"{location}.snapshot.data_availability.{availability_name} says available but has no observations"
                )
            if status in {"unavailable", "not_covered"} and present:
                raise EvaluationError(
                    f"{location}.snapshot.data_availability.{availability_name} conflicts with supplied observations"
                )
        normalized_snapshot = dict(snapshot)
        normalized_availability = dict(availability)
        asset_candles_partial = _has_candle_gaps(candles)
        if asset_candles_partial and normalized_availability.get("candles") == "available":
            raise EvaluationError(
                f"{location}.snapshot.data_availability.candles claims available despite missing intervals"
            )
        if asset_candles_partial:
            normalized_availability["candles"] = "partial"
        elif normalized_availability.get("candles") in {"unavailable", "not_covered"}:
            raise EvaluationError(
                f"{location}.snapshot.data_availability.candles conflicts with supplied candles"
            )
        if benchmark_candles:
            benchmark_partial = _has_candle_gaps(benchmark_candles)
            if benchmark_partial and normalized_availability.get("benchmark") == "available":
                raise EvaluationError(
                    f"{location}.snapshot.data_availability.benchmark claims available despite missing intervals"
                )
            if benchmark_partial:
                normalized_availability["benchmark"] = "partial"
        if normalized_availability:
            normalized_snapshot["data_availability"] = normalized_availability
        validated.append(
            {
                "source_case_key": source_case_key,
                "asset": asset,
                "instrument": instrument,
                "venue": venue,
                "horizon": horizon,
                "as_of": candidate["as_of"],
                "data_cutoff": candidate["data_cutoff"],
                "bar_interval_seconds": interval_seconds,
                "horizon_bars": horizon_bars,
                "market_regime": candidate.get("market_regime"),
                "btc_regime": candidate.get("btc_regime"),
                "leverage_stress": candidate.get("leverage_stress"),
                "liquidity_tier": candidate.get("liquidity_tier"),
                "benchmark_asset": benchmark_asset.upper() if benchmark_asset else None,
                "snapshot": normalized_snapshot,
            }
        )
    normalized_manifest = {
        "scheduled_case_count": scheduled_count,
        "included_case_count": included_count,
        "excluded_cases": sorted(
            normalized_exclusions,
            key=lambda item: (
                parse_timestamp(item["scheduled_at"], "sampling_manifest.scheduled_at"),
                item["source_case_key"],
            ),
        ),
    }
    if expected_schedule_count is not None and len(schedule_keys) != expected_schedule_count:
        raise EvaluationError(
            "candidate/exclusion rows do not cover every scheduled asset/time slot"
        )
    return validated, source_id, data_origin, normalized_manifest


def chronological_fold_map(
    timestamps: list[datetime], split: dict[str, Any]
) -> dict[datetime, str]:
    unique_times = sorted(set(timestamps))
    if len(unique_times) < 3:
        raise EvaluationError(
            "walk-forward construction needs at least three distinct as_of timestamps"
        )
    train_count = max(1, int(len(unique_times) * split["train_fraction"]))
    validation_count = max(1, int(len(unique_times) * split["validation_fraction"]))
    if train_count + validation_count >= len(unique_times):
        validation_count = max(1, len(unique_times) - train_count - 1)
    if train_count + validation_count >= len(unique_times):
        train_count = len(unique_times) - 2
    validation_end = train_count + validation_count
    return {
        timestamp: (
            "train"
            if index < train_count
            else "validation"
            if index < validation_end
            else "test"
        )
        for index, timestamp in enumerate(unique_times)
    }


def validate_dataset(dataset: Any) -> dict[str, Any]:
    if not isinstance(dataset, dict) or dataset.get("schema_version") != "crypto-eval.dataset.v1":
        raise EvaluationError("dataset must use schema_version crypto-eval.dataset.v1")
    unexpected_top_level = set(dataset) - {
        "schema_version",
        "dataset_id",
        "dataset_version",
        "spec_version",
        "spec_hash",
        "source_id",
        "data_origin",
        "spec",
        "coverage_summary",
        "sampling_audit",
        "cases",
        "dataset_hash",
    }
    if unexpected_top_level:
        raise EvaluationError(
            "dataset has unsupported fields: " + ", ".join(sorted(unexpected_top_level))
        )
    if not isinstance(dataset.get("cases"), list) or not dataset["cases"]:
        raise EvaluationError("dataset cases must be a non-empty array")
    if not isinstance(dataset.get("dataset_id"), str) or not dataset["dataset_id"]:
        raise EvaluationError("dataset_id is required")
    spec = validate_spec(dataset.get("spec"))
    if dataset["dataset_id"] != spec["dataset_id"]:
        raise EvaluationError("dataset_id does not match its versioned spec")
    if dataset.get("dataset_version") != spec["dataset_version"]:
        raise EvaluationError("dataset_version does not match its versioned spec")
    if dataset.get("spec_version") != spec["spec_version"]:
        raise EvaluationError("spec_version does not match its versioned spec")
    if dataset.get("spec_hash") != digest(spec):
        raise EvaluationError("spec_hash does not match the embedded dataset spec")
    if not isinstance(dataset.get("source_id"), str) or not dataset["source_id"]:
        raise EvaluationError("dataset source_id is required")
    if dataset.get("data_origin") not in {
        "historical_archive",
        "forward_paper",
        "synthetic_fixture",
    }:
        raise EvaluationError("dataset data_origin is unsupported")

    cases = dataset["cases"]
    case_ids: set[str] = set()
    previous_sort_key: tuple[datetime, str] | None = None
    candidate_cases: list[dict[str, Any]] = []
    timestamps: list[datetime] = []
    for index, case in enumerate(cases):
        location = f"dataset.cases[{index}]"
        if not isinstance(case, dict):
            raise EvaluationError(f"{location} must be an object")
        allowed_case_fields = {
            "case_id",
            "source_case_key",
            "asset",
            "instrument",
            "venue",
            "horizon",
            "as_of",
            "data_cutoff",
            "bar_interval_seconds",
            "horizon_bars",
            "market_regime",
            "btc_regime",
            "leverage_stress",
            "liquidity_tier",
            "benchmark_asset",
            "snapshot",
            "data_availability",
            "fold",
        }
        unexpected_case_fields = set(case) - allowed_case_fields
        if unexpected_case_fields:
            raise EvaluationError(
                f"{location} has unsupported fields: "
                + ", ".join(sorted(unexpected_case_fields))
            )
        for field in (
            "case_id",
            "source_case_key",
            "asset",
            "instrument",
            "venue",
            "horizon",
            "as_of",
            "data_cutoff",
            "snapshot",
            "fold",
        ):
            if field not in case:
                raise EvaluationError(f"{location}.{field} is required")
        if not isinstance(case["case_id"], str) or not case["case_id"]:
            raise EvaluationError(f"{location}.case_id must be a non-empty string")
        if case["case_id"] in case_ids:
            raise EvaluationError(f"duplicate case_id: {case['case_id']}")
        case_ids.add(case["case_id"])
        case_time = parse_timestamp(case["as_of"], f"{location}.as_of")
        sort_key = (case_time, case["case_id"])
        if previous_sort_key is not None and sort_key < previous_sort_key:
            raise EvaluationError("dataset cases must be ordered chronologically")
        if not isinstance(case["source_case_key"], str) or not case["source_case_key"]:
            raise EvaluationError(f"{location}.source_case_key must be a non-empty string")
        expected_id = f"case-{digest({'dataset_id': dataset['dataset_id'], 'dataset_version': dataset['dataset_version'], 'source_case_key': case['source_case_key']})[:20]}"
        if case["case_id"] != expected_id:
            raise EvaluationError(f"{location}.case_id does not match its stable source identity")
        availability = case.get("data_availability")
        if not isinstance(availability, dict):
            raise EvaluationError(f"{location}.data_availability must be an object")
        _reject_unknown_fields(
            availability, _DATA_AVAILABILITY_FIELDS, f"{location}.data_availability"
        )
        for name, status in availability.items():
            if not isinstance(name, str) or not name:
                raise EvaluationError(f"{location}.data_availability has an invalid key")
            if not isinstance(status, str) or status not in AVAILABILITY_STATES:
                raise EvaluationError(f"{location}.data_availability.{name} is invalid")
        expected_availability_fields = {
            "candles",
            "funding",
            "open_interest",
            "news",
            "options",
            "on_chain",
            "macro",
            "benchmark",
        }
        missing_availability = expected_availability_fields - set(availability)
        if missing_availability:
            raise EvaluationError(
                f"{location}.data_availability is missing "
                + ", ".join(sorted(missing_availability))
            )
        if availability["candles"] not in {"available", "partial"}:
            raise EvaluationError(f"{location}.data_availability.candles is not usable")
        snapshot = case.get("snapshot", {})
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("candles"), list):
            raise EvaluationError(f"{location}.snapshot must contain a candle array")
        if _has_candle_gaps(snapshot["candles"]) and availability["candles"] != "partial":
            raise EvaluationError(
                f"{location}.data_availability.candles must be partial when intervals are missing"
            )
        availability_sources = {
            "funding": "funding",
            "open_interest": "open_interest",
            "news": "news",
            "options": "options",
            "on_chain": "on_chain",
            "macro": "macro",
        }
        for feature, source_name in availability_sources.items():
            present = bool(snapshot.get(source_name)) or (
                source_name == "open_interest" and bool(snapshot.get("oi"))
            )
            status = availability[feature]
            if status == "available" and not present:
                raise EvaluationError(
                    f"{location}.data_availability.{feature} is available without observations"
                )
            if status in {"unavailable", "not_covered"} and present:
                raise EvaluationError(
                    f"{location}.data_availability.{feature} conflicts with observations"
                )
        has_benchmark = bool(snapshot.get("benchmark_candles"))
        if availability["benchmark"] == "available" and not has_benchmark:
            raise EvaluationError(
                f"{location}.data_availability.benchmark is available without a series"
            )
        if availability["benchmark"] in {"unavailable", "not_covered"} and has_benchmark:
            raise EvaluationError(
                f"{location}.data_availability.benchmark conflicts with a series"
            )
        if has_benchmark and _has_candle_gaps(snapshot["benchmark_candles"]) and availability[
            "benchmark"
        ] != "partial":
            raise EvaluationError(
                f"{location}.data_availability.benchmark must be partial when intervals are missing"
            )

        candidate_cases.append(
            {
                key: case[key]
                for key in (
                    "source_case_key",
                    "asset",
                    "instrument",
                    "venue",
                    "horizon",
                    "as_of",
                    "data_cutoff",
                    "bar_interval_seconds",
                    "horizon_bars",
                    "market_regime",
                    "btc_regime",
                    "leverage_stress",
                    "liquidity_tier",
                    "benchmark_asset",
                    "snapshot",
                )
                if key in case
            }
        )
        timestamps.append(case_time)
        previous_sort_key = sort_key

    normalized_cases, source_id, origin, sampling_audit = validate_candidate_bundle(
        {
            "schema_version": "crypto-eval.candidates.v1",
            "source_id": dataset["source_id"],
            "data_origin": dataset["data_origin"],
            "sampling_manifest": dataset.get("sampling_audit"),
            "candidates": candidate_cases,
        },
        spec,
    )
    if sampling_audit != dataset["sampling_audit"]:
        raise EvaluationError("dataset sampling_audit is not normalized")
    expected_folds = chronological_fold_map(timestamps, spec["walk_forward"])
    for index, case in enumerate(cases):
        timestamp = timestamps[index]
        if case["fold"] != expected_folds[timestamp]:
            raise EvaluationError(
                f"walk-forward fold for {case['case_id']} does not match chronological spec"
            )
        if case["case_id"] != f"case-{digest({'dataset_id': dataset['dataset_id'], 'dataset_version': dataset['dataset_version'], 'source_case_key': normalized_cases[index]['source_case_key']})[:20]}":
            raise EvaluationError(f"unstable case ID for {case['source_case_key']}")

    if source_id != dataset["source_id"] or origin != dataset["data_origin"]:
        raise EvaluationError("dataset source metadata does not match its case bundle")
    coverage = spec["coverage"]
    asset_counts: dict[str, int] = {}
    regime_counts: dict[str, int] = {}
    for case in cases:
        asset_counts[case["asset"]] = asset_counts.get(case["asset"], 0) + 1
        regime = case.get("market_regime")
        if regime is not None:
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
    if coverage["require_all_assets"]:
        missing = [asset for asset in spec["universe"] if asset.upper() not in asset_counts]
        if missing:
            raise EvaluationError(f"dataset is missing required assets: {', '.join(missing)}")
    if any(count < coverage["minimum_cases_per_asset"] for count in asset_counts.values()):
        raise EvaluationError("dataset does not meet minimum_cases_per_asset")
    for regime in coverage.get("required_market_regimes", []):
        count = regime_counts.get(regime, 0)
        if count < coverage.get("minimum_cases_per_regime", 0):
            raise EvaluationError(f"dataset does not meet minimum coverage for regime {regime}")
    missing_target_assets = [
        asset.upper() for asset in spec["universe"] if asset.upper() not in asset_counts
    ]
    expected_coverage = {
        "cases": len(cases),
        "scheduled_cases": sampling_audit["scheduled_case_count"],
        "included_cases": sampling_audit["included_case_count"],
        "excluded_cases": len(sampling_audit["excluded_cases"]),
        "assets": dict(sorted(asset_counts.items())),
        "market_regimes": {
            str(regime): count
            for regime, count in sorted(regime_counts.items(), key=lambda item: str(item[0]))
        },
        "missing_target_assets": missing_target_assets,
    }
    if dataset.get("coverage_summary") != expected_coverage:
        raise EvaluationError("dataset coverage_summary does not match its cases")

    expected_hash = dataset.get("dataset_hash")
    if not isinstance(expected_hash, str):
        raise EvaluationError("dataset_hash is required")
    hashed = dict(dataset)
    hashed.pop("dataset_hash", None)
    if digest(hashed) != expected_hash:
        raise EvaluationError("dataset_hash does not match the immutable dataset contents")
    return dataset


def validate_outcomes(outcomes: Any, dataset: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(outcomes, dict) or outcomes.get("schema_version") != "crypto-eval.outcomes.v1":
        raise EvaluationError("outcomes must use schema_version crypto-eval.outcomes.v1")
    if (
        outcomes.get("dataset_id") != dataset["dataset_id"]
        or outcomes.get("dataset_version") != dataset["dataset_version"]
        or outcomes.get("dataset_hash") != dataset["dataset_hash"]
    ):
        raise EvaluationError("outcome dataset identity/hash does not match the dataset")
    unexpected_top_level = set(outcomes) - {
        "schema_version",
        "dataset_id",
        "dataset_version",
        "dataset_hash",
        "records",
    }
    if unexpected_top_level:
        raise EvaluationError(
            f"outcomes have unsupported fields: {', '.join(sorted(unexpected_top_level))}"
        )
    records = outcomes.get("records")
    if not isinstance(records, list):
        raise EvaluationError("outcomes records must be an array")
    case_map = {case["case_id"]: case for case in dataset["cases"]}
    seen: set[str] = set()
    for index, record in enumerate(records):
        location = f"outcomes.records[{index}]"
        if not isinstance(record, dict):
            raise EvaluationError(f"{location} must be an object")
        _reject_labels(record, location)
        unexpected = set(record) - {
            "case_id",
            "source_case_key",
            "status",
            "known_at",
            "candles",
            "benchmark_candles",
            "reason",
        }
        if unexpected:
            raise EvaluationError(
                f"{location} has unsupported fields: {', '.join(sorted(unexpected))}"
            )
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or case_id not in case_map:
            raise EvaluationError(f"{location}.case_id does not identify a dataset case")
        if case_id in seen:
            raise EvaluationError(f"duplicate outcome record for case_id {case_id}")
        seen.add(case_id)
        source_key = record.get("source_case_key")
        if source_key is not None and source_key != case_map[case_id]["source_case_key"]:
            raise EvaluationError(f"{location}.source_case_key does not match case_id")
        if not isinstance(record.get("status"), str) or record.get("status") not in OUTCOME_STATUSES:
            raise EvaluationError(f"{location}.status must be complete, partial, or unavailable")
        if record.get("reason") is not None and not isinstance(record["reason"], str):
            raise EvaluationError(f"{location}.reason must be a string or null")
        known_at = parse_timestamp(record.get("known_at"), f"{location}.known_at")
        case = case_map[case_id]
        cutoff = parse_timestamp(case["data_cutoff"], f"{case_id}.data_cutoff")
        if known_at <= cutoff:
            raise EvaluationError(f"{location}.known_at must be after data_cutoff")
        status = record["status"]
        if status == "unavailable":
            if record.get("candles") or record.get("benchmark_candles"):
                raise EvaluationError(f"{location} unavailable outcomes cannot contain price data")
            continue
        candles = _validate_candles(
            record.get("candles"),
            f"{location}.candles",
            interval_seconds=case["bar_interval_seconds"],
            allow_future=True,
        )
        for candle_index, candle in enumerate(candles):
            if parse_timestamp(candle["open_time"], f"{location}.candles[{candle_index}].open_time") < cutoff:
                raise EvaluationError(f"{location} contains a candle opening before data_cutoff")
            if parse_timestamp(candle["close_time"], f"{location}.candles[{candle_index}].close_time") > known_at:
                raise EvaluationError(f"{location} includes a candle after known_at")
        if status == "complete" and len(candles) < case["horizon_bars"]:
            raise EvaluationError(
                f"{location} is marked complete but has fewer than horizon_bars"
            )
        benchmark_candles = record.get("benchmark_candles", [])
        if benchmark_candles:
            if not case.get("benchmark_asset"):
                raise EvaluationError(
                    f"{location} benchmark candles supplied without a case benchmark_asset"
                )
            _validate_candles(
                benchmark_candles,
                f"{location}.benchmark_candles",
                interval_seconds=case["bar_interval_seconds"],
                allow_future=True,
            )
            for candle_index, candle in enumerate(benchmark_candles):
                if parse_timestamp(candle["open_time"], f"{location}.benchmark_candles[{candle_index}].open_time") < cutoff:
                    raise EvaluationError(
                        f"{location} benchmark has a candle opening before data_cutoff"
                    )
                if parse_timestamp(candle["close_time"], f"{location}.benchmark_candles[{candle_index}].close_time") > known_at:
                    raise EvaluationError(
                        f"{location} benchmark includes a candle after known_at"
                    )
    return outcomes


def validate_prediction_record(
    record: Any,
    case: dict[str, Any],
    dataset_id: str,
    dataset_version: str | None = None,
    dataset_hash: str | None = None,
) -> dict[str, Any]:
    if not isinstance(record, dict) or record.get("schema_version") != "crypto-eval.prediction.v1":
        raise EvaluationError("prediction must use schema_version crypto-eval.prediction.v1")
    _reject_labels(record, "prediction")
    required = {
        "schema_version",
        "prediction_id",
        "prediction_hash",
        "dataset_id",
        "dataset_version",
        "dataset_hash",
        "case_id",
        "frozen_at",
        "runner",
        "decision",
    }
    missing = required - set(record)
    if missing:
        raise EvaluationError(f"prediction is missing {', '.join(sorted(missing))}")
    unexpected = set(record) - required
    if unexpected:
        raise EvaluationError(
            f"prediction has unsupported fields: {', '.join(sorted(unexpected))}"
        )
    if (
        record["dataset_id"] != dataset_id
        or record["case_id"] != case["case_id"]
        or (dataset_version is not None and record["dataset_version"] != dataset_version)
        or (dataset_hash is not None and record["dataset_hash"] != dataset_hash)
    ):
        raise EvaluationError("prediction dataset_id/case_id does not match the case")
    for field in ("dataset_version", "dataset_hash", "prediction_id"):
        if not isinstance(record[field], str) or not record[field]:
            raise EvaluationError(f"prediction.{field} must be a non-empty string")
    frozen_at = parse_timestamp(record["frozen_at"], "prediction.frozen_at")
    as_of = parse_timestamp(case["as_of"], "case.as_of")
    if frozen_at < as_of:
        raise EvaluationError("prediction frozen_at cannot precede as_of")
    runner = record["runner"]
    if not isinstance(runner, dict):
        raise EvaluationError("prediction.runner must be an object")
    runner_fields = (
        "name",
        "version",
        "mode",
        "execution_status",
        "provider",
        "model_id",
        "inference_config_hash",
        "prompt_version",
        "skill_commit",
        "variant",
        "run_id",
    )
    unexpected_runner_fields = set(runner) - set(runner_fields)
    if unexpected_runner_fields:
        raise EvaluationError(
            "prediction.runner has unsupported fields: "
            + ", ".join(sorted(unexpected_runner_fields))
        )
    for key in runner_fields:
        if key not in runner:
            raise EvaluationError(f"prediction.runner.{key} is required")
    for key in (
        "name",
        "version",
        "mode",
        "provider",
        "model_id",
        "inference_config_hash",
        "prompt_version",
        "run_id",
    ):
        if not isinstance(runner[key], str) or not runner[key]:
            raise EvaluationError(f"prediction.runner.{key} must be a non-empty string")
    if runner["skill_commit"] is not None and not isinstance(runner["skill_commit"], str):
        raise EvaluationError("prediction.runner.skill_commit must be a string or null")
    if not isinstance(runner["execution_status"], str) or runner["execution_status"] not in {
        "invoked",
        "not_invoked",
    }:
        raise EvaluationError("runner.execution_status must be invoked or not_invoked")
    if not isinstance(runner["variant"], str) or runner["variant"] not in {
        "fixture",
        "skill",
        "control",
    }:
        raise EvaluationError("runner.variant must be fixture, skill, or control")
    if runner["execution_status"] == "invoked" and (
        runner["provider"] == "none" or runner["model_id"] in {"none", "not_invoked"}
    ):
        raise EvaluationError("invoked model runs must identify a real provider and model")
    if runner["execution_status"] == "not_invoked" and runner["variant"] in {"skill", "control"}:
        raise EvaluationError("skill/control variants require an actually invoked model runner")
    if runner["variant"] == "fixture" and runner["execution_status"] != "not_invoked":
        raise EvaluationError("fixture variants cannot claim a model invocation")
    if runner["variant"] == "skill" and (
        not isinstance(runner["skill_commit"], str) or not runner["skill_commit"].strip()
    ):
        raise EvaluationError("skill predictions must identify the evaluated skill commit")
    decision = record["decision"]
    if not isinstance(decision, dict):
        raise EvaluationError("prediction.decision must be an object")
    required_decision_fields = {
        "decision_state",
        "bias",
        "confidence",
        "entry",
        "invalidation",
        "targets",
    }
    missing_decision_fields = required_decision_fields - set(decision)
    if missing_decision_fields:
        raise EvaluationError(
            "prediction.decision is missing "
            + ", ".join(sorted(missing_decision_fields))
        )
    allowed_decision_fields = {
        "decision_state",
        "bias",
        "confidence",
        "entry",
        "invalidation",
        "targets",
        "leverage_stress",
        "rationale",
    }
    unexpected_decision_fields = set(decision) - allowed_decision_fields
    if unexpected_decision_fields:
        raise EvaluationError(
            "prediction.decision has unsupported fields: "
            + ", ".join(sorted(unexpected_decision_fields))
        )
    if decision.get("rationale") is not None and not isinstance(decision["rationale"], str):
        raise EvaluationError("prediction rationale must be a string or null")
    if not isinstance(decision.get("decision_state"), str) or decision.get(
        "decision_state"
    ) not in DECISION_STATES:
        raise EvaluationError("prediction decision_state is not in the canonical state schema")
    if not isinstance(decision.get("bias"), str) or decision.get("bias") not in BIAS_DIRECTIONS:
        raise EvaluationError("prediction bias must be bullish, bearish, or neutral")
    if (
        not isinstance(decision.get("confidence"), str)
        or decision.get("confidence") not in CONFIDENCE_LEVELS
    ):
        raise EvaluationError("prediction confidence must be low, moderate, or high")
    if decision.get("leverage_stress") is not None and (
        not isinstance(decision.get("leverage_stress"), str)
        or decision["leverage_stress"] not in {"low", "elevated", "high", "extreme"}
    ):
        raise EvaluationError("prediction leverage_stress is invalid")
    targets = decision.get("targets", [])
    if not isinstance(targets, list):
        raise EvaluationError("prediction targets must be an array")
    numeric_targets = [_number(price, f"prediction.targets[{i}]", minimum=0) for i, price in enumerate(targets)]
    if any(price <= 0 for price in numeric_targets):
        raise EvaluationError("prediction target prices must be greater than zero")
    entry = decision.get("entry")
    state = decision["decision_state"]
    if entry is not None and not isinstance(entry, dict):
        raise EvaluationError("prediction entry must be an object or null")
    if state == "WAIT_FOR_PULLBACK":
        if not isinstance(entry, dict) or entry.get("kind") != "pullback":
            raise EvaluationError("WAIT_FOR_PULLBACK requires a pullback entry condition")
    if state == "WAIT_FOR_BREAKOUT_CONFIRMATION":
        if not isinstance(entry, dict) or entry.get("kind") != "breakout":
            raise EvaluationError(
                "WAIT_FOR_BREAKOUT_CONFIRMATION requires a breakout entry condition"
            )
    if state in {"ENTER_LONG", "ENTER_SHORT", "ACCUMULATE"}:
        if not isinstance(entry, dict) or entry.get("kind") != "immediate":
            raise EvaluationError(f"{state} requires an explicit immediate entry reference")
    if state in {"NO_TRADE", "AVOID_CHASING", "HOLD", "REDUCE", "TAKE_PARTIAL_PROFIT", "HEDGE_DE_RISK", "EXIT"}:
        if entry is not None and entry.get("kind") not in ("none", None):
            raise EvaluationError(f"{state} cannot carry an executable entry trigger")
    if entry is not None:
        kind = entry.get("kind")
        if not isinstance(kind, str) or kind not in {
            "pullback",
            "breakout",
            "immediate",
            "none",
        }:
            raise EvaluationError("prediction entry.kind is unsupported")
        allowed_entry_fields = {
            "pullback": {"kind", "direction", "zone_low", "zone_high", "confirmation"},
            "breakout": {"kind", "direction", "level", "confirmation"},
            "immediate": {"kind", "direction", "reference_price"},
            "none": {"kind"},
        }[kind]
        unexpected_entry_fields = set(entry) - allowed_entry_fields
        if unexpected_entry_fields:
            raise EvaluationError(
                "prediction.entry has unsupported fields: "
                + ", ".join(sorted(unexpected_entry_fields))
            )
        if kind != "none":
            if not isinstance(entry.get("direction"), str) or entry.get("direction") not in {
                "long",
                "short",
            }:
                raise EvaluationError("entry.direction must be long or short")
            if kind == "pullback":
                low = _number(entry.get("zone_low"), "entry.zone_low", minimum=0)
                high = _number(entry.get("zone_high"), "entry.zone_high", minimum=0)
                if low <= 0 or high < low:
                    raise EvaluationError("pullback zone must have positive ordered bounds")
                confirmation = entry.get("confirmation", "touch")
                if not isinstance(confirmation, str) or confirmation not in {
                    "touch",
                    "close_inside_zone",
                    "close_beyond_zone",
                }:
                    raise EvaluationError("unsupported pullback confirmation")
            if kind == "breakout":
                level = _number(entry.get("level"), "entry.level", minimum=0)
                confirmation = entry.get("confirmation", "close")
                if level <= 0 or confirmation != "close":
                    raise EvaluationError("breakout requires a positive level and close confirmation")
            if kind == "immediate":
                reference_price = _number(
                    entry.get("reference_price"), "entry.reference_price", minimum=0
                )
                if reference_price <= 0:
                    raise EvaluationError("immediate entry reference must be positive")
        if kind in {"pullback", "breakout", "immediate"}:
            direction = entry["direction"]
            expected_bias = "bullish" if direction == "long" else "bearish"
            if decision["bias"] != expected_bias:
                raise EvaluationError("decision bias must agree with the entry direction")
            if state == "ENTER_LONG" and direction != "long":
                raise EvaluationError("ENTER_LONG requires a long-side immediate entry")
            if state == "ENTER_SHORT" and direction != "short":
                raise EvaluationError("ENTER_SHORT requires a short-side immediate entry")
    invalidation = decision.get("invalidation")
    if invalidation is not None and _number(invalidation, "decision.invalidation", minimum=0) <= 0:
        raise EvaluationError("decision.invalidation must be greater than zero")
    if state in WAIT_STATES_FOR_CONTRACTS | {"ENTER_LONG", "ENTER_SHORT", "ACCUMULATE"}:
        if invalidation is None or not numeric_targets:
            raise EvaluationError(
                f"{state} requires an invalidation and at least one target for evaluation"
            )
    if numeric_targets and entry and entry.get("kind") != "none":
        side = entry.get("direction")
        if side == "long" and any(
            right <= left for left, right in zip(numeric_targets, numeric_targets[1:])
        ):
            raise EvaluationError("long-side targets must be strictly increasing and nearest-first")
        if side == "short" and any(
            right >= left for left, right in zip(numeric_targets, numeric_targets[1:])
        ):
            raise EvaluationError("short-side targets must be strictly decreasing and nearest-first")
        reference_close = float(case["snapshot"]["candles"][-1]["close"])
        if entry["kind"] == "pullback":
            entry_reference = float(entry["zone_high"] if side == "long" else entry["zone_low"])
            other_bound = float(entry["zone_low"] if side == "long" else entry["zone_high"])
            if side == "long" and float(entry["zone_high"]) >= reference_close:
                raise EvaluationError("long pullback zone must be below the as_of close")
            if side == "short" and float(entry["zone_low"]) <= reference_close:
                raise EvaluationError("short pullback zone must be above the as_of close")
        elif entry["kind"] == "breakout":
            entry_reference = float(entry["level"])
            other_bound = entry_reference
            if side == "long" and entry_reference <= reference_close:
                raise EvaluationError("long breakout level must be above the as_of close")
            if side == "short" and entry_reference >= reference_close:
                raise EvaluationError("short breakout level must be below the as_of close")
        else:
            entry_reference = float(entry["reference_price"])
            other_bound = entry_reference
            if abs(entry_reference / reference_close - 1.0) > 1e-6:
                raise EvaluationError("immediate entry reference must match the as_of close")
        if side == "long":
            if invalidation is not None and float(invalidation) >= other_bound:
                raise EvaluationError("long invalidation must be below its entry reference/zone")
            if numeric_targets and numeric_targets[0] <= entry_reference:
                raise EvaluationError("long target must be above its entry reference/zone")
        else:
            if invalidation is not None and float(invalidation) <= other_bound:
                raise EvaluationError("short invalidation must be above its entry reference/zone")
            if numeric_targets and numeric_targets[0] >= entry_reference:
                raise EvaluationError("short target must be below its entry reference/zone")

    identity = {
        "dataset_id": record["dataset_id"],
        "dataset_version": record["dataset_version"],
        "dataset_hash": record["dataset_hash"],
        "case_id": record["case_id"],
        "runner": runner,
    }
    if record["prediction_id"] != f"pred-{digest(identity)[:24]}":
        raise EvaluationError("prediction_id does not match its immutable dataset/run identity")
    if digest({key: value for key, value in record.items() if key != "prediction_hash"}) != record[
        "prediction_hash"
    ]:
        raise EvaluationError("prediction_hash does not match the frozen prediction contents")
    return record
