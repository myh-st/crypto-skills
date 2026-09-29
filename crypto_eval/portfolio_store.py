"""SQLite schema and settings for the PAPER portfolio OS (Spot, orders, authority, journal).

Tables live in the same database as the futures runtime (created by ``PaperStore``).
Ledger-style tables (fills, management events, activity, reviews) are append-only.
No credential value or reference is ever stored here.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from .execution_safety import DEFAULT_SAFETY_SETTINGS, validate_safety_settings
from .paper_contracts import PaperTradingError


MANAGEMENT_MODES = ("AUTO_PAPER", "RECOMMEND_ONLY", "MANUAL_OVERRIDE", "PAUSED")
ACTIVITY_SOURCES = ("AI", "USER", "SYSTEM")
ACTIVITY_CATEGORIES = ("SIGNAL", "DECISION", "RISK", "ORDER", "FILL", "MANAGEMENT", "COST", "ALERT", "OUTCOME")
SEVERITIES = ("INFO", "WATCH", "ACTION", "CRITICAL")
QUICK_INTENTS = (
    "tighten_risk",
    "protect_profit",
    "give_more_room",
    "reduce_exposure",
    "exit_if_thesis_weakened",
    "reassess",
)
LEARNING_TAGS = (
    "BAD_DIRECTION",
    "LATE_ENTRY",
    "FALSE_BREAKOUT",
    "STOP_TOO_TIGHT",
    "STOP_TOO_WIDE",
    "TARGET_TOO_AMBITIOUS",
    "PREMATURE_EXIT",
    "REGIME_MISCLASSIFIED",
    "CORRELATION_OVEREXPOSURE",
    "BTC_SHOCK",
    "FUNDING_DRAG",
    "SLIPPAGE_DRAG",
    "AI_COST_TOO_HIGH",
    "AI_FILTER_HELPED",
    "AI_OVERRIDE_HELPED",
    "AI_OVERRIDE_HURT",
    "HUMAN_OVERRIDE_HELPED",
    "HUMAN_OVERRIDE_HURT",
)

PORTFOLIO_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_settings(
    experiment_id TEXT PRIMARY KEY,
    settings_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spot_wallets(
    experiment_id TEXT NOT NULL,
    quote TEXT NOT NULL,
    starting_balance REAL NOT NULL,
    cash_balance REAL NOT NULL,
    reserved_quote REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(experiment_id, quote)
);
CREATE TABLE IF NOT EXISTS spot_holdings(
    holding_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    exchange_symbol TEXT NOT NULL,
    symbol TEXT NOT NULL,
    base TEXT NOT NULL,
    quote TEXT NOT NULL,
    quantity REAL NOT NULL,
    reserved_quantity REAL NOT NULL DEFAULT 0,
    avg_cost REAL NOT NULL,
    cost_basis REAL NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    fees_paid REAL NOT NULL DEFAULT 0,
    slippage_paid REAL NOT NULL DEFAULT 0,
    total_bought REAL NOT NULL DEFAULT 0,
    total_sold REAL NOT NULL DEFAULT 0,
    mark_price REAL,
    mark_at TEXT,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    exit_reason TEXT,
    data_origin TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS spot_holdings_open_idx ON spot_holdings(experiment_id, status, instrument_id);
CREATE TABLE IF NOT EXISTS spot_orders(
    order_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    exchange_symbol TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    source TEXT NOT NULL,
    requested_quantity REAL NOT NULL,
    requested_quote REAL,
    limit_price REAL,
    filled_quantity REAL NOT NULL DEFAULT 0,
    filled_quote REAL NOT NULL DEFAULT 0,
    fees REAL NOT NULL DEFAULT 0,
    avg_fill_price REAL,
    status TEXT NOT NULL,
    holding_id TEXT,
    reserved_quote REAL NOT NULL DEFAULT 0,
    reserved_quantity REAL NOT NULL DEFAULT 0,
    reject_code TEXT,
    risk_json TEXT NOT NULL,
    replaces_order_id TEXT,
    replaced_by_order_id TEXT,
    last_checked_bar TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS spot_orders_status_idx ON spot_orders(experiment_id, status);
CREATE TABLE IF NOT EXISTS spot_fills(
    fill_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    holding_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL,
    slippage_cost REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    liquidity TEXT NOT NULL,
    as_of TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS position_meta(
    position_ref TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    market_type TEXT NOT NULL,
    instrument_id TEXT,
    management_mode TEXT NOT NULL,
    owner_source TEXT NOT NULL,
    plan_version INTEGER NOT NULL DEFAULT 1,
    stop_price REAL,
    targets_json TEXT NOT NULL DEFAULT '[]',
    thesis_status TEXT NOT NULL DEFAULT 'unknown',
    last_ai_review_at TEXT,
    next_ai_review_at TEXT,
    last_user_override_at TEXT,
    review_exposure REAL,
    initial_risk REAL,
    review_count INTEGER NOT NULL DEFAULT 0,
    core_quantity REAL NOT NULL DEFAULT 0,
    stop_breaches INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS management_events(
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    position_ref TEXT,
    order_ref TEXT,
    market_type TEXT,
    action TEXT NOT NULL,
    source TEXT NOT NULL,
    request_id TEXT,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    risk_before REAL,
    risk_after REAL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS management_events_position_idx ON management_events(experiment_id, position_ref);
CREATE TABLE IF NOT EXISTS order_requests(
    client_request_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replan_proposals(
    proposal_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    position_ref TEXT NOT NULL,
    market_type TEXT NOT NULL,
    status TEXT NOT NULL,
    origin TEXT NOT NULL,
    intent TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT
);
CREATE INDEX IF NOT EXISTS replan_status_idx ON replan_proposals(experiment_id, status);
CREATE TABLE IF NOT EXISTS activity_events(
    event_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    instrument_id TEXT,
    symbol TEXT,
    market_type TEXT,
    position_ref TEXT,
    source TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload_ref TEXT
);
CREATE INDEX IF NOT EXISTS activity_events_time_idx ON activity_events(experiment_id, timestamp);
CREATE TABLE IF NOT EXISTS attention_items(
    attention_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    instrument_id TEXT,
    symbol TEXT,
    position_ref TEXT,
    action_json TEXT NOT NULL,
    status TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT,
    UNIQUE(experiment_id, dedupe_key, first_seen_at)
);
CREATE INDEX IF NOT EXISTS attention_open_idx ON attention_items(experiment_id, status);
CREATE TABLE IF NOT EXISTS post_trade_reviews(
    review_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    position_ref TEXT NOT NULL UNIQUE,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    outcome TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS improvement_hypotheses(
    hypothesis_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    statement TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(experiment_id, tag)
);
CREATE TABLE IF NOT EXISTS portfolio_snapshots(
    experiment_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    total_equity REAL NOT NULL,
    perp_equity REAL NOT NULL,
    spot_equity REAL NOT NULL,
    open_risk REAL NOT NULL,
    gross_exposure REAL NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(experiment_id, as_of)
);
CREATE TABLE IF NOT EXISTS brain_decisions(
    decision_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    cycle_id TEXT,
    cohort TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(decision_id, cohort, created_at)
);
"""

