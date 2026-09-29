"""AI inference cost accounting: versioned price book, usage ledger, and hard budget guard.

Every paid provider call is a financial event. Budgeting is deterministic code; a model
never decides its own budget. Unknown usage or price is recorded as unavailable, never
as zero, and historical price versions are append-only.
"""

from __future__ import annotations

import math
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator

from .contracts import canonical_json
from .paper_contracts import PaperTradingError, iso_utc, parse_utc


BUDGET_SCHEMA_VERSION = "ai-budget.v1"
BUDGET_POLICY_VERSION = "ai-budget-policy.v1"
PRICE_BOOK_SCHEMA_VERSION = "ai-price-book.v1"
FX_POLICY_VERSION = "ai-cost-fx.v1"
LIMIT_ACTIONS = ("PAUSE_NEW_ENTRIES", "FALLBACK_QUANT", "JEV_ONLY", "BLOCK_PAID_AI")
CALL_TYPES = (
    "test_connection",
    "jev_decision",
    "gpt_escalation",
    "gpt_arm",
    "manual_analysis",
    "jev_replan",
    "gpt_replan",
)
REASONING_RULES = ("included_in_output", "separate_rate", "unknown")
COST_STATUSES = ("exact", "estimated", "unavailable")
DEFAULT_WARNING_THRESHOLDS = (0.5, 0.8, 0.95, 1.0)
CHARS_PER_TOKEN_CONSERVATIVE = 3.0


class BudgetError(PaperTradingError):
    """Budget or price-book configuration is invalid."""


