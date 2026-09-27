"""Server-side OpenAI Responses API runner for frozen skill/control decisions."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .contracts import (
    BIAS_DIRECTIONS,
    CONFIDENCE_LEVELS,
    DECISION_STATES,
    EvaluationError,
    digest,
    iso_utc,
)


DEFAULT_MODEL_ID = "gpt-6-luna"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_REASONING_EFFORT = "max"
PROMPT_TEMPLATE_VERSION = "crypto-market-decision.v1"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class OpenAIResponsesError(EvaluationError):
    """Raised for safe, sanitized OpenAI Responses API failures."""


class MissingOpenAICredentialsError(OpenAIResponsesError):
    """Raised when the runtime-only API key is not configured."""


PostTransport = Callable[[str, dict[str, str], bytes, float], bytes]


DECISION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision_state": {"type": "string", "enum": sorted(DECISION_STATES)},
        "bias": {"type": "string", "enum": sorted(BIAS_DIRECTIONS)},
        "confidence": {"type": "string", "enum": sorted(CONFIDENCE_LEVELS)},
        "entry": {
            "type": ["object", "null"],
            "properties": {
                "kind": {
                    "type": ["string", "null"],
                    "enum": ["pullback", "breakout", "immediate", "none", None],
                },
                "direction": {
                    "type": ["string", "null"],
                    "enum": ["long", "short", None],
                },
                "zone_low": {"type": ["number", "null"]},
                "zone_high": {"type": ["number", "null"]},
                "level": {"type": ["number", "null"]},
                "reference_price": {"type": ["number", "null"]},
                "confirmation": {
                    "type": ["string", "null"],
                    "enum": [
                        "touch",
                        "close_inside_zone",
                        "close_beyond_zone",
                        "close",
                        None,
                    ],
                },
            },
            "required": [
                "kind",
                "direction",
                "zone_low",
                "zone_high",
                "level",
                "reference_price",
                "confirmation",
            ],
            "additionalProperties": False,
        },
        "invalidation": {"type": ["number", "null"]},
        "targets": {"type": "array", "items": {"type": "number"}},
        "leverage_stress": {
            "type": ["string", "null"],
            "enum": ["low", "elevated", "high", "extreme", None],
        },
        "rationale": {"type": "string"},
    },
    "required": [
        "decision_state",
        "bias",
        "confidence",
        "entry",
        "invalidation",
        "targets",
        "leverage_stress",
        "rationale",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class OpenAIResponsesConfig:
    """Non-secret model configuration; credentials are deliberately separate."""

    model_id: str = DEFAULT_MODEL_ID
    base_url: str = DEFAULT_OPENAI_BASE_URL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise OpenAIResponsesError("OPENAI_MODEL must be a non-empty model ID")
        if not isinstance(self.base_url, str):
            raise OpenAIResponsesError("OPENAI_BASE_URL must be an HTTPS API base URL")
        normalized_url = self.base_url.strip().rstrip("/")
        parsed = urllib.parse.urlsplit(normalized_url)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.endswith("/responses")
        ):
            raise OpenAIResponsesError(
                "OPENAI_BASE_URL must not contain credentials, query parameters, or a Responses path"
            )
        if self.reasoning_effort not in {"low", "medium", "high", "max"}:
            raise OpenAIResponsesError("OPENAI_REASONING_EFFORT is unsupported")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 1 <= self.timeout_seconds <= 300
        ):
            raise OpenAIResponsesError("OPENAI_TIMEOUT_SECONDS must be between 1 and 300")
        object.__setattr__(self, "model_id", self.model_id.strip())
        object.__setattr__(self, "base_url", normalized_url)
        object.__setattr__(self, "reasoning_effort", self.reasoning_effort.strip())
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    @property
    def responses_url(self) -> str:
        return f"{self.base_url}/responses"

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> "OpenAIResponsesConfig":
        values = os.environ if environ is None else environ
        raw_timeout = values.get("OPENAI_TIMEOUT_SECONDS", "60")
        try:
            timeout_seconds = float(raw_timeout)
        except (TypeError, ValueError):
            raise OpenAIResponsesError("OPENAI_TIMEOUT_SECONDS must be numeric") from None
        return cls(
            model_id=values.get("OPENAI_MODEL", DEFAULT_MODEL_ID),
            base_url=values.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
            reasoning_effort=values.get(
                "OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT
            ),
            timeout_seconds=timeout_seconds,
        )


def api_key_from_environment(environ: dict[str, str] | None = None) -> str:
    values = os.environ if environ is None else environ
    api_key = values.get("OPENAI_API_KEY")
    if not isinstance(api_key, str) or not api_key.strip():
        raise MissingOpenAICredentialsError(
            "OPENAI_API_KEY is not configured in the server environment; no model call was made"
        )
    return api_key.strip()


def _default_http_post(
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> bytes:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_RESPONSE_BYTES + 1)


def _case_prompt(case: dict[str, Any], question: str) -> str:
    snapshot = case.get("snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("candles"), list):
        raise OpenAIResponsesError("model case must contain a closed-candle snapshot")
    candles = [
        {
            field: candle[field]
            for field in ("open_time", "close_time", "open", "high", "low", "close", "volume")
        }
        for candle in snapshot["candles"]
    ]
    safe_case = {
        "case_id": case["case_id"],
        "asset": case["asset"],
        "instrument": case["instrument"],
        "venue": case["venue"],
        "horizon": case["horizon"],
        "as_of": case["as_of"],
        "data_cutoff": case["data_cutoff"],
        "bar_interval_seconds": case["bar_interval_seconds"],
        "horizon_bars": case["horizon_bars"],
        "snapshot": {"candles": candles},
    }
    return json.dumps(
        {"question": question, "case": safe_case},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _instructions(skill_text: str) -> str:
    return (
        "You are producing one research-only, point-in-time crypto market decision. "
        "Use only the market snapshot in the user input; all candles are closed and "
        "available no later than data_cutoff. Do not browse, call tools, infer future "
        "prices, use outcomes, or claim evidence that is not present. Be conservative "
        "when data is insufficient and use NO_TRADE rather than inventing an entry. "
        "Do not place or propose exchange orders. Return only the required structured "
        "decision fields; rationale must be concise and tied to the supplied evidence.\n"
        "<skill_instructions>\n"
        f"{skill_text}"
        "\n</skill_instructions>"
    )


def _normalized_decision(value: Any) -> dict[str, Any]:
    required = {
        "decision_state",
        "bias",
        "confidence",
        "entry",
        "invalidation",
        "targets",
        "leverage_stress",
        "rationale",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise OpenAIResponsesError("model output is missing or contains unsupported decision fields")
    if value["decision_state"] not in DECISION_STATES:
        raise OpenAIResponsesError("model output contains an unsupported decision state")
    if value["bias"] not in BIAS_DIRECTIONS or value["confidence"] not in CONFIDENCE_LEVELS:
        raise OpenAIResponsesError("model output contains an unsupported bias or confidence")
    if value["leverage_stress"] not in {None, "low", "elevated", "high", "extreme"}:
        raise OpenAIResponsesError("model output contains an unsupported leverage state")
    if not isinstance(value["rationale"], str) or not value["rationale"].strip():
        raise OpenAIResponsesError("model output rationale must be a non-empty string")
    if not isinstance(value["targets"], list):
        raise OpenAIResponsesError("model output targets must be an array")

    raw_entry = value["entry"]
    if raw_entry is None:
        entry = None
    else:
        entry_fields = {
            "kind",
            "direction",
            "zone_low",
            "zone_high",
            "level",
            "reference_price",
            "confirmation",
        }
        if not isinstance(raw_entry, dict) or set(raw_entry) != entry_fields:
            raise OpenAIResponsesError("model output entry is malformed")
        kind = raw_entry["kind"]
        if kind not in {"pullback", "breakout", "immediate", "none"}:
            raise OpenAIResponsesError("model output entry kind is unsupported")
        shapes = {
            "pullback": {
                "kind",
                "direction",
                "zone_low",
                "zone_high",
                "confirmation",
            },
            "breakout": {"kind", "direction", "level", "confirmation"},
            "immediate": {"kind", "direction", "reference_price"},
            "none": {"kind"},
        }
        allowed = shapes[kind]
        unused_fields = entry_fields - allowed
        if any(raw_entry[field] is not None for field in unused_fields):
            raise OpenAIResponsesError("model output entry mixes unsupported entry fields")
        entry = {field: raw_entry[field] for field in allowed}

    return {
        "decision_state": value["decision_state"],
        "bias": value["bias"],
        "confidence": value["confidence"],
        "entry": entry,
        "invalidation": value["invalidation"],
        "targets": value["targets"],
        "leverage_stress": value["leverage_stress"],
        "rationale": value["rationale"].strip(),
    }


def _extract_output_text(response: Any) -> str:
    if not isinstance(response, dict):
        raise OpenAIResponsesError("OpenAI Responses API returned a malformed response")
    if response.get("error"):
        raise OpenAIResponsesError("OpenAI Responses API reported an error")
    if response.get("status") != "completed":
        raise OpenAIResponsesError("OpenAI Responses API did not complete the response")
    if response.get("refusal"):
        raise OpenAIResponsesError("OpenAI Responses API returned a refusal")

    texts: list[str] = []
    output = response.get("output")
    if output is not None and not isinstance(output, list):
        raise OpenAIResponsesError("OpenAI Responses API returned malformed output items")
    for item in output or []:
        if not isinstance(item, dict):
            raise OpenAIResponsesError("OpenAI Responses API returned malformed output items")
        item_type = item.get("type")
        if isinstance(item_type, str) and (
            item_type.endswith("_call") or item_type in {"tool_call", "computer_call"}
        ):
            raise OpenAIResponsesError("model tool use is not permitted")
        if item_type == "refusal":
            raise OpenAIResponsesError("OpenAI Responses API returned a refusal")
        if item_type == "reasoning":
            continue
        if item_type != "message":
            raise OpenAIResponsesError("OpenAI Responses API returned an unsupported output type")
        if item.get("status") not in {None, "completed"}:
            raise OpenAIResponsesError("OpenAI Responses API returned an incomplete message")
        content = item.get("content")
        if not isinstance(content, list):
            raise OpenAIResponsesError("OpenAI Responses API returned malformed message content")
        for block in content:
            if not isinstance(block, dict):
                raise OpenAIResponsesError("OpenAI Responses API returned malformed message content")
            if block.get("type") == "refusal":
                raise OpenAIResponsesError("OpenAI Responses API returned a refusal")
            if block.get("type") == "output_text" and isinstance(block.get("text"), str):
                texts.append(block["text"])

    if not texts and isinstance(response.get("output_text"), str):
        texts.append(response["output_text"])
    if len(texts) != 1 or not texts[0].strip():
        raise OpenAIResponsesError("OpenAI Responses API returned no single structured text result")
    return texts[0]


class OpenAIResponsesRunner:
    """One skill or control arm using the same immutable Responses API setup."""

    def __init__(
        self,
        config: OpenAIResponsesConfig,
        *,
        api_key: str | None,
        variant: str,
        run_id: str,
        skill_text: str,
        skill_commit: str | None = None,
        question: str = "",
        transport: PostTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if variant not in {"skill", "control"}:
            raise OpenAIResponsesError("model runner variant must be skill or control")
        if not isinstance(run_id, str) or not run_id.strip():
            raise OpenAIResponsesError("model runner requires a run_id")
        if not isinstance(skill_text, str):
            raise OpenAIResponsesError("skill text must be a string")
        if variant == "skill" and (
            not isinstance(skill_commit, str) or not skill_commit.strip()
        ):
            raise OpenAIResponsesError("skill arm requires a skill commit")
        if variant == "control" and skill_commit is not None:
            raise OpenAIResponsesError("control arm cannot claim a skill commit")
        if not isinstance(question, str) or len(question) > 1200:
            raise OpenAIResponsesError("question must be a string of at most 1200 characters")
        self.config = config
        self._api_key = api_key
        self.variant = variant
        self.run_id = run_id.strip()
        self.skill_text = skill_text
        self.skill_commit = skill_commit.strip() if isinstance(skill_commit, str) else None
        self.question = question
        self._transport = transport or _default_http_post
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._skill_fingerprint = digest(skill_text)
        self._config_hash = digest(
            {
                "provider": "openai",
                "model_id": config.model_id,
                "base_url": config.base_url,
                "endpoint_path": "/responses",
                "reasoning": {"effort": config.reasoning_effort},
                "response_schema_hash": digest(DECISION_RESPONSE_SCHEMA),
                "prompt_template_version": PROMPT_TEMPLATE_VERSION,
                "timeout_seconds": config.timeout_seconds,
            }
        )

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "name": "openai_responses",
            "version": "1.0.0",
            "mode": "live",
            "execution_status": "invoked",
            "provider": "openai",
            "model_id": self.config.model_id,
            "inference_config_hash": self._config_hash,
            "prompt_version": (
                f"{PROMPT_TEMPLATE_VERSION}:skill-sha256-{self._skill_fingerprint}"
            ),
            "skill_commit": self.skill_commit,
            "variant": self.variant,
            "run_id": self.run_id,
        }

    def build_request_body(self, case: dict[str, Any]) -> dict[str, Any]:
        """Return the exact, credential-free API body used by this inference arm."""

        skill_instructions = self.skill_text if self.variant == "skill" else ""
        return {
            "model": self.config.model_id,
            "reasoning": {"effort": self.config.reasoning_effort},
            "instructions": _instructions(skill_instructions),
            "input": _case_prompt(case, self.question),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "crypto_eval_decision",
                    "strict": True,
                    "schema": DECISION_RESPONSE_SCHEMA,
                }
            },
        }

    def predict(self, case: dict[str, Any]) -> dict[str, Any]:
        api_key = self._api_key
        if not isinstance(api_key, str) or not api_key.strip():
            raise MissingOpenAICredentialsError(
                "OPENAI_API_KEY is not configured in the server environment; no model call was made"
            )
        body = self.build_request_body(case)
        encoded = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            response_bytes = self._transport(
                self.config.responses_url,
                headers,
                encoded,
                self.config.timeout_seconds,
            )
        except urllib.error.HTTPError as exc:
            raise OpenAIResponsesError(
                f"OpenAI Responses API returned HTTP {exc.code}"
            ) from None
        except (TimeoutError, socket.timeout):
            raise OpenAIResponsesError("OpenAI Responses API request timed out") from None
        except urllib.error.URLError:
            raise OpenAIResponsesError("OpenAI Responses API request failed") from None
        except OpenAIResponsesError:
            raise
        except Exception:
            raise OpenAIResponsesError("OpenAI Responses API request failed") from None

        if not isinstance(response_bytes, (bytes, bytearray)) or len(response_bytes) > MAX_RESPONSE_BYTES:
            raise OpenAIResponsesError("OpenAI Responses API returned an invalid response")
        try:
            response = json.loads(response_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OpenAIResponsesError("OpenAI Responses API returned invalid JSON") from None
        text = _extract_output_text(response)
        try:
            raw_decision = json.loads(text)
        except json.JSONDecodeError:
            raise OpenAIResponsesError("OpenAI Responses API output was not valid decision JSON") from None
        decision = _normalized_decision(raw_decision)
        frozen_at = self._clock()
        if not isinstance(frozen_at, datetime) or frozen_at.tzinfo is None:
            raise OpenAIResponsesError("runtime clock must return a timezone-aware timestamp")
        return {**decision, "frozen_at": iso_utc(frozen_at.astimezone(timezone.utc))}