DEFAULT_PORTFOLIO_SETTINGS: dict[str, Any] = {
    "schema_version": "portfolio-settings.v1",
    "spot_quote": "USDT",
    "spot_starting_balance_usdt": 100.0,
    "spot_fee_rate": None,
    "spot_slippage_bps": 5.0,
    "spot_max_allocation_pct": 0.35,
    "spot_max_deployed_pct": 0.9,
    "spot_min_cash_reserve_pct": 0.1,
    "spot_limit_participation": 0.25,
    "perp_manual_max_risk_pct": 0.02,
    "default_ai_management_mode": "AUTO_PAPER",
    "default_user_management_mode": "RECOMMEND_ONLY",
    "automation": {
        "new_entries_paused": False,
        "ai_management_paused": False,
        "emergency_stop": False,
    },
    "safety": dict(DEFAULT_SAFETY_SETTINGS),
    "ai_spot": {
        "enabled": False,
        "trigger": "prefer_spot",
        "allocation_pct": 0.1,
    },
    "brain": {
        "enabled": True,
        "max_asset_risk_pct": 0.03,
        "max_correlated_risk_pct": 0.04,
        "max_direction_risk_pct": 0.05,
        "max_gross_exposure_x": 6.0,
        "min_resize_fraction": 0.25,
        "resize_multiplier": 0.5,
        "de_risk_drawdown_fraction": 0.5,
        "hold_cash_drawdown_fraction": 0.85,
        "hold_cash_min_pct": 0.1,
        "prefer_spot_funding_rate": 0.0005,
        "protect_profit_r": 1.5,
        "spot_max_allocation_pct": 0.35,
    },
    "review": {
        "enabled": True,
        "management_interval_minutes": 60,
        "min_minutes_between_reviews": 15,
        "stop_proximity_pct": 0.01,
        "target_proximity_pct": 0.01,
        "exposure_change_pct": 0.25,
        "use_jev": True,
        "use_luna": True,
        "auto_apply_max_reduce_fraction": 0.5,
    },
    "attention": {
        "near_stop_pct": 0.01,
        "liquidation_buffer_critical_pct": 0.03,
        "budget_warning_utilization": 0.8,
        "pending_order_review_minutes": 60,
        "scheduler_stall_minutes": 45,
        "info_ttl_minutes": 360,
    },
}

