"""Strict, secret-free contracts for the local PAPER futures research runtime."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .contracts import EvaluationError


PAPER_SCHEMA_VERSION = "paper-futures.v1"
INTENT_SCHEMA_VERSION = "paper-trading-intent.v1"
JEV_VECTOR_SCHEMA_VERSION = "jev-decision-vector.v1"
QUESTION_SCHEMA_VERSION = "jev-questions.v1"
ESCALATION_POLICY_VERSION = "jev-escalation.v1"
EXECUTION_MODE = "PAPER"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "SEIUSDT")
DEFAULT_SHADOW_LEVERAGE = (1, 2, 3, 5, 10)
EXPERIMENT_ARMS = (
    "quant",
    "jev",
    "luna",
    "luna_skill",
    "quant_jev",
    "hybrid",
)
PROVIDER_KINDS = {
    "fixture_jev",
    "fixture_gpt",
    "typesafe_jev",
    "openai_responses",
    "foundry_responses",
    "compatible_responses",
}
PROVIDER_FIELDS = {
    "provider_id",
    "kind",
    "display_name",
    "base_url",
    "model",
    "auth_scheme",
    "timeout_seconds",
    "reasoning_effort",
    "enabled",
    "credential_env",
    "pricing",
}
EXPERIMENT_FIELDS = {
    "experiment_id",
    "execution_mode",
    "market_data_mode",
    "symbols",
    "decision_timeframe",
    "context_timeframes",
    "starting_balance_usdt",
    "risk_per_trade",
    "max_positions",
    "primary_leverage",
    "entry_order_type",
    "minimum_notional_usdt",
    "max_leverage",
    "shadow_leverage",
    "max_daily_loss",
    "max_drawdown_stop",
    "max_consecutive_losses",
    "taker_fee_rate",
    "maker_fee_rate",
    "slippage_bps",
    "maintenance_margin_rate",
    "market_data_retention_days",
    "schedule_delay_seconds",
    "monitor_interval_seconds",
    "signal_gate_enabled",
    "minimum_signal_strength",
    "primary_arm",
    "evaluation_arms",
    "jev_enabled",
    "jev_provider_id",
    "gpt_escalation_enabled",
    "gpt_provider_id",
    "fallback_policy",
    "escalation_policy",
    "force_escalation",
    "auto_resume",
}
INTENT_FIELDS = {
    "schema_version",
    "action",
    "symbol",
    "side",
    "order_type",
    "entry_price",
    "stop_price",
    "target_price",
    "reduce_only",
    "reduce_fraction",
    "position_id",
    "reason",
    "source_arm",
    "as_of",
}


class PaperTradingError(EvaluationError):
    """Raised when a PAPER runtime contract is invalid or cannot be honored."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PaperTradingError("timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PaperTradingError(f"{field} must be a timezone-aware ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise PaperTradingError(f"{field} must be a timezone-aware ISO-8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PaperTradingError(f"{field} must be a timezone-aware ISO-8601 timestamp")
    return parsed.astimezone(timezone.utc)


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PaperTradingError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise PaperTradingError(f"{field} must be a finite number in range")
    return result


def _integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PaperTradingError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def _unknown_fields(value: dict[str, Any], allowed: set[str], contract: str) -> None:
    extra = set(value) - allowed
    if extra:
        if extra & {"api_key", "secret", "token", "authorization", "password"}:
            raise PaperTradingError(f"{contract} must not contain credentials")
        raise PaperTradingError(f"{contract} contains unsupported fields")


def default_experiment_config() -> dict[str, Any]:
    return {
        "experiment_id": "EXP-001",
        "execution_mode": EXECUTION_MODE,
        "market_data_mode": "fixture",
        "symbols": list(DEFAULT_SYMBOLS),
        "decision_timeframe": "15m",
        "context_timeframes": ["1h", "4h"],
        "starting_balance_usdt": 100.0,
        "risk_per_trade": 0.01,
        "max_positions": 3,
        "primary_leverage": 3,
        "entry_order_type": "market",
        "minimum_notional_usdt": 5.0,
        "max_leverage": 10,
        "shadow_leverage": list(DEFAULT_SHADOW_LEVERAGE),
        "max_daily_loss": 0.05,
        "max_drawdown_stop": 0.15,
        "max_consecutive_losses": 5,
        "taker_fee_rate": 0.0004,
        "maker_fee_rate": 0.0002,
        "slippage_bps": 2.0,
        "maintenance_margin_rate": 0.005,
        "market_data_retention_days": None,
        "schedule_delay_seconds": 60,
        "monitor_interval_seconds": 30,
        "signal_gate_enabled": True,
        "minimum_signal_strength": 0.55,
        "primary_arm": "hybrid",
        "evaluation_arms": list(EXPERIMENT_ARMS),
        "jev_enabled": True,
        "jev_provider_id": "fixture-jev",
        "gpt_escalation_enabled": True,
        "gpt_provider_id": "fixture-gpt",
        "fallback_policy": "SKIP",
        "escalation_policy": {
            "version": ESCALATION_POLICY_VERSION,
            "minimum_confidence": 0.65,
            "conflict_threshold": 0.65,
            "setup_borderline_min": 0.4,
            "setup_borderline_max": 1.2,
            "funding_concern_threshold": 0.8,
            "oi_conflict_score_threshold": 1.0,
            "liquidity_score_threshold": 3.0,
            "leverage_stress_score_threshold": 3.0,
        },
        "force_escalation": False,
        "auto_resume": True,
    }


def validate_experiment_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PaperTradingError("experiment configuration must be an object")
    _unknown_fields(value, EXPERIMENT_FIELDS, "experiment configuration")
    config = default_experiment_config()
    config.update(value)

    if config["execution_mode"] != EXECUTION_MODE:
        raise PaperTradingError("execution mode is permanently PAPER")
    if config["market_data_mode"] not in {"fixture", "binance_usdm"}:
        raise PaperTradingError("market_data_mode must be fixture or binance_usdm")
    symbols = config["symbols"]
    if (
        not isinstance(symbols, list)
        or not 1 <= len(symbols) <= 20
        or any(
            not isinstance(symbol, str)
            or not re.fullmatch(r"[A-Z0-9]{5,20}", symbol)
            for symbol in symbols
        )
        or len(set(symbols)) != len(symbols)
    ):
        raise PaperTradingError("symbols must be a unique list of uppercase contract symbols")
    if config["decision_timeframe"] != "15m" or config["context_timeframes"] != ["1h", "4h"]:
        raise PaperTradingError("EXP-001 uses a 15m decision timeframe with 1h and 4h context")

    config["starting_balance_usdt"] = _number(
        config["starting_balance_usdt"], "starting_balance_usdt", minimum=1
    )
    config["risk_per_trade"] = _number(config["risk_per_trade"], "risk_per_trade", minimum=0.0001)
    if config["risk_per_trade"] > 0.02:
        raise PaperTradingError("risk_per_trade cannot exceed 2%")
    config["max_positions"] = _integer(config["max_positions"], "max_positions", minimum=1, maximum=20)
    config["primary_leverage"] = _integer(
        config["primary_leverage"], "primary_leverage", minimum=1, maximum=10
    )
    if config["entry_order_type"] not in {"market", "limit"}:
        raise PaperTradingError("entry_order_type must be market or limit")
    config["minimum_notional_usdt"] = _number(
        config["minimum_notional_usdt"], "minimum_notional_usdt", minimum=0.1
    )
    if config["minimum_notional_usdt"] > 100_000:
        raise PaperTradingError("minimum_notional_usdt cannot exceed 100000")
    config["max_leverage"] = _integer(config["max_leverage"], "max_leverage", minimum=1, maximum=10)
    if config["primary_leverage"] > config["max_leverage"]:
        raise PaperTradingError("primary_leverage cannot exceed max_leverage")
    leverage = config["shadow_leverage"]
    if (
        not isinstance(leverage, list)
        or not leverage
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 1 or item > 10 for item in leverage)
        or len(set(leverage)) != len(leverage)
    ):
        raise PaperTradingError("shadow_leverage must contain unique integer cohorts from 1x to 10x")
    config["shadow_leverage"] = sorted(leverage)
    for field in ("max_daily_loss", "max_drawdown_stop"):
        config[field] = _number(config[field], field, minimum=0.001)
        if config[field] > 0.5:
            raise PaperTradingError(f"{field} cannot exceed 50%")
    config["max_consecutive_losses"] = _integer(
        config["max_consecutive_losses"], "max_consecutive_losses", minimum=1, maximum=100
    )
    for field in ("taker_fee_rate", "maker_fee_rate", "maintenance_margin_rate"):
        config[field] = _number(config[field], field, minimum=0)
        if config[field] > 0.02:
            raise PaperTradingError(f"{field} is outside the supported range")
    retention_days = config["market_data_retention_days"]
    if retention_days is not None and (
        isinstance(retention_days, bool)
        or not isinstance(retention_days, int)
        or retention_days not in {30, 90, 365}
    ):
        raise PaperTradingError(
            "market_data_retention_days must be null, 30, 90, or 365"
        )
    config["slippage_bps"] = _number(config["slippage_bps"], "slippage_bps", minimum=0)
    if config["slippage_bps"] > 500:
        raise PaperTradingError("slippage_bps cannot exceed 500")
    config["schedule_delay_seconds"] = _integer(
        config["schedule_delay_seconds"], "schedule_delay_seconds", minimum=0, maximum=900
    )
    config["monitor_interval_seconds"] = _integer(
        config["monitor_interval_seconds"], "monitor_interval_seconds", minimum=5, maximum=3600
    )
    config["minimum_signal_strength"] = _number(
        config["minimum_signal_strength"], "minimum_signal_strength", minimum=0
    )
    if config["minimum_signal_strength"] > 1:
        raise PaperTradingError("minimum_signal_strength cannot exceed 1")
    if config["primary_arm"] not in EXPERIMENT_ARMS:
        raise PaperTradingError("primary_arm is not a supported experiment arm")
    arms = config["evaluation_arms"]
    if not isinstance(arms, list) or not arms or any(arm not in EXPERIMENT_ARMS for arm in arms):
        raise PaperTradingError("evaluation_arms must select at least one supported arm")
    config["evaluation_arms"] = list(dict.fromkeys(arms))
    if config["primary_arm"] not in config["evaluation_arms"]:
        config["evaluation_arms"].append(config["primary_arm"])
    if config["fallback_policy"] not in {"DEFER", "GPT_FALLBACK", "SKIP"}:
        raise PaperTradingError("fallback_policy must be DEFER, GPT_FALLBACK, or SKIP")
    for field in (
        "signal_gate_enabled",
        "jev_enabled",
        "gpt_escalation_enabled",
        "force_escalation",
        "auto_resume",
    ):
        if not isinstance(config[field], bool):
            raise PaperTradingError(f"{field} must be boolean")
    for field in ("jev_provider_id", "gpt_provider_id"):
        if not isinstance(config[field], str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", config[field]):
            raise PaperTradingError(f"{field} is invalid")

    policy = config["escalation_policy"]
    policy_fields = {
        "version",
        "minimum_confidence",
        "conflict_threshold",
        "setup_borderline_min",
        "setup_borderline_max",
        "funding_concern_threshold",
        "oi_conflict_score_threshold",
        "liquidity_score_threshold",
        "leverage_stress_score_threshold",
    }
    if not isinstance(policy, dict) or set(policy) != policy_fields:
        raise PaperTradingError("escalation_policy is malformed")
    if policy["version"] != ESCALATION_POLICY_VERSION:
        raise PaperTradingError("escalation_policy version is unsupported")
    for field in (
        "minimum_confidence",
        "conflict_threshold",
        "funding_concern_threshold",
    ):
        policy[field] = _number(policy[field], f"escalation_policy.{field}", minimum=0)
        if policy[field] > 1:
            raise PaperTradingError(f"escalation_policy.{field} cannot exceed 1")
    for field in ("setup_borderline_min", "setup_borderline_max"):
        policy[field] = _number(policy[field], f"escalation_policy.{field}", minimum=0)
        if policy[field] > 4:
            raise PaperTradingError(f"escalation_policy.{field} cannot exceed the Jev Score range")
    policy["liquidity_score_threshold"] = _number(
        policy["liquidity_score_threshold"], "escalation_policy.liquidity_score_threshold", minimum=0
    )
    if policy["liquidity_score_threshold"] > 4:
        raise PaperTradingError("escalation_policy.liquidity_score_threshold cannot exceed 4")
    for field in ("oi_conflict_score_threshold", "leverage_stress_score_threshold"):
        policy[field] = _number(policy[field], f"escalation_policy.{field}", minimum=0)
        if policy[field] > 4:
            raise PaperTradingError(f"escalation_policy.{field} cannot exceed 4")
    if policy["setup_borderline_min"] > policy["setup_borderline_max"]:
        raise PaperTradingError("escalation setup score range is invalid")
    return config


def validate_provider_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PaperTradingError("provider configuration must be an object")
    _unknown_fields(value, PROVIDER_FIELDS, "provider configuration")
    required = {"provider_id", "kind", "display_name", "model", "enabled"}
    if not required <= set(value):
        raise PaperTradingError("provider configuration is missing required fields")
    provider_id = value["provider_id"]
    if not isinstance(provider_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", provider_id):
        raise PaperTradingError("provider_id is invalid")
    kind = value["kind"]
    if kind not in PROVIDER_KINDS:
        raise PaperTradingError("provider kind is unsupported")
    display_name = value["display_name"]
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 80:
        raise PaperTradingError("display_name must contain 1 to 80 characters")
    model = value["model"]
    if not isinstance(model, str) or not model.strip() or len(model) > 160:
        raise PaperTradingError("model must contain 1 to 160 characters")
    if not isinstance(value["enabled"], bool):
        raise PaperTradingError("enabled must be boolean")

    result = {
        "provider_id": provider_id,
        "kind": kind,
        "display_name": display_name.strip(),
        "model": model.strip(),
        "enabled": value["enabled"],
        "base_url": value.get("base_url", "").strip() if isinstance(value.get("base_url", ""), str) else "",
        "timeout_seconds": value.get("timeout_seconds", 20),
        "reasoning_effort": value.get("reasoning_effort", "high"),
        "auth_scheme": value.get("auth_scheme", "bearer"),
        "credential_env": value.get("credential_env") or None,
        "pricing": value.get("pricing"),
    }
    if not 1 <= _number(result["timeout_seconds"], "timeout_seconds", minimum=1) <= 120:
        raise PaperTradingError("timeout_seconds must be between 1 and 120")
    result["timeout_seconds"] = float(result["timeout_seconds"])
    if result["reasoning_effort"] not in {"low", "medium", "high", "max"}:
        raise PaperTradingError("reasoning_effort must be low, medium, high, or max")
    if result["auth_scheme"] not in {"bearer", "raw"}:
        raise PaperTradingError("auth_scheme must be bearer or raw")
    env_ref = result["credential_env"]
    if env_ref is not None and (
        not isinstance(env_ref, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", env_ref)
    ):
        raise PaperTradingError("credential_env must be an environment-variable name")
    fixture = kind.startswith("fixture_")
    if fixture and (result["base_url"] or env_ref):
        raise PaperTradingError("fixture providers do not accept external endpoints or credentials")
    if not fixture and not result["base_url"]:
        raise PaperTradingError("base_url is required for external providers")
    if result["base_url"]:
        parsed = urlsplit(result["base_url"])
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or len(result["base_url"]) > 500
        ):
            raise PaperTradingError("base_url must be a valid endpoint without embedded credentials")
        query_keys = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        if query_keys & {"api_key", "apikey", "token", "access_token", "secret", "authorization", "password"}:
            raise PaperTradingError("base_url must not contain credential query parameters")
        if query_keys - {"api-version", "api_version", "version"}:
            raise PaperTradingError("base_url query parameters are limited to API-version metadata")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise PaperTradingError("external provider endpoints must use HTTPS")
    pricing = result["pricing"]
    if pricing is not None:
        if not isinstance(pricing, dict) or set(pricing) != {
            "version",
            "input_per_million",
            "output_per_million",
        }:
            raise PaperTradingError("pricing must contain version and per-million rates")
        if (
            not isinstance(pricing["version"], str)
            or not pricing["version"].strip()
            or len(pricing["version"]) > 80
        ):
            raise PaperTradingError("pricing version is invalid")
        for field in ("input_per_million", "output_per_million"):
            pricing[field] = _number(pricing[field], f"pricing.{field}", minimum=0)
        result["pricing"] = {
            "version": pricing["version"].strip(),
            "input_per_million": pricing["input_per_million"],
            "output_per_million": pricing["output_per_million"],
        }
    return result


def public_provider_config(
    value: dict[str, Any],
    *,
    credential_present: bool | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = value["kind"]
    if kind.startswith("fixture_"):
        credential_status = "not_required"
    elif credential_present is True:
        credential_status = "credential_reference_configured"
    else:
        credential_status = "not_configured"
    return {
        "provider_id": value["provider_id"],
        "kind": kind,
        "display_name": value["display_name"],
        "base_url": value.get("base_url", ""),
        "model": value["model"],
        "auth_scheme": value.get("auth_scheme", "bearer"),
        "timeout_seconds": value.get("timeout_seconds", 20),
        "reasoning_effort": value.get("reasoning_effort", "high"),
        "enabled": value["enabled"],
        "credential_status": credential_status,
        "pricing": value.get("pricing"),
        "last_validation_status": (validation or {}).get(
            "status", "not_tested"
        ),
        "last_validated_at": (validation or {}).get("validated_at"),
        "last_validation_latency_ms": (validation or {}).get("latency_ms"),
    }


@dataclass(frozen=True)
class TradingIntent:
    """Validated recommendation; quantity, leverage, and execution authority stay deterministic."""

    action: str
    symbol: str
    side: str
    order_type: str
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    reduce_only: bool
    reduce_fraction: float | None
    position_id: str | None
    reason: str
    source_arm: str
    as_of: str
    schema_version: str = INTENT_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Any) -> "TradingIntent":
        if not isinstance(value, dict):
            raise PaperTradingError("TradingIntent must be an object")
        _unknown_fields(value, INTENT_FIELDS, "TradingIntent")
        required = INTENT_FIELDS - {"reduce_fraction", "position_id"}
        if not required <= set(value):
            raise PaperTradingError("TradingIntent is missing required fields")
        if value["schema_version"] != INTENT_SCHEMA_VERSION:
            raise PaperTradingError("TradingIntent schema version is unsupported")
        action = value["action"]
        if action not in {"open", "close", "reduce"}:
            raise PaperTradingError("TradingIntent action is unsupported")
        symbol = value["symbol"]
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{5,20}", symbol):
            raise PaperTradingError("TradingIntent symbol is invalid")
        side = value["side"]
        if side not in {"long", "short"}:
            raise PaperTradingError("TradingIntent side is unsupported")
        order_type = value["order_type"]
        if order_type not in {"market", "limit"}:
            raise PaperTradingError("TradingIntent order_type is unsupported")
        if not isinstance(value["reduce_only"], bool):
            raise PaperTradingError("TradingIntent reduce_only must be boolean")
        reason = value["reason"]
        source_arm = value["source_arm"]
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise PaperTradingError("TradingIntent reason must contain 1 to 500 characters")
        if source_arm not in EXPERIMENT_ARMS:
            raise PaperTradingError("TradingIntent source_arm is unsupported")
        as_of = iso_utc(parse_utc(value["as_of"], "TradingIntent.as_of"))
        if action == "open":
            if value["reduce_only"] or value.get("reduce_fraction") is not None or value.get("position_id"):
                raise PaperTradingError("an opening intent cannot be reduce-only")
            entry = _number(value["entry_price"], "TradingIntent.entry_price", minimum=0)
            stop = _number(value["stop_price"], "TradingIntent.stop_price", minimum=0)
            target = _number(value["target_price"], "TradingIntent.target_price", minimum=0)
            if not (stop < entry < target if side == "long" else target < entry < stop):
                raise PaperTradingError("TradingIntent stop and target must bracket its entry")
            fraction = None
            position_id = None
        else:
            if not value["reduce_only"]:
                raise PaperTradingError("close and reduce intents must be reduce-only")
            if value["entry_price"] is not None or value["stop_price"] is not None or value["target_price"] is not None:
                raise PaperTradingError("close and reduce intents cannot set new entry or risk levels")
            if order_type != "market":
                raise PaperTradingError("close and reduce intents must use market execution")
            position_id = value.get("position_id")
            if position_id is not None and (
                not isinstance(position_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", position_id)
            ):
                raise PaperTradingError("TradingIntent position_id is invalid")
            fraction = 1.0 if action == "close" else _number(
                value.get("reduce_fraction"), "TradingIntent.reduce_fraction", minimum=0
            )
            if fraction <= 0 or fraction > 1:
                raise PaperTradingError("TradingIntent.reduce_fraction must be greater than 0 and at most 1")
            entry = stop = target = None
        return cls(
            action=action,
            symbol=symbol,
            side=side,
            order_type=order_type,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            reduce_only=value["reduce_only"],
            reduce_fraction=fraction,
            position_id=position_id,
            reason=reason.strip(),
            source_arm=source_arm,
            as_of=as_of,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def empty_intent(
    symbol: str,
    side: str,
    as_of: datetime,
    source_arm: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": INTENT_SCHEMA_VERSION,
        "action": "open",
        "symbol": symbol,
        "side": side,
        "order_type": "market",
        "entry_price": None,
        "stop_price": None,
        "target_price": None,
        "reduce_only": False,
        "reduce_fraction": None,
        "position_id": None,
        "reason": reason,
        "source_arm": source_arm,
        "as_of": iso_utc(as_of),
    }