COST_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_price_book(
    price_id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_kind TEXT NOT NULL,
    model TEXT NOT NULL,
    version TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    currency TEXT NOT NULL,
    input_per_million REAL,
    cached_input_per_million REAL,
    output_per_million REAL,
    reasoning_per_million REAL,
    per_call_usd REAL,
    reasoning_billing_rule TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(provider_kind, model, version)
);
CREATE TABLE IF NOT EXISTS ai_usage_events(
    usage_event_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    cycle_id TEXT,
    symbol TEXT,
    arm TEXT,
    provider_id TEXT NOT NULL,
    provider_kind TEXT NOT NULL,
    model TEXT NOT NULL,
    returned_model TEXT,
    reasoning_effort TEXT,
    call_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    latency_ms REAL,
    status TEXT NOT NULL,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    price_id INTEGER,
    price_book_version TEXT,
    estimated_cost_usd REAL,
    billed_cost_usd REAL,
    cost_status TEXT NOT NULL,
    reservation_id TEXT,
    provider_request_id TEXT,
    provider_response_id TEXT,
    error_code TEXT,
    real_external_call INTEGER NOT NULL DEFAULT 0,
    decision TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_usage_events_time_idx
    ON ai_usage_events(experiment_id, started_at);
CREATE TABLE IF NOT EXISTS ai_budget_reservations(
    reservation_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    cycle_id TEXT,
    provider_id TEXT NOT NULL,
    provider_kind TEXT NOT NULL,
    call_type TEXT NOT NULL,
    reserved_usd REAL NOT NULL,
    charged_usd REAL,
    status TEXT NOT NULL,
    price_id INTEGER,
    created_at TEXT NOT NULL,
    settled_at TEXT
);
CREATE INDEX IF NOT EXISTS ai_budget_reservations_time_idx
    ON ai_budget_reservations(experiment_id, created_at);
CREATE TABLE IF NOT EXISTS ai_budget_events(
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    cycle_id TEXT,
    provider_id TEXT,
    call_type TEXT,
    event_type TEXT NOT NULL,
    scope TEXT,
    code TEXT NOT NULL,
    limit_action TEXT,
    policy_version TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def default_budget_config() -> dict[str, Any]:
    return {
        "version": BUDGET_SCHEMA_VERSION,
        "policy_version": BUDGET_POLICY_VERSION,
        "enabled": True,
        "daily_usd": 5.0,
        "experiment_usd": 30.0,
        "max_gpt_call_usd": 0.5,
        "max_cycle_usd": 1.0,
        "max_gpt_calls_per_cycle": 2,
        "max_gpt_calls_per_hour": 8,
        "max_gpt_calls_per_day": 50,
        "max_jev_calls_per_day": 1000,
        "max_paid_calls": None,
        "gpt_max_input_tokens": 60_000,
        "gpt_max_output_tokens": 32_000,
        "jev_max_input_tokens": 16_000,
        "jev_max_output_tokens": 4_000,
        "warning_thresholds": list(DEFAULT_WARNING_THRESHOLDS),
        "limit_action": "PAUSE_NEW_ENTRIES",
        "unknown_price_policy": "FAIL_CLOSED",
    }


def default_fx_policy() -> dict[str, Any]:
    return {
        "version": FX_POLICY_VERSION,
        "mode": "none",
        "usdt_per_usd": None,
        "source": None,
        "recorded_at": None,
    }


def _number(value: Any, field: str, *, minimum: float = 0.0, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BudgetError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise BudgetError(f"{field} must be a finite number >= {minimum}")
    return result


def _integer(value: Any, field: str, *, minimum: int = 0, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise BudgetError(f"{field} must be an integer >= {minimum}")
    return value


def validate_budget_config(value: Any) -> dict[str, Any]:
    if value is None:
        return default_budget_config()
    if not isinstance(value, dict):
        raise BudgetError("ai_budget must be an object")
    defaults = default_budget_config()
    extra = set(value) - set(defaults)
    if extra:
        raise BudgetError("ai_budget contains unsupported fields")
    config = {**defaults, **value}
    if config["version"] != BUDGET_SCHEMA_VERSION:
        raise BudgetError("ai_budget version is unsupported")
    if config["policy_version"] != BUDGET_POLICY_VERSION:
        raise BudgetError("ai_budget policy version is unsupported")
    if not isinstance(config["enabled"], bool):
        raise BudgetError("ai_budget.enabled must be boolean")
    for field in ("daily_usd", "experiment_usd", "max_gpt_call_usd", "max_cycle_usd"):
        config[field] = _number(config[field], f"ai_budget.{field}", allow_none=True)
    for field in (
        "max_gpt_calls_per_cycle",
        "max_gpt_calls_per_hour",
        "max_gpt_calls_per_day",
        "max_jev_calls_per_day",
        "max_paid_calls",
    ):
        config[field] = _integer(config[field], f"ai_budget.{field}", allow_none=True)
    for field in (
        "gpt_max_input_tokens",
        "gpt_max_output_tokens",
        "jev_max_input_tokens",
        "jev_max_output_tokens",
    ):
        config[field] = _integer(config[field], f"ai_budget.{field}", minimum=1)
    thresholds = config["warning_thresholds"]
    if (
        not isinstance(thresholds, list)
        or not thresholds
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) or not 0 < item <= 1 for item in thresholds)
    ):
        raise BudgetError("ai_budget.warning_thresholds must be fractions in (0, 1]")
    config["warning_thresholds"] = sorted({float(item) for item in thresholds})
    if config["limit_action"] not in LIMIT_ACTIONS:
        raise BudgetError("ai_budget.limit_action is unsupported")
    if config["unknown_price_policy"] not in {"FAIL_CLOSED"}:
        raise BudgetError("ai_budget.unknown_price_policy must be FAIL_CLOSED; add an explicit price-book entry instead")
    return config


def validate_fx_policy(value: Any) -> dict[str, Any]:
    if value is None:
        return default_fx_policy()
    if not isinstance(value, dict):
        raise BudgetError("cost_fx must be an object")
    defaults = default_fx_policy()
    if set(value) - set(defaults):
        raise BudgetError("cost_fx contains unsupported fields")
    config = {**defaults, **value}
    if config["version"] != FX_POLICY_VERSION:
        raise BudgetError("cost_fx version is unsupported")
    if config["mode"] not in {"none", "manual"}:
        raise BudgetError("cost_fx.mode must be none or manual")
    if config["mode"] == "none":
        return {**defaults}
    config["usdt_per_usd"] = _number(config["usdt_per_usd"], "cost_fx.usdt_per_usd", minimum=0.5)
    if config["usdt_per_usd"] > 2:
        raise BudgetError("cost_fx.usdt_per_usd must be between 0.5 and 2")
    source = config["source"]
    if not isinstance(source, str) or not source.strip() or len(source) > 120:
        raise BudgetError("cost_fx.source must describe the rate source")
    config["source"] = source.strip()
    config["recorded_at"] = iso_utc(parse_utc(config["recorded_at"], "cost_fx.recorded_at"))
    return config


def validate_price_entry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BudgetError("price-book entry must be an object")
    allowed = {
        "provider_kind",
        "model",
        "version",
        "effective_from",
        "currency",
        "input_per_million",
        "cached_input_per_million",
        "output_per_million",
        "reasoning_per_million",
        "per_call_usd",
        "reasoning_billing_rule",
        "source",
    }
    if set(value) - allowed:
        raise BudgetError("price-book entry contains unsupported fields")
    kind = value.get("provider_kind")
    if not isinstance(kind, str) or not re.fullmatch(r"[a-z_]{3,40}", kind):
        raise BudgetError("price-book provider_kind is invalid")
    model = value.get("model")
    if not isinstance(model, str) or not model.strip() or len(model) > 160:
        raise BudgetError("price-book model is invalid")
    version = value.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", version):
        raise BudgetError("price-book version is invalid")
    currency = value.get("currency", "USD")
    if currency != "USD":
        raise BudgetError("price-book currency must be USD")
    rule = value.get("reasoning_billing_rule", "included_in_output")
    if rule not in REASONING_RULES:
        raise BudgetError("reasoning_billing_rule is unsupported")
    source = value.get("source")
    if not isinstance(source, str) or not source.strip() or len(source) > 240:
        raise BudgetError("price-book source/reference is required")
    entry = {
        "provider_kind": kind,
        "model": model.strip(),
        "version": version,
        "effective_from": iso_utc(parse_utc(value.get("effective_from"), "price.effective_from")),
        "currency": "USD",
        "input_per_million": _number(value.get("input_per_million"), "input_per_million", allow_none=True),
        "cached_input_per_million": _number(
            value.get("cached_input_per_million"), "cached_input_per_million", allow_none=True
        ),
        "output_per_million": _number(value.get("output_per_million"), "output_per_million", allow_none=True),
        "reasoning_per_million": _number(
            value.get("reasoning_per_million"), "reasoning_per_million", allow_none=True
        ),
        "per_call_usd": _number(value.get("per_call_usd"), "per_call_usd", allow_none=True),
        "reasoning_billing_rule": rule,
        "source": source.strip(),
    }
    if rule == "separate_rate" and entry["reasoning_per_million"] is None:
        raise BudgetError("separate_rate reasoning billing requires reasoning_per_million")
    if (
        entry["input_per_million"] is None
        and entry["output_per_million"] is None
        and entry["per_call_usd"] is None
    ):
        raise BudgetError("price-book entry must define token or per-call pricing")
    return entry


def price_is_complete(price: dict[str, Any] | None) -> bool:
    if not price:
        return False
    if price.get("reasoning_billing_rule") == "unknown":
        return False
    has_tokens = price.get("input_per_million") is not None and price.get("output_per_million") is not None
    return has_tokens or price.get("per_call_usd") is not None


def normalize_usage(usage: Any) -> dict[str, int | None]:
    """Normalize Responses/Jev usage; absent fields stay None rather than zero."""

    result: dict[str, int | None] = {
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
    }
    if not isinstance(usage, dict):
        return result

    def count(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    result["input_tokens"] = count(usage.get("input_tokens", usage.get("prompt_tokens")))
    result["output_tokens"] = count(usage.get("output_tokens", usage.get("completion_tokens")))
    input_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details")
    if isinstance(input_details, dict):
        result["cached_input_tokens"] = count(input_details.get("cached_tokens"))
    if result["cached_input_tokens"] is None:
        result["cached_input_tokens"] = count(usage.get("cached_input_tokens"))
    output_details = usage.get("output_tokens_details") or usage.get("completion_tokens_details")
    if isinstance(output_details, dict):
        result["reasoning_tokens"] = count(output_details.get("reasoning_tokens"))
    if result["reasoning_tokens"] is None:
        result["reasoning_tokens"] = count(usage.get("reasoning_tokens"))
    return result


def estimate_cost(price: dict[str, Any] | None, usage: dict[str, int | None]) -> tuple[float | None, str]:
    """Return (usd, status). Reasoning tokens are already inside Responses output_tokens."""

    if not price_is_complete(price):
        return None, "unavailable"
    assert price is not None
    cost = float(price.get("per_call_usd") or 0.0)
    if price.get("input_per_million") is not None or price.get("output_per_million") is not None:
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if input_tokens is None or output_tokens is None:
            return None, "unavailable"
        cached = usage.get("cached_input_tokens") or 0
        cached = min(cached, input_tokens)
        cached_rate = price.get("cached_input_per_million")
        if cached_rate is None:
            cached_rate = price["input_per_million"]
        cost += (input_tokens - cached) * price["input_per_million"] / 1_000_000
        cost += cached * cached_rate / 1_000_000
        cost += output_tokens * price["output_per_million"] / 1_000_000
        if price.get("reasoning_billing_rule") == "separate_rate":
            reasoning = usage.get("reasoning_tokens")
            if reasoning is None:
                return None, "unavailable"
            cost += reasoning * price["reasoning_per_million"] / 1_000_000
    return cost, "estimated"


def worst_case_cost(
    price: dict[str, Any] | None,
    *,
    input_tokens: int,
    max_output_tokens: int,
) -> float | None:
    if not price_is_complete(price):
        return None
    assert price is not None
    cost = float(price.get("per_call_usd") or 0.0)
    if price.get("input_per_million") is not None:
        cost += input_tokens * price["input_per_million"] / 1_000_000
    if price.get("output_per_million") is not None:
        cost += max_output_tokens * price["output_per_million"] / 1_000_000
    if price.get("reasoning_billing_rule") == "separate_rate":
        cost += max_output_tokens * price["reasoning_per_million"] / 1_000_000
    return cost


def estimate_input_tokens(payload_bytes: int) -> int:
    """Conservative (over-)estimate used only for pre-call reservations."""

    return int(math.ceil(max(0, payload_bytes) / CHARS_PER_TOKEN_CONSERVATIVE)) + 64


class BudgetDecision(dict):
    @property
    def allowed(self) -> bool:
        return bool(self.get("allowed"))


class CostLedger:
    """Price book, reservations, and usage events on the PAPER SQLite store."""

    def __init__(
        self,
        transaction: Callable[[], Any],
        read: Callable[[str, tuple], list[sqlite3.Row]],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._transaction = transaction
        self._read = read
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ---- price book -------------------------------------------------
    def add_price(self, value: dict[str, Any]) -> dict[str, Any]:
        entry = validate_price_entry(value)
        with self._transaction() as db:
            existing = db.execute(
                "SELECT price_id FROM ai_price_book WHERE provider_kind=? AND model=? AND version=?",
                (entry["provider_kind"], entry["model"], entry["version"]),
            ).fetchone()
            if existing is not None:
                raise BudgetError("price-book versions are immutable; add a new version")
            cursor = db.execute(
                "INSERT INTO ai_price_book(provider_kind, model, version, effective_from, currency, "
                "input_per_million, cached_input_per_million, output_per_million, reasoning_per_million, "
                "per_call_usd, reasoning_billing_rule, source, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["provider_kind"],
                    entry["model"],
                    entry["version"],
                    entry["effective_from"],
                    entry["currency"],
                    entry["input_per_million"],
                    entry["cached_input_per_million"],
                    entry["output_per_million"],
                    entry["reasoning_per_million"],
                    entry["per_call_usd"],
                    entry["reasoning_billing_rule"],
                    entry["source"],
                    iso_utc(self._clock()),
                ),
            )
            entry["price_id"] = int(cursor.lastrowid)
        return entry

    def price_book(self) -> list[dict[str, Any]]:
        rows = self._read("SELECT * FROM ai_price_book ORDER BY provider_kind, model, effective_from, price_id", ())
        return [dict(row) for row in rows]

    def current_price(
        self, provider_kind: str, model: str, *, at: datetime | None = None
    ) -> dict[str, Any] | None:
        moment = iso_utc(at or self._clock())
        rows = self._read(
            "SELECT * FROM ai_price_book WHERE provider_kind=? AND model=? AND effective_from<=? "
            "ORDER BY effective_from DESC, price_id DESC LIMIT 1",
            (provider_kind, model, moment),
        )
        return dict(rows[0]) if rows else None

    # ---- budget -----------------------------------------------------
    @staticmethod
    def _spent(db: sqlite3.Connection, experiment_id: str, since: str | None, cycle_id: str | None = None) -> float:
        clauses = ["experiment_id=?", "status IN ('reserved', 'reconciled')"]
        params: list[Any] = [experiment_id]
        if since is not None:
            clauses.append("created_at>=?")
            params.append(since)
        if cycle_id is not None:
            clauses.append("cycle_id=?")
            params.append(cycle_id)
        row = db.execute(
            "SELECT COALESCE(SUM(CASE WHEN status='reserved' THEN reserved_usd ELSE COALESCE(charged_usd, 0) END), 0) "
            f"AS value FROM ai_budget_reservations WHERE {' AND '.join(clauses)}",
            tuple(params),
        ).fetchone()
        return float(row["value"])

    @staticmethod
    def _count(
        db: sqlite3.Connection,
        experiment_id: str,
        *,
        kinds: tuple[str, ...] | None,
        since: str | None,
        cycle_id: str | None = None,
    ) -> int:
        clauses = ["experiment_id=?", "status IN ('reserved', 'reconciled')", "call_type!='test_connection'"]
        params: list[Any] = [experiment_id]
        if kinds is not None:
            clauses.append(f"call_type IN ({','.join('?' for _ in kinds)})")
            params.extend(kinds)
        if since is not None:
            clauses.append("created_at>=?")
            params.append(since)
        if cycle_id is not None:
            clauses.append("cycle_id=?")
            params.append(cycle_id)
        row = db.execute(
            f"SELECT COUNT(*) AS value FROM ai_budget_reservations WHERE {' AND '.join(clauses)}",
            tuple(params),
        ).fetchone()
        return int(row["value"])

    def _event_locked(
        self,
        db: sqlite3.Connection,
        experiment_id: str,
        *,
        cycle_id: str | None,
        provider_id: str | None,
        call_type: str | None,
        event_type: str,
        code: str,
        scope: str | None,
        limit_action: str | None,
        details: dict[str, Any],
    ) -> None:
        db.execute(
            "INSERT INTO ai_budget_events(experiment_id, cycle_id, provider_id, call_type, event_type, "
            "scope, code, limit_action, policy_version, details_json, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                experiment_id,
                cycle_id,
                provider_id,
                call_type,
                event_type,
                scope,
                code,
                limit_action,
                BUDGET_POLICY_VERSION,
                canonical_json(details),
                iso_utc(self._clock()),
            ),
        )

    def record_event(self, experiment_id: str, **kwargs: Any) -> None:
        with self._transaction() as db:
            self._event_locked(db, experiment_id, **kwargs)

    def reserve(
        self,
        *,
        experiment_id: str,
        cycle_id: str | None,
        provider: dict[str, Any],
        call_type: str,
        budget: dict[str, Any],
        payload_bytes: int,
    ) -> BudgetDecision:
        """Atomically check limits and reserve worst-case spend before a paid call."""

        if call_type not in CALL_TYPES:
            raise BudgetError("AI call type is unsupported")
        is_gpt = call_type.startswith("gpt") or call_type == "manual_analysis"
        is_jev = call_type in {"jev_decision", "jev_replan"}
        max_in = budget["gpt_max_input_tokens"] if not provider["kind"].endswith("jev") else budget["jev_max_input_tokens"]
        max_out = budget["gpt_max_output_tokens"] if not provider["kind"].endswith("jev") else budget["jev_max_output_tokens"]
        input_estimate = estimate_input_tokens(payload_bytes)
        now = self._clock().astimezone(timezone.utc)
        price = self.current_price(provider["kind"], provider["model"], at=now)
        worst = worst_case_cost(price, input_tokens=min(input_estimate, max_in), max_output_tokens=max_out)
        base = {
            "provider_id": provider["provider_id"],
            "call_type": call_type,
            "input_token_estimate": input_estimate,
            "max_output_tokens": max_out,
            "worst_case_usd": worst,
            "price_id": price["price_id"] if price else None,
            "price_book_version": price["version"] if price else None,
            "limit_action": budget["limit_action"],
            "policy_version": BUDGET_POLICY_VERSION,
        }
        day_start = iso_utc(now.replace(hour=0, minute=0, second=0, microsecond=0))
        hour_start = iso_utc(now - timedelta(hours=1))
        with self._transaction() as db:
            def block(code: str, scope: str, reason: str, **extra: Any) -> BudgetDecision:
                details = {**base, "reason": reason, **extra}
                self._event_locked(
                    db,
                    experiment_id,
                    cycle_id=cycle_id,
                    provider_id=provider["provider_id"],
                    call_type=call_type,
                    event_type="blocked",
                    code=code,
                    scope=scope,
                    limit_action=budget["limit_action"],
                    details=details,
                )
                return BudgetDecision({**details, "allowed": False, "code": code, "scope": scope})

            if input_estimate > max_in:
                return block("INPUT_TOKEN_LIMIT", "call", "estimated input tokens exceed the configured limit")
            if worst is None and call_type != "test_connection":
                # Scheduled/decision calls fail closed. An explicit operator Test Connection may
                # proceed unpriced; its ledger row is marked cost_status=unavailable, never zero.
                return block(
                    "UNKNOWN_PRICE",
                    "price_book",
                    "no complete price-book entry exists for this provider/model; unknown price is not zero",
                )
            if worst is None:
                self._event_locked(
                    db,
                    experiment_id,
                    cycle_id=cycle_id,
                    provider_id=provider["provider_id"],
                    call_type=call_type,
                    event_type="unpriced",
                    code="UNPRICED_TEST_CONNECTION",
                    scope="price_book",
                    limit_action=budget["limit_action"],
                    details=base,
                )
            worst_value = float(worst or 0.0)
            if budget["enabled"]:
                if is_gpt and budget["max_gpt_call_usd"] is not None and worst_value > budget["max_gpt_call_usd"]:
                    return block("MAX_CALL_COST", "call", "worst-case call cost exceeds the per-call limit")
                if call_type != "test_connection":
                    if budget["max_paid_calls"] is not None and self._count(
                        db, experiment_id, kinds=None, since=None
                    ) >= budget["max_paid_calls"]:
                        return block("MAX_PAID_CALLS", "experiment", "experiment paid-call limit reached")
                    if is_gpt:
                        if budget["max_gpt_calls_per_hour"] is not None and self._count(
                            db, experiment_id, kinds=("gpt_escalation", "gpt_arm", "manual_analysis", "gpt_replan"), since=hour_start
                        ) >= budget["max_gpt_calls_per_hour"]:
                            return block("GPT_HOURLY_CALLS", "hour", "GPT calls per hour limit reached")
                        if budget["max_gpt_calls_per_day"] is not None and self._count(
                            db, experiment_id, kinds=("gpt_escalation", "gpt_arm", "manual_analysis", "gpt_replan"), since=day_start
                        ) >= budget["max_gpt_calls_per_day"]:
                            return block("GPT_DAILY_CALLS", "day", "GPT calls per day limit reached")
                        if cycle_id and budget["max_gpt_calls_per_cycle"] is not None and self._count(
                            db, experiment_id, kinds=("gpt_escalation", "gpt_arm"), since=None, cycle_id=cycle_id
                        ) >= budget["max_gpt_calls_per_cycle"]:
                            return block("GPT_CYCLE_CALLS", "cycle", "GPT calls per cycle limit reached")
                    if is_jev and budget["max_jev_calls_per_day"] is not None and self._count(
                        db, experiment_id, kinds=("jev_decision", "jev_replan"), since=day_start
                    ) >= budget["max_jev_calls_per_day"]:
                        return block("JEV_DAILY_CALLS", "day", "Jev calls per day limit reached")
                day_spent = self._spent(db, experiment_id, day_start)
                experiment_spent = self._spent(db, experiment_id, None)
                if budget["daily_usd"] is not None and day_spent + worst_value > budget["daily_usd"] + 1e-12:
                    return block(
                        "DAILY_BUDGET", "day", "reserving this call would exceed the daily AI budget",
                        spent_usd=day_spent,
                    )
                if budget["experiment_usd"] is not None and experiment_spent + worst_value > budget["experiment_usd"] + 1e-12:
                    return block(
                        "EXPERIMENT_BUDGET", "experiment", "reserving this call would exceed the experiment AI budget",
                        spent_usd=experiment_spent,
                    )
                if cycle_id and budget["max_cycle_usd"] is not None:
                    cycle_spent = self._spent(db, experiment_id, None, cycle_id=cycle_id)
                    if cycle_spent + worst_value > budget["max_cycle_usd"] + 1e-12:
                        return block(
                            "CYCLE_BUDGET", "cycle", "reserving this call would exceed the per-cycle AI budget",
                            spent_usd=cycle_spent,
                        )
            reservation_id = f"rsv-{uuid.uuid4().hex}"
            db.execute(
                "INSERT INTO ai_budget_reservations(reservation_id, experiment_id, cycle_id, provider_id, "
                "provider_kind, call_type, reserved_usd, status, price_id, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?)",
                (
                    reservation_id,
                    experiment_id,
                    cycle_id,
                    provider["provider_id"],
                    provider["kind"],
                    call_type,
                    worst_value,
                    base["price_id"],
                    iso_utc(now),
                ),
            )
            self._event_locked(
                db,
                experiment_id,
                cycle_id=cycle_id,
                provider_id=provider["provider_id"],
                call_type=call_type,
                event_type="reserved",
                code="RESERVED",
                scope="call",
                limit_action=budget["limit_action"],
                details={**base, "reservation_id": reservation_id},
            )
        return BudgetDecision({**base, "allowed": True, "code": "RESERVED", "reservation_id": reservation_id, "price": price})

    def settle(
        self,
        reservation_id: str,
        *,
        experiment_id: str,
        charged_usd: float | None,
        keep_reserved_when_unknown: bool,
        budget: dict[str, Any],
    ) -> dict[str, Any]:
        """Replace a reservation with actual cost; release unused budget; emit warnings."""

        now = iso_utc(self._clock())
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM ai_budget_reservations WHERE reservation_id=?", (reservation_id,)
            ).fetchone()
            if row is None or row["status"] != "reserved":
                raise BudgetError("budget reservation is not open")
            reserved = float(row["reserved_usd"])
            if charged_usd is None:
                charged = reserved if keep_reserved_when_unknown else 0.0
                status = "reconciled" if keep_reserved_when_unknown else "released"
            else:
                charged = float(charged_usd)
                status = "reconciled"
            db.execute(
                "UPDATE ai_budget_reservations SET status=?, charged_usd=?, settled_at=? WHERE reservation_id=?",
                (status, charged, now, reservation_id),
            )
            self._event_locked(
                db,
                experiment_id,
                cycle_id=row["cycle_id"],
                provider_id=row["provider_id"],
                call_type=row["call_type"],
                event_type="reconciled" if status == "reconciled" else "released",
                code="RECONCILED" if status == "reconciled" else "RELEASED",
                scope="call",
                limit_action=budget["limit_action"],
                details={
                    "reservation_id": reservation_id,
                    "reserved_usd": reserved,
                    "charged_usd": charged,
                    "released_usd": max(0.0, reserved - charged),
                    "actual_usage_known": charged_usd is not None,
                },
            )
            warnings = self._warnings_locked(db, experiment_id, budget)
        return {"reservation_id": reservation_id, "charged_usd": charged, "status": status, "warnings": warnings}

    def _warnings_locked(self, db: sqlite3.Connection, experiment_id: str, budget: dict[str, Any]) -> list[dict[str, Any]]:
        now = self._clock().astimezone(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        emitted: list[dict[str, Any]] = []
        for scope, limit, since, key in (
            ("day", budget["daily_usd"], iso_utc(day_start), day_start.date().isoformat()),
            ("experiment", budget["experiment_usd"], None, "experiment"),
        ):
            if not limit:
                continue
            utilization = self._spent(db, experiment_id, since) / limit
            for threshold in budget["warning_thresholds"]:
                if utilization + 1e-12 < threshold:
                    continue
                code = f"WARN_{int(round(threshold * 100))}"
                already = db.execute(
                    "SELECT 1 FROM ai_budget_events WHERE experiment_id=? AND event_type='warning' "
                    "AND scope=? AND code=? AND details_json LIKE ?",
                    (experiment_id, scope, code, f'%"period":"{key}"%'),
                ).fetchone()
                if already:
                    continue
                details = {"period": key, "utilization": utilization, "threshold": threshold, "limit_usd": limit}
                self._event_locked(
                    db,
                    experiment_id,
                    cycle_id=None,
                    provider_id=None,
                    call_type=None,
                    event_type="warning",
                    code=code,
                    scope=scope,
                    limit_action=budget["limit_action"],
                    details=details,
                )
                emitted.append({"scope": scope, "code": code, **details})
        return emitted

    def record_usage(self, event: dict[str, Any]) -> str:
        usage_event_id = event.get("usage_event_id") or f"use-{uuid.uuid4().hex}"
        if event.get("cost_status") not in COST_STATUSES:
            raise BudgetError("cost_status is invalid")
        with self._transaction() as db:
            db.execute(
                "INSERT INTO ai_usage_events(usage_event_id, experiment_id, cycle_id, symbol, arm, provider_id, "
                "provider_kind, model, returned_model, reasoning_effort, call_type, started_at, completed_at, "
                "latency_ms, status, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, price_id, "
                "price_book_version, estimated_cost_usd, billed_cost_usd, cost_status, reservation_id, "
                "provider_request_id, provider_response_id, error_code, real_external_call, decision, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    usage_event_id,
                    event["experiment_id"],
                    event.get("cycle_id"),
                    event.get("symbol"),
                    event.get("arm"),
                    event["provider_id"],
                    event["provider_kind"],
                    event["model"],
                    event.get("returned_model"),
                    event.get("reasoning_effort"),
                    event["call_type"],
                    event["started_at"],
                    event.get("completed_at"),
                    event.get("latency_ms"),
                    event["status"],
                    event.get("input_tokens"),
                    event.get("cached_input_tokens"),
                    event.get("output_tokens"),
                    event.get("reasoning_tokens"),
                    event.get("price_id"),
                    event.get("price_book_version"),
                    event.get("estimated_cost_usd"),
                    event.get("billed_cost_usd"),
                    event["cost_status"],
                    event.get("reservation_id"),
                    event.get("provider_request_id"),
                    event.get("provider_response_id"),
                    event.get("error_code"),
                    1 if event.get("real_external_call") else 0,
                    event.get("decision"),
                    iso_utc(self._clock()),
                ),
            )
        return usage_event_id

    def set_billed_cost(self, usage_event_id: str, billed_usd: float) -> None:
        billed = _number(billed_usd, "billed_cost_usd")
        with self._transaction() as db:
            cursor = db.execute(
                "UPDATE ai_usage_events SET billed_cost_usd=?, cost_status='exact' WHERE usage_event_id=?",
                (billed, usage_event_id),
            )
            if cursor.rowcount != 1:
                raise BudgetError("usage event was not found")

    # ---- reads -------------------------------------------------------
    def usage_events(self, experiment_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._read(
                "SELECT * FROM ai_usage_events WHERE experiment_id=? ORDER BY started_at, usage_event_id",
                (experiment_id,),
            )
        ]

    def reservations(self, experiment_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._read(
                "SELECT * FROM ai_budget_reservations WHERE experiment_id=? ORDER BY created_at, reservation_id",
                (experiment_id,),
            )
        ]

    def budget_events(self, experiment_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ai_budget_events WHERE experiment_id=? ORDER BY event_id DESC"
        params: tuple = (experiment_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (experiment_id, limit)
        return [dict(row) for row in self._read(sql, params)]

    def budget_status(self, experiment_id: str, budget: dict[str, Any]) -> dict[str, Any]:
        now = self._clock().astimezone(timezone.utc)
        day_start = iso_utc(now.replace(hour=0, minute=0, second=0, microsecond=0))
        rows = self.reservations(experiment_id)

        def spent(since: str | None) -> float:
            total = 0.0
            for row in rows:
                if since is not None and row["created_at"] < since:
                    continue
                if row["status"] == "reserved":
                    total += float(row["reserved_usd"])
                elif row["status"] == "reconciled":
                    total += float(row["charged_usd"] or 0.0)
            return total

        day = spent(day_start)
        experiment = spent(None)
        reserved_open = sum(float(row["reserved_usd"]) for row in rows if row["status"] == "reserved")

        def remaining(limit: float | None, used: float) -> float | None:
            return None if limit is None else max(0.0, limit - used)

        def utilization(limit: float | None, used: float) -> float | None:
            return None if not limit else used / limit

        day_util = utilization(budget["daily_usd"], day)
        exp_util = utilization(budget["experiment_usd"], experiment)
        exhausted = bool(
            budget["enabled"]
            and ((day_util is not None and day_util >= 1.0) or (exp_util is not None and exp_util >= 1.0))
        )
        hours_elapsed = max(1 / 60, (now - now.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 3600)
        projected_day = day / hours_elapsed * 24 if day > 0 else 0.0
        return {
            "budget": budget,
            "spent_today_usd": day,
            "spent_experiment_usd": experiment,
            "open_reservations_usd": reserved_open,
            "remaining_today_usd": remaining(budget["daily_usd"], day),
            "remaining_experiment_usd": remaining(budget["experiment_usd"], experiment),
            "utilization_today": day_util,
            "utilization_experiment": exp_util,
            "exhausted": exhausted,
            "projection": {
                "label": "ESTIMATE",
                "projected_today_usd": projected_day,
                "days_until_experiment_budget_exhausted": (
                    (budget["experiment_usd"] - experiment) / projected_day
                    if budget["experiment_usd"] and projected_day > 0
                    else None
                ),
            },
            "limit_action": budget["limit_action"],
            "policy_version": BUDGET_POLICY_VERSION,
        }


def economic_summary(
    *,
    positions: list[dict[str, Any]],
    usage_events: list[dict[str, Any]],
    cycles: list[dict[str, Any]],
    fx: dict[str, Any],
    starting_balance_usdt: float,
    minimum_aligned_sample: int = 30,
) -> dict[str, Any]:
    """Trading PnL (USDT) and AI cost (USD) kept separate unless an explicit FX policy exists."""

    primary = [p for p in positions if p.get("cohort") == "primary"]
    closed = [p for p in primary if p.get("status") == "closed" and p.get("closed_pnl_recorded")]
    open_ = [p for p in primary if p.get("status") == "open"]
    gross = sum(float(p.get("realized_gross") or 0.0) for p in closed)
    fees = sum(float(p.get("entry_fee") or 0.0) + float(p.get("exit_fees") or 0.0) for p in closed)
    funding = sum(float(p.get("funding_paid") or 0.0) for p in closed)
    slippage = sum(float(p.get("slippage_paid") or 0.0) for p in closed)
    realized_net = sum(float(p.get("realized_pnl") or 0.0) for p in closed)
    unrealized = sum(
        (1 if p["side"] == "long" else -1)
        * (float(p["mark_price"]) - float(p["entry_price"]))
        * float(p["quantity"])
        for p in open_
    )
    net_trading = realized_net + unrealized
    wins = [p for p in closed if float(p.get("realized_pnl") or 0) > 0]

    real_events = [
        e
        for e in usage_events
        if e.get("call_type") != "test_connection" and e.get("real_external_call")
    ]
    known = [e for e in real_events if e.get("cost_status") in {"exact", "estimated"}]
    unknown_count = sum(1 for e in real_events if e.get("cost_status") == "unavailable" and e.get("status") == "ok")

    def cost(event: dict[str, Any]) -> float:
        billed = event.get("billed_cost_usd")
        return float(billed if billed is not None else event.get("estimated_cost_usd") or 0.0)

    jev_cost = sum(cost(e) for e in known if str(e.get("provider_kind", "")).endswith("jev"))
    gpt_cost = sum(cost(e) for e in known if not str(e.get("provider_kind", "")).endswith("jev"))
    total_ai = jev_cost + gpt_cost
    ai_cost_complete = unknown_count == 0

    def group(key: Callable[[dict[str, Any]], str]) -> dict[str, float]:
        result: dict[str, float] = {}
        for event in known:
            name = key(event) or "unattributed"
            result[name] = result.get(name, 0.0) + cost(event)
        return dict(sorted(result.items()))

    no_trade_cycles = {
        cycle["cycle_id"]
        for cycle in cycles
        if (cycle.get("payload") or {}).get("primary_decision", {}).get("decision") == "NO_TRADE"
    }
    eligible_cycles = {
        cycle["cycle_id"]
        for cycle in cycles
        if (cycle.get("payload") or {}).get("quant_gate", {}).get("eligible")
    }
    analyzed = [c for c in cycles if c.get("status") == "complete"]

    rate = fx.get("usdt_per_usd") if fx.get("mode") == "manual" else None
    ai_cost_usdt = total_ai * rate if rate is not None else None
    net_economic = net_trading - ai_cost_usdt if ai_cost_usdt is not None and ai_cost_complete else None
    reason = None
    if rate is None:
        reason = "no USD/USDT cost FX policy configured; AI cost (USD) and trading PnL (USDT) remain separate"
    elif not ai_cost_complete:
        reason = "some paid calls have unavailable cost; net economic PnL is not computed"

    def ratio(numerator: float | None, denominator: float) -> float | None:
        return None if numerator is None or denominator <= 0 else numerator / denominator

    arm_cost: dict[str, float] = group(lambda e: e.get("arm") or "shared")
    aligned: dict[str, Any] = {"status": "insufficient_sample", "minimum_closed_trades_per_arm": minimum_aligned_sample}
    arm_closed: dict[str, list[dict[str, Any]]] = {}
    for position in positions:
        cohort = str(position.get("cohort", ""))
        if cohort.startswith("arm-") and position.get("status") == "closed" and position.get("closed_pnl_recorded"):
            arm_closed.setdefault(cohort[4:], []).append(position)
    quant_n = len(arm_closed.get("quant", []))
    hybrid_n = len(arm_closed.get("hybrid", []))
    if quant_n >= minimum_aligned_sample and hybrid_n >= minimum_aligned_sample and rate is not None:
        quant_pnl = sum(float(p.get("realized_pnl") or 0) for p in arm_closed["quant"])
        hybrid_pnl = sum(float(p.get("realized_pnl") or 0) for p in arm_closed["hybrid"])
        hybrid_ai = arm_cost.get("hybrid", 0.0) + arm_cost.get("shared", 0.0)
        aligned = {
            "status": "computed",
            "comparison": "hybrid_vs_quant",
            "incremental_pnl_usdt": hybrid_pnl - quant_pnl,
            "incremental_ai_cost_usd": hybrid_ai,
            "ai_cost_efficiency": ratio((hybrid_pnl - quant_pnl), hybrid_ai * rate) if hybrid_ai else None,
            "sample": {"quant_closed": quant_n, "hybrid_closed": hybrid_n},
            "causal_claim": False,
        }
    else:
        aligned["sample"] = {"quant_closed": quant_n, "hybrid_closed": hybrid_n}

    return {
        "schema_version": "paper-economics.v1",
        "trading_currency": "USDT",
        "ai_cost_currency": "USD",
        "trading": {
            "gross_trading_pnl_usdt": gross,
            "fees_usdt": fees,
            "funding_usdt": funding,
            "slippage_usdt": slippage,
            "realized_net_pnl_usdt": realized_net,
            "unrealized_pnl_usdt": unrealized,
            "net_trading_pnl_usdt": net_trading,
            "closed_trades": len(closed),
            "winning_trades": len(wins),
        },
        "ai_cost": {
            "jev_cost_usd": jev_cost,
            "gpt_cost_usd": gpt_cost,
            "total_ai_cost_usd": total_ai,
            "paid_calls": len(real_events),
            "calls_with_unavailable_cost": unknown_count,
            "complete": ai_cost_complete,
            "gpt_escalation_cost_usd": sum(
                cost(e) for e in known if e.get("call_type") == "gpt_escalation"
            ),
            "cost_on_no_trade_usd": sum(cost(e) for e in known if e.get("cycle_id") in no_trade_cycles),
            "by_provider": group(lambda e: e.get("provider_id")),
            "by_model": group(lambda e: e.get("returned_model") or e.get("model")),
            "by_asset": group(lambda e: e.get("symbol")),
            "by_arm": arm_cost,
            "by_day": group(lambda e: str(e.get("started_at", ""))[:10]),
        },
        "kpis": {
            "ai_cost_per_analysis_usd": ratio(total_ai, len(analyzed)),
            "ai_cost_per_eligible_case_usd": ratio(total_ai, len(eligible_cycles)),
            "ai_cost_per_trade_usd": ratio(total_ai, len(closed)),
            "ai_cost_per_winning_trade_usd": ratio(total_ai, len(wins)),
            "ai_cost_pct_of_gross_profit": (
                ratio(ai_cost_usdt, gross) if ai_cost_usdt is not None and gross > 0 else None
            ),
            "ai_cost_pct_of_net_trading_pnl": (
                ratio(ai_cost_usdt, net_trading) if ai_cost_usdt is not None and net_trading > 0 else None
            ),
            "net_economic_expectancy_per_trade_usdt": ratio(net_economic, len(closed)),
            "net_economic_return_on_capital": (
                net_economic / starting_balance_usdt if net_economic is not None and starting_balance_usdt > 0 else None
            ),
            "denominators": {
                "analyses": len(analyzed),
                "eligible_cases": len(eligible_cycles),
                "closed_trades": len(closed),
                "winning_trades": len(wins),
            },
        },
        "fx": fx,
        "ai_cost_usdt": ai_cost_usdt,
        "net_economic_pnl_usdt": net_economic,
        "net_economic_unavailable_reason": reason,
        "aligned_arm_value": aligned,
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def provider_ledger_summary(
    usage_events: list[dict[str, Any]], budget_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Per provider/model: calls, tokens, latency percentiles, estimated vs billed cost, blocks."""

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in usage_events:
        groups.setdefault((event["provider_id"], event.get("model") or "unavailable"), []).append(event)
    blocks: dict[str, int] = {}
    for event in budget_events:
        if event.get("event_type") == "blocked" and event.get("provider_id"):
            blocks[event["provider_id"]] = blocks.get(event["provider_id"], 0) + 1
    rows = []
    for (provider_id, model), events in sorted(groups.items()):
        attempted = [e for e in events if e.get("status") != "budget_blocked"]
        latencies = [float(e["latency_ms"]) for e in attempted if isinstance(e.get("latency_ms"), (int, float))]

        def total(field: str) -> int | None:
            values = [e.get(field) for e in attempted]
            known = [v for v in values if isinstance(v, int)]
            return sum(known) if known else None

        estimated = [e["estimated_cost_usd"] for e in attempted if e.get("estimated_cost_usd") is not None]
        billed = [e["billed_cost_usd"] for e in attempted if e.get("billed_cost_usd") is not None]
        rows.append(
            {
                "provider_id": provider_id,
                "provider_kind": events[0].get("provider_kind"),
                "model": model,
                "returned_models": sorted({e["returned_model"] for e in events if e.get("returned_model")}),
                "reasoning_effort": events[0].get("reasoning_effort"),
                "calls": len(attempted),
                "real_external_calls": sum(1 for e in attempted if e.get("real_external_call")),
                "failed_calls": sum(1 for e in attempted if e.get("status") == "failed"),
                "input_tokens": total("input_tokens"),
                "cached_input_tokens": total("cached_input_tokens"),
                "output_tokens": total("output_tokens"),
                "reasoning_tokens": total("reasoning_tokens"),
                "latency_p50_ms": _percentile(latencies, 0.5),
                "latency_p95_ms": _percentile(latencies, 0.95),
                "estimated_cost_usd": sum(estimated) if estimated else None,
                "billed_cost_usd": sum(billed) if billed else None,
                "calls_with_unavailable_cost": sum(1 for e in attempted if e.get("cost_status") == "unavailable"),
                "budget_blocks": blocks.get(provider_id, 0),
                "price_book_versions": sorted({e["price_book_version"] for e in events if e.get("price_book_version")}),
            }
        )
    return rows