_NUMERIC_BOUNDS: dict[str, tuple[float, float]] = {
    "spot_starting_balance_usdt": (1.0, 10_000_000.0),
    "spot_slippage_bps": (0.0, 200.0),
    "spot_max_allocation_pct": (0.01, 1.0),
    "spot_max_deployed_pct": (0.01, 1.0),
    "spot_min_cash_reserve_pct": (0.0, 0.9),
    "spot_limit_participation": (0.01, 1.0),
    "perp_manual_max_risk_pct": (0.0005, 0.05),
    "ai_spot.allocation_pct": (0.01, 0.35),
    "brain.max_asset_risk_pct": (0.001, 0.5),
    "brain.max_correlated_risk_pct": (0.001, 0.5),
    "brain.max_direction_risk_pct": (0.001, 0.5),
    "brain.max_gross_exposure_x": (0.1, 50.0),
    "brain.min_resize_fraction": (0.01, 1.0),
    "brain.resize_multiplier": (0.05, 1.0),
    "brain.de_risk_drawdown_fraction": (0.05, 1.0),
    "brain.hold_cash_drawdown_fraction": (0.05, 1.0),
    "brain.hold_cash_min_pct": (0.0, 0.9),
    "brain.prefer_spot_funding_rate": (0.0, 0.05),
    "brain.protect_profit_r": (0.25, 20.0),
    "brain.spot_max_allocation_pct": (0.01, 1.0),
    "review.management_interval_minutes": (5, 1440),
    "review.min_minutes_between_reviews": (1, 1440),
    "review.stop_proximity_pct": (0.0005, 0.2),
    "review.target_proximity_pct": (0.0005, 0.2),
    "review.exposure_change_pct": (0.01, 5.0),
    "review.auto_apply_max_reduce_fraction": (0.0, 1.0),
    "attention.near_stop_pct": (0.0005, 0.2),
    "attention.liquidation_buffer_critical_pct": (0.001, 0.5),
    "attention.budget_warning_utilization": (0.1, 1.0),
    "attention.pending_order_review_minutes": (1, 10_080),
    "attention.scheduler_stall_minutes": (16, 1440),
    "attention.info_ttl_minutes": (5, 10_080),
}


def default_portfolio_settings() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_PORTFOLIO_SETTINGS)


def _merge(base: dict[str, Any], patch: dict[str, Any], path: str = "") -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        dotted = f"{path}{key}"
        if key not in base:
            raise PaperTradingError(f"portfolio setting {dotted} is not supported")
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise PaperTradingError(f"portfolio setting {dotted} must be an object")
            result[key] = _merge(base[key], value, f"{dotted}.")
        else:
            result[key] = value
    return result


def validate_portfolio_settings(value: dict[str, Any], *, base: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PaperTradingError("portfolio settings must be an object")
    merged = _merge(base or default_portfolio_settings(), {k: v for k, v in value.items() if k != "schema_version"})
    for dotted, (low, high) in _NUMERIC_BOUNDS.items():
        container = merged
        parts = dotted.split(".")
        for part in parts[:-1]:
            container = container[part]
        item = container[parts[-1]]
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise PaperTradingError(f"portfolio setting {dotted} must be a finite number")
        if not low <= float(item) <= high:
            raise PaperTradingError(f"portfolio setting {dotted} must be between {low} and {high}")
        container[parts[-1]] = float(item)
    fee = merged["spot_fee_rate"]
    if fee is not None and (isinstance(fee, bool) or not isinstance(fee, (int, float)) or not 0 <= fee <= 0.01):
        raise PaperTradingError("spot_fee_rate must be null or between 0 and 0.01")
    for field in ("default_ai_management_mode", "default_user_management_mode"):
        if merged[field] not in MANAGEMENT_MODES:
            raise PaperTradingError(f"{field} must be one of {', '.join(MANAGEMENT_MODES)}")
    if merged["spot_quote"] != "USDT":
        raise PaperTradingError("the PAPER spot wallet is quoted in USDT")
    for group in ("automation",):
        for key, item in merged[group].items():
            if not isinstance(item, bool):
                raise PaperTradingError(f"{group}.{key} must be boolean")
    for key in ("enabled",):
        if not isinstance(merged["brain"][key], bool) or not isinstance(merged["review"][key], bool):
            raise PaperTradingError(f"{key} flags must be boolean")
    if not isinstance(merged["ai_spot"]["enabled"], bool):
        raise PaperTradingError("ai_spot.enabled must be boolean")
    if merged["ai_spot"]["trigger"] not in {"prefer_spot", "all_long"}:
        raise PaperTradingError("ai_spot.trigger must be prefer_spot or all_long")
    for key in ("use_jev", "use_luna"):
        if not isinstance(merged["review"][key], bool):
            raise PaperTradingError(f"review.{key} must be boolean")
    if merged["brain"]["hold_cash_drawdown_fraction"] < merged["brain"]["de_risk_drawdown_fraction"]:
        raise PaperTradingError("brain.hold_cash_drawdown_fraction must be at least the de-risk fraction")
    merged["safety"] = validate_safety_settings(merged["safety"])
    merged["schema_version"] = DEFAULT_PORTFOLIO_SETTINGS["schema_version"]
    return merged
