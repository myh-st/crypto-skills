"""Jev typed decisions, deterministic routing, and Responses-compatible adapters."""

from __future__ import annotations

import json
import math
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from .contracts import digest
from .paper_contracts import (
    ESCALATION_POLICY_VERSION,
    JEV_VECTOR_SCHEMA_VERSION,
    QUESTION_SCHEMA_VERSION,
    PaperTradingError,
    iso_utc,
    parse_utc,
)
from .paper_market import MarketSnapshot


MAX_AI_RESPONSE_BYTES = 2 * 1024 * 1024
TYPE_SAFE_DEFAULT_URL = "https://api.typesafe.ai"
OPENAI_DEFAULT_URL = "https://api.openai.com/v1"
INTENT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["open", "no_trade"]},
        "symbol": {"type": "string"},
        "side": {"type": ["string", "null"], "enum": ["long", "short", None]},
        "entry_price": {"type": ["number", "null"]},
        "stop_price": {"type": ["number", "null"]},
        "target_price": {"type": ["number", "null"]},
        "reason": {"type": "string"},
    },
    "required": [
        "action",
        "symbol",
        "side",
        "entry_price",
        "stop_price",
        "target_price",
        "reason",
    ],
    "additionalProperties": False,
}


class AIProviderError(PaperTradingError):
    """Provider failure with no response bodies, credentials, or prompt content."""


PostTransport = Callable[[str, dict[str, str], bytes, float], Any]
SAFE_RESPONSE_HEADERS = ("x-request-id", "apim-request-id", "x-ms-region", "request-id")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post_json(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirectHandler)
    with opener.open(request, timeout=timeout) as response:
        safe_headers = {
            key.lower(): value[:120]
            for key, value in response.headers.items()
            if key.lower() in SAFE_RESPONSE_HEADERS
        }
        return response.read(MAX_AI_RESPONSE_BYTES + 1), safe_headers


def _credentials(
    provider: dict[str, Any],
    environ: dict[str, str] | None = None,
    resolver: Any | None = None,
) -> str:
    """Resolve from the OS credential store first, then the configured env reference."""

    secret_id = provider.get("credential_secret")
    if resolver is not None:
        value, _source = resolver.resolve(secret_id=secret_id, env_name=provider.get("credential_env"))
        if value:
            return value
        raise AIProviderError("provider credential is not stored; no external model call was made")
    name = provider.get("credential_env")
    values = os.environ if environ is None else environ
    if not isinstance(name, str) or not name or not isinstance(values.get(name), str) or not values[name].strip():
        raise AIProviderError("provider credential reference is unset; no external model call was made")
    return values[name].strip()


def _typesafe_authorization(provider: dict[str, Any], token: str) -> str:
    if provider.get("auth_scheme", "bearer") == "raw":
        return token
    return "Bearer " + token


def encoded_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _safe_post(
    transport: PostTransport,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
    provider_name: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST JSON and return the parsed object; ``meta`` receives safe transport facts only."""

    if meta is not None:
        meta.setdefault("request_sent", False)
    try:
        body = encoded_payload(payload)
        if meta is not None:
            meta["request_sent"] = True
            meta["request_bytes"] = len(body)
        response = transport(
            url,
            headers,
            body,
            timeout,
        )
    except urllib.error.HTTPError as exc:
        if meta is not None:
            meta["error_kind"] = "http"
            meta["http_status"] = exc.code
            try:
                request_id = exc.headers.get("x-request-id") if exc.headers else None
            except Exception:
                request_id = None
            if isinstance(request_id, str):
                meta["provider_request_id"] = request_id[:120]
        raise AIProviderError(f"{provider_name} returned HTTP {exc.code}") from None
    except (TimeoutError, socket.timeout):
        if meta is not None:
            meta["error_kind"] = "timeout"
        raise AIProviderError(f"{provider_name} request timed out") from None
    except (urllib.error.URLError, OSError):
        if meta is not None:
            meta["error_kind"] = "network"
        raise AIProviderError(f"{provider_name} request failed") from None
    except AIProviderError:
        raise
    except Exception:
        if meta is not None:
            meta["error_kind"] = "transport"
        raise AIProviderError(f"{provider_name} request failed") from None
    if isinstance(response, tuple) and len(response) == 2:
        response, response_headers = response
        if meta is not None and isinstance(response_headers, dict):
            request_id = response_headers.get("x-request-id") or response_headers.get("apim-request-id")
            if isinstance(request_id, str):
                meta["provider_request_id"] = request_id[:120]
            region = response_headers.get("x-ms-region")
            if isinstance(region, str):
                meta["provider_region"] = region[:60]
    if not isinstance(response, (bytes, bytearray)) or len(response) > MAX_AI_RESPONSE_BYTES:
        raise AIProviderError(f"{provider_name} returned an invalid response")
    try:
        result = json.loads(response)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AIProviderError(f"{provider_name} returned invalid JSON") from None
    if not isinstance(result, dict):
        raise AIProviderError(f"{provider_name} returned a malformed response")
    return result


def build_jev_questions(*, include_open_interest: bool, include_spread: bool) -> dict[str, dict[str, Any]]:
    questions: dict[str, dict[str, Any]] = {
        "market_regime": {
            "type": "choice",
            "instructions": "Classify the tradeable regime from the closed-candle evidence.",
            "criteria": {
                "bull": "Directional bullish market structure",
                "bear": "Directional bearish market structure",
                "sideways": "Range-bound or mean-reverting conditions",
                "unstable": "Conflicting or unusually unstable conditions",
            },
        },
        "trend_alignment": {
            "type": "score",
            "instructions": "Rate alignment between the 15m setup and 1h/4h context.",
            "criteria": ["conflicting", "weak", "mixed", "aligned", "strongly aligned"],
        },
        "momentum_quality": {
            "type": "score",
            "instructions": "Rate the quality of confirmed price momentum without forecasting returns.",
            "criteria": ["poor", "weak", "acceptable", "good", "excellent"],
        },
        "breakout_valid": {
            "type": "noul",
            "instructions": "The latest closed 15m bar confirms a meaningful breakout.",
            "criteria": {"true": "Close is accepted beyond the prior range", "false": "No confirmed breakout"},
        },
        "pullback_quality": {
            "type": "score",
            "instructions": "Rate whether a pullback offers a structurally grounded setup.",
            "criteria": ["poor", "weak", "acceptable", "good", "excellent"],
        },
        "volume_confirmation": {
            "type": "score",
            "instructions": "Rate whether observed candle volume confirms the price move.",
            "criteria": ["poor", "weak", "acceptable", "good", "excellent"],
        },
        "leverage_stress": {
            "type": "score",
            "instructions": "Rate observed leverage fragility; higher means more stress.",
            "criteria": ["low", "contained", "elevated", "high", "extreme"],
        },
        "funding_concern": {
            "type": "noul",
            "instructions": "Observed funding is unusually crowded against the proposed setup.",
        },
        "signal_conflict": {
            "type": "noul",
            "instructions": "Important observed evidence materially conflicts with the proposed direction.",
        },
        "setup_quality": {
            "type": "score",
            "instructions": "Rate setup quality from evidence confluence, not a probability of profit.",
            "criteria": ["poor", "weak", "borderline", "good", "excellent"],
        },
        "escalation_needed": {
            "type": "noul",
            "instructions": "This case warrants deeper multi-factor review before creating an intent.",
        },
    }
    if include_open_interest:
        questions["oi_confirmation"] = {
            "type": "score",
            "instructions": "Rate whether the point-in-time open-interest change confirms the move.",
            "criteria": ["conflicts", "weak", "mixed", "supports", "strongly supports"],
        }
    if include_spread:
        questions["liquidity_risk"] = {
            "type": "score",
            "instructions": "Rate execution liquidity risk from the observed spread.",
            "criteria": ["low", "contained", "elevated", "high", "extreme"],
        }
    return questions


def jev_state(
    snapshot: MarketSnapshot,
    features: dict[str, Any],
    portfolio: dict[str, Any],
) -> dict[str, Any]:
    """Project only point-in-time-safe inputs; future bars and labels are never forwarded."""

    safe_features = {
        key: value
        for key, value in features.items()
        if key not in {"future_bars", "outcome", "prediction", "label"}
        and isinstance(value, (str, int, float, bool, type(None)))
    }
    safe_snapshot = {
        "symbol": snapshot.symbol,
        "data_origin": snapshot.data_origin,
        "as_of": snapshot.as_of,
        "data_cutoff": snapshot.data_cutoff,
        "last_15m_candles": snapshot.candles_15m[-32:],
        "last_1h_candles": snapshot.candles_1h[-12:],
        "last_4h_candles": snapshot.candles_4h[-8:],
        "funding_rate": snapshot.funding_rate,
        "funding_observed_at": snapshot.funding_observed_at,
        "open_interest_change_1h": snapshot.open_interest_change_1h,
        "open_interest_observed_at": snapshot.open_interest_observed_at,
        "spread_bps": snapshot.spread_bps,
        **_safe_market_context(snapshot.market_context),
    }
    portfolio_state = {
        "has_position": bool(portfolio.get("has_position")),
        "open_position_count": int(portfolio.get("open_position_count", 0)),
        "exposure_pct": float(portfolio.get("exposure_pct", 0.0)),
    }
    state = {
        "schema_version": "paper-market-state.v1",
        "symbol": snapshot.symbol,
        "decision_tf": "15m",
        "context_tf": ["1h", "4h"],
        "market": safe_snapshot,
        "features": safe_features,
        "quant": {
            "direction": features.get("quant_direction", "none"),
            "signal_strength": features.get("signal_strength", 0.0),
            "trigger": features.get("signal_trigger", "none"),
        },
        "portfolio": portfolio_state,
    }
    state["snapshot_hash"] = digest(state)
    return state


def parse_jev_response(
    response: Any,
    questions: dict[str, dict[str, Any]],
    *,
    snapshot_hash: str,
    question_schema_version: str = QUESTION_SCHEMA_VERSION,
    latency_ms: float = 0.0,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(response, dict) or not isinstance(response.get("model"), str):
        raise AIProviderError("Jev response is missing its model")
    raw_answers = response.get("answers")
    if not isinstance(raw_answers, dict) or set(raw_answers) != set(questions):
        raise AIProviderError("Jev response does not contain the requested typed answers")
    normalized: dict[str, dict[str, Any]] = {}
    for question_id, question in questions.items():
        raw = raw_answers.get(question_id)
        if not isinstance(raw, dict) or raw.get("type") != question["type"]:
            raise AIProviderError("Jev response answer type is malformed")
        kind = question["type"]
        if kind == "choice":
            value = raw.get("choice")
            if value not in question["criteria"]:
                raise AIProviderError("Jev Choice answer is unsupported")
        elif kind == "score":
            value = raw.get("score")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AIProviderError("Jev Score answer is malformed")
            if not math.isfinite(float(value)) or not 0 <= float(value) < len(question["criteria"]):
                raise AIProviderError("Jev Score answer is outside its rubric")
            value = float(value)
        elif kind == "noul":
            value = raw.get("noul")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AIProviderError("Jev Noul answer is malformed")
            if not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
                raise AIProviderError("Jev Noul answer is outside the 0–1 range")
            value = float(value)
        else:
            raise AIProviderError("Jev question type is unsupported")

        answer: dict[str, Any] = {"type": kind, "value": value}
        confidence = raw.get("confidence")
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise AIProviderError("Jev confidence is malformed")
            if not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1:
                raise AIProviderError("Jev confidence is outside the 0–1 range")
            answer["confidence"] = float(confidence)
        probabilities = raw.get("probabilities")
        if probabilities is not None:
            if not isinstance(probabilities, dict) or not probabilities:
                raise AIProviderError("Jev probability distribution is malformed")
            parsed_probabilities: dict[str, float] = {}
            for key, probability in probabilities.items():
                if not isinstance(key, str) or isinstance(probability, bool) or not isinstance(
                    probability, (int, float)
                ):
                    raise AIProviderError("Jev probability distribution is malformed")
                numeric = float(probability)
                if not math.isfinite(numeric) or not 0 <= numeric <= 1:
                    raise AIProviderError("Jev probability is outside the 0–1 range")
                parsed_probabilities[key] = numeric
            if abs(sum(parsed_probabilities.values()) - 1.0) > 0.02:
                raise AIProviderError("Jev probability distribution does not sum to one")
            answer["probabilities"] = parsed_probabilities
        if kind in {"choice", "score"}:
            if confidence is None or probabilities is None:
                raise AIProviderError("Jev Choice and Score require confidence and probabilities")
            expected_keys = (
                set(question["criteria"])
                if kind == "choice"
                else {str(index) for index in range(len(question["criteria"]))}
            )
            if set(answer["probabilities"]) != expected_keys:
                raise AIProviderError("Jev probability keys do not match the configured rubric")
        if kind == "score":
            legend = raw.get("legend")
            expected_legend = {
                str(index): description
                for index, description in enumerate(question["criteria"])
            }
            if not isinstance(legend, dict) or set(legend) != set(expected_legend):
                raise AIProviderError("Jev Score legend is malformed")
            if legend != expected_legend:
                raise AIProviderError("Jev Score legend does not match the configured rubric")
            answer["legend"] = {key: legend[key] for key in sorted(legend)}
        normalized[question_id] = answer

    usage = response.get("usage")
    normalized_usage = None
    if usage is not None:
        if not isinstance(usage, dict):
            raise AIProviderError("Jev usage metadata is malformed")
        normalized_usage = {}
        for field in ("input_tokens", "output_tokens"):
            count = usage.get(field)
            if count is not None and (
                isinstance(count, bool) or not isinstance(count, int) or count < 0
            ):
                raise AIProviderError("Jev usage token count is malformed")
            normalized_usage[field] = count
    latency = float(latency_ms)
    if not math.isfinite(latency) or latency < 0:
        raise AIProviderError("Jev latency is invalid")
    return {
        "schema_version": JEV_VECTOR_SCHEMA_VERSION,
        "snapshot_hash": snapshot_hash,
        "model": response["model"],
        "question_schema_version": question_schema_version,
        "answers": normalized,
        "usage": normalized_usage,
        "latency_ms": latency,
        "observed_at": iso_utc(observed_at or datetime.now(timezone.utc)),
    }


def _type_safe_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/") or TYPE_SAFE_DEFAULT_URL
    parsed = urllib.parse.urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise AIProviderError("TypeSafe base URL must be an HTTPS URL without credentials or query data")
    if parsed.path.rstrip("/").endswith("/systemone"):
        return base
    return f"{base}/v1/systemone" if not parsed.path.rstrip("/").endswith("/v1") else f"{base}/systemone"


class JevAdapter:
    """TypeSafe System-One API adapter; one request carries its atomic question set."""

    def __init__(
        self,
        provider: dict[str, Any],
        *,
        environ: dict[str, str] | None = None,
        transport: PostTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        resolver: Any | None = None,
    ) -> None:
        self.provider = provider
        self.environ = environ
        self.resolver = resolver
        self._transport = transport or _post_json
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.last_call: dict[str, Any] = {}

    def _headers(self) -> dict[str, str]:
        token = _credentials(self.provider, self.environ, self.resolver)
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": _typesafe_authorization(self.provider, token),
        }

    def evaluation_payload(
        self,
        snapshot: MarketSnapshot,
        features: dict[str, Any],
        portfolio: dict[str, Any],
    ) -> dict[str, Any]:
        questions = build_jev_questions(
            include_open_interest=snapshot.open_interest_change_1h is not None,
            include_spread=snapshot.spread_bps is not None,
        )
        return {"state": jev_state(snapshot, features, portfolio), "model": self.provider["model"], "questions": questions}

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        features: dict[str, Any],
        portfolio: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self.evaluation_payload(snapshot, features, portfolio)
        headers = self._headers()
        meta: dict[str, Any] = {}
        self.last_call = meta
        started = time.perf_counter()
        response = _safe_post(
            self._transport,
            _type_safe_url(self.provider.get("base_url", TYPE_SAFE_DEFAULT_URL)),
            headers,
            payload,
            float(self.provider.get("timeout_seconds", 20)),
            "TypeSafe Jev",
            meta,
        )
        latency = (time.perf_counter() - started) * 1000
        meta["latency_ms"] = latency
        meta["raw_usage"] = response.get("usage")
        vector = parse_jev_response(
            response,
            payload["questions"],
            snapshot_hash=payload["state"]["snapshot_hash"],
            latency_ms=latency,
            observed_at=self._clock(),
        )
        vector["provider_request_id"] = meta.get("provider_request_id")
        return vector

    def test_connection(self) -> dict[str, Any]:
        questions = {
            "connection_noul": {
                "type": "noul",
                "instructions": "The supplied test sentence is about market data.",
            },
            "connection_choice": {
                "type": "choice",
                "instructions": "Choose the sentence category.",
                "criteria": {"market": "A market-data statement", "other": "Anything else"},
            },
            "connection_score": {
                "type": "score",
                "instructions": "Rate whether the sentence is relevant to a test.",
                "criteria": ["unrelated", "partly relevant", "relevant"],
            },
        }
        payload = {
            "state": {"purpose": "provider connection validation", "text": "Market data test."},
            "model": self.provider["model"],
            "questions": questions,
        }
        headers = self._headers()
        meta: dict[str, Any] = {}
        self.last_call = meta
        started = time.perf_counter()
        response = _safe_post(
            self._transport,
            _type_safe_url(self.provider.get("base_url", TYPE_SAFE_DEFAULT_URL)),
            headers,
            payload,
            float(self.provider.get("timeout_seconds", 20)),
            "TypeSafe Jev",
            meta,
        )
        latency = (time.perf_counter() - started) * 1000
        meta["latency_ms"] = latency
        meta["raw_usage"] = response.get("usage")
        vector = parse_jev_response(
            response,
            questions,
            snapshot_hash=digest(payload["state"]),
            latency_ms=latency,
            observed_at=self._clock(),
        )
        answers = vector["answers"]
        return {
            "ok": True,
            "provider_id": self.provider["provider_id"],
            "requested_model": self.provider["model"],
            "model": vector["model"],
            "latency_ms": vector["latency_ms"],
            "validated_question_types": ["noul", "choice", "score"],
            "typed_outputs": {
                "choice": {
                    "value": answers["connection_choice"]["value"],
                    "confidence": answers["connection_choice"].get("confidence"),
                    "probabilities": answers["connection_choice"].get("probabilities"),
                },
                "score": {
                    "value": answers["connection_score"]["value"],
                    "confidence": answers["connection_score"].get("confidence"),
                    "probabilities": answers["connection_score"].get("probabilities"),
                },
                "noul": {"value": answers["connection_noul"]["value"]},
            },
            "usage": vector["usage"],
            "provider_request_id": meta.get("provider_request_id"),
            "real_external_call": True,
        }


class FixtureJevProvider:
    """Deterministic local typed answers for offline UI and integration tests."""

    provider_id = "fixture-jev"
    model = "fixture-jev-v1"

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        features: dict[str, Any],
        portfolio: dict[str, Any],
    ) -> dict[str, Any]:
        questions = build_jev_questions(
            include_open_interest=snapshot.open_interest_change_1h is not None,
            include_spread=snapshot.spread_bps is not None,
        )
        direction = features.get("quant_direction", "none")
        eligible = direction in {"long", "short"} and features.get("gate_eligible") is True
        regime = "bull" if direction == "long" else "bear" if direction == "short" else "sideways"
        uncertain = not eligible or features.get("signal_strength", 0) < 0.7
        def score_answer(question_id: str, score: float, confidence: float) -> dict[str, Any]:
            probabilities = {str(level): 0.05 for level in range(5)}
            probabilities[str(int(score))] = 0.8
            for key in probabilities:
                if key != str(int(score)):
                    probabilities[key] = 0.05
            return {
                "type": "score",
                "score": float(score),
                "confidence": confidence,
                "probabilities": probabilities,
                "legend": {
                    str(index): label
                    for index, label in enumerate(questions[question_id]["criteria"])
                },
            }

        answers: dict[str, dict[str, Any]] = {
            "market_regime": {
                "type": "choice",
                "choice": regime,
                "confidence": 0.84 if eligible else 0.55,
                "probabilities": (
                    {
                        regime: 0.84,
                        ("bear" if regime == "bull" else "bull"): 0.05,
                        "sideways": 0.06,
                        "unstable": 0.05,
                    }
                    if eligible
                    else {"sideways": 0.55, "bull": 0.25, "bear": 0.1, "unstable": 0.1}
                ),
            },
            "trend_alignment": score_answer(
                "trend_alignment", 3.0 if eligible else 1.0, 0.83 if eligible else 0.5
            ),
            "momentum_quality": score_answer(
                "momentum_quality", 3.0 if eligible else 1.0, 0.82 if eligible else 0.5
            ),
            "breakout_valid": {
                "type": "noul",
                "noul": 0.88 if features.get("signal_trigger") == "breakout" else 0.35,
            },
            "pullback_quality": score_answer("pullback_quality", 2.0 if eligible else 1.0, 0.7),
            "volume_confirmation": score_answer(
                "volume_confirmation",
                3.0 if features.get("volume_zscore", 0) > 0.5 else 2.0,
                0.78,
            ),
            "leverage_stress": score_answer("leverage_stress", 1.0, 0.72),
            "funding_concern": {
                "type": "noul",
                "noul": 0.15 if snapshot.funding_rate is not None else 0.3,
            },
            "signal_conflict": {
                "type": "noul",
                "noul": 0.12 if eligible else 0.7,
            },
            "setup_quality": score_answer(
                "setup_quality", 3.0 if eligible else 1.0, 0.8 if eligible else 0.5
            ),
            "escalation_needed": {
                "type": "noul",
                "noul": 0.75 if uncertain else 0.12,
            },
        }
        if "oi_confirmation" in questions:
            answers["oi_confirmation"] = score_answer(
                "oi_confirmation",
                3.0 if snapshot.open_interest_change_1h is not None else 2.0,
                0.65,
            )
        if "liquidity_risk" in questions:
            answers["liquidity_risk"] = score_answer("liquidity_risk", 1.0, 0.8)
        response = {
            "model": self.model,
            "answers": answers,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        return parse_jev_response(
            response,
            questions,
            snapshot_hash=jev_state(snapshot, features, portfolio)["snapshot_hash"],
            latency_ms=0,
            observed_at=parse_utc(snapshot.as_of, "snapshot.as_of"),
        )

    def test_connection(self) -> dict[str, Any]:
        return {
            "ok": True,
            "provider_id": self.provider_id,
            "model": self.model,
            "latency_ms": 0,
            "validated_question_types": ["noul", "choice", "score"],
            "fixture": True,
            "message": "Local fixture adapter; no external request was made.",
        }


def route_escalation(
    vector: dict[str, Any],
    features: dict[str, Any],
    portfolio: dict[str, Any],
    policy: dict[str, Any],
    *,
    force: bool = False,
    arm_requires_luna: bool = False,
) -> dict[str, Any]:
    if vector.get("schema_version") != JEV_VECTOR_SCHEMA_VERSION:
        raise AIProviderError("escalation router received an unsupported Jev decision vector")
    answers = vector["answers"]
    reasons: list[str] = []
    if force:
        reasons.append("manual_force")
    if arm_requires_luna:
        reasons.append("experiment_arm_requires_luna")
    if answers.get("market_regime", {}).get("value") == "unstable":
        reasons.append("unstable_regime")
    if answers.get("signal_conflict", {}).get("value", 0) >= policy["conflict_threshold"]:
        reasons.append("signal_conflict")
    if answers.get("escalation_needed", {}).get("value", 0) >= 0.7:
        reasons.append("jev_escalation_evidence")
    critical_confidences = [
        answer.get("confidence")
        for key, answer in answers.items()
        if key in {"market_regime", "trend_alignment", "setup_quality"}
        and answer.get("confidence") is not None
    ]
    if critical_confidences and min(critical_confidences) < policy["minimum_confidence"]:
        reasons.append("low_critical_confidence")
    quant = features.get("quant_direction", "none")
    regime = answers.get("market_regime", {}).get("value")
    jev_direction = "long" if regime == "bull" else "short" if regime == "bear" else "none"
    if quant in {"long", "short"} and jev_direction in {"long", "short"} and quant != jev_direction:
        reasons.append("quant_jev_disagreement")
    setup = answers.get("setup_quality", {}).get("value")
    if isinstance(setup, (int, float)) and policy["setup_borderline_min"] <= setup <= policy["setup_borderline_max"]:
        reasons.append("borderline_setup")
    if answers.get("funding_concern", {}).get("value", 0) >= policy["funding_concern_threshold"]:
        reasons.append("funding_conflict")
    oi_confirmation = answers.get("oi_confirmation", {}).get("value")
    if (
        isinstance(oi_confirmation, (int, float))
        and oi_confirmation <= policy["oi_conflict_score_threshold"]
    ):
        reasons.append("oi_conflict")
    liquidity = answers.get("liquidity_risk", {}).get("value")
    if isinstance(liquidity, (int, float)) and liquidity >= policy["liquidity_score_threshold"]:
        reasons.append("liquidity_risk")
    leverage_stress = answers.get("leverage_stress", {}).get("value")
    if (
        isinstance(leverage_stress, (int, float))
        and leverage_stress >= policy["leverage_stress_score_threshold"]
    ):
        reasons.append("leverage_stress")
    if portfolio.get("reassess_open_position"):
        reasons.append("open_position_reassessment")
    return {
        "policy_version": policy.get("version", ESCALATION_POLICY_VERSION),
        "escalate": bool(reasons),
        "reasons": reasons,
        "jev_suggestion_is_non_authoritative": True,
    }


def _safe_snapshot(snapshot: MarketSnapshot) -> dict[str, Any]:
    value = snapshot.to_dict()
    return {
        "symbol": value["symbol"],
        "as_of": value["as_of"],
        "data_cutoff": value["data_cutoff"],
        "data_origin": value["data_origin"],
        "candles_15m": value["candles_15m"][-32:],
        "candles_1h": value["candles_1h"][-12:],
        "candles_4h": value["candles_4h"][-8:],
        "funding_rate": value["funding_rate"],
        "funding_observed_at": value["funding_observed_at"],
        "open_interest_change_1h": value["open_interest_change_1h"],
        "open_interest_observed_at": value["open_interest_observed_at"],
        "spread_bps": value["spread_bps"],
        **_safe_market_context(value.get("market_context")),
    }


def _safe_market_context(context: Any) -> dict[str, Any]:
    """Point-in-time exchange context observed at as_of (never later bars or outcomes)."""

    if not isinstance(context, dict):
        return {}
    keys = (
        "last_price",
        "mark_price",
        "index_price",
        "best_bid",
        "best_ask",
        "spread_bps",
        "funding_rate_current",
        "change_24h_pct",
        "volume_24h_quote",
    )
    return {"market_context": {key: context.get(key) for key in keys}}


def load_skill_bundle(root: Path | None = None) -> str:
    repository_root = root or Path(__file__).resolve().parents[1]
    paths = [
        repository_root / "skills/crypto-market-trading-analysis/SKILL.md",
        repository_root / "skills/crypto-market-trading-analysis/references/technical-analysis.md",
        repository_root / "skills/crypto-market-trading-analysis/references/derivatives.md",
        repository_root / "skills/crypto-market-trading-analysis/references/portfolio-risk.md",
        repository_root / "skills/crypto-market-trading-analysis/references/point-in-time.md",
        repository_root / "skills/crypto-market-trading-analysis/references/evidence-ledger.md",
    ]
    chunks = []
    for path in paths:
        try:
            chunks.append(f"## {path.name}\n{path.read_text(encoding='utf-8')}")
        except OSError as exc:
            raise AIProviderError("crypto trading skill context is unavailable") from exc
    return "\n\n".join(chunks)


def build_gpt_input(
    snapshot: MarketSnapshot,
    features: dict[str, Any],
    *,
    jev_vector: dict[str, Any] | None,
    portfolio: dict[str, Any],
) -> dict[str, Any]:
    safe_features = {
        key: features[key]
        for key in (
            "ema12",
            "ema26",
            "ema12_1h",
            "ema26_1h",
            "ema12_4h",
            "ema26_4h",
            "market_mark_price",
            "feature_history_hash",
            "feature_history_15m_bars",
            "feature_history_1h_bars",
            "feature_history_4h_bars",
            "atr",
            "atr_pct",
            "rsi14",
            "volume_zscore",
            "distance_to_breakout",
            "quant_direction",
            "signal_trigger",
            "signal_strength",
            "gate_eligible",
            "data_cutoff",
        )
        if key in features
    }
    safe_portfolio = {
        "has_position": bool(portfolio.get("has_position")),
        "open_position_count": int(portfolio.get("open_position_count", 0)),
        "exposure_pct": float(portfolio.get("exposure_pct", 0)),
        "positions": [
            {
                key: position[key]
                for key in (
                    "symbol",
                    "side",
                    "quantity",
                    "entry_price",
                    "mark_price",
                    "stop_price",
                    "target_price",
                    "leverage",
                    "liquidation_price",
                )
                if key in position
            }
            for position in portfolio.get("positions", [])
            if isinstance(position, dict)
        ][:10],
    }
    safe_jev = None
    if jev_vector is not None:
        safe_jev = {
            "model": jev_vector["model"],
            "question_schema_version": jev_vector["question_schema_version"],
            "answers": jev_vector["answers"],
        }
    result = {
        "snapshot": _safe_snapshot(snapshot),
        "features": safe_features,
        "jev_decision_vector": safe_jev,
        "portfolio": safe_portfolio,
        "instruction": (
            "Assess only the supplied closed-candle observations available by data_cutoff. "
            "Return no_trade if evidence or levels are insufficient. Do not invent data, "
            "claim calibrated probabilities, use later outcomes, or set quantity/leverage. "
            "A market entry must remain near the observed last price and be bracketed by "
            "objective stop and target levels; otherwise return no_trade."
        ),
    }
    result["responses_input_hash"] = digest(result)
    return result


def _responses_url(provider: dict[str, Any]) -> str:
    base = provider["base_url"].strip()
    parsed = urllib.parse.urlsplit(base)
    path = parsed.path.rstrip("/")
    if provider["kind"] == "foundry_responses" and "/api/projects/" in f"{path}/":
        # A Foundry *project* endpoint; the v1 Responses API lives on the resource root.
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/openai/v1/responses", "", ""))
    if path.endswith("/responses"):
        return base
    if path.endswith("/v1"):
        path += "/responses"
    elif provider["kind"] == "foundry_responses" and path.endswith("/openai"):
        path += "/v1/responses"
    elif path:
        path += "/responses"
    else:
        path = "/v1/responses" if provider["kind"] == "openai_responses" else "/openai/v1/responses"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def responses_metadata(response: dict[str, Any], requested_effort: str) -> dict[str, Any]:
    """Safe, normalized facts about a Responses reply (no prompt or output text)."""

    reasoning = response.get("reasoning") if isinstance(response.get("reasoning"), dict) else {}
    echoed = reasoning.get("effort") if isinstance(reasoning.get("effort"), str) else None
    if echoed is not None and echoed != requested_effort:
        raise AIProviderError(
            f"provider applied reasoning effort {echoed!r} instead of the requested {requested_effort!r}; "
            "silent downgrades are rejected"
        )
    response_id = response.get("id")
    model = response.get("model")
    return {
        "provider_response_id": response_id[:120] if isinstance(response_id, str) else None,
        "returned_model": model[:160] if isinstance(model, str) else None,
        "reasoning_effort_requested": requested_effort,
        "reasoning_effort_echoed": echoed,
        "status": response.get("status"),
    }


def _response_text(response: dict[str, Any]) -> str:
    if response.get("status") == "incomplete":
        raise AIProviderError(
            "Responses-compatible provider returned an incomplete response (output-token cap reached)"
        )
    if response.get("status") not in {None, "completed"} or response.get("error"):
        raise AIProviderError("Responses-compatible provider did not complete the request")
    direct = response.get("output_text")
    if isinstance(direct, str):
        return direct
    output = response.get("output")
    if not isinstance(output, list):
        raise AIProviderError("Responses-compatible provider returned no structured output")
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                text = part.get("text")
                if isinstance(text, str):
                    texts.append(text)
    if not texts:
        raise AIProviderError("Responses-compatible provider returned no structured output")
    return "".join(texts)


def parse_gpt_intent(
    response: dict[str, Any],
    symbol: str,
    *,
    snapshot: MarketSnapshot,
    source_arm: str,
) -> dict[str, Any] | None:
    try:
        content = json.loads(_response_text(response))
    except (json.JSONDecodeError, TypeError):
        raise AIProviderError("Responses-compatible provider returned malformed intent JSON") from None
    fields = {"action", "symbol", "side", "entry_price", "stop_price", "target_price", "reason"}
    if not isinstance(content, dict) or set(content) != fields:
        raise AIProviderError("Responses-compatible provider returned an unsupported intent shape")
    if content["symbol"] != symbol or content["action"] not in {"open", "no_trade"}:
        raise AIProviderError("Responses-compatible provider returned an invalid action or symbol")
    if not isinstance(content["reason"], str) or not content["reason"].strip():
        raise AIProviderError("Responses-compatible provider returned an empty rationale")
    if content["action"] == "no_trade":
        if (
            content["side"] is not None
            or content["entry_price"] is not None
            or content["stop_price"] is not None
            or content["target_price"] is not None
        ):
            raise AIProviderError("no_trade response must not include executable price levels")
        return None
    side = content["side"]
    if side not in {"long", "short"}:
        raise AIProviderError("Responses-compatible provider returned an unsupported direction")
    levels = [content[field] for field in ("entry_price", "stop_price", "target_price")]
    if any(isinstance(level, bool) or not isinstance(level, (int, float)) for level in levels):
        raise AIProviderError("Responses-compatible provider returned malformed price levels")
    entry, stop, target = (float(level) for level in levels)
    if not all(math.isfinite(level) and level > 0 for level in (entry, stop, target)):
        raise AIProviderError("Responses-compatible provider returned invalid price levels")
    if not (stop < entry < target if side == "long" else target < entry < stop):
        raise AIProviderError("Responses-compatible provider returned inconsistent risk levels")
    return {
        "schema_version": "paper-trading-intent.v1",
        "action": "open",
        "symbol": symbol,
        "side": side,
        "order_type": "market",
        "entry_price": entry,
        "stop_price": stop,
        "target_price": target,
        "reduce_only": False,
        "reduce_fraction": None,
        "position_id": None,
        "reason": content["reason"].strip()[:500],
        "source_arm": source_arm,
        "as_of": snapshot.data_cutoff,
    }


REASONING_EFFORTS = {"low", "medium", "high", "max"}


class ResponsesAdapter:
    """OpenAI Responses, Microsoft Foundry/Azure, or validated-compatible adapter."""

    def __init__(
        self,
        provider: dict[str, Any],
        *,
        environ: dict[str, str] | None = None,
        transport: PostTransport | None = None,
        skill_context: str | None = None,
        resolver: Any | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        self.provider = provider
        self.environ = environ
        self.resolver = resolver
        self.max_output_tokens = max_output_tokens
        self._transport = transport or _post_json
        self.skill_context = skill_context
        self.last_call: dict[str, Any] = {}

    def _headers(self) -> dict[str, str]:
        api_key = _credentials(self.provider, self.environ, self.resolver)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.provider["kind"] == "foundry_responses":
            headers["api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _effort(self, action: str) -> str:
        reasoning_effort = self.provider.get("reasoning_effort")
        if reasoning_effort not in REASONING_EFFORTS:
            raise AIProviderError(
                f"configured reasoning effort is unsupported; no external {action} was made"
            )
        return reasoning_effort

    def intent_payload(
        self,
        snapshot: MarketSnapshot,
        features: dict[str, Any],
        portfolio: dict[str, Any],
        *,
        jev_vector: dict[str, Any] | None,
        include_skill: bool,
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        user_input = build_gpt_input(
            snapshot,
            features,
            jev_vector=jev_vector,
            portfolio=portfolio,
        )
        if include_skill:
            skill = self.skill_context if self.skill_context is not None else load_skill_bundle()
            system_instructions = (
                "You are a research-only crypto futures analyst. Apply the local "
                "crypto-market-trading-analysis skill supplied below to the frozen "
                "point-in-time snapshot. Separate observed facts from interpretation, "
                "challenge both directions, distinguish trend from timing, and use "
                "objective entry/invalidation/target levels. The deterministic application "
                "owns sizing, leverage, margin, fees, funding, liquidation, and fills. "
                "Never request or reveal credentials, browse, call tools, use later outcomes, "
                "or claim calibrated probabilities. Return the required structured intent only.\n"
                "<crypto-market-trading-analysis>\n"
                f"{skill}\n"
                "</crypto-market-trading-analysis>"
            )
        else:
            system_instructions = (
                "You are a research-only crypto futures analyst. Use only the supplied "
                "closed-candle snapshot, identify material opposing evidence, and return "
                "no_trade if levels or evidence are insufficient. Do not browse, call tools, "
                "use later outcomes, or claim calibrated probabilities. Deterministic code "
                "owns sizing, leverage, risk, and execution."
            )
        prompt = {
            "model": self.provider["model"],
            "instructions": system_instructions,
            "input": json.dumps(user_input, sort_keys=True, separators=(",", ":"), allow_nan=False),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "paper_trading_intent",
                    "strict": True,
                    "schema": INTENT_RESPONSE_SCHEMA,
                }
            },
            "reasoning": {"effort": self._effort("model call")},
        }
        if self.max_output_tokens is not None:
            prompt["max_output_tokens"] = int(self.max_output_tokens)
        return prompt, system_instructions, user_input

    def generate_intent(
        self,
        snapshot: MarketSnapshot,
        features: dict[str, Any],
        portfolio: dict[str, Any],
        *,
        jev_vector: dict[str, Any] | None,
        source_arm: str,
        include_skill: bool,
    ) -> dict[str, Any]:
        prompt, system_instructions, user_input = self.intent_payload(
            snapshot, features, portfolio, jev_vector=jev_vector, include_skill=include_skill
        )
        headers = self._headers()
        meta: dict[str, Any] = {}
        self.last_call = meta
        started = time.perf_counter()
        response = _safe_post(
            self._transport,
            _responses_url(self.provider),
            headers,
            prompt,
            float(self.provider.get("timeout_seconds", 30)),
            "Responses-compatible provider",
            meta,
        )
        latency = (time.perf_counter() - started) * 1000
        meta["latency_ms"] = latency
        meta["raw_usage"] = response.get("usage")
        meta.update(responses_metadata(response, prompt["reasoning"]["effort"]))
        intent = parse_gpt_intent(
            response,
            snapshot.symbol,
            snapshot=snapshot,
            source_arm=source_arm,
        )
        usage = response.get("usage")
        normalized_usage: dict[str, Any] = {"input_tokens": None, "output_tokens": None}
        if isinstance(usage, dict):
            input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
            output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
            output_details = usage.get("output_tokens_details")
            reasoning_tokens = usage.get("reasoning_tokens")
            if reasoning_tokens is None and isinstance(output_details, dict):
                reasoning_tokens = output_details.get("reasoning_tokens")
            input_details = usage.get("input_tokens_details")
            cached = input_details.get("cached_tokens") if isinstance(input_details, dict) else None
            if isinstance(input_tokens, int) and input_tokens >= 0:
                normalized_usage["input_tokens"] = input_tokens
            if isinstance(output_tokens, int) and output_tokens >= 0:
                normalized_usage["output_tokens"] = output_tokens
            if isinstance(reasoning_tokens, int) and reasoning_tokens >= 0:
                normalized_usage["reasoning_tokens"] = reasoning_tokens
            if isinstance(cached, int) and cached >= 0:
                normalized_usage["cached_input_tokens"] = cached
        return {
            "intent": intent,
            "model": self.provider["model"],
            "returned_model": meta.get("returned_model"),
            "reasoning_effort": prompt["reasoning"]["effort"],
            "reasoning_effort_echoed": meta.get("reasoning_effort_echoed"),
            "usage": normalized_usage,
            "latency_ms": latency,
            "prompt_hash": digest({"instructions": system_instructions, "input": user_input}),
            "source_arm": source_arm,
            "provider_request_id": meta.get("provider_request_id"),
            "provider_response_id": meta.get("provider_response_id"),
            "real_external_call": True,
        }

    def connection_payload(self) -> dict[str, Any]:
        payload = {
            "model": self.provider["model"],
            "instructions": "Return no_trade with a concise test reason. This is a connection test.",
            "input": json.dumps(
                {
                    "snapshot": {"symbol": "BTCUSDT", "data_origin": "CONNECTION_TEST", "closed_candles": []},
                    "features": {},
                    "jev_decision_vector": None,
                    "portfolio": {"has_position": False},
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "paper_trading_intent",
                    "strict": True,
                    "schema": INTENT_RESPONSE_SCHEMA,
                }
            },
            "reasoning": {"effort": self._effort("provider call")},
        }
        if self.max_output_tokens is not None:
            payload["max_output_tokens"] = int(self.max_output_tokens)
        return payload

    def test_connection(self) -> dict[str, Any]:
        payload = self.connection_payload()
        reasoning_effort = payload["reasoning"]["effort"]
        headers = self._headers()
        meta: dict[str, Any] = {}
        self.last_call = meta
        endpoint = _responses_url(self.provider)
        started = time.perf_counter()
        try:
            response = _safe_post(
                self._transport,
                endpoint,
                headers,
                payload,
                float(self.provider.get("timeout_seconds", 30)),
                "Responses-compatible provider",
                meta,
            )
        except AIProviderError as exc:
            if "HTTP 400" in str(exc) or "HTTP 422" in str(exc):
                raise AIProviderError(
                    "Responses-compatible provider rejected the configured reasoning "
                    f"effort or structured test request ({exc})"
                ) from None
            raise
        latency = (time.perf_counter() - started) * 1000
        meta["latency_ms"] = latency
        meta["raw_usage"] = response.get("usage")
        facts = responses_metadata(response, reasoning_effort)
        meta.update(facts)
        try:
            structured = json.loads(_response_text(response))
        except (AIProviderError, json.JSONDecodeError, TypeError):
            raise AIProviderError(
                "Responses-compatible provider rejected the configured reasoning "
                "effort or structured validation output"
            ) from None
        if (
            not isinstance(structured, dict)
            or set(structured)
            != {"action", "symbol", "side", "entry_price", "stop_price", "target_price", "reason"}
            or structured.get("action") != "no_trade"
            or not isinstance(structured.get("reason"), str)
        ):
            raise AIProviderError(
                "Responses-compatible provider rejected the configured reasoning "
                "effort or structured intent validation"
            )
        parsed = urllib.parse.urlsplit(endpoint)
        return {
            "ok": True,
            "provider_id": self.provider["provider_id"],
            "provider_kind": self.provider["kind"],
            "model": self.provider["model"],
            "returned_model": facts["returned_model"],
            "latency_ms": latency,
            "responses_compatible": True,
            "responses_endpoint_path": parsed.path,
            "structured_output_validated": True,
            "normalized_action": structured["action"],
            "reasoning_effort_validated": reasoning_effort,
            "reasoning_effort_echoed": facts["reasoning_effort_echoed"],
            "reasoning_effort_accepted": facts["reasoning_effort_echoed"] in {None, reasoning_effort},
            "usage": response.get("usage"),
            "provider_request_id": meta.get("provider_request_id"),
            "provider_response_id": facts["provider_response_id"],
            "real_external_call": True,
        }


class FixtureGPTProvider:
    """Deterministic mock Responses adapter; no credentials or network activity."""

    provider_id = "fixture-gpt"
    model = "fixture-gpt-v1"

    def generate_intent(
        self,
        snapshot: MarketSnapshot,
        features: dict[str, Any],
        portfolio: dict[str, Any],
        *,
        jev_vector: dict[str, Any] | None,
        source_arm: str,
        include_skill: bool,
    ) -> dict[str, Any]:
        direction = features.get("quant_direction")
        if direction not in {"long", "short"} or not features.get("gate_eligible"):
            return {
                "intent": None,
                "model": self.model,
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "latency_ms": 0,
                "prompt_hash": digest(
                    build_gpt_input(snapshot, features, jev_vector=jev_vector, portfolio=portfolio)
                ),
                "source_arm": source_arm,
                "fixture": True,
            }
        entry = float(features["last_price"])
        atr = max(float(features["atr"]), entry * 0.002)
        stop = entry - 1.5 * atr if direction == "long" else entry + 1.5 * atr
        target = entry + 2.0 * atr if direction == "long" else entry - 2.0 * atr
        intent = {
            "schema_version": "paper-trading-intent.v1",
            "action": "open",
            "symbol": snapshot.symbol,
            "side": direction,
            "order_type": "market",
            "entry_price": entry,
            "stop_price": stop,
            "target_price": target,
            "reduce_only": False,
            "reduce_fraction": None,
            "position_id": None,
            "reason": (
                "Deterministic fixture escalation. "
                f"Skill treatment={'enabled' if include_skill else 'disabled'}."
            ),
            "source_arm": source_arm,
            "as_of": snapshot.data_cutoff,
        }
        return {
            "intent": intent,
            "model": self.model,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "latency_ms": 0,
            "prompt_hash": digest(
                build_gpt_input(snapshot, features, jev_vector=jev_vector, portfolio=portfolio)
            ),
            "source_arm": source_arm,
            "fixture": True,
        }

    def test_connection(self) -> dict[str, Any]:
        return {
            "ok": True,
            "provider_id": self.provider_id,
            "model": self.model,
            "latency_ms": 0,
            "responses_compatible": True,
            "fixture": True,
            "message": "Local fixture adapter; no external request was made.",
        }
