"""Persistent PAPER portfolio, deterministic risk, aligned arms, and scheduler."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .ai_cost import (
    COST_SCHEMA,
    CostLedger,
    economic_summary,
    estimate_cost,
    normalize_usage,
    provider_ledger_summary,
)
from .contracts import canonical_json, digest
from .gate_market import GATE_PROVIDER_ID, GateUsdtFuturesMarketDataProvider
from .paper_ai import (
    AIProviderError,
    FixtureGPTProvider,
    FixtureJevProvider,
    JevAdapter,
    ResponsesAdapter,
    jev_state,
    parse_gpt_intent,
    route_escalation,
)

from .paper_contracts import (
    DEFAULT_SHADOW_LEVERAGE,
    EXPERIMENT_ARMS,
    INTENT_SCHEMA_VERSION,
    MARKET_DATA_ORIGINS,
    OPERATIONAL_EXPERIMENT_FIELDS,
    PaperTradingError,
    TradingIntent,
    default_experiment_config,
    iso_utc,
    parse_utc,
    public_provider_config,
    validate_experiment_config,
    validate_provider_config,
)
from .paper_market import (
    BinanceUsdMFuturesMarketDataProvider,
    FixtureFuturesMarketDataProvider,
    FuturesMarketDataProvider,
    INTERVAL_SECONDS,
    MarketSnapshot,
    WARMUP_PROFILES,
    floor_time,
)
from .gate_account import ReadOnlyGateClient, sync_read_only_account
from .secret_store import CredentialResolver, store_secret
from .portfolio_brain import evaluate_entry as portfolio_brain_entry
from .portfolio_store import PORTFOLIO_SCHEMA
from .execution_safety import KILL_RANK, SAFETY_SCHEMA, classify_market, suspect_print
from .spot_lifecycle import LIFECYCLE_SCHEMA
from .resilience import (
    DB_SCHEMA_VERSION,
    RESILIENCE_SCHEMA,
    ResilienceLedger,
    compact_storage,
    database_health,
    database_path,
    missed_slots,
    storage_health,
)


MARKET_PROVIDER_IDS = {
    "fixture": FixtureFuturesMarketDataProvider.provider_id,
    "binance_usdm": BinanceUsdMFuturesMarketDataProvider.provider_id,
    "gate_usdt": GATE_PROVIDER_ID,
}
DATA_ORIGINS = set(MARKET_DATA_ORIGINS.values())
EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS exchange_accounts(
    account_id TEXT PRIMARY KEY,
    exchange TEXT NOT NULL,
    display_name TEXT NOT NULL,
    environment TEXT NOT NULL,
    settle_currency TEXT NOT NULL,
    expected_ip TEXT,
    enabled INTEGER NOT NULL,
    sync_enabled INTEGER NOT NULL,
    key_secret_id TEXT NOT NULL,
    secret_secret_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exchange_account_syncs(
    sync_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_stream_health(
    health_id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    event TEXT NOT NULL,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS integration_checks(
    check_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _json(value: Any) -> str:
    return canonical_json(value)


def _loads(value: str | bytes | None, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _finite(value: Any, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PaperTradingError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise PaperTradingError(f"{name} must be a finite number in range")
    return result


def _ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise PaperTradingError("market history is too short for feature calculation")
    value = sum(values[:period]) / period
    alpha = 2 / (period + 1)
    for item in values[period:]:
        value = item * alpha + value * (1 - alpha)
    return value


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        raise PaperTradingError("market history is too short for RSI")
    changes = [right - left for left, right in zip(values[-period - 1 : -1], values[-period:])]
    gains = sum(max(change, 0.0) for change in changes) / period
    losses = sum(max(-change, 0.0) for change in changes) / period
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100 - 100 / (1 + gains / losses)


def _atr(candles: list[dict[str, Any]], period: int = 14) -> float:
    if len(candles) < period + 1:
        raise PaperTradingError("market history is too short for ATR")
    ranges = []
    for previous, current in zip(candles[-period - 1 : -1], candles[-period:]):
        ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )
    return sum(ranges) / period


def _merge_history_lane(
    current: list[dict[str, Any]],
    archived: list[dict[str, Any]],
    interval: str,
) -> list[dict[str, Any]]:
    seconds = INTERVAL_SECONDS[interval]
    current_cutoff = parse_utc(current[-1]["close_time"], f"{interval}.current_cutoff")
    merged = {
        candle["open_time"]: dict(candle)
        for candle in archived
        if parse_utc(candle["close_time"], f"{interval}.archive_close") <= current_cutoff
    }
    merged.update({candle["open_time"]: dict(candle) for candle in current})
    ordered = sorted(merged.values(), key=lambda candle: candle["open_time"])
    contiguous: list[dict[str, Any]] = []
    previous_close: datetime | None = None
    for candle in ordered:
        opened = parse_utc(candle["open_time"], f"{interval}.open_time")
        closed = parse_utc(candle["close_time"], f"{interval}.close_time")
        if (closed - opened).total_seconds() != seconds:
            raise PaperTradingError("archived feature history has an invalid interval")
        if previous_close is None or opened != previous_close:
            contiguous = [candle]
        else:
            contiguous.append(candle)
        previous_close = closed
    if contiguous[-1]["close_time"] != current[-1]["close_time"]:
        raise PaperTradingError("archived feature history does not reach the latest closed bar")
    return contiguous


def compute_features(
    snapshot: MarketSnapshot,
    *,
    minimum_signal_strength: float = 0.55,
    signal_gate_enabled: bool = True,
    historical_bars: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Compute deterministic price/volume features from closed bars only."""

    snapshot.validate()
    archived = historical_bars or {}
    bars = _merge_history_lane(
        snapshot.candles_15m, archived.get("15m", []), "15m"
    )
    bars_1h = _merge_history_lane(
        snapshot.candles_1h, archived.get("1h", []), "1h"
    )
    bars_4h = _merge_history_lane(
        snapshot.candles_4h, archived.get("4h", []), "4h"
    )
    closes = [float(bar["close"]) for bar in bars]
    volumes = [float(bar["volume"]) for bar in bars]
    last = bars[-1]
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    ema12_1h = _ema([float(bar["close"]) for bar in bars_1h], 12)
    ema26_1h = _ema([float(bar["close"]) for bar in bars_1h], 26)
    ema12_4h = _ema([float(bar["close"]) for bar in bars_4h], 12)
    ema26_4h = _ema([float(bar["close"]) for bar in bars_4h], 26)
    atr = _atr(bars)
    previous_high = max(bar["high"] for bar in bars[-21:-1])
    previous_low = min(bar["low"] for bar in bars[-21:-1])
    volume_history = volumes[-21:-1]
    volume_mean = sum(volume_history) / len(volume_history)
    variance = sum((value - volume_mean) ** 2 for value in volume_history) / len(volume_history)
    volume_std = math.sqrt(variance)
    volume_zscore = (volumes[-1] - volume_mean) / volume_std if volume_std > 0 else 0.0
    close = float(last["close"])
    direction = "none"
    trigger = "none"
    up_breakout = close > previous_high
    down_breakout = close < previous_low
    uptrend = ema12 > ema26 and ema12_1h > ema26_1h and ema12_4h > ema26_4h
    downtrend = ema12 < ema26 and ema12_1h < ema26_1h and ema12_4h < ema26_4h
    quant_regime = "bull" if uptrend else "bear" if downtrend else "sideways"
    if up_breakout and uptrend:
        direction, trigger = "long", "breakout"
    elif down_breakout and downtrend:
        direction, trigger = "short", "breakout"
    distance = (
        (close - previous_high) / previous_high
        if direction == "long"
        else (previous_low - close) / previous_low
        if direction == "short"
        else 0.0
    )
    strength = min(1.0, max(0.0, 0.58 + max(0.0, volume_zscore) * 0.04 + max(0.0, distance) * 8))
    gate = direction in {"long", "short"} and strength >= minimum_signal_strength
    if not signal_gate_enabled:
        gate = direction in {"long", "short"}
    last_bar_close = parse_utc(last["close_time"], "last_bar.close_time")
    observed_at = parse_utc(snapshot.as_of, "snapshot.as_of")
    rsi = _rsi(closes)
    feature_values = {
        "symbol": snapshot.symbol,
        "data_cutoff": snapshot.data_cutoff,
        "last_bar_close_time": last["close_time"],
        "last_price": close,
        "market_mark_price": float(snapshot.candles_1m[-1]["close"]),
        "feature_history_hash": digest(
            {"15m": bars, "1h": bars_1h, "4h": bars_4h}
        ),
        "feature_history_15m_bars": max(0, len(bars) - len(snapshot.candles_15m)),
        "feature_history_1h_bars": max(0, len(bars_1h) - len(snapshot.candles_1h)),
        "feature_history_4h_bars": max(0, len(bars_4h) - len(snapshot.candles_4h)),
        "ema12": ema12,
        "ema26": ema26,
        "ema12_1h": ema12_1h,
        "ema26_1h": ema26_1h,
        "ema12_4h": ema12_4h,
        "ema26_4h": ema26_4h,
        "atr": atr,
        "atr_pct": atr / close,
        "rsi14": rsi,
        "volume_zscore": volume_zscore,
        "previous_20_bar_high": previous_high,
        "previous_20_bar_low": previous_low,
        "distance_to_breakout": distance,
        "funding_rate": snapshot.funding_rate,
        "open_interest_change_1h": snapshot.open_interest_change_1h,
        "spread_bps": snapshot.spread_bps,
        "quant_direction": direction,
        "quant_regime": quant_regime,
        "signal_trigger": trigger,
        "signal_strength": strength,
        "gate_eligible": bool(gate),
        "freshness_seconds": max(
            0.0,
            (observed_at - last_bar_close).total_seconds(),
        ),
    }
    return feature_values


def build_fast_intent(
    snapshot: MarketSnapshot,
    features: dict[str, Any],
    *,
    side: str,
    source_arm: str,
    reason: str,
) -> dict[str, Any]:
    entry = float(features["last_price"])
    atr = max(float(features["atr"]), entry * 0.002)
    stop_distance = 1.5 * atr
    target_distance = 2.0 * atr
    intent = {
        "schema_version": INTENT_SCHEMA_VERSION,
        "action": "open",
        "symbol": snapshot.symbol,
        "side": side,
        "order_type": "market",
        "entry_price": entry,
        "stop_price": entry - stop_distance if side == "long" else entry + stop_distance,
        "target_price": entry + target_distance if side == "long" else entry - target_distance,
        "reduce_only": False,
        "reduce_fraction": None,
        "position_id": None,
        "reason": reason[:500],
        "source_arm": source_arm,
        "as_of": snapshot.data_cutoff,
    }
    return TradingIntent.from_dict(intent).to_dict()


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    code: str
    reason: str
    quantity: float = 0.0
    leverage: int = 0
    notional: float = 0.0
    margin: float = 0.0
    risk_amount: float = 0.0
    liquidation_price: float | None = None
    reduce_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "reason": self.reason,
            "quantity": self.quantity,
            "leverage": self.leverage,
            "notional": self.notional,
            "margin": self.margin,
            "risk_amount": self.risk_amount,
            "liquidation_price": self.liquidation_price,
            "reduce_only": self.reduce_only,
        }


def liquidation_price(entry: float, side: str, leverage: int, maintenance_rate: float) -> float:
    if side == "long":
        return entry * (1 - 1 / leverage) / (1 - maintenance_rate)
    return entry * (1 + 1 / leverage) / (1 + maintenance_rate)


class RiskEngine:
    """Fail-closed deterministic sizing, exposure, leverage, and margin checks."""

    def evaluate(
        self,
        intent: TradingIntent,
        config: dict[str, Any],
        *,
        equity: float,
        margin_used: float,
        open_positions: list[dict[str, Any]],
        data_cutoff: str,
        decision_as_of: datetime,
        signal_approved: bool = True,
        position_lookup: dict[str, Any] | None = None,
        leverage: int | None = None,
        current_price: float | None = None,
        current_atr: float | None = None,
        last_bar_close_time: str | None = None,
        daily_loss: float = 0.0,
        drawdown: float = 0.0,
        consecutive_losses: int = 0,
    ) -> RiskDecision:
        if intent.action in {"close", "reduce"}:
            if not intent.reduce_only:
                return RiskDecision(False, "REDUCE_ONLY_REQUIRED", "close actions must be reduce-only")
            found = None
            if intent.position_id and position_lookup:
                found = position_lookup.get(intent.position_id)
            if found is None:
                found = next(
                    (
                        position
                        for position in open_positions
                        if position["symbol"] == intent.symbol
                        and position["side"] == intent.side
                        and position["cohort"] == "primary"
                        and position["status"] == "open"
                    ),
                    None,
                )
            if (
                found is None
                or found["symbol"] != intent.symbol
                or found["side"] != intent.side
                or found["status"] != "open"
                or found["cohort"] != "primary"
            ):
                return RiskDecision(False, "POSITION_NOT_FOUND", "reduce-only intent has no matching open position")
            quantity = float(found["quantity"])
            if intent.action == "reduce":
                quantity *= float(intent.reduce_fraction or 0)
            quantity = min(quantity, float(found["quantity"]))
            if quantity <= 0:
                return RiskDecision(False, "INVALID_REDUCE_QUANTITY", "reduce-only quantity is not positive")
            return RiskDecision(
                True,
                "REDUCE_ONLY_APPROVED",
                "quantity is clipped to an existing position; no exposure can be added",
                quantity=quantity,
                leverage=int(found["leverage"]),
                notional=quantity * float(found["mark_price"] or found["entry_price"]),
                margin=quantity * float(found["entry_price"]) / int(found["leverage"]),
                reduce_only=True,
            )

        if intent.action != "open" or intent.reduce_only:
            return RiskDecision(False, "INVALID_ACTION", "unsupported or unsafe intent action")
        if not signal_approved:
            return RiskDecision(False, "SIGNAL_GATE_BLOCKED", "deterministic signal gate did not approve entry")
        if intent.symbol not in config["symbols"]:
            return RiskDecision(False, "SYMBOL_NOT_ALLOWED", "symbol is not in the experiment allowlist")
        if len([p for p in open_positions if p["cohort"] == "primary" and p["status"] == "open"]) >= config[
            "max_positions"
        ]:
            return RiskDecision(False, "POSITION_LIMIT", "maximum concurrent primary positions reached")
        if any(
            p["symbol"] == intent.symbol and p["cohort"] == "primary" and p["status"] == "open"
            for p in open_positions
        ):
            return RiskDecision(False, "POSITION_ALREADY_OPEN", "an open primary position already exists for symbol")
        if daily_loss >= config["max_daily_loss"]:
            return RiskDecision(False, "DAILY_LOSS_LIMIT", "daily loss stop has been reached")
        if drawdown >= config["max_drawdown_stop"]:
            return RiskDecision(False, "DRAWDOWN_LIMIT", "portfolio drawdown stop has been reached")
        if consecutive_losses >= config["max_consecutive_losses"]:
            return RiskDecision(False, "LOSS_STREAK_LIMIT", "consecutive-loss pause is active")
        cutoff = parse_utc(data_cutoff, "risk.data_cutoff")
        intent_time = parse_utc(intent.as_of, "risk.intent.as_of")
        if cutoff > decision_as_of or intent_time > cutoff:
            return RiskDecision(False, "FUTURE_DATA", "intent or market cutoff is later than the decision time")
        freshness_reference = (
            parse_utc(last_bar_close_time, "risk.last_bar_close_time")
            if last_bar_close_time is not None
            else cutoff
        )
        if freshness_reference > cutoff:
            return RiskDecision(False, "FUTURE_DATA", "latest candle closed after the input cutoff")
        if (decision_as_of - freshness_reference).total_seconds() > 2 * 15 * 60:
            return RiskDecision(False, "STALE_DATA", "the latest closed 15m market data is stale")

        selected_leverage = config["primary_leverage"] if leverage is None else leverage
        if (
            isinstance(selected_leverage, bool)
            or not isinstance(selected_leverage, int)
            or selected_leverage < 1
            or selected_leverage > min(config["max_leverage"], 10)
        ):
            return RiskDecision(False, "LEVERAGE_LIMIT", "leverage exceeds the configured hard cap")
        entry = float(intent.entry_price)
        stop = float(intent.stop_price)
        if intent.order_type == "market" and current_price is not None:
            current_price = _finite(current_price, "risk.current_price", minimum=0)
            atr = (
                _finite(current_atr, "risk.current_atr", minimum=0)
                if current_atr is not None
                else current_price * 0.002
            )
            max_deviation = max(atr * 0.5, current_price * 0.001)
            if abs(entry - current_price) > max_deviation:
                return RiskDecision(
                    False,
                    "MARKET_ENTRY_DRIFT",
                    "market intent entry moved beyond the volatility-adjusted tolerance",
                )
        stop_distance = abs(entry - stop)
        if stop_distance <= 0 or stop_distance / entry > 0.25:
            return RiskDecision(False, "STOP_DISTANCE", "stop distance is invalid or exceeds 25% of entry")
        risk_budget = max(0.0, equity) * config["risk_per_trade"]
        fee_drag_per_unit = entry * (2 * config["taker_fee_rate"] + 2 * config["slippage_bps"] / 10_000)
        unit_risk = stop_distance + fee_drag_per_unit
        if unit_risk <= 0 or risk_budget <= 0:
            return RiskDecision(False, "RISK_BUDGET", "no positive risk budget is available")
        quantity = risk_budget / unit_risk
        available_margin = max(0.0, equity - margin_used)
        entry_slippage = config["slippage_bps"] / 10_000
        estimated_fill_price = entry * (
            1 + entry_slippage if intent.side == "long" else 1 - entry_slippage
        )
        margin_and_fee_per_unit = estimated_fill_price * (
            1 / selected_leverage + config["taker_fee_rate"]
        )
        quantity = min(quantity, available_margin / margin_and_fee_per_unit)
        quantity = math.floor(quantity * 100_000_000) / 100_000_000
        if quantity <= 0:
            return RiskDecision(False, "INSUFFICIENT_MARGIN", "available isolated margin is insufficient")
        notional = quantity * entry
        if notional < config["minimum_notional_usdt"]:
            return RiskDecision(
                False,
                "MINIMUM_NOTIONAL",
                "available margin would produce a position below the configured paper minimum",
            )
        margin = notional / selected_leverage
        actual_risk = quantity * unit_risk
        liq = liquidation_price(entry, intent.side, selected_leverage, config["maintenance_margin_rate"])
        if intent.side == "long" and liq >= float(intent.stop_price):
            return RiskDecision(False, "LIQUIDATION_BEFORE_STOP", "isolated liquidation would precede the stop")
        if intent.side == "short" and liq <= float(intent.stop_price):
            return RiskDecision(False, "LIQUIDATION_BEFORE_STOP", "isolated liquidation would precede the stop")
        if actual_risk > risk_budget * 1.000001:
            return RiskDecision(False, "RISK_BUDGET", "sized position exceeds the configured risk budget")
        return RiskDecision(
            True,
            "APPROVED",
            "risk, margin, stop, liquidation, and leverage checks passed",
            quantity=quantity,
            leverage=selected_leverage,
            notional=notional,
            margin=margin,
            risk_amount=actual_risk,
            liquidation_price=liq,
        )


class PaperStore:
    """SQLite persistence for PAPER state; only provider references, never secret values."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            self.database_path,
            timeout=15,
            check_same_thread=False,
            isolation_level=None,
        )
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=15000")
        if self.database_path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._create_schema()
        self._migrate_schema()
        if self._db.execute("PRAGMA user_version").fetchone()[0] < DB_SCHEMA_VERSION:
            self._db.execute(f"PRAGMA user_version={int(DB_SCHEMA_VERSION)}")
        self._seed()
        self.cost_ledger = CostLedger(self.transaction, self._query)
        self.resolver: CredentialResolver | None = None

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except Exception:
                self._db.rollback()
                raise
            else:
                self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _create_schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS experiments(
                experiment_id TEXT PRIMARY KEY,
                config_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'stopped',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS providers(
                provider_id TEXT PRIMARY KEY,
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_validation_status TEXT NOT NULL DEFAULT 'not_tested',
                last_validated_at TEXT,
                last_validation_latency_ms REAL,
                last_validation_error_code TEXT
            );
            CREATE TABLE IF NOT EXISTS wallets(
                experiment_id TEXT NOT NULL,
                cohort TEXT NOT NULL,
                starting_balance REAL NOT NULL,
                cash_balance REAL NOT NULL,
                PRIMARY KEY(experiment_id, cohort),
                FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
            );
            CREATE TABLE IF NOT EXISTS cycles(
                cycle_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                cycle_slot TEXT NOT NULL,
                status TEXT NOT NULL,
                snapshot_hash TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(experiment_id, symbol, cycle_slot),
                FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
            );
            CREATE INDEX IF NOT EXISTS cycles_recent_idx
                ON cycles(experiment_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS positions(
                position_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                cohort TEXT NOT NULL,
                is_shadow INTEGER NOT NULL,
                source_arm TEXT NOT NULL,
                market_regime TEXT NOT NULL DEFAULT 'unknown',
                regime_source TEXT NOT NULL DEFAULT 'unknown',
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                opened_quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                mark_price REAL NOT NULL,
                stop_price REAL NOT NULL,
                target_price REAL NOT NULL,
                leverage INTEGER NOT NULL,
                margin REAL NOT NULL,
                liquidation_price REAL NOT NULL,
                entry_fee REAL NOT NULL,
                entry_fee_remaining REAL NOT NULL,
                exit_fees REAL NOT NULL DEFAULT 0,
                funding_paid REAL NOT NULL DEFAULT 0,
                slippage_paid REAL NOT NULL DEFAULT 0,
                realized_gross REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                exit_price REAL,
                exit_reason TEXT,
                realized_pnl REAL,
                closed_pnl_recorded INTEGER NOT NULL DEFAULT 0,
                last_funding_slot INTEGER NOT NULL,
                UNIQUE(cycle_id, cohort)
            );
            CREATE INDEX IF NOT EXISTS positions_open_idx
                ON positions(experiment_id, status, cohort);
            CREATE TABLE IF NOT EXISTS orders(
                order_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                position_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                cohort TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                reduce_only INTEGER NOT NULL,
                quantity REAL NOT NULL,
                filled_quantity REAL NOT NULL DEFAULT 0,
                limit_price REAL,
                last_checked_bar TEXT,
                status TEXT NOT NULL,
                risk_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS orders_pending_idx
                ON orders(experiment_id, status, symbol);
            CREATE TABLE IF NOT EXISTS fills(
                fill_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                position_id TEXT NOT NULL,
                cohort TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                fee REAL NOT NULL,
                slippage_cost REAL NOT NULL DEFAULT 0,
                as_of TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS equity(
                experiment_id TEXT NOT NULL,
                cohort TEXT NOT NULL,
                as_of TEXT NOT NULL,
                cash_balance REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                equity REAL NOT NULL,
                margin_used REAL NOT NULL,
                available_margin REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                data_origin TEXT NOT NULL,
                PRIMARY KEY(experiment_id, cohort, as_of)
            );
            CREATE INDEX IF NOT EXISTS equity_recent_idx
                ON equity(experiment_id, cohort, as_of);
            CREATE TABLE IF NOT EXISTS risk_events(
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL,
                cycle_id TEXT,
                symbol TEXT,
                cohort TEXT NOT NULL,
                status TEXT NOT NULL,
                code TEXT NOT NULL,
                reason TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ai_calls(
                call_id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                provider_kind TEXT NOT NULL,
                path TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                error_code TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                reasoning_tokens INTEGER,
                latency_ms REAL,
                cost_estimate REAL,
                pricing_version TEXT,
                prompt_hash TEXT,
                observed_at TEXT NOT NULL,
                UNIQUE(cycle_id, provider_id, path)
            );
            CREATE TABLE IF NOT EXISTS events(
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL,
                cycle_id TEXT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS processed_bars(
                experiment_id TEXT NOT NULL,
                position_id TEXT NOT NULL,
                close_time TEXT NOT NULL,
                PRIMARY KEY(experiment_id, position_id, close_time)
            );
            CREATE TABLE IF NOT EXISTS market_history(
                provider_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                data_origin TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY(provider_id, symbol, interval, open_time)
            );
            CREATE INDEX IF NOT EXISTS market_history_cutoff_idx
                ON market_history(provider_id, symbol, interval, close_time);
            CREATE TABLE IF NOT EXISTS market_history_runs(
                run_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                requested_bars INTEGER NOT NULL,
                retrieved_bars INTEGER NOT NULL,
                inserted_bars INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                data_cutoff TEXT NOT NULL,
                data_origin TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                error_code TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS market_history_runs_lookup_idx
                ON market_history_runs(experiment_id, created_at DESC);
            """
        )
        self._db.executescript(COST_SCHEMA)
        self._db.executescript(EXTRA_SCHEMA)
        self._db.executescript(PORTFOLIO_SCHEMA)
        self._db.executescript(SAFETY_SCHEMA)
        self._db.executescript(LIFECYCLE_SCHEMA)
        self._db.executescript(RESILIENCE_SCHEMA)

    def _migrate_schema(self) -> None:
        with self._lock:
            position_columns = {
                row["name"]
                for row in self._db.execute("PRAGMA table_info(positions)").fetchall()
            }
            if "slippage_paid" not in position_columns:
                self._db.execute(
                    "ALTER TABLE positions ADD COLUMN slippage_paid REAL NOT NULL DEFAULT 0"
                )
            for name in ("market_regime", "regime_source"):
                if name not in position_columns:
                    self._db.execute(
                        f"ALTER TABLE positions ADD COLUMN {name} TEXT NOT NULL DEFAULT 'unknown'"
                    )
            fill_columns = {
                row["name"] for row in self._db.execute("PRAGMA table_info(fills)").fetchall()
            }
            if "slippage_cost" not in fill_columns:
                self._db.execute(
                    "ALTER TABLE fills ADD COLUMN slippage_cost REAL NOT NULL DEFAULT 0"
                )
            provider_columns = {
                row["name"]
                for row in self._db.execute("PRAGMA table_info(providers)").fetchall()
            }
            for name, declaration in (
                ("last_validation_status", "TEXT NOT NULL DEFAULT 'not_tested'"),
                ("last_validated_at", "TEXT"),
                ("last_validation_latency_ms", "REAL"),
                ("last_validation_error_code", "TEXT"),
                ("last_validation_json", "TEXT"),
            ):
                if name not in provider_columns:
                    self._db.execute(
                        f"ALTER TABLE providers ADD COLUMN {name} {declaration}"
                    )
            meta_columns = {
                row["name"] for row in self._db.execute("PRAGMA table_info(position_meta)").fetchall()
            }
            for name, declaration in (
                ("initial_risk", "REAL"),
                ("review_count", "INTEGER NOT NULL DEFAULT 0"),
                ("core_quantity", "REAL NOT NULL DEFAULT 0"),
                ("stop_breaches", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in meta_columns:
                    self._db.execute(f"ALTER TABLE position_meta ADD COLUMN {name} {declaration}")
            order_columns = {
                row["name"] for row in self._db.execute("PRAGMA table_info(orders)").fetchall()
            }
            if "last_checked_bar" not in order_columns:
                self._db.execute("ALTER TABLE orders ADD COLUMN last_checked_bar TEXT")

    def _seed(self) -> None:
        now = iso_utc(datetime.now(timezone.utc))
        with self.transaction() as db:
            config = default_experiment_config()
            db.execute(
                "INSERT OR IGNORE INTO experiments(experiment_id, config_json, status, created_at, updated_at) "
                "VALUES(?, ?, 'stopped', ?, ?)",
                (config["experiment_id"], _json(config), now, now),
            )
            for provider in (
                {
                    "provider_id": "fixture-jev",
                    "kind": "fixture_jev",
                    "display_name": "Jev • Offline fixture",
                    "model": "fixture-jev-v1",
                    "enabled": True,
                    "base_url": "",
                    "timeout_seconds": 20,
                    "reasoning_effort": "high",
                    "credential_env": None,
                    "pricing": None,
                },
                {
                    "provider_id": "fixture-gpt",
                    "kind": "fixture_gpt",
                    "display_name": "GPT • Offline fixture",
                    "model": "fixture-gpt-v1",
                    "enabled": True,
                    "base_url": "",
                    "timeout_seconds": 20,
                    "reasoning_effort": "high",
                    "credential_env": None,
                    "pricing": None,
                },
            ):
                db.execute(
                    "INSERT OR IGNORE INTO providers(provider_id, config_json, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?)",
                    (provider["provider_id"], _json(provider), now, now),
                )
            experiment = db.execute(
                "SELECT config_json FROM experiments WHERE experiment_id='EXP-001'"
            ).fetchone()
            current_config = _loads(experiment["config_json"], config)
            starting = float(current_config["starting_balance_usdt"])
            db.execute(
                "INSERT OR IGNORE INTO wallets(experiment_id, cohort, starting_balance, cash_balance) "
                "VALUES('EXP-001', 'primary', ?, ?)",
                (starting, starting),
            )
            self._record_equity_locked(
                db, "EXP-001", "primary", now, "FIXTURE", starting_balance=starting
            )

    def _record_equity_locked(
        self,
        db: sqlite3.Connection,
        experiment_id: str,
        cohort: str,
        as_of: str,
        data_origin: str,
        *,
        starting_balance: float | None = None,
    ) -> dict[str, float]:
        if db.execute(
            "SELECT 1 FROM wallets WHERE experiment_id=? AND cohort=?",
            (experiment_id, cohort),
        ).fetchone() is None:
            if starting_balance is None:
                experiment = db.execute(
                    "SELECT config_json FROM experiments WHERE experiment_id=?",
                    (experiment_id,),
                ).fetchone()
                starting_balance = float(_loads(experiment["config_json"])["starting_balance_usdt"])
            db.execute(
                "INSERT INTO wallets(experiment_id, cohort, starting_balance, cash_balance) "
                "VALUES(?, ?, ?, ?)",
                (experiment_id, cohort, starting_balance, starting_balance),
            )
        wallet = db.execute(
            "SELECT starting_balance, cash_balance FROM wallets WHERE experiment_id=? AND cohort=?",
            (experiment_id, cohort),
        ).fetchone()
        positions = db.execute(
            "SELECT side, quantity, entry_price, mark_price, leverage, realized_pnl "
            "FROM positions WHERE experiment_id=? AND cohort=? AND status='open'",
            (experiment_id, cohort),
        ).fetchall()
        unrealized = 0.0
        margin = 0.0
        for position in positions:
            sign = 1 if position["side"] == "long" else -1
            unrealized += sign * (
                float(position["mark_price"]) - float(position["entry_price"])
            ) * float(position["quantity"])
            margin += (
                float(position["quantity"])
                * float(position["entry_price"])
                / int(position["leverage"])
            )
        realized = db.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS value FROM positions "
            "WHERE experiment_id=? AND cohort=? AND status='closed' AND closed_pnl_recorded=1",
            (experiment_id, cohort),
        ).fetchone()["value"]
        cash = float(wallet["cash_balance"])
        total_equity = cash + unrealized
        available = total_equity - margin
        db.execute(
            "INSERT OR REPLACE INTO equity(experiment_id, cohort, as_of, cash_balance, "
            "unrealized_pnl, equity, margin_used, available_margin, realized_pnl, data_origin) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                experiment_id,
                cohort,
                as_of,
                cash,
                unrealized,
                total_equity,
                margin,
                available,
                float(realized),
                data_origin,
            ),
        )
        return {
            "cash_balance": cash,
            "unrealized_pnl": unrealized,
            "equity": total_equity,
            "margin_used": margin,
            "available_margin": available,
            "realized_pnl": float(realized),
            "starting_balance": float(wallet["starting_balance"]),
        }

    def experiment(self) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT experiment_id, config_json, status, created_at, updated_at "
                "FROM experiments ORDER BY created_at LIMIT 1"
            ).fetchone()
        if row is None:
            raise PaperTradingError("paper experiment is not initialized")
        return {
            "experiment_id": row["experiment_id"],
            "config": validate_experiment_config(_loads(row["config_json"], {})),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _market_provider_id(mode: str) -> str:
        return MARKET_PROVIDER_IDS[mode]

    @staticmethod
    def _prune_market_history_locked(
        db: sqlite3.Connection,
        provider_id: str,
        older_than: datetime,
    ) -> int:
        cursor = db.execute(
            "DELETE FROM market_history WHERE provider_id=? AND close_time<?",
            (provider_id, iso_utc(older_than)),
        )
        return int(cursor.rowcount)

    def prune_market_history(
        self,
        *,
        provider_id: str,
        older_than: datetime,
    ) -> int:
        return self._prune_market_history_locked_wrapper(provider_id, older_than)

    def _prune_market_history_locked_wrapper(
        self,
        provider_id: str,
        older_than: datetime,
    ) -> int:
        if provider_id not in set(MARKET_PROVIDER_IDS.values()):
            raise PaperTradingError("market-history provider is unsupported")
        with self.transaction() as db:
            return self._prune_market_history_locked(db, provider_id, older_than)

    def save_experiment(self, value: dict[str, Any]) -> dict[str, Any]:
        config = validate_experiment_config(value)
        current = self.experiment()
        if config["experiment_id"] != current["experiment_id"]:
            raise PaperTradingError("EXP-001 identity cannot be changed")
        if current["status"] != "stopped":
            raise PaperTradingError("stop the experiment before editing its configuration")
        with self.transaction() as db:
            cycles = db.execute(
                "SELECT COUNT(*) AS count FROM cycles WHERE experiment_id=?",
                (config["experiment_id"],),
            ).fetchone()["count"]
            current_strategy = {
                key: value
                for key, value in current["config"].items()
                if key not in OPERATIONAL_EXPERIMENT_FIELDS
            }
            next_strategy = {
                key: value
                for key, value in config.items()
                if key not in OPERATIONAL_EXPERIMENT_FIELDS
            }
            if cycles and next_strategy != current_strategy:
                raise PaperTradingError("an experiment with recorded cycles is frozen; create a new experiment")
            now = iso_utc(datetime.now(timezone.utc))
            for field in ("ai_budget", "cost_fx"):
                if current["config"].get(field) != config.get(field):
                    # Cost controls are operational policy; every change is auditable.
                    db.execute(
                        "INSERT INTO events(experiment_id, event_type, payload_json, created_at) "
                        "VALUES(?, ?, ?, ?)",
                        (
                            config["experiment_id"],
                            f"{field}_changed",
                            _json({"previous": current["config"].get(field), "next": config.get(field)}),
                            now,
                        ),
                    )
            db.execute(
                "UPDATE experiments SET config_json=?, updated_at=? WHERE experiment_id=?",
                (_json(config), now, config["experiment_id"]),
            )
            db.execute(
                "UPDATE wallets SET starting_balance=?, cash_balance=? "
                "WHERE experiment_id=? AND cohort='primary' "
                "AND cash_balance=starting_balance",
                (
                    config["starting_balance_usdt"],
                    config["starting_balance_usdt"],
                    config["experiment_id"],
                ),
            )
            if not cycles:
                self._record_equity_locked(
                    db,
                    config["experiment_id"],
                    "primary",
                    now,
                    "FIXTURE",
                    starting_balance=config["starting_balance_usdt"],
                )
            retention_days = config["market_data_retention_days"]
            if retention_days is not None:
                self._prune_market_history_locked(
                    db,
                    self._market_provider_id(config["market_data_mode"]),
                    datetime.now(timezone.utc) - timedelta(days=retention_days),
                )
        return self.experiment()

    def update_cost_controls(
        self,
        *,
        ai_budget: Any | None = None,
        cost_fx: Any | None = None,
    ) -> dict[str, Any]:
        """Tighten/loosen operational cost controls, even while running; every change is audited."""

        from .ai_cost import validate_budget_config, validate_fx_policy

        current = self.experiment()
        config = dict(current["config"])
        if ai_budget is not None:
            config["ai_budget"] = validate_budget_config(ai_budget)
        if cost_fx is not None:
            config["cost_fx"] = validate_fx_policy(cost_fx)
        config = validate_experiment_config(config)
        now = iso_utc(datetime.now(timezone.utc))
        with self.transaction() as db:
            for field in ("ai_budget", "cost_fx"):
                if current["config"].get(field) != config.get(field):
                    db.execute(
                        "INSERT INTO events(experiment_id, event_type, payload_json, created_at) "
                        "VALUES(?, ?, ?, ?)",
                        (
                            config["experiment_id"],
                            f"{field}_changed",
                            _json({"previous": current["config"].get(field), "next": config.get(field)}),
                            now,
                        ),
                    )
            db.execute(
                "UPDATE experiments SET config_json=?, updated_at=? WHERE experiment_id=?",
                (_json(config), now, config["experiment_id"]),
            )
        return self.experiment()

    def set_status(self, status: str) -> dict[str, Any]:
        if status not in {"stopped", "running", "paused"}:
            raise PaperTradingError("runtime status is invalid")
        current = self.experiment()
        if current["status"] == status:
            return current
        now = iso_utc(datetime.now(timezone.utc))
        with self.transaction() as db:
            db.execute(
                "UPDATE experiments SET status=?, updated_at=? WHERE experiment_id=?",
                (status, now, current["experiment_id"]),
            )
            db.execute(
                "INSERT INTO events(experiment_id, event_type, payload_json, created_at) "
                "VALUES(?, 'runtime_status', ?, ?)",
                (current["experiment_id"], _json({"status": status}), now),
            )
            if status in {"paused", "stopped"}:
                cursor = db.execute(
                    "UPDATE orders SET status='cancelled', updated_at=? "
                    "WHERE experiment_id=? AND status='pending' AND cycle_id NOT LIKE 'manual:%'",
                    (now, current["experiment_id"]),
                )
                if cursor.rowcount:
                    db.execute(
                        "INSERT INTO events(experiment_id, event_type, payload_json, created_at) "
                        "VALUES(?, 'pending_orders_cancelled', ?, ?)",
                        (
                            current["experiment_id"],
                            _json({"count": cursor.rowcount, "reason": status}),
                            now,
                        ),
                    )
        return self.experiment()

    def credential_status(self, provider: dict[str, Any]) -> dict[str, Any] | None:
        if self.resolver is None or provider["kind"].startswith("fixture_"):
            return None
        return self.resolver.status(
            secret_id=provider.get("credential_secret"), env_name=provider.get("credential_env")
        )

    def list_providers(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT config_json, last_validation_status, last_validated_at, "
                "last_validation_latency_ms, last_validation_error_code, last_validation_json "
                "FROM providers ORDER BY provider_id"
            ).fetchall()
        result = []
        for row in rows:
            provider = _loads(row["config_json"], {})
            result.append(
                public_provider_config(
                    provider,
                    credential_present=bool(provider.get("credential_env") or provider.get("credential_secret")),
                    validation={
                        "status": row["last_validation_status"],
                        "validated_at": row["last_validated_at"],
                        "latency_ms": row["last_validation_latency_ms"],
                        "error_code": row["last_validation_error_code"],
                        "details": _loads(row["last_validation_json"], None),
                    },
                    credential=self.credential_status(provider),
                )
            )
        return result

    def providers_internal(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT config_json FROM providers ORDER BY provider_id").fetchall()
        return [_loads(row["config_json"], {}) for row in rows]

    def provider(self, provider_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT config_json FROM providers WHERE provider_id=?", (provider_id,)
            ).fetchone()
        return None if row is None else _loads(row["config_json"], {})

    def provider_validation_status(self, provider_id: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT last_validation_status FROM providers WHERE provider_id=?",
                (provider_id,),
            ).fetchone()
        return None if row is None else row["last_validation_status"]

    def save_provider(self, value: dict[str, Any]) -> dict[str, Any]:
        provider_value = dict(value)
        provider_id = provider_value.get("provider_id")
        if isinstance(provider_id, str):
            existing_config = self.provider(provider_id)
            if existing_config is not None and not existing_config["kind"].startswith("fixture_"):
                for field in ("credential_env", "credential_secret"):
                    if field not in provider_value:
                        provider_value[field] = existing_config.get(field)
        provider = validate_provider_config(provider_value)
        current = self.experiment()
        if current["status"] != "stopped":
            raise PaperTradingError("stop the experiment before editing provider configuration")
        now = iso_utc(datetime.now(timezone.utc))
        with self.transaction() as db:
            cycles = db.execute(
                "SELECT COUNT(*) AS count FROM cycles WHERE experiment_id=?",
                (current["experiment_id"],),
            ).fetchone()["count"]
            existing = db.execute(
                "SELECT config_json, last_validation_status, last_validated_at, "
                "last_validation_latency_ms, last_validation_error_code "
                "FROM providers WHERE provider_id=?",
                (provider["provider_id"],),
            ).fetchone()
            def material(value: dict[str, Any]) -> dict[str, Any]:
                # Credential references may be rotated without changing research treatment.
                return {
                    key: item
                    for key, item in value.items()
                    if key not in {"credential_env", "credential_secret"}
                }

            if cycles and (existing is None or material(_loads(existing["config_json"])) != material(provider)):
                raise PaperTradingError("provider settings are frozen after the first cycle")
            unchanged = existing is not None and _loads(existing["config_json"]) == provider
            validation = (
                {
                    "status": existing["last_validation_status"],
                    "validated_at": existing["last_validated_at"],
                    "latency_ms": existing["last_validation_latency_ms"],
                    "error_code": existing["last_validation_error_code"],
                }
                if unchanged
                else {
                    "status": "not_tested",
                    "validated_at": None,
                    "latency_ms": None,
                    "error_code": None,
                }
            )
            db.execute(
                "INSERT INTO providers(provider_id, config_json, created_at, updated_at, "
                "last_validation_status, last_validated_at, last_validation_latency_ms, "
                "last_validation_error_code) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(provider_id) DO UPDATE SET "
                "config_json=excluded.config_json, updated_at=excluded.updated_at, "
                "last_validation_status=excluded.last_validation_status, "
                "last_validated_at=excluded.last_validated_at, "
                "last_validation_latency_ms=excluded.last_validation_latency_ms, "
                "last_validation_error_code=excluded.last_validation_error_code",
                (
                    provider["provider_id"],
                    _json(provider),
                    now,
                    now,
                    validation["status"],
                    validation["validated_at"],
                    validation["latency_ms"],
                    validation["error_code"],
                ),
            )
        return public_provider_config(
            provider,
            credential_present=bool(provider.get("credential_env") or provider.get("credential_secret")),
            validation=validation,
            credential=self.credential_status(provider),
        )

    def record_provider_validation(
        self,
        provider_id: str,
        *,
        status: str,
        latency_ms: float | None,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if status not in {"passed", "failed"}:
            raise PaperTradingError("provider validation status is invalid")
        if latency_ms is not None and (
            isinstance(latency_ms, bool)
            or not isinstance(latency_ms, (int, float))
            or not math.isfinite(float(latency_ms))
            or latency_ms < 0
        ):
            raise PaperTradingError("provider validation latency is invalid")
        with self.transaction() as db:
            cursor = db.execute(
                "UPDATE providers SET last_validation_status=?, last_validated_at=?, "
                "last_validation_latency_ms=?, last_validation_error_code=?, last_validation_json=?, "
                "updated_at=? WHERE provider_id=?",
                (
                    status,
                    iso_utc(datetime.now(timezone.utc)),
                    None if latency_ms is None else float(latency_ms),
                    error_code if status == "failed" else None,
                    None if details is None else _json(details),
                    iso_utc(datetime.now(timezone.utc)),
                    provider_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PaperTradingError("provider configuration was not found")

    def begin_cycle(
        self,
        cycle_id: str,
        experiment_id: str,
        symbol: str,
        cycle_slot: str,
        snapshot_hash: str,
        as_of: str,
        data_origin: str,
    ) -> bool:
        now = iso_utc(datetime.now(timezone.utc))
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO cycles(cycle_id, experiment_id, symbol, cycle_slot, "
                "status, snapshot_hash, payload_json, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, 'processing', ?, ?, ?, ?)",
                (
                    cycle_id,
                    experiment_id,
                    symbol,
                    cycle_slot,
                    snapshot_hash,
                    _json(
                        {
                            "cycle_id": cycle_id,
                            "symbol": symbol,
                            "cycle_slot": cycle_slot,
                            "as_of": as_of,
                            "snapshot_hash": snapshot_hash,
                            "data_origin": data_origin,
                            "status": "processing",
                        }
                    ),
                    now,
                    now,
                ),
            )
            return cursor.rowcount == 1

    def cycle(self, cycle_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT cycle_id, experiment_id, symbol, cycle_slot, status, snapshot_hash, "
                "payload_json, created_at, updated_at FROM cycles WHERE cycle_id=?",
                (cycle_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "cycle_id": row["cycle_id"],
            "experiment_id": row["experiment_id"],
            "symbol": row["symbol"],
            "cycle_slot": row["cycle_slot"],
            "status": row["status"],
            "snapshot_hash": row["snapshot_hash"],
            **_loads(row["payload_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _portfolio_locked(
        self, db: sqlite3.Connection, experiment_id: str, cohort: str = "primary"
    ) -> dict[str, Any]:
        wallet = db.execute(
            "SELECT starting_balance, cash_balance FROM wallets WHERE experiment_id=? AND cohort=?",
            (experiment_id, cohort),
        ).fetchone()
        if wallet is None:
            config = _loads(
                db.execute(
                    "SELECT config_json FROM experiments WHERE experiment_id=?",
                    (experiment_id,),
                ).fetchone()["config_json"]
            )
            starting = float(config["starting_balance_usdt"])
            db.execute(
                "INSERT INTO wallets(experiment_id, cohort, starting_balance, cash_balance) VALUES(?, ?, ?, ?)",
                (experiment_id, cohort, starting, starting),
            )
            wallet = {"starting_balance": starting, "cash_balance": starting}
        positions = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM positions WHERE experiment_id=? AND status='open'",
                (experiment_id,),
            ).fetchall()
        ]
        cohort_positions = [position for position in positions if position["cohort"] == cohort]
        unrealized = sum(
            (1 if position["side"] == "long" else -1)
            * (float(position["mark_price"]) - float(position["entry_price"]))
            * float(position["quantity"])
            for position in cohort_positions
        )
        margin = sum(float(position["margin"]) for position in cohort_positions)
        realized_row = db.execute(
            "SELECT COALESCE(SUM(realized_pnl),0) AS value FROM positions "
            "WHERE experiment_id=? AND cohort=? AND status='closed' AND closed_pnl_recorded=1",
            (experiment_id, cohort),
        ).fetchone()
        cash = float(wallet["cash_balance"])
        total_equity = cash + unrealized
        return {
            "cash_balance": cash,
            "unrealized_pnl": unrealized,
            "equity": total_equity,
            "margin_used": margin,
            "available_margin": total_equity - margin,
            "realized_pnl": float(realized_row["value"]),
            "starting_balance": float(wallet["starting_balance"]),
            "open_positions": cohort_positions,
        }

    def portfolio(self, experiment_id: str, cohort: str = "primary") -> dict[str, Any]:
        with self.transaction() as db:
            return self._portfolio_locked(db, experiment_id, cohort)

    def open_positions(self, experiment_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM positions WHERE experiment_id=? AND status='open' ORDER BY opened_at",
                (experiment_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_orders(self, experiment_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM orders WHERE experiment_id=? AND status='pending' "
                "ORDER BY created_at, order_id",
                (experiment_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _position_lookup(self, experiment_id: str) -> dict[str, dict[str, Any]]:
        return {row["position_id"]: row for row in self.open_positions(experiment_id)}

    def record_ai_call(
        self,
        db: sqlite3.Connection,
        experiment_id: str,
        cycle_id: str,
        record: dict[str, Any],
    ) -> None:
        db.execute(
            "INSERT OR IGNORE INTO ai_calls(experiment_id, cycle_id, provider_id, provider_kind, "
            "path, model, status, error_code, input_tokens, output_tokens, reasoning_tokens, "
            "latency_ms, cost_estimate, pricing_version, prompt_hash, observed_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                experiment_id,
                cycle_id,
                record["provider_id"],
                record["provider_kind"],
                record["path"],
                record["model"],
                record.get("status", "ok"),
                record.get("error_code"),
                record.get("input_tokens"),
                record.get("output_tokens"),
                record.get("reasoning_tokens"),
                record.get("latency_ms"),
                record.get("cost_estimate"),
                record.get("pricing_version"),
                record.get("prompt_hash"),
                record.get("observed_at", iso_utc(datetime.now(timezone.utc))),
            ),
        )

    def record_risk_event(
        self,
        db: sqlite3.Connection,
        experiment_id: str,
        cycle_id: str | None,
        symbol: str | None,
        cohort: str,
        risk: dict[str, Any],
    ) -> None:
        db.execute(
            "INSERT INTO risk_events(experiment_id, cycle_id, symbol, cohort, status, code, reason, "
            "details_json, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                experiment_id,
                cycle_id,
                symbol,
                cohort,
                "approved" if risk.get("allowed") else "blocked",
                risk["code"],
                risk["reason"],
                _json({key: value for key, value in risk.items() if key != "reason"}),
                iso_utc(datetime.now(timezone.utc)),
            ),
        )

    def _insert_order_locked(
        self,
        db: sqlite3.Connection,
        experiment_id: str,
        cycle_id: str,
        execution: dict[str, Any],
        *,
        require_running: bool = True,
    ) -> dict[str, Any]:
        risk = dict(execution["risk"])
        status_row = db.execute(
            "SELECT status FROM experiments WHERE experiment_id=?",
            (experiment_id,),
        ).fetchone()
        if status_row is None or (require_running and status_row["status"] != "running"):
            risk.update(
                {
                    "allowed": False,
                    "code": "RUNTIME_NOT_RUNNING",
                    "reason": "runtime was paused or stopped before the paper fill",
                    "quantity": 0.0,
                    "notional": 0.0,
                    "margin": 0.0,
                    "risk_amount": 0.0,
                }
            )
        if not risk["allowed"]:
            self.record_risk_event(
                db,
                experiment_id,
                cycle_id,
                execution["intent"]["symbol"],
                execution["cohort"],
                risk,
            )
            return {
                "cohort": execution["cohort"],
                "status": "rejected",
                "filled_quantity": 0.0,
                "reason": risk["reason"],
            }
        intent = execution["intent"]
        cohort = execution["cohort"]
        position_id = f"{cycle_id}:{cohort}"
        order_id = f"{position_id}:entry"
        order_type = intent["order_type"]
        reference = float(execution["reference_price"])
        pending = order_type == "limit"
        db.execute(
            "INSERT OR IGNORE INTO orders(order_id, experiment_id, cycle_id, position_id, symbol, "
            "cohort, side, order_type, reduce_only, quantity, filled_quantity, limit_price, status, "
            "risk_json, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?, ?, ?, ?)",
            (
                order_id,
                experiment_id,
                cycle_id,
                position_id,
                intent["symbol"],
                cohort,
                intent["side"],
                order_type,
                float(risk["quantity"]),
                float(intent["entry_price"]) if pending else None,
                "pending" if pending else "open",
                _json(
                    {
                        "risk": risk,
                        "intent": intent,
                        "execution": {
                            key: value
                            for key, value in execution.items()
                            if key not in {"risk", "intent"}
                        },
                    }
                ),
                execution["as_of"],
                execution["as_of"],
            ),
        )
        self.record_risk_event(
            db,
            experiment_id,
            cycle_id,
            intent["symbol"],
            cohort,
            risk,
        )
        if pending:
            return {
                "cohort": cohort,
                "order_id": order_id,
                "position_id": position_id,
                "status": "pending",
                "filled_quantity": 0.0,
                "reason": "limit awaits a future execution bar",
            }
        if not pending:
            slipped = reference * (
                1 + execution["slippage_bps"] / 10_000
                if intent["side"] == "long"
                else 1 - execution["slippage_bps"] / 10_000
            )
            filled = self._fill_entry_locked(
                db,
                experiment_id,
                order_id,
                position_id,
                cohort,
                intent,
                float(risk["quantity"]),
                slipped,
                int(risk["leverage"]),
                float(risk["liquidation_price"]),
                float(execution["fee_rate"]),
                execution["as_of"],
                execution["data_origin"],
            )
            return {
                "cohort": cohort,
                "order_id": order_id,
                "position_id": position_id,
                "status": "filled" if filled else "rejected",
                "filled_quantity": float(risk["quantity"]) if filled else 0.0,
                "reason": "paper fill recorded" if filled else "margin or position checks rejected the fill",
            }
        return {"cohort": cohort, "status": "rejected", "filled_quantity": 0.0}

    def _fill_entry_locked(
        self,
        db: sqlite3.Connection,
        experiment_id: str,
        order_id: str,
        position_id: str,
        cohort: str,
        intent: dict[str, Any],
        quantity: float,
        fill_price: float,
        leverage: int,
        liq_price: float,
        fee_rate: float,
        as_of: str,
        data_origin: str,
    ) -> bool:
        order = db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if order is None or order["reduce_only"] or order["status"] not in {"open", "pending"}:
            return False

        def reject_fill(code: str, reason: str) -> bool:
            self.record_risk_event(
                db,
                experiment_id,
                order["cycle_id"],
                order["symbol"],
                cohort,
                {
                    "allowed": False,
                    "code": code,
                    "reason": reason,
                    "quantity": 0.0,
                    "leverage": leverage,
                    "notional": 0.0,
                    "margin": 0.0,
                    "risk_amount": 0.0,
                    "liquidation_price": None,
                    "reduce_only": False,
                },
            )
            return False

        quantity = min(float(quantity), float(order["quantity"]) - float(order["filled_quantity"]))
        if quantity <= 0:
            return False
        config = validate_experiment_config(
            _loads(
                db.execute(
                    "SELECT config_json FROM experiments WHERE experiment_id=?",
                    (experiment_id,),
                ).fetchone()["config_json"]
            )
        )
        current_positions = db.execute(
            "SELECT COUNT(*) AS count FROM positions WHERE experiment_id=? AND cohort=? AND status='open'",
            (experiment_id, cohort),
        ).fetchone()["count"]
        if (cohort == "primary" or cohort.startswith("arm-")) and current_positions >= int(
            config["max_positions"]
        ):
            db.execute(
                "UPDATE orders SET status='rejected', updated_at=? WHERE order_id=?",
                (as_of, order_id),
            )
            return reject_fill(
                "POSITION_LIMIT_AT_FILL",
                "maximum concurrent positions changed before the fill",
            )
        if db.execute(
            "SELECT 1 FROM positions WHERE experiment_id=? AND cohort=? AND symbol=? AND status='open'",
            (experiment_id, cohort, intent["symbol"]),
        ).fetchone():
            db.execute(
                "UPDATE orders SET status='rejected', updated_at=? WHERE order_id=?",
                (as_of, order_id),
            )
            return reject_fill(
                "POSITION_ALREADY_OPEN_AT_FILL",
                "an open position for the symbol already exists in this cohort",
            )
        self._portfolio_locked(db, experiment_id, cohort)
        wallet = db.execute(
            "SELECT cash_balance FROM wallets WHERE experiment_id=? AND cohort=?",
            (experiment_id, cohort),
        ).fetchone()
        margin = quantity * fill_price / leverage
        margin_used = db.execute(
            "SELECT COALESCE(SUM(margin),0) AS value FROM positions "
            "WHERE experiment_id=? AND cohort=? AND status='open'",
            (experiment_id, cohort),
        ).fetchone()["value"]
        unrealized = db.execute(
            "SELECT COALESCE(SUM(CASE side WHEN 'long' THEN (mark_price-entry_price)*quantity "
            "ELSE (entry_price-mark_price)*quantity END),0) AS value FROM positions "
            "WHERE experiment_id=? AND cohort=? AND status='open'",
            (experiment_id, cohort),
        ).fetchone()["value"]
        equity = float(wallet["cash_balance"]) + float(unrealized)
        if margin > equity - float(margin_used):
            db.execute(
                "UPDATE orders SET status='rejected', updated_at=? WHERE order_id=?",
                (as_of, order_id),
            )
            return reject_fill(
                "INSUFFICIENT_MARGIN_AT_FILL",
                "isolated margin became insufficient before the fill",
            )
        if quantity * fill_price < float(config["minimum_notional_usdt"]):
            db.execute(
                "UPDATE orders SET status='rejected', updated_at=? WHERE order_id=?",
                (as_of, order_id),
            )
            return reject_fill(
                "MINIMUM_NOTIONAL_AT_FILL",
                "actual paper fill is below the configured minimum notional",
            )
        if not (
            float(intent["stop_price"]) < fill_price < float(intent["target_price"])
            if intent["side"] == "long"
            else float(intent["target_price"]) < fill_price < float(intent["stop_price"])
        ):
            db.execute(
                "UPDATE orders SET status='rejected', updated_at=? WHERE order_id=?",
                (as_of, order_id),
            )
            return reject_fill(
                "ENTRY_LEVELS_INVALID_AT_FILL",
                "actual paper fill no longer fits the configured stop and target levels",
            )
        liq_price = liquidation_price(
            fill_price,
            intent["side"],
            leverage,
            float(config["maintenance_margin_rate"]),
        )
        if (
            intent["side"] == "long"
            and liq_price >= float(intent["stop_price"])
        ) or (
            intent["side"] == "short"
            and liq_price <= float(intent["stop_price"])
        ):
            db.execute(
                "UPDATE orders SET status='rejected', updated_at=? WHERE order_id=?",
                (as_of, order_id),
            )
            return reject_fill(
                "LIQUIDATION_BEFORE_STOP_AT_FILL",
                "actual isolated liquidation price would precede the stop",
            )
        fee = quantity * fill_price * fee_rate
        stored_order_data = _loads(order["risk_json"], {})
        execution_data = stored_order_data.get("execution", {})
        market_regime = execution_data.get("market_regime", "unknown")
        if market_regime not in {"bull", "bear", "sideways", "unstable"}:
            market_regime = "unknown"
        regime_source = execution_data.get("regime_source", "unknown")
        if regime_source not in {"jev", "quant"}:
            regime_source = "unknown"
        expected_entry = float(intent["entry_price"])
        slippage_cost = quantity * (
            max(0.0, fill_price - expected_entry)
            if intent["side"] == "long"
            else max(0.0, expected_entry - fill_price)
        )
        db.execute(
            "INSERT OR IGNORE INTO positions(position_id, experiment_id, cycle_id, symbol, cohort, "
            "is_shadow, source_arm, market_regime, regime_source, side, quantity, opened_quantity, entry_price, mark_price, "
            "stop_price, target_price, leverage, margin, liquidation_price, entry_fee, "
            "entry_fee_remaining, slippage_paid, status, opened_at, last_funding_slot) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (
                position_id,
                experiment_id,
                order["cycle_id"],
                intent["symbol"],
                cohort,
                0 if cohort == "primary" else 1,
                intent["source_arm"],
                market_regime,
                regime_source,
                intent["side"],
                quantity,
                quantity,
                fill_price,
                fill_price,
                float(intent["stop_price"]),
                float(intent["target_price"]),
                leverage,
                margin,
                liq_price,
                fee,
                fee,
                slippage_cost,
                as_of,
                int(parse_utc(as_of, "fill.as_of").timestamp()) // (8 * 60 * 60),
            ),
        )
        side = "buy" if intent["side"] == "long" else "sell"
        fill_id = f"{order_id}:fill:1"
        db.execute(
            "INSERT OR IGNORE INTO fills(fill_id, experiment_id, cycle_id, order_id, position_id, "
            "cohort, side, quantity, price, fee, slippage_cost, as_of) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fill_id,
                experiment_id,
                order["cycle_id"],
                order_id,
                position_id,
                cohort,
                side,
                quantity,
                fill_price,
                fee,
                slippage_cost,
                as_of,
            ),
        )
        db.execute(
            "UPDATE wallets SET cash_balance=cash_balance-? WHERE experiment_id=? AND cohort=?",
            (fee, experiment_id, cohort),
        )
        db.execute(
            "UPDATE orders SET status='filled', filled_quantity=?, updated_at=?, "
            "last_checked_bar=? WHERE order_id=?",
            (quantity, as_of, as_of, order_id),
        )
        db.execute(
            "INSERT INTO events(experiment_id, cycle_id, event_type, payload_json, created_at) "
            "VALUES(?, ?, 'paper_fill', ?, ?)",
            (
                experiment_id,
                order["cycle_id"],
                _json(
                    {
                        "position_id": position_id,
                        "cohort": cohort,
                        "symbol": intent["symbol"],
                        "side": side,
                        "quantity": quantity,
                        "price": fill_price,
                        "fee": fee,
                    }
                ),
                as_of,
            ),
        )
        self._record_equity_locked(db, experiment_id, cohort, as_of, data_origin)
        return True

    def complete_cycle(
        self,
        cycle_id: str,
        payload: dict[str, Any],
        *,
        ai_calls: list[dict[str, Any]],
        executions: list[dict[str, Any]],
        risk_events: list[dict[str, Any]],
        data_origin: str,
    ) -> dict[str, Any]:
        with self.transaction() as db:
            cycle = db.execute("SELECT * FROM cycles WHERE cycle_id=?", (cycle_id,)).fetchone()
            if cycle is None:
                raise PaperTradingError("cycle was not reserved before processing")
            if cycle["status"] != "processing":
                return _loads(cycle["payload_json"], {})
            experiment_id = cycle["experiment_id"]
            for call in ai_calls:
                self.record_ai_call(db, experiment_id, cycle_id, call)
            for event in risk_events:
                self.record_risk_event(
                    db,
                    experiment_id,
                    cycle_id,
                    cycle["symbol"],
                    event.get("cohort", "primary"),
                    event["risk"],
                )
            receipts = [
                self._insert_order_locked(db, experiment_id, cycle_id, execution)
                for execution in executions
            ]
            payload = dict(payload)
            payload["executions"] = receipts
            payload["status"] = "complete"
            payload["cycle_id"] = cycle_id
            now = iso_utc(datetime.now(timezone.utc))
            db.execute(
                "UPDATE cycles SET status='complete', payload_json=?, updated_at=? WHERE cycle_id=?",
                (_json(payload), now, cycle_id),
            )
            self._record_equity_locked(db, experiment_id, "primary", payload["as_of"], data_origin)
            config = _loads(
                db.execute(
                    "SELECT config_json FROM experiments WHERE experiment_id=?",
                    (experiment_id,),
                ).fetchone()["config_json"]
            )
            for leverage in config["shadow_leverage"]:
                self._record_equity_locked(
                    db,
                    experiment_id,
                    f"x{leverage}",
                    payload["as_of"],
                    data_origin,
                    starting_balance=float(config["starting_balance_usdt"]),
                )
            for arm in config["evaluation_arms"]:
                self._record_equity_locked(
                    db,
                    experiment_id,
                    f"arm-{arm}",
                    payload["as_of"],
                    data_origin,
                    starting_balance=float(config["starting_balance_usdt"]),
                )
            return payload

    def fail_cycle(self, cycle_id: str, error_code: str) -> None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT experiment_id, payload_json FROM cycles WHERE cycle_id=?", (cycle_id,)
            ).fetchone()
            if row is None:
                return
            payload = _loads(row["payload_json"], {})
            payload.update({"status": "failed", "error_code": error_code})
            db.execute(
                "UPDATE cycles SET status='failed', payload_json=?, updated_at=? WHERE cycle_id=?",
                (_json(payload), iso_utc(datetime.now(timezone.utc)), cycle_id),
            )
            db.execute(
                "INSERT INTO events(experiment_id, cycle_id, event_type, payload_json, created_at) "
                "VALUES(?, ?, 'cycle_failed', ?, ?)",
                (
                    row["experiment_id"],
                    cycle_id,
                    _json({"error_code": error_code}),
                    iso_utc(datetime.now(timezone.utc)),
                ),
            )

    def recover_interrupted_cycles(self, experiment_id: str) -> int:
        """Mark abandoned in-flight cycles; never repeat an unknown external AI call."""
        recovered = 0
        with self.transaction() as db:
            rows = db.execute(
                "SELECT cycle_id, payload_json FROM cycles "
                "WHERE experiment_id=? AND status='processing'",
                (experiment_id,),
            ).fetchall()
            now = iso_utc(datetime.now(timezone.utc))
            for row in rows:
                payload = _loads(row["payload_json"], {})
                payload.update(
                    {
                        "status": "interrupted",
                        "error_code": "process_interrupted",
                        "retry_policy": "no_ai_retry_for_consumed_cycle",
                    }
                )
                db.execute(
                    "UPDATE cycles SET status='interrupted', payload_json=?, updated_at=? "
                    "WHERE cycle_id=? AND status='processing'",
                    (_json(payload), now, row["cycle_id"]),
                )
                db.execute(
                    "INSERT INTO events(experiment_id, cycle_id, event_type, payload_json, created_at) "
                    "VALUES(?, ?, 'cycle_interrupted', ?, ?)",
                    (
                        experiment_id,
                        row["cycle_id"],
                        _json({"error_code": "process_interrupted", "retry": False}),
                        now,
                    ),
                )
                recovered += 1
        return recovered

    def list_cycles(self, experiment_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT cycle_id, experiment_id, symbol, cycle_slot, status, snapshot_hash, "
                "payload_json, created_at, updated_at FROM cycles WHERE experiment_id=? "
                "ORDER BY cycle_slot DESC, symbol LIMIT ?",
                (experiment_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "cycle_id": row["cycle_id"],
                    "experiment_id": row["experiment_id"],
                    "symbol": row["symbol"],
                    "cycle_slot": row["cycle_slot"],
                    "status": row["status"],
                    "snapshot_hash": row["snapshot_hash"],
                    **_loads(row["payload_json"], {}),
                    "created_at": row["created_at"],
                }
            )
        return result

    def cycle_execution_metadata(
        self, experiment_id: str, cycle_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        if not cycle_ids:
            return {}
        placeholders = ",".join("?" for _ in cycle_ids)
        parameters = (experiment_id, *cycle_ids)
        with self._lock:
            order_rows = self._db.execute(
                f"SELECT cycle_id, cohort, status, COUNT(*) AS count FROM orders "
                f"WHERE experiment_id=? AND cycle_id IN ({placeholders}) "
                f"GROUP BY cycle_id, cohort, status",
                parameters,
            ).fetchall()
            fill_rows = self._db.execute(
                f"SELECT cycle_id, cohort, COUNT(*) AS count FROM fills "
                f"WHERE experiment_id=? AND cycle_id IN ({placeholders}) "
                f"GROUP BY cycle_id, cohort",
                parameters,
            ).fetchall()
            call_rows = self._db.execute(
                f"SELECT cycle_id, path, status FROM ai_calls "
                f"WHERE experiment_id=? AND cycle_id IN ({placeholders}) ORDER BY call_id",
                parameters,
            ).fetchall()
        result = {
            cycle_id: {
                "order_count": 0,
                "all_order_count": 0,
                "order_status_counts": {},
                "fill_count": 0,
                "all_fill_count": 0,
                "ai_calls": [],
            }
            for cycle_id in cycle_ids
        }
        for row in order_rows:
            summary = result[row["cycle_id"]]
            summary["all_order_count"] += int(row["count"])
            if row["cohort"] == "primary":
                summary["order_count"] += int(row["count"])
                summary["order_status_counts"][row["status"]] = int(row["count"])
        for row in fill_rows:
            summary = result[row["cycle_id"]]
            summary["all_fill_count"] += int(row["count"])
            if row["cohort"] == "primary":
                summary["fill_count"] = int(row["count"])
        for row in call_rows:
            result[row["cycle_id"]]["ai_calls"].append(
                {"path": row["path"], "status": row["status"]}
            )
        return result

    def list_positions(self, experiment_id: str, *, cohort: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if cohort is None:
                rows = self._db.execute(
                    "SELECT * FROM positions WHERE experiment_id=? ORDER BY opened_at DESC",
                    (experiment_id,),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM positions WHERE experiment_id=? AND cohort=? ORDER BY opened_at DESC",
                    (experiment_id, cohort),
                ).fetchall()
        return [dict(row) for row in rows]

    def equity_series(self, experiment_id: str, cohort: str = "primary", *, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM equity WHERE experiment_id=? AND cohort=? ORDER BY as_of DESC LIMIT ?",
                (experiment_id, cohort, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def risk_events(self, experiment_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM risk_events WHERE experiment_id=? ORDER BY event_id DESC LIMIT ?",
                (experiment_id, limit),
            ).fetchall()
        return [
            {**dict(row), "details": _loads(row["details_json"], {})}
            for row in rows
        ]

    def ai_usage(self, experiment_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM ai_calls WHERE experiment_id=? ORDER BY call_id",
                (experiment_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def activity(self, experiment_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT event_id, cycle_id, event_type, payload_json, created_at "
                "FROM events WHERE experiment_id=? ORDER BY event_id DESC LIMIT ?",
                (experiment_id, limit),
            ).fetchall()
        return [
            {**dict(row), "payload": _loads(row["payload_json"], {})}
            for row in rows
        ]

    def runtime_status_events(self, experiment_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT payload_json, created_at FROM events "
                "WHERE experiment_id=? AND event_type='runtime_status' ORDER BY event_id",
                (experiment_id,),
            ).fetchall()
        return [
            {
                **_loads(row["payload_json"], {}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def record_runtime_event(
        self,
        experiment_id: str,
        cycle_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO events(experiment_id, cycle_id, event_type, payload_json, created_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (
                    experiment_id,
                    cycle_id,
                    event_type,
                    _json(payload),
                    iso_utc(datetime.now(timezone.utc)),
                ),
            )

    # ---- exchange accounts (metadata only; secrets live in the OS store) ----
    def save_exchange_account(self, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise PaperTradingError("exchange account must be an object")
        allowed = {
            "account_id",
            "exchange",
            "display_name",
            "environment",
            "settle_currency",
            "expected_ip",
            "enabled",
            "sync_enabled",
        }
        extra = set(value) - allowed
        if extra:
            if extra & {"api_key", "api_secret", "secret", "key", "password", "token"}:
                raise PaperTradingError("send exchange credentials to the secret endpoint, not account metadata")
            raise PaperTradingError("exchange account contains unsupported fields")
        account_id = value.get("account_id", "gate-main")
        if not isinstance(account_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", account_id):
            raise PaperTradingError("account_id must be lowercase letters, digits or '-'")
        if value.get("exchange", "gate") != "gate":
            raise PaperTradingError("only Gate.io exchange accounts are supported")
        display_name = value.get("display_name", "Gate.io")
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 80:
            raise PaperTradingError("display_name must contain 1 to 80 characters")
        environment = value.get("environment", "live")
        if environment not in {"live", "testnet"}:
            raise PaperTradingError("environment must be live or testnet")
        settle = value.get("settle_currency", "USDT")
        if settle != "USDT":
            raise PaperTradingError("settle currency must be USDT")
        expected_ip = value.get("expected_ip") or None
        if expected_ip is not None and (
            not isinstance(expected_ip, str) or not re.fullmatch(r"[0-9a-fA-F.:/, ]{3,200}", expected_ip)
        ):
            raise PaperTradingError("expected_ip must list IP addresses")
        for field in ("enabled", "sync_enabled"):
            if not isinstance(value.get(field, True), bool):
                raise PaperTradingError(f"{field} must be boolean")
        now = iso_utc(datetime.now(timezone.utc))
        with self.transaction() as db:
            db.execute(
                "INSERT INTO exchange_accounts(account_id, exchange, display_name, environment, settle_currency, "
                "expected_ip, enabled, sync_enabled, key_secret_id, secret_secret_id, created_at, updated_at) "
                "VALUES(?, 'gate', ?, ?, 'USDT', ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(account_id) DO UPDATE SET "
                "display_name=excluded.display_name, environment=excluded.environment, "
                "expected_ip=excluded.expected_ip, enabled=excluded.enabled, "
                "sync_enabled=excluded.sync_enabled, updated_at=excluded.updated_at",
                (
                    account_id,
                    display_name.strip(),
                    environment,
                    expected_ip,
                    1 if value.get("enabled", True) else 0,
                    1 if value.get("sync_enabled", True) else 0,
                    f"gate.{account_id}.api-key",
                    f"gate.{account_id}.api-secret",
                    now,
                    now,
                ),
            )
        return self.exchange_account(account_id)

    def exchange_account(self, account_id: str) -> dict[str, Any]:
        rows = self._query("SELECT * FROM exchange_accounts WHERE account_id=?", (account_id,))
        if not rows:
            raise PaperTradingError("exchange account was not found")
        return self._public_account(dict(rows[0]))

    def exchange_account_internal(self, account_id: str) -> dict[str, Any]:
        rows = self._query("SELECT * FROM exchange_accounts WHERE account_id=?", (account_id,))
        if not rows:
            raise PaperTradingError("exchange account was not found")
        return dict(rows[0])

    def _public_account(self, row: dict[str, Any]) -> dict[str, Any]:
        credential = {"api_key": False, "api_secret": False, "backend": None}
        if self.resolver is not None:
            key = self.resolver.status(secret_id=row["key_secret_id"], env_name=None)
            secret = self.resolver.status(secret_id=row["secret_secret_id"], env_name=None)
            credential = {"api_key": key["stored"], "api_secret": secret["stored"], "backend": key["secret_backend"]}
        latest = self.latest_account_sync(row["account_id"])
        return {
            "account_id": row["account_id"],
            "exchange": "gate",
            "display_name": row["display_name"],
            "environment": row["environment"],
            "settle_currency": row["settle_currency"],
            "expected_ip": row["expected_ip"],
            "enabled": bool(row["enabled"]),
            "sync_enabled": bool(row["sync_enabled"]),
            "credentials": credential,
            "write_execution": False,
            "last_sync": latest,
        }

    def list_exchange_accounts(self) -> list[dict[str, Any]]:
        return [
            self._public_account(dict(row))
            for row in self._query("SELECT * FROM exchange_accounts ORDER BY account_id")
        ]

    def record_account_sync(self, account_id: str, payload: dict[str, Any]) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO exchange_account_syncs(account_id, source, status, payload_json, synced_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (
                    account_id,
                    payload.get("source", "unknown"),
                    payload.get("status", "unknown"),
                    _json(payload),
                    payload.get("synced_at") or iso_utc(datetime.now(timezone.utc)),
                ),
            )

    def latest_account_sync(self, account_id: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT payload_json FROM exchange_account_syncs WHERE account_id=? ORDER BY sync_id DESC LIMIT 1",
            (account_id,),
        )
        return _loads(rows[0]["payload_json"], None) if rows else None

    def account_syncs(self) -> list[dict[str, Any]]:
        return [
            {"account_id": row["account_id"], **_loads(row["payload_json"], {})}
            for row in self._query(
                "SELECT account_id, payload_json FROM exchange_account_syncs ORDER BY sync_id"
            )
        ]

    # ---- market stream health / integration evidence ----
    def record_stream_health(self, row: dict[str, Any]) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO market_stream_health(provider, event, state, payload_json, observed_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (
                    row.get("provider", "gate_usdt_futures"),
                    str(row.get("event", "health")),
                    str(row.get("state", "UNKNOWN")),
                    _json(row),
                    row.get("observed_at") or iso_utc(datetime.now(timezone.utc)),
                ),
            )
            db.execute(
                "DELETE FROM market_stream_health WHERE health_id <= "
                "(SELECT MAX(health_id) - 20000 FROM market_stream_health)"
            )

    def stream_health(self, *, limit: int = 5000) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT health_id, provider, event, state, payload_json, observed_at FROM market_stream_health "
            "ORDER BY health_id DESC LIMIT ?",
            (limit,),
        )
        return [
            {**{k: row[k] for k in ("health_id", "provider", "event", "state", "observed_at")},
             "payload": _loads(row["payload_json"], {})}
            for row in reversed(rows)
        ]

    def record_integration_check(self, check_id: str, payload: dict[str, Any]) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO integration_checks(check_id, payload_json, created_at) VALUES(?, ?, ?)",
                (check_id, _json(payload), iso_utc(datetime.now(timezone.utc))),
            )

    def latest_integration_check(self) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT payload_json FROM integration_checks ORDER BY created_at DESC LIMIT 1"
        )
        return _loads(rows[0]["payload_json"], None) if rows else None

    def export_records(self, experiment_id: str) -> dict[str, Any]:
        with self._lock:
            cycles = self._db.execute(
                "SELECT cycle_id, symbol, cycle_slot, status, snapshot_hash, payload_json, created_at "
                "FROM cycles WHERE experiment_id=? ORDER BY cycle_slot, symbol",
                (experiment_id,),
            ).fetchall()
            positions = self._db.execute(
                "SELECT * FROM positions WHERE experiment_id=? ORDER BY opened_at, symbol, cohort",
                (experiment_id,),
            ).fetchall()
            fills = self._db.execute(
                "SELECT * FROM fills WHERE experiment_id=? ORDER BY as_of, fill_id",
                (experiment_id,),
            ).fetchall()
            orders = self._db.execute(
                "SELECT * FROM orders WHERE experiment_id=? ORDER BY created_at, order_id",
                (experiment_id,),
            ).fetchall()
            risk = self._db.execute(
                "SELECT * FROM risk_events WHERE experiment_id=? ORDER BY event_id",
                (experiment_id,),
            ).fetchall()
            calls = self._db.execute(
                "SELECT * FROM ai_calls WHERE experiment_id=? ORDER BY call_id",
                (experiment_id,),
            ).fetchall()
            equity = self._db.execute(
                "SELECT * FROM equity WHERE experiment_id=? ORDER BY cohort, as_of",
                (experiment_id,),
            ).fetchall()
            wallet_rows = self._db.execute(
                "SELECT * FROM wallets WHERE experiment_id=? ORDER BY cohort",
                (experiment_id,),
            ).fetchall()
            events = self._db.execute(
                "SELECT event_id, cycle_id, event_type, payload_json, created_at FROM events "
                "WHERE experiment_id=? ORDER BY event_id",
                (experiment_id,),
            ).fetchall()
            market_history_runs = self._db.execute(
                "SELECT run_id, provider_id, symbol, interval, requested_bars, retrieved_bars, "
                "inserted_bars, start_time, data_cutoff, data_origin, content_sha256, status, "
                "error_code, created_at FROM market_history_runs WHERE experiment_id=? "
                "ORDER BY created_at, run_id",
                (experiment_id,),
            ).fetchall()
            market_history_bars = self._db.execute(
                "SELECT DISTINCT h.provider_id, h.symbol, h.interval, h.open_time, h.close_time, "
                "h.open, h.high, h.low, h.close, h.volume, h.data_origin "
                "FROM market_history AS h JOIN market_history_runs AS r "
                "ON h.provider_id=r.provider_id AND h.symbol=r.symbol AND h.interval=r.interval "
                "AND h.open_time>=r.start_time AND h.close_time<=r.data_cutoff "
                "WHERE r.experiment_id=? ORDER BY h.provider_id, h.symbol, h.interval, h.open_time",
                (experiment_id,),
            ).fetchall()
        return {
            "cycles": [
                {
                    "cycle_id": row["cycle_id"],
                    "symbol": row["symbol"],
                    "cycle_slot": row["cycle_slot"],
                    "status": row["status"],
                    "snapshot_hash": row["snapshot_hash"],
                    "payload": _loads(row["payload_json"], {}),
                    "created_at": row["created_at"],
                }
                for row in cycles
            ],
            "positions": [dict(row) for row in positions],
            "fills": [dict(row) for row in fills],
            "orders": [dict(row) for row in orders],
            "risk_events": [
                {**dict(row), "details": _loads(row["details_json"], {})}
                for row in risk
            ],
            "ai_calls": [dict(row) for row in calls],
            "equity": [dict(row) for row in equity],
            "wallets": [dict(row) for row in wallet_rows],
            "events": [
                {**dict(row), "payload": _loads(row["payload_json"], {})}
                for row in events
            ],
            "market_history_runs": [dict(row) for row in market_history_runs],
            "market_history_bars": [dict(row) for row in market_history_bars],
        }

    def save_market_history_lane(
        self,
        *,
        experiment_id: str,
        provider_id: str,
        symbol: str,
        interval: str,
        candles: list[dict[str, Any]],
        requested_bars: int,
        start_time: str,
        data_cutoff: str,
        data_origin: str,
    ) -> dict[str, Any]:
        if interval not in INTERVAL_SECONDS:
            raise PaperTradingError("warm-up interval is unsupported")
        if (
            not isinstance(candles, list)
            or isinstance(requested_bars, bool)
            or not isinstance(requested_bars, int)
            or len(candles) != requested_bars
        ):
            raise PaperTradingError("warm-up candle count does not match the requested profile")
        if data_origin not in DATA_ORIGINS:
            raise PaperTradingError("warm-up data origin is unsupported")
        cutoff = parse_utc(data_cutoff, "warmup.data_cutoff")
        expected_open = parse_utc(start_time, "warmup.start_time")
        interval_seconds = INTERVAL_SECONDS[interval]
        for index, candle in enumerate(candles):
            if not isinstance(candle, dict) or set(candle) != {
                "open_time",
                "close_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
            }:
                raise PaperTradingError(f"warm-up candle {index} is malformed")
            opened = parse_utc(candle["open_time"], f"warmup.candles[{index}].open_time")
            closed = parse_utc(candle["close_time"], f"warmup.candles[{index}].close_time")
            if opened != expected_open or (closed - opened).total_seconds() != interval_seconds:
                raise PaperTradingError("warm-up candles are not contiguous for their interval")
            if closed > cutoff:
                raise PaperTradingError("warm-up candle closes after the requested cutoff")
            values = {
                name: _finite(candle[name], f"warmup.candles[{index}].{name}")
                for name in ("open", "high", "low", "close", "volume")
            }
            if (
                values["open"] <= 0
                or values["high"] <= 0
                or values["low"] <= 0
                or values["close"] <= 0
                or values["volume"] < 0
                or values["high"] < max(values["open"], values["close"], values["low"])
                or values["low"] > min(values["open"], values["close"], values["high"])
            ):
                raise PaperTradingError("warm-up candle values are inconsistent")
            expected_open = closed
        if candles and parse_utc(candles[-1]["close_time"], "warmup.last.close_time") > cutoff:
            raise PaperTradingError("warm-up history exceeds its point-in-time cutoff")

        content_hash = digest(candles)
        identity = {
            "experiment_id": experiment_id,
            "provider_id": provider_id,
            "symbol": symbol,
            "interval": interval,
            "start_time": start_time,
            "data_cutoff": data_cutoff,
            "content_sha256": content_hash,
        }
        run_id = f"warmup-{digest(identity)[:24]}"
        now = iso_utc(datetime.now(timezone.utc))
        inserted = 0
        with self.transaction() as db:
            for candle in candles:
                cursor = db.execute(
                    "INSERT OR IGNORE INTO market_history(provider_id, symbol, interval, "
                    "open_time, close_time, open, high, low, close, volume, data_origin, recorded_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        provider_id,
                        symbol,
                        interval,
                        candle["open_time"],
                        candle["close_time"],
                        candle["open"],
                        candle["high"],
                        candle["low"],
                        candle["close"],
                        candle["volume"],
                        data_origin,
                        now,
                    ),
                )
                inserted += cursor.rowcount
            db.execute(
                "INSERT OR IGNORE INTO market_history_runs(run_id, experiment_id, provider_id, "
                "symbol, interval, requested_bars, retrieved_bars, inserted_bars, start_time, "
                "data_cutoff, data_origin, content_sha256, status, error_code, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'complete', NULL, ?)",
                (
                    run_id,
                    experiment_id,
                    provider_id,
                    symbol,
                    interval,
                    requested_bars,
                    len(candles),
                    inserted,
                    start_time,
                    data_cutoff,
                    data_origin,
                    content_hash,
                    now,
                ),
            )
        return {
            **identity,
            "run_id": run_id,
            "requested_bars": requested_bars,
            "retrieved_bars": len(candles),
            "inserted_bars": inserted,
            "status": "complete",
        }

    def market_history_status(self, experiment_id: str) -> dict[str, Any]:
        with self._lock:
            runs = self._db.execute(
                "SELECT run_id, provider_id, symbol, interval, requested_bars, retrieved_bars, "
                "inserted_bars, start_time, data_cutoff, data_origin, content_sha256, status, "
                "error_code, created_at FROM market_history_runs WHERE experiment_id=? "
                "ORDER BY created_at DESC, symbol, interval",
                (experiment_id,),
            ).fetchall()
            summary = self._db.execute(
                "SELECT provider_id, symbol, interval, COUNT(*) AS stored_bars, "
                "MIN(open_time) AS first_open_time, MAX(close_time) AS last_close_time, "
                "MAX(data_origin) AS data_origin FROM market_history "
                "GROUP BY provider_id, symbol, interval "
                "ORDER BY symbol, interval",
            ).fetchall()
        return {
            "runs": [dict(row) for row in runs],
            "lanes": [dict(row) for row in summary],
        }

    def archive_market_snapshot(
        self,
        snapshot: MarketSnapshot,
        provider_id: str,
        *,
        retention_days: int | None = None,
    ) -> dict[str, int]:
        snapshot.validate()
        if retention_days is not None and (
            isinstance(retention_days, bool)
            or retention_days not in {30, 90, 365}
        ):
            raise PaperTradingError("market_data_retention_days is invalid")
        now = iso_utc(datetime.now(timezone.utc))
        lane_data = {
            "15m": snapshot.candles_15m,
            "1h": snapshot.candles_1h,
            "4h": snapshot.candles_4h,
        }
        inserted = {interval: 0 for interval in lane_data}
        with self.transaction() as db:
            for interval, candles in lane_data.items():
                for candle in candles:
                    close_time = parse_utc(
                        candle["close_time"], f"snapshot.{interval}.close_time"
                    )
                    if close_time > parse_utc(snapshot.data_cutoff, "snapshot.data_cutoff"):
                        raise PaperTradingError(
                            "cannot archive market data newer than its point-in-time cutoff"
                        )
                    cursor = db.execute(
                        "INSERT OR IGNORE INTO market_history(provider_id, symbol, interval, "
                        "open_time, close_time, open, high, low, close, volume, data_origin, recorded_at) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            provider_id,
                            snapshot.symbol,
                            interval,
                            candle["open_time"],
                            candle["close_time"],
                            candle["open"],
                            candle["high"],
                            candle["low"],
                            candle["close"],
                            candle["volume"],
                            snapshot.data_origin,
                            now,
                        ),
                    )
                    inserted[interval] += cursor.rowcount
            if retention_days is not None:
                self._prune_market_history_locked(
                    db,
                    provider_id,
                    parse_utc(snapshot.data_cutoff, "snapshot.data_cutoff")
                    - timedelta(days=retention_days),
                )
        return inserted

    def load_market_history(
        self,
        *,
        provider_id: str,
        symbol: str,
        interval: str,
        through: str,
        limit: int = 20_000,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20_000:
            raise PaperTradingError("market history limit must be between 1 and 20000")
        cutoff = iso_utc(parse_utc(through, "market_history.through"))
        with self._lock:
            rows = self._db.execute(
                "SELECT open_time, close_time, open, high, low, close, volume "
                "FROM market_history WHERE provider_id=? AND symbol=? AND interval=? "
                "AND close_time<=? ORDER BY open_time DESC LIMIT ?",
                (provider_id, symbol, interval, cutoff, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def wallet_summary(self, experiment_id: str, cohort: str = "primary") -> dict[str, Any]:
        with self.transaction() as db:
            summary = self._portfolio_locked(
                db, experiment_id, cohort
            )
            return summary

    def last_processed_bar(self, experiment_id: str, position_id: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(close_time) AS close_time FROM processed_bars "
                "WHERE experiment_id=? AND position_id=?",
                (experiment_id, position_id),
            ).fetchone()
            if row is not None and row["close_time"]:
                return row["close_time"]
            opened = self._db.execute(
                "SELECT opened_at FROM positions WHERE position_id=? AND experiment_id=?",
                (position_id, experiment_id),
            ).fetchone()
        return None if opened is None else opened["opened_at"]

    def execute_reduce(
        self,
        experiment_id: str,
        intent: TradingIntent,
        risk: RiskDecision,
        *,
        mark_price: float,
        fee_rate: float,
        as_of: str,
        data_origin: str,
        slippage_cost: float = 0.0,
        order_key: str | None = None,
    ) -> dict[str, Any]:
        if not risk.allowed or not risk.reduce_only:
            return {"accepted": False, "filled_quantity": 0.0, "reason": risk.reason}
        with self.transaction() as db:
            if intent.position_id:
                row = db.execute(
                    "SELECT * FROM positions WHERE position_id=? AND experiment_id=? AND status='open'",
                    (intent.position_id, experiment_id),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM positions WHERE experiment_id=? AND symbol=? AND side=? "
                    "AND cohort='primary' AND status='open' ORDER BY opened_at LIMIT 1",
                    (experiment_id, intent.symbol, intent.side),
                ).fetchone()
            if row is None or row["cohort"] != "primary":
                return {"accepted": False, "filled_quantity": 0.0, "reason": "position no longer open"}
            quantity = min(float(risk.quantity), float(row["quantity"]))
            if quantity <= 0:
                return {"accepted": False, "filled_quantity": 0.0, "reason": "no remaining quantity"}
            order_id = f"{row['position_id']}:reduce:{order_key or as_of}"
            inserted = db.execute(
                "INSERT OR IGNORE INTO orders(order_id, experiment_id, cycle_id, position_id, symbol, "
                "cohort, side, order_type, reduce_only, quantity, filled_quantity, limit_price, status, "
                "risk_json, created_at, updated_at) VALUES(?, ?, ?, ?, ?, 'primary', ?, 'market', 1, ?, 0, NULL, "
                "'open', ?, ?, ?)",
                (
                    order_id,
                    experiment_id,
                    row["cycle_id"],
                    row["position_id"],
                    row["symbol"],
                    "sell" if row["side"] == "long" else "buy",
                    quantity,
                    _json(risk.to_dict()),
                    as_of,
                    as_of,
                ),
            )
            if inserted.rowcount != 1:
                return {
                    "accepted": False,
                    "filled_quantity": 0.0,
                    "reason": "duplicate reduce-only request was ignored",
                }
            executed = self._reduce_position_locked(
                db,
                row,
                quantity,
                float(mark_price),
                fee_rate,
                as_of,
                "reduce_only",
                data_origin,
                slippage_cost=slippage_cost,
            )
            filled = float(executed.get("quantity", 0.0))
            db.execute(
                "UPDATE orders SET status='filled', filled_quantity=?, updated_at=? WHERE order_id=?",
                (filled, as_of, order_id),
            )
            self.record_risk_event(
                db,
                experiment_id,
                row["cycle_id"],
                row["symbol"],
                "primary",
                risk.to_dict(),
            )
            return {
                "accepted": filled > 0,
                "filled_quantity": filled,
                "remaining_quantity": executed.get("remaining_quantity", float(row["quantity"])),
                "realized_pnl": executed.get("realized_pnl"),
                "position_id": row["position_id"],
            }

    def processed(self, position_id: str, close_time: str, experiment_id: str) -> bool:
        with self._lock:
            return self._db.execute(
                "SELECT 1 FROM processed_bars WHERE experiment_id=? AND position_id=? AND close_time=?",
                (experiment_id, position_id, close_time),
            ).fetchone() is not None

    def apply_monitor_bar(
        self,
        experiment_id: str,
        symbol: str,
        bar: dict[str, Any],
        *,
        funding_rate: float | None,
        taker_fee_rate: float,
        data_origin: str,
        maker_fee_rate: float | None = None,
        slippage_bps: float = 0.0,
        suspect: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Apply one closed execution bar once; no AI/provider is invoked here.

        ``suspect`` marks an isolated spike (see ``execution_safety.suspect_print``). Stops and
        liquidation are then not triggered from that bar's extreme (mark-price semantics: a bad
        last print does not liquidate); the bar's close still marks the position.
        """
        close_time = iso_utc(parse_utc(bar["close_time"], "monitor.close_time"))
        open_time = iso_utc(parse_utc(bar["open_time"], "monitor.open_time"))
        o, h, l, c = (float(bar[key]) for key in ("open", "high", "low", "close"))
        completed: list[dict[str, Any]] = []
        with self.transaction() as db:
            orders = db.execute(
                "SELECT * FROM orders WHERE experiment_id=? AND symbol=? AND status='pending'",
                (experiment_id, symbol),
            ).fetchall()
            for order in orders:
                if order["last_checked_bar"] and order["last_checked_bar"] >= close_time:
                    continue
                limit = float(order["limit_price"])
                if order["side"] == "long":
                    touched = l <= limit
                    fill_price = min(o, limit) if o <= limit else limit
                else:
                    touched = h >= limit
                    fill_price = max(o, limit) if o >= limit else limit
                if touched:
                    saved = _loads(order["risk_json"], {})
                    risk_data = saved.get("risk", {})
                    intent_data = saved.get("intent", {})
                    if self._fill_entry_locked(
                        db,
                        experiment_id,
                        order["order_id"],
                        order["position_id"],
                        order["cohort"],
                        intent_data,
                        float(order["quantity"]),
                        fill_price,
                        int(risk_data["leverage"]),
                        float(risk_data["liquidation_price"]),
                        taker_fee_rate if maker_fee_rate is None else maker_fee_rate,
                        close_time,
                        data_origin,
                    ):
                        completed.append(
                            {"position_id": order["position_id"], "event": "limit_filled"}
                        )
                else:
                    db.execute(
                        "UPDATE orders SET last_checked_bar=?, updated_at=? WHERE order_id=? "
                        "AND status='pending'",
                        (close_time, close_time, order["order_id"]),
                    )

            positions = db.execute(
                "SELECT * FROM positions WHERE experiment_id=? AND symbol=? AND status='open'",
                (experiment_id, symbol),
            ).fetchall()
            for position in positions:
                duplicate = db.execute(
                    "SELECT 1 FROM processed_bars WHERE experiment_id=? AND position_id=? AND close_time=?",
                    (experiment_id, position["position_id"], close_time),
                ).fetchone()
                if duplicate:
                    continue
                db.execute(
                    "INSERT INTO processed_bars(experiment_id, position_id, close_time) VALUES(?, ?, ?)",
                    (experiment_id, position["position_id"], close_time),
                )
                self._settle_funding_locked(
                    db,
                    position,
                    open_time,
                    close_time,
                    o,
                    funding_rate,
                    data_origin,
                )
                current = db.execute(
                    "SELECT * FROM positions WHERE position_id=?",
                    (position["position_id"],),
                ).fetchone()
                side = current["side"]
                liq = float(current["liquidation_price"])
                stop = float(current["stop_price"])
                target = float(current["target_price"])
                exit_reason = None
                exit_price = None
                if suspect is not None:
                    db.execute(
                        "INSERT INTO safety_events(experiment_id, instrument_id, position_ref, kind, code, source, detail_json, created_at) "
                        "VALUES(?, ?, ?, 'guard', 'SUSPECT_PRINT', 'SYSTEM', ?, ?)",
                        (experiment_id, None, f"perp:{current['position_id']}", _json({**suspect, "stop": stop, "liquidation": liq}), close_time),
                    )
                    h = max(o, c) if side == "short" else h
                    l = min(o, c) if side == "long" else l
                if side == "long":
                    if o <= liq or l <= liq:
                        exit_reason, exit_price = "liquidation", min(o, liq)
                    elif o <= stop or l <= stop:
                        exit_reason, exit_price = "stop", min(o, stop)
                    elif o >= target:
                        exit_reason, exit_price = "target", max(o, target)
                    elif h >= target:
                        exit_reason, exit_price = "target", target
                else:
                    if o >= liq or h >= liq:
                        exit_reason, exit_price = "liquidation", max(o, liq)
                    elif o >= stop or h >= stop:
                        exit_reason, exit_price = "stop", max(o, stop)
                    elif o <= target:
                        exit_reason, exit_price = "target", min(o, target)
                    elif l <= target:
                        exit_reason, exit_price = "target", target
                if exit_reason is not None:
                    reference_exit = float(exit_price)
                    if exit_reason == "target":
                        exit_fee_rate = (
                            taker_fee_rate if maker_fee_rate is None else maker_fee_rate
                        )
                    else:
                        side_slippage = -1 if side == "long" else 1
                        exit_price = reference_exit * (
                            1 + side_slippage * slippage_bps / 10_000
                        )
                        exit_fee_rate = taker_fee_rate
                    slippage_cost = abs(float(exit_price) - reference_exit) * float(
                        current["quantity"]
                    )
                    self._reduce_position_locked(
                        db,
                        current,
                        float(current["quantity"]),
                        float(exit_price),
                        exit_fee_rate,
                        close_time,
                        exit_reason,
                        data_origin,
                        slippage_cost=slippage_cost,
                    )
                    completed.append(
                        {
                            "position_id": current["position_id"],
                            "event": exit_reason,
                            "exit_price": float(exit_price),
                        }
                    )
                else:
                    completed.extend(
                        self._apply_partial_targets_locked(
                            db,
                            current,
                            h,
                            l,
                            close_time,
                            taker_fee_rate if maker_fee_rate is None else maker_fee_rate,
                            data_origin,
                        )
                    )
                    db.execute(
                        "UPDATE positions SET mark_price=? WHERE position_id=? AND status='open'",
                        (c, current["position_id"]),
                    )
                    self._record_equity_locked(
                        db, experiment_id, current["cohort"], close_time, data_origin
                    )
        return completed

    def _apply_partial_targets_locked(
        self,
        db: sqlite3.Connection,
        position: sqlite3.Row,
        high: float,
        low: float,
        close_time: str,
        fee_rate: float,
        data_origin: str,
    ) -> list[dict[str, Any]]:
        """Scale-out targets from the position plan. At most one partial per bar; the stop
        (checked earlier) always wins a bar that touches both, and a partial never closes
        the final remainder (the final target does)."""

        if position["cohort"] != "primary" or position["status"] != "open":
            return []
        ref = f"perp:{position['position_id']}"
        meta = db.execute("SELECT targets_json FROM position_meta WHERE position_ref=?", (ref,)).fetchone()
        if meta is None:
            return []
        targets = _loads(meta["targets_json"], [])
        for target in targets:
            if target.get("hit") or target.get("final"):
                continue
            price = float(target["price"])
            touched = high >= price if position["side"] == "long" else low <= price
            if not touched:
                continue
            quantity = min(float(target.get("quantity") or 0.0), float(position["quantity"]))
            if quantity <= 0 or float(position["quantity"]) - quantity <= 1e-10:
                continue
            executed = self._reduce_position_locked(
                db, position, quantity, price, fee_rate, close_time, "target_partial", data_origin
            )
            target["hit"] = True
            target["hit_at"] = close_time
            db.execute(
                "UPDATE position_meta SET targets_json=?, updated_at=? WHERE position_ref=?",
                (_json(targets), close_time, ref),
            )
            db.execute(
                "INSERT INTO management_events(experiment_id, position_ref, order_ref, market_type, action, source, "
                "request_id, before_json, after_json, risk_before, risk_after, snapshot_json, created_at) "
                "VALUES(?, ?, NULL, 'perpetual', 'TARGET_PARTIAL', 'SYSTEM', NULL, ?, ?, NULL, NULL, ?, ?)",
                (
                    position["experiment_id"],
                    ref,
                    _json({"quantity": float(position["quantity"])}),
                    _json({"filled_quantity": executed.get("quantity"), "price": price}),
                    _json({"bar_close": close_time}),
                    close_time,
                ),
            )
            return [{"position_id": position["position_id"], "event": "target_partial", "exit_price": price}]
        return []

    def _settle_funding_locked(
        self,
        db: sqlite3.Connection,
        position: sqlite3.Row,
        open_time: str,
        close_time: str,
        reference_price: float,
        funding_rate: float | None,
        data_origin: str,
    ) -> None:
        if funding_rate is None or not math.isfinite(float(funding_rate)):
            return
        start_slot = int(position["last_funding_slot"])
        close_timestamp = int(parse_utc(close_time, "funding.close_time").timestamp())
        close_slot = close_timestamp // (8 * 60 * 60)
        if close_slot <= start_slot:
            return
        quantity = float(position["quantity"])
        price = reference_price
        signed_cost = (1 if position["side"] == "long" else -1) * quantity * price * float(funding_rate)
        db.execute(
            "UPDATE wallets SET cash_balance=cash_balance-? WHERE experiment_id=? AND cohort=?",
            (signed_cost, position["experiment_id"], position["cohort"]),
        )
        db.execute(
            "UPDATE positions SET funding_paid=funding_paid+?, last_funding_slot=? WHERE position_id=?",
            (signed_cost, close_slot, position["position_id"]),
        )
        db.execute(
            "INSERT INTO events(experiment_id, cycle_id, event_type, payload_json, created_at) "
            "VALUES(?, ?, 'funding_settlement', ?, ?)",
            (
                position["experiment_id"],
                position["cycle_id"],
                _json(
                    {
                        "position_id": position["position_id"],
                        "funding_rate": float(funding_rate),
                        "funding_cost": signed_cost,
                        "observed_bar_close": close_time,
                        "data_origin": data_origin,
                    }
                ),
                close_time,
            ),
        )
        self._record_equity_locked(
            db,
            position["experiment_id"],
            position["cohort"],
            close_time,
            data_origin,
        )

    def _reduce_position_locked(
        self,
        db: sqlite3.Connection,
        position: sqlite3.Row,
        requested_quantity: float,
        fill_price: float,
        fee_rate: float,
        as_of: str,
        reason: str,
        data_origin: str,
        *,
        slippage_cost: float = 0.0,
    ) -> dict[str, Any]:
        if position["status"] != "open" or position["closed_pnl_recorded"]:
            return {"closed": False, "reason": "already_closed"}
        current_quantity = float(position["quantity"])
        quantity = min(max(0.0, requested_quantity), current_quantity)
        if quantity <= 0:
            return {"closed": False, "reason": "no_remaining_quantity"}
        entry = float(position["entry_price"])
        sign = 1 if position["side"] == "long" else -1
        gross = (fill_price - entry) * quantity * sign
        exit_fee = quantity * fill_price * fee_rate
        fee_share = float(position["entry_fee_remaining"]) * quantity / current_quantity
        left = max(0.0, current_quantity - quantity)
        accumulated_gross = float(position["realized_gross"]) + gross
        accumulated_exit_fees = float(position["exit_fees"]) + exit_fee
        remaining_entry_fee = max(0.0, float(position["entry_fee_remaining"]) - fee_share)
        db.execute(
            "UPDATE wallets SET cash_balance=cash_balance+?-? WHERE experiment_id=? AND cohort=?",
            (gross, exit_fee, position["experiment_id"], position["cohort"]),
        )
        db.execute(
            "INSERT INTO fills(fill_id, experiment_id, cycle_id, order_id, position_id, cohort, "
            "side, quantity, price, fee, slippage_cost, as_of) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"{position['position_id']}:exit:{as_of}:{uuid.uuid4().hex[:6]}",
                position["experiment_id"],
                position["cycle_id"],
                f"{position['position_id']}:exit",
                position["position_id"],
                position["cohort"],
                "sell" if position["side"] == "long" else "buy",
                quantity,
                fill_price,
                exit_fee,
                slippage_cost,
                as_of,
            ),
        )
        final = left <= 1e-10
        closed_pnl = None
        if final:
            closed_pnl = (
                accumulated_gross
                - float(position["entry_fee"])
                - accumulated_exit_fees
                - float(position["funding_paid"])
            )
            db.execute(
                "UPDATE positions SET quantity=0, mark_price=?, exit_price=?, exit_reason=?, "
                "realized_gross=?, exit_fees=?, entry_fee_remaining=0, margin=0, status='closed', "
                "closed_at=?, realized_pnl=?, closed_pnl_recorded=1, "
                "slippage_paid=slippage_paid+? WHERE position_id=? AND status='open'",
                (
                    fill_price,
                    fill_price,
                    reason,
                    accumulated_gross,
                    accumulated_exit_fees,
                    as_of,
                    closed_pnl,
                    slippage_cost,
                    position["position_id"],
                ),
            )
            db.execute(
                "UPDATE orders SET status='closed', updated_at=? WHERE position_id=? AND status='filled'",
                (as_of, position["position_id"]),
            )
        else:
            db.execute(
                "UPDATE positions SET quantity=?, mark_price=?, exit_price=?, exit_reason=?, "
                "realized_gross=?, exit_fees=?, entry_fee_remaining=?, margin=margin*?/quantity, "
                "slippage_paid=slippage_paid+? WHERE position_id=? AND status='open'",
                (
                    left,
                    fill_price,
                    fill_price,
                    reason,
                    accumulated_gross,
                    accumulated_exit_fees,
                    remaining_entry_fee,
                    left,
                    slippage_cost,
                    position["position_id"],
                ),
            )
        db.execute(
            "INSERT INTO events(experiment_id, cycle_id, event_type, payload_json, created_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (
                position["experiment_id"],
                position["cycle_id"],
                "paper_position_closed" if final else "paper_position_reduced",
                _json(
                    {
                        "position_id": position["position_id"],
                        "quantity": quantity,
                        "price": fill_price,
                        "gross_pnl": gross,
                        "exit_fee": exit_fee,
                        "exit_reason": reason,
                        "realized_pnl": closed_pnl,
                    }
                ),
                as_of,
            ),
        )
        self._record_equity_locked(
            db, position["experiment_id"], position["cohort"], as_of, data_origin
        )
        return {
            "closed": final,
            "quantity": quantity,
            "remaining_quantity": left,
            "gross_pnl": gross,
            "exit_fee": exit_fee,
            "realized_pnl": closed_pnl,
        }


class _BudgetBlocked(Exception):
    """Internal control flow: the budget guard refused a paid call before any request."""


def _jev_direction(vector: dict[str, Any] | None) -> str:
    if not vector:
        return "none"
    regime = vector.get("answers", {}).get("market_regime", {}).get("value")
    if regime == "bull":
        return "long"
    if regime == "bear":
        return "short"
    return "none"


def _jev_fast_approved(vector: dict[str, Any] | None) -> tuple[bool, str]:
    if not vector:
        return False, "Jev decision is unavailable"
    answers = vector.get("answers", {})
    setup = answers.get("setup_quality", {}).get("value")
    breakout = answers.get("breakout_valid", {}).get("value")
    conflict = answers.get("signal_conflict", {}).get("value", 1)
    if _jev_direction(vector) not in {"long", "short"}:
        return False, "Jev did not identify a directional regime"
    if not isinstance(setup, (int, float)) or setup < 2:
        return False, "Jev setup quality did not pass the fast-path threshold"
    if not isinstance(breakout, (int, float)) or breakout < 0.65:
        return False, "breakout evidence did not pass the fast-path threshold"
    if not isinstance(conflict, (int, float)) or conflict >= 0.65:
        return False, "Jev evidence indicates a material conflict"
    return True, "confident typed decision passed the configured fast path"


class PaperRuntime:
    """End-to-end frozen-snapshot → AI routing → deterministic PAPER execution."""

    def __init__(
        self,
        store: PaperStore,
        *,
        market_provider: FuturesMarketDataProvider | None = None,
        jev_provider: Any | None = None,
        gpt_provider: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        resolver: CredentialResolver | None = None,
        live_stream: Any | None = None,
        catalog: Any | None = None,
    ) -> None:
        self.store = store
        self._catalog_override = catalog
        self._portfolio: Any | None = None
        self._market_provider_override = market_provider
        self._jev_provider_override = jev_provider
        self._gpt_provider_override = gpt_provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.risk_engine = RiskEngine()
        self._cycle_lock = threading.RLock()
        if resolver is None:
            from .secret_store import MemorySecretStore

            # Safe default: session memory + environment references. The server passes the OS store.
            resolver = CredentialResolver(MemorySecretStore())
        self.resolver = resolver
        self.store.resolver = resolver
        self.live_stream = live_stream
        self._gate_provider: GateUsdtFuturesMarketDataProvider | None = None
        self.store.cost_ledger._clock = self._clock
        self.resilience = ResilienceLedger(self.store, self._clock)

    @property
    def portfolio(self) -> Any:
        """Portfolio OS service (Spot, unified orders, authority, re-plan, attention, learning)."""

        if self._portfolio is None:
            from .portfolio_os import PortfolioOS

            self._portfolio = PortfolioOS(self, catalog=self._catalog_override)
        return self._portfolio

    # ------------------------------------------------------------ resilience
    def _escalate(self, level: str, reason: str) -> None:
        from .execution_safety import KILL_RANK as _RANK

        portfolio = self.portfolio
        if _RANK[portfolio.kill_switch()["level"]] < _RANK[level]:
            portfolio.set_kill_switch(level, reason=reason, source="SYSTEM")

    def recover_on_startup(self, *, disk_usage: Callable[[str], Any] | None = None) -> dict[str, Any]:
        """Deterministic startup recovery before any autonomous action. Never repeats a consumed
        AI call, never resumes an interrupted execution by itself, and fails closed on damage."""

        experiment_id = self.store.experiment()["experiment_id"]
        res = self.resilience
        actions: dict[str, Any] = {}
        previous = res.heartbeats().get("process")
        if previous and not (previous.get("detail") or {}).get("clean_shutdown", False):
            res.open_incident("UNCLEAN_SHUTDOWN", severity="WARNING", experiment_id=experiment_id, resolved=True,
                              summary="previous process ended without a graceful shutdown",
                              detail={"last_seen": previous.get("updated_at")})
            actions["unclean_shutdown"] = True
        database = database_health(self.store)
        actions["database_ok"] = database["ok"]
        if not database["ok"]:
            res.open_incident("DATABASE_FAILURE", severity="CRITICAL", experiment_id=experiment_id, dedupe_key="database",
                              summary="SQLite quick_check failed; automation halted to risk-reducing only", detail=database)
            self._escalate("RISK_REDUCING_ONLY", "DATABASE_FAILURE: integrity check failed at startup")
            if self.store.experiment()["status"] == "running":
                self.store.set_status("paused")
        actions["interrupted_cycles"] = self.store.recover_interrupted_cycles(experiment_id)
        with self.store.transaction() as db:
            plans = db.execute("SELECT plan_id, position_ref FROM execution_plans WHERE experiment_id=? AND status='executing'",
                               (experiment_id,)).fetchall()
            db.execute("UPDATE execution_plans SET status='interrupted', updated_at=? WHERE experiment_id=? AND status='executing'",
                       (iso_utc(self._clock()), experiment_id))
        for plan in plans:
            self.portfolio.safety.event(experiment_id, kind="execution", code="EXECUTION_DEFERRED", source="SYSTEM",
                                        detail={"plan_id": plan["plan_id"], "reason": "process restarted during sliced execution; "
                                                "filled slices stand, the remainder is not resumed automatically"},
                                        position_ref=plan["position_ref"])
        actions["interrupted_plans"] = len(plans)
        budget = self.store.experiment()["config"].get("ai_budget") or {}
        stale = self.store._query("SELECT reservation_id FROM ai_budget_reservations WHERE experiment_id=? AND status='reserved'",
                                  (experiment_id,))
        for row in stale:
            try:
                self.store.cost_ledger.settle(row["reservation_id"], experiment_id=experiment_id, charged_usd=None,
                                              keep_reserved_when_unknown=True, budget=budget)
            except Exception:  # a reservation that cannot be settled stays conservative (reserved)
                continue
        actions["stale_ai_reservations_kept_conservative"] = len(stale)
        storage = storage_health(database_path(self.store), min_free_mb=res.settings["min_free_disk_mb"],
                                 **({"disk_usage": disk_usage} if disk_usage else {}))
        actions["storage_ok"] = storage.get("ok", False)
        if not storage.get("ok", False):
            res.open_incident("STORAGE_LOW", severity="CRITICAL", experiment_id=experiment_id, dedupe_key="storage",
                              summary="free disk below the minimum; new entries blocked", detail=storage)
            self._escalate("NO_NEW_ENTRIES", "STORAGE_LOW: free disk below the configured minimum")
        reconciliation = self.portfolio.reconcile(escalate=True)
        actions["reconciliation_ok"] = reconciliation["ok"]
        res.open_incident("PROCESS_RESTART", severity="INFO", experiment_id=experiment_id, resolved=True,
                          summary="startup recovery completed", detail=actions)
        res.heartbeat("process", detail={"clean_shutdown": False, "started_at": iso_utc(self._clock()), "pid": os.getpid()})
        return actions

    def mark_clean_shutdown(self, *, reason: str = "shutdown requested") -> None:
        experiment_id = self.store.experiment()["experiment_id"]
        self.resilience.open_incident("GRACEFUL_SHUTDOWN", severity="INFO", experiment_id=experiment_id, resolved=True,
                                      summary=reason[:200])
        self.resilience.heartbeat("process", detail={"clean_shutdown": True, "stopped_at": iso_utc(self._clock())})
        try:
            self.store._query("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass

    def health(self, *, disk_usage: Callable[[str], Any] | None = None) -> dict[str, Any]:
        """Compact health summary. Read-only: computing health never triggers an action."""

        res = self.resilience
        experiment = self.store.experiment()
        config = experiment["config"]
        beats = res.heartbeats()
        now = self._clock().astimezone(timezone.utc)

        def age(name: str) -> float | None:
            value = (beats.get(name) or {}).get("last_ok_at")
            return None if not value else round((now - parse_utc(value, name)).total_seconds(), 1)

        monitor_interval = int(config["monitor_interval_seconds"])
        running = experiment["status"] == "running"
        scheduler_age, monitor_age = age("scheduler"), age("monitor")
        components: dict[str, dict[str, Any]] = {}
        components["scheduler"] = {
            "status": "IDLE" if not running else "OK" if scheduler_age is not None and scheduler_age < 120 else "DEGRADED",
            "last_heartbeat_age_seconds": scheduler_age, "last_slot": res.last_slot(experiment["experiment_id"]),
            "last_error": (beats.get("scheduler") or {}).get("last_error"),
        }
        components["monitor"] = {
            "status": "OK" if monitor_age is not None and monitor_age < max(60, 3 * monitor_interval) else "DEGRADED" if monitor_age is not None else "UNKNOWN",
            "last_heartbeat_age_seconds": monitor_age, "last_error": (beats.get("monitor") or {}).get("last_error"),
        }
        stream = self.live_stream.status() if self.live_stream is not None else None
        components["market_feed"] = {"status": "OFF" if stream is None else ("OK" if str(stream.get("state") or "").upper() == "LIVE" else "DEGRADED"),
                                     "detail": None if stream is None else {k: stream.get(k) for k in ("state", "last_error", "last_message_at", "counters") if k in stream}}
        database = database_health(self.store)
        components["database"] = {"status": "OK" if database.get("ok") else "FAILED", **database}
        storage = storage_health(database_path(self.store), min_free_mb=res.settings["min_free_disk_mb"],
                                 **({"disk_usage": disk_usage} if disk_usage else {}))
        components["storage"] = {"status": "OK" if storage.get("ok") else "LOW", **storage}
        circuits = res.circuits()
        open_circuits = [c["provider_id"] for c in circuits if c["opened_until"] and parse_utc(c["opened_until"], "o") > now]
        components["ai_providers"] = {"status": "DEGRADED" if open_circuits else "OK", "open_circuits": open_circuits,
                                      "providers": circuits}
        try:
            budget = self.store.cost_ledger.budget_status(experiment["experiment_id"], config["ai_budget"])
            components["budget_guard"] = {"status": "BLOCKED" if budget.get("exhausted") else "OK",
                                          **{k: budget.get(k) for k in ("spent_today_usd", "remaining_today_usd", "open_reservations_usd", "limit_action")}}
        except Exception:
            components["budget_guard"] = {"status": "UNKNOWN"}
        from .execution_safety import reconcile_ledgers

        reconciliation = reconcile_ledgers(self.store, experiment["experiment_id"])
        components["reconciliation"] = {"status": "OK" if reconciliation["ok"] else "FAILED", "problems": reconciliation["problems"][:5]}
        cycles = self.store._query("SELECT MAX(updated_at) AS t FROM cycles WHERE experiment_id=? AND status='completed'",
                                   (experiment["experiment_id"],))
        reviews = self.store._query("SELECT MAX(last_ai_review_at) AS t FROM position_meta WHERE experiment_id=?",
                                    (experiment["experiment_id"],))
        components["last_success"] = {"status": "OK", "cycle_at": cycles[0]["t"] if cycles else None,
                                      "position_review_at": reviews[0]["t"] if reviews else None}
        components["kill_switch"] = {"status": "OK" if self.portfolio.kill_switch()["level"] == "NORMAL" else "RESTRICTED",
                                     "level": self.portfolio.kill_switch()["level"]}
        order = {"FAILED": 3, "LOW": 3, "BLOCKED": 2, "DEGRADED": 2, "RESTRICTED": 1, "UNKNOWN": 1, "OFF": 0, "IDLE": 0, "OK": 0}
        worst = max((order.get(c["status"], 0) for c in components.values()), default=0)
        return {
            "schema_version": "runtime-health.v1",
            "as_of": iso_utc(now),
            "overall": {0: "OK", 1: "ATTENTION", 2: "DEGRADED", 3: "CRITICAL"}[worst],
            "experiment_status": experiment["status"],
            "components": components,
            "open_incidents": res.incidents(status="OPEN", limit=20),
        }

    def _market(self, config: dict[str, Any]) -> FuturesMarketDataProvider:
        if self._market_provider_override is not None:
            return self._market_provider_override
        if config["market_data_mode"] == "binance_usdm":
            return BinanceUsdMFuturesMarketDataProvider()
        if config["market_data_mode"] == "gate_usdt":
            if self._gate_provider is None:
                self._gate_provider = GateUsdtFuturesMarketDataProvider(live_state=self.live_stream)
            self._gate_provider.live_state = self.live_stream
            return self._gate_provider
        return FixtureFuturesMarketDataProvider()

    def feed_status(self, config: dict[str, Any], symbol: str) -> dict[str, Any]:
        """Freshness of the backend-owned live feed; stale required feeds block new entries."""

        required = config["market_data_mode"] == "gate_usdt" and self.live_stream is not None
        if not required:
            return {"required": False, "fresh": True, "state": "NOT_STREAMED", "age_seconds": None}
        state = self.live_stream.symbol_state(symbol)
        return {
            "required": True,
            "fresh": bool(state.get("fresh")),
            "state": state.get("stream_state"),
            "age_seconds": state.get("age_seconds"),
        }

    @staticmethod
    def _is_real_adapter(adapter: Any) -> bool:
        return isinstance(adapter, (JevAdapter, ResponsesAdapter))

    def _paid_call(
        self,
        *,
        experiment_id: str,
        cycle_id: str | None,
        symbol: str | None,
        arm: str | None,
        provider_config: dict[str, Any],
        adapter: Any,
        call_type: str,
        payload_bytes: int,
        budget: dict[str, Any],
        invoke: Callable[[], Any],
    ) -> tuple[Any, dict[str, Any] | None]:
        """Budget-guarded provider call with a persisted cost-ledger event.

        Returns (result, blocked_decision). ``blocked_decision`` is set when the budget guard
        refused the call before any request was made. Provider errors propagate after the
        failure is recorded and the reservation reconciled.
        """

        ledger = self.store.cost_ledger
        started = self._clock()
        real = self._is_real_adapter(adapter)
        base_event = {
            "experiment_id": experiment_id,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "arm": arm,
            "provider_id": provider_config["provider_id"],
            "provider_kind": provider_config["kind"],
            "model": provider_config.get("model", "unavailable"),
            "reasoning_effort": (
                provider_config.get("reasoning_effort")
                if not provider_config["kind"].endswith("jev")
                else None
            ),
            "call_type": call_type,
            "started_at": iso_utc(started),
            "real_external_call": real,
        }
        if not real:
            result = invoke()
            usage = normalize_usage((result or {}).get("usage") if isinstance(result, dict) else None)
            ledger.record_usage(
                {
                    **base_event,
                    "returned_model": (result or {}).get("model") if isinstance(result, dict) else None,
                    "completed_at": iso_utc(self._clock()),
                    "latency_ms": (result or {}).get("latency_ms") if isinstance(result, dict) else None,
                    "status": "ok",
                    **usage,
                    "estimated_cost_usd": 0.0,
                    "cost_status": "exact",
                    "real_external_call": False,
                }
            )
            return result, None
        circuit_ok, circuit_reason = self.resilience.circuit_allows(provider_config["provider_id"])
        if not circuit_ok:
            ledger.record_usage(
                {
                    **base_event,
                    "completed_at": iso_utc(self._clock()),
                    "status": "failed",
                    "estimated_cost_usd": 0.0,
                    "cost_status": "exact",
                    "error_code": "circuit_open",
                    "real_external_call": False,
                }
            )
            raise AIProviderError(circuit_reason or "PROVIDER_CIRCUIT_OPEN")
        decision = ledger.reserve(
            experiment_id=experiment_id,
            cycle_id=cycle_id,
            provider=provider_config,
            call_type=call_type,
            budget=budget,
            payload_bytes=payload_bytes,
        )
        if not decision.allowed:
            ledger.record_usage(
                {
                    **base_event,
                    "completed_at": iso_utc(self._clock()),
                    "status": "budget_blocked",
                    "estimated_cost_usd": 0.0,
                    "cost_status": "exact",
                    "price_book_version": decision.get("price_book_version"),
                    "error_code": decision.get("code"),
                    "real_external_call": False,
                }
            )
            return None, dict(decision)
        price = decision.get("price")
        try:
            result = invoke()
        except (AIProviderError, PaperTradingError) as exc:
            meta = getattr(adapter, "last_call", {}) or {}
            timed_out = meta.get("error_kind") == "timeout"
            ledger.settle(
                decision["reservation_id"],
                experiment_id=experiment_id,
                charged_usd=None,
                keep_reserved_when_unknown=bool(timed_out and meta.get("request_sent")),
                budget=budget,
            )
            code = (
                f"http_{meta['http_status']}"
                if meta.get("http_status")
                else meta.get("error_kind") or ("validation_failed" if meta.get("request_sent") else "not_sent")
            )
            if code != "validation_failed":
                # Transport/provider failures feed the circuit; a schema-invalid answer is not an outage.
                self.resilience.record_provider(provider_config["provider_id"], ok=False, error=code, experiment_id=experiment_id)
            usage = normalize_usage(meta.get("raw_usage"))
            cost, cost_status = estimate_cost(price, usage)
            ledger.record_usage(
                {
                    **base_event,
                    "completed_at": iso_utc(self._clock()),
                    "latency_ms": meta.get("latency_ms"),
                    "status": "failed",
                    **usage,
                    "price_id": decision.get("price_id"),
                    "price_book_version": decision.get("price_book_version"),
                    "estimated_cost_usd": cost,
                    "cost_status": cost_status,
                    "reservation_id": decision["reservation_id"],
                    "provider_request_id": meta.get("provider_request_id"),
                    "provider_response_id": meta.get("provider_response_id"),
                    "returned_model": meta.get("returned_model"),
                    "error_code": code,
                    "real_external_call": bool(meta.get("request_sent")),
                }
            )
            raise
        meta = getattr(adapter, "last_call", {}) or {}
        self.resilience.record_provider(provider_config["provider_id"], ok=True)
        usage = normalize_usage(meta.get("raw_usage"))
        cost, cost_status = estimate_cost(price, usage)
        settlement = ledger.settle(
            decision["reservation_id"],
            experiment_id=experiment_id,
            charged_usd=cost,
            keep_reserved_when_unknown=True,
            budget=budget,
        )
        returned_model = meta.get("returned_model")
        if returned_model is None and isinstance(result, dict):
            returned_model = result.get("model")
        ledger.record_usage(
            {
                **base_event,
                "returned_model": returned_model,
                "completed_at": iso_utc(self._clock()),
                "latency_ms": meta.get("latency_ms"),
                "status": "ok",
                **usage,
                "price_id": decision.get("price_id"),
                "price_book_version": decision.get("price_book_version"),
                "estimated_cost_usd": cost,
                "cost_status": cost_status,
                "reservation_id": decision["reservation_id"],
                "provider_request_id": meta.get("provider_request_id"),
                "provider_response_id": meta.get("provider_response_id"),
                "decision": (
                    ("NO_TRADE" if result.get("intent") is None else "INTENT")
                    if isinstance(result, dict) and "intent" in result
                    else None
                ),
            }
        )
        if isinstance(result, dict):
            result["budget_settlement"] = settlement
        return result, None

    def _jev(self, config: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        if self._jev_provider_override is not None:
            provider_config = self.store.provider(config["jev_provider_id"]) or {
                "provider_id": "test-jev",
                "kind": "fixture_jev",
                "model": "test-jev",
            }
            return self._jev_provider_override, provider_config
        provider_config = self.store.provider(config["jev_provider_id"])
        if provider_config is None or not provider_config.get("enabled"):
            raise AIProviderError("configured Jev provider is unavailable")
        if provider_config["kind"] == "fixture_jev":
            return FixtureJevProvider(), provider_config
        if provider_config["kind"] == "typesafe_jev":
            return JevAdapter(provider_config, resolver=self.resolver), provider_config
        raise AIProviderError("configured provider is not a Jev adapter")

    def _gpt(self, config: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        if self._gpt_provider_override is not None:
            provider_config = self.store.provider(config["gpt_provider_id"]) or {
                "provider_id": "test-gpt",
                "kind": "fixture_gpt",
                "model": "test-gpt",
            }
            return self._gpt_provider_override, provider_config
        provider_config = self.store.provider(config["gpt_provider_id"])
        if provider_config is None or not provider_config.get("enabled"):
            raise AIProviderError("configured GPT provider is unavailable")
        if provider_config["kind"] == "fixture_gpt":
            return FixtureGPTProvider(), provider_config
        if provider_config["kind"] in {
            "openai_responses",
            "foundry_responses",
            "compatible_responses",
        }:
            return (
                ResponsesAdapter(
                    provider_config,
                    resolver=self.resolver,
                    max_output_tokens=config["ai_budget"]["gpt_max_output_tokens"],
                ),
                provider_config,
            )
        raise AIProviderError("configured provider is not a Responses adapter")

    @staticmethod
    def _call_record(
        provider: dict[str, Any],
        path: str,
        result: dict[str, Any] | None,
        *,
        error: bool = False,
    ) -> dict[str, Any]:
        result = result or {}
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        pricing = provider.get("pricing")
        cost = None
        pricing_version = None
        if (
            isinstance(pricing, dict)
            and isinstance(input_tokens, int)
            and isinstance(output_tokens, int)
        ):
            cost = (
                input_tokens * pricing["input_per_million"]
                + output_tokens * pricing["output_per_million"]
            ) / 1_000_000
            pricing_version = pricing["version"]
        return {
            "provider_id": provider["provider_id"],
            "provider_kind": provider["kind"],
            "path": path,
            "model": result.get("model", provider.get("model", "unavailable")),
            "status": "failed" if error else "ok",
            "error_code": "provider_error" if error else None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": usage.get("reasoning_tokens"),
            "latency_ms": result.get("latency_ms"),
            "cost_estimate": cost,
            "pricing_version": pricing_version,
            "prompt_hash": result.get("prompt_hash"),
            "observed_at": result.get("observed_at", iso_utc(datetime.now(timezone.utc))),
        }

    def _portfolio_context(
        self,
        experiment_id: str,
        snapshot: MarketSnapshot,
        features: dict[str, Any],
    ) -> dict[str, Any]:
        positions = [
            item for item in self.store.open_positions(experiment_id) if item["cohort"] == "primary"
        ]
        wallet = self.store.wallet_summary(experiment_id, "primary")
        notional = sum(float(item["quantity"]) * float(item["mark_price"]) for item in positions)
        equity = max(0.01, float(wallet["equity"]))
        price = float(features["last_price"])
        atr = max(float(features["atr"]), price * 0.002)
        reassess = any(
            position["symbol"] == snapshot.symbol
            and (
                abs(price - float(position["entry_price"])) >= 1.5 * atr
                or (
                    float(position["stop_price"]) - price <= 0.5 * atr
                    if position["side"] == "long"
                    else price - float(position["stop_price"]) <= 0.5 * atr
                )
                or (
                    price - float(position["target_price"]) >= -0.5 * atr
                    if position["side"] == "long"
                    else float(position["target_price"]) - price >= -0.5 * atr
                )
            )
            for position in positions
        )
        return {
            "has_position": bool(positions),
            "open_position_count": len(positions),
            "exposure_pct": notional / equity,
            "reassess_open_position": reassess,
            "positions": positions[:10],
        }

    def _risk_statistics(
        self, experiment_id: str, cohort: str = "primary"
    ) -> tuple[float, float, int]:
        records = self.store.export_records(experiment_id)
        config = self.store.experiment()["config"]
        day_start = self._clock().astimezone(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = day_start + timedelta(days=1)
        cohort_equity = [
            row
            for row in records["equity"]
            if row["cohort"] == cohort
        ]
        before_day = [
            row
            for row in cohort_equity
            if parse_utc(row["as_of"], "equity.as_of") < day_start
        ]
        in_day = [
            row
            for row in cohort_equity
            if day_start
            <= parse_utc(row["as_of"], "equity.as_of")
            < day_end
        ]
        daily_start_equity = (
            float(before_day[-1]["equity"])
            if before_day
            else float(in_day[0]["equity"])
            if in_day
            else float(config["starting_balance_usdt"])
        )
        current_equity = float(self.store.wallet_summary(experiment_id, cohort)["equity"])
        daily_loss = (
            max(0.0, (daily_start_equity - current_equity) / daily_start_equity)
            if daily_start_equity > 0
            else 0.0
        )
        series = [
            row["equity"]
            for row in records["equity"]
            if row["cohort"] == cohort
        ]
        peak = -math.inf
        drawdown = 0.0
        for point in series:
            peak = max(peak, float(point))
            if peak > 0:
                drawdown = max(drawdown, (peak - float(point)) / peak)
        closed = sorted(
            [
                position
                for position in records["positions"]
                if position["cohort"] == cohort
                and position["status"] == "closed"
                and position["closed_pnl_recorded"]
            ],
            key=lambda item: item["closed_at"] or "",
            reverse=True,
        )
        streak = 0
        for position in closed:
            if float(position["realized_pnl"] or 0) >= 0:
                break
            streak += 1
        return daily_loss, drawdown, streak

    def _feature_history(
        self,
        snapshot: MarketSnapshot,
        provider_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        lanes = {
            "15m": snapshot.candles_15m,
            "1h": snapshot.candles_1h,
            "4h": snapshot.candles_4h,
        }
        return {
            interval: self.store.load_market_history(
                provider_id=provider_id,
                symbol=snapshot.symbol,
                interval=interval,
                through=lane[-1]["close_time"],
            )
            for interval, lane in lanes.items()
        }

    def _build_arm_result(
        self,
        arm: str,
        intent: dict[str, Any] | None,
        reason: str,
        snapshot_hash: str,
        *,
        risk_eligible: bool,
        escalation: dict[str, Any] | None = None,
        ai_path: str | None = None,
    ) -> dict[str, Any]:
        return {
            "arm": arm,
            "decision": (
                f"ENTER_{intent['side'].upper()}" if intent is not None else "NO_TRADE"
            ),
            "intent": intent,
            "reason": reason,
            "risk_eligible": bool(risk_eligible),
            "escalation": escalation,
            "ai_path": ai_path,
            "snapshot_hash": snapshot_hash,
        }

    def run_cycle(
        self,
        symbol: str,
        *,
        as_of: datetime | None = None,
        manual: bool = False,
    ) -> dict[str, Any]:
        with self._cycle_lock:
            return self._run_cycle(symbol, as_of=as_of, manual=manual)

    def _run_cycle(
        self,
        symbol: str,
        *,
        as_of: datetime | None = None,
        manual: bool = False,
    ) -> dict[str, Any]:
        experiment = self.store.experiment()
        config = experiment["config"]
        if experiment["status"] != "running":
            raise PaperTradingError("start the PAPER runtime before running a cycle")
        kill_level = self.portfolio.kill_switch()["level"]
        if kill_level == "FULL_AUTOMATION_HALT":
            raise PaperTradingError("KILL_SWITCH_FULL_AUTOMATION_HALT: decision cycles are halted")
        normalized_symbol = symbol.strip().upper()
        if normalized_symbol not in config["symbols"]:
            raise PaperTradingError("symbol is not in the configured experiment universe")
        current_time = (as_of or self._clock()).astimezone(timezone.utc)
        if current_time > self._clock().astimezone(timezone.utc) + timedelta(seconds=1):
            raise PaperTradingError("cycle as_of cannot be in the future")
        cycle_close = floor_time(current_time, "15m")
        if not manual and current_time < cycle_close + timedelta(
            seconds=config["schedule_delay_seconds"]
        ):
            raise PaperTradingError("the closed-candle schedule delay has not elapsed")
        cycle_slot = iso_utc(cycle_close)
        cycle_id = f"{experiment['experiment_id']}:{normalized_symbol}:{int(cycle_close.timestamp())}"
        existing = self.store.cycle(cycle_id)
        if existing is not None:
            self.store.record_runtime_event(
                experiment["experiment_id"],
                cycle_id,
                "duplicate_cycle_prevented",
                {"symbol": normalized_symbol, "cycle_slot": cycle_slot},
            )
            existing["duplicate"] = True
            return existing

        cycle_started = time.perf_counter()
        market_provider = self._market(config)
        try:
            snapshot = market_provider.fetch_snapshot(normalized_symbol, current_time).validate()
        except PaperTradingError:
            self.store.record_runtime_event(
                experiment["experiment_id"],
                cycle_id,
                "market_data_failure",
                {"symbol": normalized_symbol, "code": "market_data_unavailable"},
            )
            raise
        if (
            snapshot.candles_15m[-1]["close_time"] != cycle_slot
            or parse_utc(snapshot.data_cutoff, "snapshot.data_cutoff") > current_time
            or parse_utc(snapshot.as_of, "snapshot.as_of") > current_time
        ):
            raise PaperTradingError("market provider returned a different 15m cutoff")
        if not self.store.begin_cycle(
            cycle_id,
            experiment["experiment_id"],
            normalized_symbol,
            cycle_slot,
            snapshot.snapshot_hash,
            snapshot.as_of,
            snapshot.data_origin,
        ):
            existing = self.store.cycle(cycle_id)
            if existing is not None:
                existing["duplicate"] = True
                return existing
            raise PaperTradingError("cycle idempotency reservation failed")

        started = cycle_started
        try:
            history_inserted = self.store.archive_market_snapshot(
                snapshot,
                market_provider.provider_id,
                retention_days=config["market_data_retention_days"],
            )
            features = compute_features(
                snapshot,
                minimum_signal_strength=config["minimum_signal_strength"],
                signal_gate_enabled=config["signal_gate_enabled"],
                historical_bars=self._feature_history(
                    snapshot, market_provider.provider_id
                ),
            )
            portfolio = self._portfolio_context(
                experiment["experiment_id"], snapshot, features
            )
            gate = {
                "eligible": features["gate_eligible"],
                "direction": features["quant_direction"],
                "strength": features["signal_strength"],
                "trigger": features["signal_trigger"],
                "reason": (
                    "quant setup passed the configured signal gate"
                    if features["gate_eligible"]
                    else "no eligible deterministic 15m setup"
                ),
            }
            experiment_id = experiment["experiment_id"]
            budget = config["ai_budget"]
            limit_action = budget["limit_action"]
            feed = self.feed_status(config, normalized_symbol)
            feed_blocks_entries = bool(
                feed["required"] and not feed["fresh"] and config["stale_feed_blocks_entries"]
            )
            if feed_blocks_entries:
                self.store.record_runtime_event(
                    experiment_id,
                    cycle_id,
                    "market_feed_stale",
                    {"symbol": normalized_symbol, **feed, "action": "new entries and AI calls blocked"},
                )
            budget_blocks: list[dict[str, Any]] = []
            paid_ai_blocked = False
            gpt_blocked = False
            jev_arms_selected = any(
                arm in {"jev", "quant_jev", "hybrid"} for arm in config["evaluation_arms"]
            )
            jev_disabled = jev_arms_selected and not config["jev_enabled"]
            jev_needed = config["jev_enabled"] and jev_arms_selected and (
                features["gate_eligible"]
                or portfolio["reassess_open_position"]
                or config["force_escalation"]
            ) and not feed_blocks_entries
            jev_vector = None
            ai_calls: list[dict[str, Any]] = []
            arm_results: dict[str, dict[str, Any]] = {}
            escalation_events: list[dict[str, Any]] = []
            gpt_cache: dict[tuple[str, bool], tuple[dict[str, Any] | None, str]] = {}
            jev_error = False
            jev_budget_blocked = False
            jev_provider_config: dict[str, Any] | None = None

            def note_budget_block(decision: dict[str, Any], *, gpt: bool) -> None:
                nonlocal paid_ai_blocked, gpt_blocked
                budget_blocks.append(
                    {key: decision.get(key) for key in ("code", "scope", "reason", "call_type", "provider_id")}
                )
                if limit_action == "JEV_ONLY" and gpt:
                    gpt_blocked = True
                else:
                    paid_ai_blocked = True
                    gpt_blocked = True

            if jev_needed:
                try:
                    provider, jev_provider_config = self._jev(config)
                    payload_builder = getattr(provider, "evaluation_payload", None)
                    payload_bytes = (
                        len(_json(payload_builder(snapshot, features, portfolio)))
                        if callable(payload_builder)
                        else 0
                    )
                    jev_vector, blocked = self._paid_call(
                        experiment_id=experiment_id,
                        cycle_id=cycle_id,
                        symbol=normalized_symbol,
                        arm="shared:jev",
                        provider_config=jev_provider_config,
                        adapter=provider,
                        call_type="jev_decision",
                        payload_bytes=payload_bytes,
                        budget=budget,
                        invoke=lambda: provider.evaluate(snapshot, features, portfolio),
                    )
                    if blocked is not None:
                        jev_budget_blocked = True
                        note_budget_block(blocked, gpt=False)
                        raise _BudgetBlocked()
                    if (
                        jev_vector.get("snapshot_hash")
                        != jev_state(snapshot, features, portfolio)["snapshot_hash"]
                        or jev_vector.get("schema_version") != "jev-decision-vector.v1"
                    ):
                        raise AIProviderError("Jev response did not match the frozen snapshot")
                    ai_calls.append(self._call_record(jev_provider_config, "jev", jev_vector))
                except _BudgetBlocked:
                    jev_vector = None
                except (AIProviderError, PaperTradingError):
                    jev_error = True
                    fallback_config = self.store.provider(config["jev_provider_id"]) or {
                        "provider_id": config["jev_provider_id"],
                        "kind": "typesafe_jev",
                        "model": "unavailable",
                    }
                    ai_calls.append(
                        self._call_record(fallback_config, "jev", None, error=True)
                    )

            def call_gpt(arm: str, include_skill: bool, vector: dict[str, Any] | None):
                cache_key = (arm, include_skill)
                if cache_key in gpt_cache:
                    return gpt_cache[cache_key]
                if feed_blocks_entries:
                    value = (None, "STALE_MARKET_FEED: live feed is stale; no GPT call was made")
                    gpt_cache[cache_key] = value
                    return value
                if paid_ai_blocked or gpt_blocked:
                    value = (None, f"GPT_BUDGET_BLOCK: {limit_action}; no GPT call was made")
                    gpt_cache[cache_key] = value
                    return value
                try:
                    provider, provider_config = self._gpt(config)
                    payload_builder = getattr(provider, "intent_payload", None)
                    payload_bytes = (
                        len(
                            _json(
                                payload_builder(
                                    snapshot,
                                    features,
                                    portfolio,
                                    jev_vector=vector,
                                    include_skill=include_skill,
                                )[0]
                            )
                        )
                        if callable(payload_builder)
                        else 0
                    )
                    result, blocked = self._paid_call(
                        experiment_id=experiment_id,
                        cycle_id=cycle_id,
                        symbol=normalized_symbol,
                        arm=arm,
                        provider_config=provider_config,
                        adapter=provider,
                        call_type="gpt_escalation" if arm == "hybrid" else "gpt_arm",
                        payload_bytes=payload_bytes,
                        budget=budget,
                        invoke=lambda: provider.generate_intent(
                            snapshot,
                            features,
                            portfolio,
                            jev_vector=vector,
                            source_arm=arm,
                            include_skill=include_skill,
                        ),
                    )
                    if blocked is not None:
                        note_budget_block(blocked, gpt=True)
                        value = (None, f"GPT_BUDGET_BLOCK: {blocked.get('code')}; no GPT call was made")
                        gpt_cache[cache_key] = value
                        return value
                    intent = result.get("intent")
                    if intent is not None:
                        intent = TradingIntent.from_dict(intent).to_dict()
                    ai_calls.append(
                        self._call_record(provider_config, f"gpt:{arm}", result)
                    )
                    value = (intent, "Responses decision returned a validated intent" if intent else "provider chose no_trade")
                except (AIProviderError, PaperTradingError):
                    provider_config = self.store.provider(config["gpt_provider_id"]) or {
                        "provider_id": config["gpt_provider_id"],
                        "kind": "openai_responses",
                        "model": "unavailable",
                    }
                    ai_calls.append(
                        self._call_record(
                            provider_config, f"gpt:{arm}", None, error=True
                        )
                    )
                    value = (None, "provider failure; no new exposure was approved")
                gpt_cache[cache_key] = value
                return value

            quant_direction = features["quant_direction"]
            quant_intent = (
                build_fast_intent(
                    snapshot,
                    features,
                    side=quant_direction,
                    source_arm="quant",
                    reason="Closed-bar breakout with aligned deterministic 15m/1h/4h trend.",
                )
                if features["gate_eligible"]
                else None
            )
            for arm in config["evaluation_arms"]:
                if arm == "quant":
                    arm_results[arm] = self._build_arm_result(
                        arm,
                        quant_intent,
                        "deterministic signal gate passed" if quant_intent else gate["reason"],
                        snapshot.snapshot_hash,
                        risk_eligible=features["gate_eligible"],
                    )
                    continue

                if arm == "jev":
                    side = _jev_direction(jev_vector)
                    approved, reason = _jev_fast_approved(jev_vector)
                    if not jev_vector and not jev_error:
                        reason = (
                            "Jev is disabled for this experiment"
                            if config["jev_enabled"] is False
                            else "AI_BUDGET_BLOCK: Jev call refused by the budget guard"
                            if jev_budget_blocked
                            else "STALE_MARKET_FEED: no Jev call"
                            if feed_blocks_entries
                            else "signal gate skipped the Jev call"
                        )
                    intent = (
                        build_fast_intent(
                            snapshot,
                            features,
                            side=side,
                            source_arm=arm,
                            reason=reason,
                        )
                        if approved
                        else None
                    )
                    arm_results[arm] = self._build_arm_result(
                        arm,
                        intent,
                        reason if not jev_error else f"{config['fallback_policy']} on Jev failure",
                        snapshot.snapshot_hash,
                        risk_eligible=approved,
                        ai_path="jev",
                    )
                    continue

                if arm in {"luna", "luna_skill"}:
                    if (
                        config["signal_gate_enabled"]
                        and not features["gate_eligible"]
                        and not config["force_escalation"]
                    ):
                        intent, reason = None, "signal gate skipped this GPT arm"
                    else:
                        intent, reason = call_gpt(arm, arm == "luna_skill", None)
                    arm_results[arm] = self._build_arm_result(
                        arm,
                        intent,
                        reason,
                        snapshot.snapshot_hash,
                        risk_eligible=intent is not None,
                        ai_path=f"gpt:{arm}",
                    )
                    continue

                if arm == "quant_jev":
                    approved, reason = _jev_fast_approved(jev_vector)
                    if not jev_vector and not jev_error:
                        approved = False
                        reason = (
                            "Jev is disabled for this experiment"
                            if config["jev_enabled"] is False
                            else "AI_BUDGET_BLOCK: Jev call refused by the budget guard"
                            if jev_budget_blocked
                            else "STALE_MARKET_FEED: no Jev call"
                            if feed_blocks_entries
                            else "signal gate skipped the Jev call"
                        )
                    if jev_error:
                        approved = False
                        reason = f"{config['fallback_policy']} on Jev failure"
                    aligned = quant_direction == _jev_direction(jev_vector)
                    if not aligned:
                        approved = False
                        reason = "quant and Jev directions do not align"
                    approved = approved and features["gate_eligible"]
                    intent = (
                        build_fast_intent(
                            snapshot,
                            features,
                            side=quant_direction,
                            source_arm=arm,
                            reason=reason,
                        )
                        if approved
                        else None
                    )
                    arm_results[arm] = self._build_arm_result(
                        arm,
                        intent,
                        reason,
                        snapshot.snapshot_hash,
                        risk_eligible=approved,
                        ai_path="jev",
                    )
                    continue

                if arm == "hybrid":
                    if jev_budget_blocked or feed_blocks_entries:
                        fallback = (
                            limit_action == "FALLBACK_QUANT"
                            and jev_budget_blocked
                            and not feed_blocks_entries
                            and features["gate_eligible"]
                            and quant_direction in {"long", "short"}
                        )
                        code = "STALE_MARKET_FEED" if feed_blocks_entries else "AI_BUDGET_BLOCK"
                        intent = (
                            build_fast_intent(
                                snapshot,
                                features,
                                side=quant_direction,
                                source_arm=arm,
                                reason="AI_BUDGET_FALLBACK: deterministic Quant decision; no paid AI call",
                            )
                            if fallback
                            else None
                        )
                        arm_results[arm] = self._build_arm_result(
                            arm,
                            intent,
                            "AI_BUDGET_FALLBACK: deterministic Quant arm"
                            if fallback
                            else f"{code}: {limit_action if code == 'AI_BUDGET_BLOCK' else 'feed stale'}; no AI decision",
                            snapshot.snapshot_hash,
                            risk_eligible=intent is not None,
                            escalation={
                                "policy_version": config["escalation_policy"]["version"],
                                "escalate": False,
                                "reasons": ["ai_budget_fallback_quant" if fallback else code.lower()],
                            },
                            ai_path="ai_budget_fallback" if fallback else code.lower(),
                        )
                        continue
                    if jev_error or jev_disabled:
                        fallback_allowed = (
                            config["gpt_escalation_enabled"]
                            and (
                                config["fallback_policy"] == "GPT_FALLBACK"
                                or config["force_escalation"]
                            )
                            and features["gate_eligible"]
                        )
                        if fallback_allowed:
                            intent, reason = call_gpt(arm, True, None)
                            arm_results[arm] = self._build_arm_result(
                                arm,
                                intent,
                                "Jev fallback to configured GPT provider: " + reason,
                                snapshot.snapshot_hash,
                                risk_eligible=intent is not None and features["gate_eligible"],
                                escalation={
                                    "policy_version": config["escalation_policy"]["version"],
                                    "escalate": True,
                                    "reasons": [
                                        "jev_disabled_gpt_fallback"
                                        if jev_disabled
                                        else "jev_unavailable_gpt_fallback"
                                    ],
                                },
                                ai_path=f"gpt:{arm}",
                            )
                        else:
                            arm_results[arm] = self._build_arm_result(
                                arm,
                                None,
                                f"Jev unavailable; configured fallback is {config['fallback_policy']}",
                                snapshot.snapshot_hash,
                                risk_eligible=False,
                                escalation={
                                    "policy_version": config["escalation_policy"]["version"],
                                    "escalate": False,
                                    "reasons": [
                                        "jev_disabled_fail_closed"
                                        if jev_disabled
                                        else "jev_unavailable_fail_closed"
                                    ],
                                },
                            )
                        escalation_events.append(
                            {
                                "symbol": normalized_symbol,
                                "cycle_id": cycle_id,
                                "arm": arm,
                                "reason": (
                                    "jev_disabled" if jev_disabled else "jev_unavailable"
                                ),
                                "escalated": fallback_allowed,
                                "fallback_policy": config["fallback_policy"],
                                "outcome": (
                                    "gpt_fallback"
                                    if fallback_allowed
                                    else "deferred"
                                    if config["fallback_policy"] == "DEFER"
                                    else "skipped"
                                ),
                            }
                        )
                        continue
                    if jev_vector is None:
                        arm_results[arm] = self._build_arm_result(
                            arm,
                            None,
                            "signal gate skipped the Jev call",
                            snapshot.snapshot_hash,
                            risk_eligible=False,
                            escalation={
                                "policy_version": config["escalation_policy"]["version"],
                                "escalate": False,
                                "reasons": ["signal_gate"],
                            },
                        )
                        continue
                    route = route_escalation(
                        jev_vector,
                        features,
                        portfolio,
                        config["escalation_policy"],
                        force=config["force_escalation"],
                    )
                    if (
                        not features["gate_eligible"]
                        and not config["force_escalation"]
                        and not portfolio["reassess_open_position"]
                    ):
                        intent = None
                        reason = gate["reason"]
                    elif route["escalate"]:
                        if config["gpt_escalation_enabled"] or config["force_escalation"]:
                            intent, reason = call_gpt(arm, True, jev_vector)
                            if (
                                intent is None
                                and reason.startswith("GPT_BUDGET_BLOCK")
                                and limit_action == "FALLBACK_QUANT"
                                and features["gate_eligible"]
                                and quant_direction in {"long", "short"}
                            ):
                                intent = build_fast_intent(
                                    snapshot,
                                    features,
                                    side=quant_direction,
                                    source_arm=arm,
                                    reason="AI_BUDGET_FALLBACK: deterministic Quant decision after GPT budget block",
                                )
                                reason = "AI_BUDGET_FALLBACK: " + reason
                            elif intent is None:
                                reason = "escalation failed closed: " + reason
                        else:
                            intent = None
                            reason = "deep reasoning was required but GPT escalation is disabled"
                    else:
                        fast_approved, reason = _jev_fast_approved(jev_vector)
                        if (
                            quant_direction != _jev_direction(jev_vector)
                            and quant_direction in {"long", "short"}
                        ):
                            fast_approved = False
                            reason = "quant and Jev directions conflict"
                        intent = (
                            build_fast_intent(
                                snapshot,
                                features,
                                side=quant_direction,
                                source_arm=arm,
                                reason=reason,
                            )
                            if fast_approved
                            else None
                        )
                    if route["escalate"]:
                        escalation_events.append(
                            {
                                "symbol": normalized_symbol,
                                "cycle_id": cycle_id,
                                "arm": arm,
                                "policy_version": route["policy_version"],
                                "reason": route["reasons"],
                                "escalated": True,
                            }
                        )
                    arm_results[arm] = self._build_arm_result(
                        arm,
                        intent,
                        reason,
                        snapshot.snapshot_hash,
                        risk_eligible=features["gate_eligible"] and intent is not None,
                        escalation=route,
                        ai_path=(
                            f"gpt:{arm}"
                            if route["escalate"]
                            and (config["gpt_escalation_enabled"] or config["force_escalation"])
                            else "blocked_escalation"
                            if route["escalate"]
                            else "jev_fast_path"
                        ),
                    )

            decision_input_hash = jev_state(
                snapshot, features, portfolio
            )["snapshot_hash"]
            jev_regime = (
                (jev_vector.get("answers", {}).get("market_regime") or {}).get("value")
                if jev_vector
                else None
            )
            if jev_regime not in {"bull", "bear", "sideways", "unstable"}:
                market_regime = features["quant_regime"]
                regime_source = "quant"
            else:
                market_regime = jev_regime
                regime_source = "jev"
            jev_status = (
                "completed"
                if jev_vector is not None
                else "failed"
                if jev_error
                else "disabled"
                if jev_disabled
                else "skipped"
                if jev_arms_selected
                else "not_requested"
            )
            if "hybrid_brain" in config["evaluation_arms"] and "hybrid" in arm_results:
                # Same frozen input and the same (shared) AI calls as Hybrid; the Portfolio Brain
                # policy is applied against this arm's own wallet at execution time.
                hybrid_result = arm_results["hybrid"]
                brain_intent = hybrid_result.get("intent")
                if brain_intent is not None:
                    brain_intent = TradingIntent.from_dict({**brain_intent, "source_arm": "hybrid_brain"}).to_dict()
                arm_results["hybrid_brain"] = {
                    **hybrid_result,
                    "arm": "hybrid_brain",
                    "intent": brain_intent,
                    "portfolio_brain": None,
                }
            for arm_result in arm_results.values():
                arm_result["decision_input_hash"] = decision_input_hash
            primary = arm_results.get(config["primary_arm"])
            if primary is None:
                primary = self._build_arm_result(
                    config["primary_arm"],
                    None,
                    "primary arm was not evaluated",
                    snapshot.snapshot_hash,
                    risk_eligible=False,
                )
            wallet = self.store.wallet_summary(experiment["experiment_id"], "primary")
            open_positions = self.store.open_positions(experiment["experiment_id"])
            daily_loss, drawdown, consecutive = self._risk_statistics(
                experiment["experiment_id"]
            )
            selected_intent = (
                TradingIntent.from_dict(primary["intent"])
                if primary.get("intent") is not None
                else None
            )
            context = snapshot.market_context or {}
            risk_config = dict(config)
            if isinstance(context.get("maintenance_rate"), (int, float)):
                # Conservative: never below the venue's current maintenance rate.
                risk_config["maintenance_margin_rate"] = max(
                    float(config["maintenance_margin_rate"]), float(context["maintenance_rate"])
                )
            book_usable = (
                isinstance(context.get("best_bid"), (int, float))
                and isinstance(context.get("best_ask"), (int, float))
                and not feed_blocks_entries
            )

            def entry_reference(side: str) -> tuple[float, str]:
                if book_usable:
                    return (
                        float(context["best_ask"]) if side == "long" else float(context["best_bid"]),
                        f"{context.get('source', 'exchange')}_best_{'ask' if side == 'long' else 'bid'}",
                    )
                return float(features["market_mark_price"]), "last_closed_1m_close"

            pause_entries_reason = None
            portfolio_settings = self.portfolio.settings()
            automation = portfolio_settings["automation"]
            safety_settings = portfolio_settings["safety"]
            instrument_id = self.portfolio.perp_instrument_id(normalized_symbol)
            book_quote = None
            if isinstance(context.get("best_bid"), (int, float)) and isinstance(context.get("best_ask"), (int, float)):
                book_quote = {
                    "best_bid": context["best_bid"], "best_ask": context["best_ask"], "last_price": context.get("last_price"),
                    "mark_price": context.get("mark_price"), "index_price": context.get("index_price"),
                    "observed_at": context.get("book_observed_at") or snapshot.as_of,
                }
            market_safety = classify_market(
                instrument_id=instrument_id, market_type="perpetual", quote=book_quote, bars_1m=snapshot.candles_1m,
                settings=safety_settings, now=current_time,
                prior=self.portfolio.safety.prior_state(experiment_id, instrument_id),
                require_quote=config["market_data_mode"] == "gate_usdt",
            )
            self.portfolio.safety.record_state(experiment_id, market_safety)
            if KILL_RANK[kill_level] >= KILL_RANK["NO_NEW_ENTRIES"]:
                pause_entries_reason = (f"KILL_SWITCH_{kill_level}", "the kill switch blocks new entries; monitoring continues")
            elif feed_blocks_entries:
                pause_entries_reason = ("STALE_MARKET_FEED", "required live market feed is stale")
            elif market_safety["restrictions"]["new_entries"] == "BLOCKED":
                code = market_safety["state"] if market_safety["state"] in {"CRASH_MODE", "MARKET_DATA_UNTRUSTED"} else "LIQUIDITY_VACUUM"
                pause_entries_reason = (code, f"market safety {market_safety['state']}: {', '.join(market_safety['reasons'])}")
            elif market_safety["restrictions"]["new_entries"] == "REDUCED_SIZE":
                risk_config = {**risk_config, "risk_per_trade": float(risk_config["risk_per_trade"]) * safety_settings["volatility_entry_multiplier"]}
            if pause_entries_reason is not None:
                pass
            elif automation["emergency_stop"]:
                pause_entries_reason = ("EMERGENCY_STOP", "emergency stop is active; new PAPER entries are blocked")
            elif automation["new_entries_paused"]:
                pause_entries_reason = (
                    "AUTOMATION_NEW_ENTRIES_PAUSED",
                    "new AI entries are paused by the user; open positions are still monitored",
                )
            elif feed_blocks_entries:
                pause_entries_reason = ("STALE_MARKET_FEED", "required live market feed is stale")
            elif budget_blocks and limit_action == "PAUSE_NEW_ENTRIES":
                pause_entries_reason = (
                    "AI_BUDGET_PAUSE_NEW_ENTRIES",
                    "AI budget limit reached; new PAPER entries paused, open positions still monitored",
                )
            if budget_blocks:
                self.store.cost_ledger.record_event(
                    experiment_id,
                    cycle_id=cycle_id,
                    provider_id=None,
                    call_type=None,
                    event_type="policy_applied",
                    code=limit_action,
                    scope="cycle",
                    limit_action=limit_action,
                    details={"blocks": budget_blocks, "symbol": normalized_symbol},
                )
            if pause_entries_reason is not None:
                selected_intent = None

            def configured_execution_intent(intent: TradingIntent) -> TradingIntent:
                if intent.action != "open" or intent.order_type == config["entry_order_type"]:
                    return intent
                return TradingIntent.from_dict(
                    {
                        **intent.to_dict(),
                        "order_type": config["entry_order_type"],
                    }
                )

            if selected_intent is not None:
                selected_intent = configured_execution_intent(selected_intent)

            def brain_gate(
                intent: TradingIntent,
                cohort: str,
                equity: float,
                cohort_positions: list[dict[str, Any]],
            ) -> dict[str, Any]:
                """Portfolio Brain policy for one entry; it can only shrink or block (never size up)."""

                entry = float(intent.entry_price)
                stop_distance = abs(entry - float(intent.stop_price))
                risk_usdt = max(0.0, equity) * float(risk_config["risk_per_trade"])
                unit = stop_distance + entry * (2 * config["taker_fee_rate"] + 2 * config["slippage_bps"] / 10_000)
                notional = risk_usdt / unit * entry if unit > 0 else 0.0
                if cohort == "primary":
                    state = self.portfolio.exposure_state()
                else:
                    state = {
                        "equity_usdt": equity,
                        "drawdown": self._risk_statistics(experiment["experiment_id"], cohort)[1],
                        "max_drawdown_stop": config["max_drawdown_stop"],
                        "exposures": [
                            {
                                "base": item["symbol"][:-4],
                                "side": item["side"],
                                "risk_usdt": max(
                                    0.0,
                                    (1 if item["side"] == "long" else -1)
                                    * (float(item["mark_price"]) - float(item["stop_price"]))
                                    * float(item["quantity"]),
                                ),
                                "notional_usdt": float(item["quantity"]) * float(item["mark_price"]),
                            }
                            for item in cohort_positions
                        ],
                    }
                return portfolio_brain_entry(
                    {
                        "symbol": intent.symbol,
                        "base": intent.symbol[:-4],
                        "side": intent.side,
                        "market_type": "perpetual",
                        "risk_usdt": risk_usdt,
                        "notional_usdt": notional,
                        "funding_rate": features.get("funding_rate"),
                    },
                    state,
                    portfolio_settings["brain"],
                )

            primary_brain = None
            primary_block_reason: tuple[str, str] | None = None
            primary_risk_config = risk_config
            if selected_intent is not None and (
                portfolio_settings["brain"]["enabled"] or config["primary_arm"] == "hybrid_brain"
            ):
                primary_brain = brain_gate(
                    selected_intent, "primary", float(wallet["equity"]), []
                )
                if not primary_brain["allowed"]:
                    primary_block_reason = (
                        "PORTFOLIO_BRAIN_BLOCK",
                        f"Portfolio Brain {primary_brain['action']}: {', '.join(primary_brain['reason_codes'])}",
                    )
                    selected_intent = None
                elif primary_brain["size_multiplier"] < 1.0:
                    primary_risk_config = {
                        **risk_config,
                        "risk_per_trade": float(risk_config["risk_per_trade"]) * primary_brain["size_multiplier"],
                    }
            executions: list[dict[str, Any]] = []
            risk_summary: dict[str, Any]
            if selected_intent is None:
                block = pause_entries_reason or primary_block_reason
                risk_summary = {
                    "allowed": False,
                    "code": block[0] if block else "NO_INTENT",
                    "reason": block[1] if block else primary["reason"],
                    "quantity": 0.0,
                    "leverage": 0,
                    "notional": 0.0,
                    "margin": 0.0,
                    "risk_amount": 0.0,
                    "liquidation_price": None,
                    "reduce_only": False,
                }
            else:
                primary_risk = self.risk_engine.evaluate(
                    selected_intent,
                    primary_risk_config,
                    equity=float(wallet["equity"]),
                    margin_used=float(wallet["margin_used"]),
                    open_positions=open_positions,
                    data_cutoff=snapshot.data_cutoff,
                    decision_as_of=current_time,
                    signal_approved=bool(primary["risk_eligible"]),
                    position_lookup=self.store._position_lookup(experiment["experiment_id"]),
                    current_price=float(features["market_mark_price"]),
                    current_atr=float(features["atr"]),
                    last_bar_close_time=features["last_bar_close_time"],
                    daily_loss=daily_loss,
                    drawdown=drawdown,
                    consecutive_losses=consecutive,
                )
                risk_summary = primary_risk.to_dict()
                reference_price, price_source = entry_reference(selected_intent.side)
                executions.append(
                    {
                        "cohort": "primary",
                        "risk": primary_risk.to_dict(),
                        "intent": selected_intent.to_dict(),
                        "reference_price": reference_price,
                        "reference_price_source": price_source,
                        "slippage_bps": config["slippage_bps"],
                        "fee_rate": config["taker_fee_rate"],
                        "as_of": snapshot.as_of,
                        "data_origin": snapshot.data_origin,
                        "market_regime": market_regime,
                        "regime_source": regime_source,
                    }
                )

            def add_arm_execution(arm_name: str, result: dict[str, Any]) -> None:
                raw_intent = result.get("intent")
                if raw_intent is None or pause_entries_reason is not None:
                    return
                intent = configured_execution_intent(TradingIntent.from_dict(raw_intent))
                cohort = f"arm-{arm_name}"
                cohort_wallet = self.store.wallet_summary(
                    experiment["experiment_id"], cohort
                )
                cohort_positions = [
                    position
                    for position in self.store.open_positions(experiment["experiment_id"])
                    if position["cohort"] == cohort
                ]
                arm_daily_loss, arm_drawdown, arm_streak = self._risk_statistics(
                    experiment["experiment_id"], cohort
                )
                arm_risk_config = risk_config
                if arm_name == "hybrid_brain":
                    arm_brain = brain_gate(intent, cohort, float(cohort_wallet["equity"]), cohort_positions)
                    result["portfolio_brain"] = arm_brain
                    if not arm_brain["allowed"]:
                        result["decision"] = "NO_TRADE"
                        result["reason"] = f"Portfolio Brain {arm_brain['action']}: {', '.join(arm_brain['reason_codes'])}"
                        return
                    if arm_brain["size_multiplier"] < 1.0:
                        arm_risk_config = {
                            **risk_config,
                            "risk_per_trade": float(risk_config["risk_per_trade"]) * arm_brain["size_multiplier"],
                        }
                decision = self.risk_engine.evaluate(
                    intent,
                    arm_risk_config,
                    equity=float(cohort_wallet["equity"]),
                    margin_used=float(cohort_wallet["margin_used"]),
                    open_positions=[
                        {**position, "cohort": "primary"} for position in cohort_positions
                    ],
                    data_cutoff=snapshot.data_cutoff,
                    decision_as_of=current_time,
                    signal_approved=bool(result["risk_eligible"]),
                    current_price=float(features["market_mark_price"]),
                    current_atr=float(features["atr"]),
                    last_bar_close_time=features["last_bar_close_time"],
                    daily_loss=arm_daily_loss,
                    drawdown=arm_drawdown,
                    consecutive_losses=arm_streak,
                )
                reference_price, price_source = entry_reference(intent.side)
                executions.append(
                    {
                        "cohort": cohort,
                        "risk": decision.to_dict(),
                        "intent": intent.to_dict(),
                        "reference_price": reference_price,
                        "reference_price_source": price_source,
                        "slippage_bps": config["slippage_bps"],
                        "fee_rate": config["taker_fee_rate"],
                        "as_of": snapshot.as_of,
                        "data_origin": snapshot.data_origin,
                        "market_regime": market_regime,
                        "regime_source": regime_source,
                    }
                )

            for arm_name, result in arm_results.items():
                add_arm_execution(arm_name, result)

            if selected_intent is not None:
                for leverage in config["shadow_leverage"]:
                    cohort = f"x{leverage}"
                    shadow_wallet = self.store.wallet_summary(
                        experiment["experiment_id"], cohort
                    )
                    existing_shadow = [
                        position
                        for position in self.store.open_positions(experiment["experiment_id"])
                        if position["cohort"] == cohort
                    ]
                    shadow_config = dict(risk_config)
                    shadow_config["primary_leverage"] = leverage
                    shadow_daily_loss, shadow_drawdown, shadow_streak = self._risk_statistics(
                        experiment["experiment_id"], cohort
                    )
                    shadow_risk = self.risk_engine.evaluate(
                        selected_intent,
                        shadow_config,
                        equity=float(shadow_wallet["equity"]),
                        margin_used=float(shadow_wallet["margin_used"]),
                        open_positions=[
                            {**position, "cohort": "primary"} for position in existing_shadow
                        ],
                        data_cutoff=snapshot.data_cutoff,
                        decision_as_of=current_time,
                        signal_approved=bool(primary["risk_eligible"]),
                        current_price=float(features["market_mark_price"]),
                        current_atr=float(features["atr"]),
                        last_bar_close_time=features["last_bar_close_time"],
                        daily_loss=shadow_daily_loss,
                        drawdown=shadow_drawdown,
                        consecutive_losses=shadow_streak,
                        leverage=leverage,
                    )
                    reference_price, price_source = entry_reference(selected_intent.side)
                    executions.append(
                        {
                            "cohort": cohort,
                            "risk": shadow_risk.to_dict(),
                            "intent": selected_intent.to_dict(),
                            "reference_price": reference_price,
                            "reference_price_source": price_source,
                            "slippage_bps": config["slippage_bps"],
                            "fee_rate": config["taker_fee_rate"],
                            "as_of": snapshot.as_of,
                            "data_origin": snapshot.data_origin,
                            "market_regime": market_regime,
                            "regime_source": regime_source,
                        }
                    )

            end_to_end_ms = (time.perf_counter() - started) * 1000
            payload = {
                "cycle_id": cycle_id,
                "experiment_id": experiment["experiment_id"],
                "symbol": normalized_symbol,
                "cycle_slot": cycle_slot,
                "as_of": snapshot.as_of,
                "data_cutoff": snapshot.data_cutoff,
                "data_origin": snapshot.data_origin,
                "snapshot_hash": snapshot.snapshot_hash,
                "decision_input_hash": decision_input_hash,
                "market_regime": market_regime,
                "regime_source": regime_source,
                "market_snapshot": snapshot.to_dict(),
                "market_history_inserted": history_inserted,
                "features": features,
                "quant_gate": gate,
                "jev_vector": jev_vector,
                "jev_status": jev_status,
                "escalation_events": escalation_events,
                "arms": arm_results,
                "primary_arm": config["primary_arm"],
                "primary_decision": primary,
                "risk": risk_summary,
                "portfolio_brain": primary_brain,
                "automation": dict(automation),
                "market_safety": {k: market_safety[k] for k in ("state", "price_confidence", "reasons", "restrictions")},
                "kill_switch": kill_level,
                "cycle_latency_ms": end_to_end_ms,
                "manual_cycle": bool(manual),
                "live_execution_enabled": False,
                "market_provider_id": market_provider.provider_id,
                "market_feed": feed,
                "execution_price_basis": (
                    "exchange_best_bid_ask_plus_slippage_model" if book_usable else "last_closed_1m_close_plus_slippage_model"
                ),
                "fee_schedule_version": config["fee_schedule_version"],
                "ai_budget": {
                    "limit_action": limit_action,
                    "policy_version": budget["policy_version"],
                    "blocks": budget_blocks,
                    "entries_paused": pause_entries_reason[0] if pause_entries_reason else None,
                },
            }
            result = self.store.complete_cycle(
                cycle_id,
                payload,
                ai_calls=ai_calls,
                executions=executions,
                risk_events=[],
                data_origin=snapshot.data_origin,
            )
            if selected_intent is not None and pause_entries_reason is None:
                spot = self.portfolio.ai_spot_entry(
                    cycle_id=cycle_id,
                    intent=selected_intent.to_dict(),
                    brain=primary_brain,
                )
                if spot is not None:
                    result = {**result, "spot_execution": spot}
            return result
        except Exception as exc:
            self.store.fail_cycle(cycle_id, "cycle_processing_error")
            if isinstance(exc, PaperTradingError):
                raise
            raise

    def run_symbol_cycles(
        self,
        *,
        as_of: datetime | None = None,
        manual: bool = False,
    ) -> list[dict[str, Any]]:
        config = self.store.experiment()["config"]
        return [
            self.run_cycle(symbol, as_of=as_of, manual=manual)
            for symbol in config["symbols"]
        ]

    def monitor_once(self, *, as_of: datetime | None = None) -> list[dict[str, Any]]:
        """Monitor closed execution bars without invoking Jev or a GPT provider."""
        current_time = (as_of or self._clock()).astimezone(timezone.utc)
        experiment = self.store.experiment()
        config = experiment["config"]
        positions = [
            position
            for position in self.store.open_positions(experiment["experiment_id"])
        ]
        pending_orders = self.store.pending_orders(experiment["experiment_id"])
        if not positions and not pending_orders:
            return []
        provider = self._market(config)
        symbols = sorted(
            {position["symbol"] for position in positions}
            | {order["symbol"] for order in pending_orders}
        )
        events: list[dict[str, Any]] = []
        for symbol in symbols:
            symbol_positions = [p for p in positions if p["symbol"] == symbol]
            last_bars = [
                self.store.last_processed_bar(experiment["experiment_id"], p["position_id"])
                for p in symbol_positions
            ]
            last_bars.extend(
                order.get("last_checked_bar") or order["created_at"]
                for order in pending_orders
                if order["symbol"] == symbol
            )
            last_bars = [value for value in last_bars if value]
            if not last_bars:
                continue
            start = min(parse_utc(value, "monitor.last_processed") for value in last_bars if value)
            try:
                bars = provider.fetch_monitor_bars(symbol, start, current_time)
                try:
                    history = provider.fetch_monitor_bars(symbol, start - timedelta(minutes=21), start)
                except PaperTradingError:
                    history = []
            except PaperTradingError:
                self.store.record_runtime_event(
                    experiment["experiment_id"],
                    None,
                    "monitor_error",
                    {"symbol": symbol, "code": "market_data_unavailable"},
                )
                continue
            funding_rates: dict[int, float | None] = {}
            safety_settings = self.portfolio.safety_settings()
            for bar in bars:
                suspect = suspect_print(bar, history, safety_settings)
                history.append(bar)
                close_time = parse_utc(bar["close_time"], "monitor.bar.close_time")
                settlement_slot = int(close_time.timestamp()) // (8 * 60 * 60)
                current_positions = [
                    position
                    for position in self.store.open_positions(experiment["experiment_id"])
                    if position["symbol"] == symbol
                ]
                needs_funding = any(
                    settlement_slot > int(position["last_funding_slot"])
                    for position in current_positions
                )
                funding_rate = None
                if needs_funding:
                    if settlement_slot not in funding_rates:
                        try:
                            funding_rates[settlement_slot] = provider.fetch_funding_rate(
                                symbol, close_time
                            )
                        except PaperTradingError:
                            funding_rates[settlement_slot] = None
                    funding_rate = funding_rates[settlement_slot]
                result = self.store.apply_monitor_bar(
                    experiment["experiment_id"],
                    symbol,
                    bar,
                    funding_rate=funding_rate,
                    taker_fee_rate=config["taker_fee_rate"],
                    slippage_bps=config["slippage_bps"],
                    maker_fee_rate=config["maker_fee_rate"],
                    data_origin=MARKET_DATA_ORIGINS[config["market_data_mode"]],
                    suspect=suspect,
                )
                events.extend(result)
        return events

    def close_or_reduce(
        self,
        position_id: str,
        *,
        fraction: float = 1.0,
        as_of: datetime | None = None,
        reference_price: float | None = None,
        data_origin: str | None = None,
        order_key: str | None = None,
    ) -> dict[str, Any]:
        if not math.isfinite(float(fraction)) or not 0 < float(fraction) <= 1:
            raise PaperTradingError("reduce fraction must be greater than 0 and at most 1")
        current_time = (as_of or self._clock()).astimezone(timezone.utc)
        experiment = self.store.experiment()
        config = experiment["config"]
        position = self.store._position_lookup(experiment["experiment_id"]).get(position_id)
        if position is None or position["cohort"] != "primary":
            raise PaperTradingError("primary paper position is not open")
        intent = TradingIntent.from_dict(
            {
                "schema_version": INTENT_SCHEMA_VERSION,
                "action": "close" if fraction == 1 else "reduce",
                "symbol": position["symbol"],
                "side": position["side"],
                "order_type": "market",
                "entry_price": None,
                "stop_price": None,
                "target_price": None,
                "reduce_only": True,
                "reduce_fraction": None if fraction == 1 else float(fraction),
                "position_id": position_id,
                "reason": "operator reduce-only action",
                "source_arm": position["source_arm"],
                "as_of": iso_utc(current_time),
            }
        )
        portfolio = self.store.wallet_summary(experiment["experiment_id"], "primary")
        risk = self.risk_engine.evaluate(
            intent,
            config,
            equity=float(portfolio["equity"]),
            margin_used=float(portfolio["margin_used"]),
            open_positions=self.store.open_positions(experiment["experiment_id"]),
            data_cutoff=iso_utc(current_time),
            decision_as_of=current_time,
            position_lookup=self.store._position_lookup(experiment["experiment_id"]),
        )
        if not risk.allowed:
            return {"accepted": False, "reason": risk.reason, "risk": risk.to_dict()}
        if reference_price is not None and math.isfinite(float(reference_price)) and reference_price > 0:
            # Live top-of-book exit reference (best bid for a long, best ask for a short).
            mark = float(reference_price)
            origin = data_origin if data_origin in DATA_ORIGINS else MARKET_DATA_ORIGINS[config["market_data_mode"]]
        else:
            market = self._market(config).fetch_snapshot(position["symbol"], current_time)
            mark = float(market.candles_1m[-1]["close"])
            origin = market.data_origin
        side_sign = -1 if position["side"] == "long" else 1
        fill_price = mark * (1 + side_sign * config["slippage_bps"] / 10_000)
        return self.store.execute_reduce(
            experiment["experiment_id"],
            intent,
            risk,
            mark_price=fill_price,
            fee_rate=config["taker_fee_rate"],
            as_of=iso_utc(current_time),
            data_origin=origin,
            slippage_cost=abs(fill_price - mark) * float(risk.quantity),
            order_key=order_key,
        )

    def test_provider(self, provider_id: str) -> dict[str, Any]:
        provider = self.store.provider(provider_id)
        if provider is None:
            raise PaperTradingError("provider configuration was not found")
        if not provider.get("enabled"):
            raise PaperTradingError("provider is disabled")
        experiment = self.store.experiment()
        config = experiment["config"]
        budget = config["ai_budget"]
        adapter: Any
        if provider["kind"] in {"fixture_jev", "typesafe_jev"} and self._jev_provider_override is not None:
            adapter = self._jev_provider_override
        elif provider["kind"] not in {"fixture_jev", "typesafe_jev"} and self._gpt_provider_override is not None:
            adapter = self._gpt_provider_override
        elif provider["kind"] == "fixture_jev":
            adapter = FixtureJevProvider()
        elif provider["kind"] == "fixture_gpt":
            adapter = FixtureGPTProvider()
        elif provider["kind"] == "typesafe_jev":
            adapter = JevAdapter(provider, resolver=self.resolver)
        elif provider["kind"] in {"openai_responses", "foundry_responses", "compatible_responses"}:
            adapter = ResponsesAdapter(
                provider,
                resolver=self.resolver,
                max_output_tokens=budget["gpt_max_output_tokens"],
            )
        else:
            raise PaperTradingError("provider kind is unsupported")
        payload_builder = getattr(adapter, "connection_payload", None)
        payload_bytes = len(_json(payload_builder())) if callable(payload_builder) else 2048
        try:
            result, blocked = self._paid_call(
                experiment_id=experiment["experiment_id"],
                cycle_id=None,
                symbol=None,
                arm=None,
                provider_config=provider,
                adapter=adapter,
                call_type="test_connection",
                payload_bytes=payload_bytes,
                budget=budget,
                invoke=adapter.test_connection,
            )
            if blocked is not None:
                raise PaperTradingError(
                    f"AI budget guard blocked the connection test ({blocked.get('code')}); no request was made"
                )
        except PaperTradingError as exc:
            meta = getattr(adapter, "last_call", {}) or {}
            self.store.record_provider_validation(
                provider_id,
                status="failed",
                latency_ms=meta.get("latency_ms"),
                error_code="provider_validation_failed",
                details={
                    "error": str(exc)[:300],
                    "provider_request_id": meta.get("provider_request_id"),
                    "http_status": meta.get("http_status"),
                    "real_external_call": bool(meta.get("request_sent")),
                },
            )
            raise
        details = {
            key: result.get(key)
            for key in (
                "model",
                "requested_model",
                "returned_model",
                "provider_kind",
                "responses_endpoint_path",
                "structured_output_validated",
                "reasoning_effort_validated",
                "reasoning_effort_echoed",
                "reasoning_effort_accepted",
                "validated_question_types",
                "typed_outputs",
                "usage",
                "provider_request_id",
                "provider_response_id",
                "real_external_call",
                "fixture",
            )
            if key in result
        }
        details["latency_ms"] = float(result["latency_ms"])
        details["validated_at"] = iso_utc(self._clock())
        self.store.record_provider_validation(
            provider_id,
            status="passed",
            latency_ms=float(result["latency_ms"]),
            details=details,
        )
        return result

    def start(self) -> dict[str, Any]:
        config = self.store.experiment()["config"]
        if config["market_data_mode"] in {"binance_usdm", "gate_usdt"}:
            market = self._market(config)
            validator = getattr(market, "validate_symbols", None)
            if not callable(validator):
                raise PaperTradingError("selected market provider cannot validate contract support")
            support = validator(config["symbols"])
            experiment_id = config["experiment_id"]
            for excluded in support["excluded"]:
                self.store.record_runtime_event(
                    experiment_id,
                    None,
                    "symbol_excluded",
                    excluded,
                )
            if not support["supported"]:
                raise PaperTradingError("no configured symbol is supported by the public futures venue")
            if support["supported"] != config["symbols"]:
                config["symbols"] = support["supported"]
                self.store.save_experiment(config)
        if config["jev_enabled"] and any(
            arm in {"jev", "quant_jev", "hybrid"} for arm in config["evaluation_arms"]
        ):
            jev = self.store.provider(config["jev_provider_id"])
            if jev is None or not jev.get("enabled"):
                raise PaperTradingError("an enabled Jev provider is required by the selected arms")
            if jev["kind"] not in {"fixture_jev", "typesafe_jev"}:
                raise PaperTradingError("selected Jev provider has an incompatible provider type")
            if (
                not jev["kind"].startswith("fixture_")
                and self.store.provider_validation_status(config["jev_provider_id"]) != "passed"
            ):
                raise PaperTradingError("test the configured Jev provider before starting")
        gpt_required = any(
            arm in {"luna", "luna_skill"} for arm in config["evaluation_arms"]
        ) or (
            "hybrid" in config["evaluation_arms"]
            and (
                config["gpt_escalation_enabled"]
                or config["force_escalation"]
                or (
                    config["jev_enabled"]
                    and config["fallback_policy"] == "GPT_FALLBACK"
                )
            )
        )
        if gpt_required:
            gpt = self.store.provider(config["gpt_provider_id"])
            if gpt is None or not gpt.get("enabled"):
                raise PaperTradingError("an enabled GPT provider is required by the selected arms")
            if gpt["kind"] not in {
                "fixture_gpt",
                "openai_responses",
                "foundry_responses",
                "compatible_responses",
            }:
                raise PaperTradingError("selected GPT provider has an incompatible provider type")
            if (
                not gpt["kind"].startswith("fixture_")
                and self.store.provider_validation_status(config["gpt_provider_id"]) != "passed"
            ):
                raise PaperTradingError("test the configured GPT provider before starting")
        return self.store.set_status("running")

    def pause(self) -> dict[str, Any]:
        return self.store.set_status("paused")

    # ---- credentials (write-only; values never returned) ----------------
    def save_provider_secret(self, provider_id: str, value: Any) -> dict[str, Any]:
        provider = self.store.provider(provider_id)
        if provider is None:
            raise PaperTradingError("provider configuration was not found")
        if provider["kind"].startswith("fixture_"):
            raise PaperTradingError("fixture providers do not use credentials")
        secret_id = provider.get("credential_secret") or f"provider.{provider_id.lower()}"
        stored = store_secret(self.resolver.store, secret_id, value)
        if provider.get("credential_secret") != secret_id:
            self.store.save_provider({**provider, "credential_secret": secret_id})
        return {"provider_id": provider_id, **stored}

    def delete_provider_secret(self, provider_id: str) -> dict[str, Any]:
        provider = self.store.provider(provider_id)
        if provider is None or not provider.get("credential_secret"):
            raise PaperTradingError("provider has no stored credential")
        removed = self.resolver.store.delete(provider["credential_secret"])
        return {"provider_id": provider_id, "deleted": bool(removed)}

    def save_account_secrets(self, account_id: str, api_key: Any, api_secret: Any) -> dict[str, Any]:
        account = self.store.exchange_account_internal(account_id)
        key = store_secret(self.resolver.store, account["key_secret_id"], api_key)
        secret = store_secret(self.resolver.store, account["secret_secret_id"], api_secret)
        return {
            "account_id": account_id,
            "api_key": {k: key[k] for k in ("stored", "backend", "masked", "stored_at")},
            "api_secret": {k: secret[k] for k in ("stored", "backend", "masked", "stored_at")},
        }

    def gate_client(self, account_id: str, *, transport: Any | None = None) -> ReadOnlyGateClient:
        account = self.store.exchange_account_internal(account_id)
        api_key, _ = self.resolver.resolve(secret_id=account["key_secret_id"], env_name=None)
        api_secret, _ = self.resolver.resolve(secret_id=account["secret_secret_id"], env_name=None)
        if not api_key or not api_secret:
            raise PaperTradingError("Gate API key/secret are not stored for this account")
        return ReadOnlyGateClient(
            api_key=api_key,
            api_secret=api_secret,
            environment=account["environment"],
            transport=transport,
        )

    def sync_gate_account(self, account_id: str, *, transport: Any | None = None) -> dict[str, Any]:
        """Signed GET-only sync into the separate REAL ACCOUNT mirror (never the PAPER wallet)."""

        account = self.store.exchange_account_internal(account_id)
        if not account["enabled"]:
            raise PaperTradingError("exchange account is disabled")
        client = self.gate_client(account_id, transport=transport)
        result = sync_read_only_account(client)
        result["account_id"] = account_id
        result["display_name"] = account["display_name"]
        result["status"] = "passed" if result["capability"]["authenticated"] else "failed"
        result["paper_wallet_merged"] = False
        self.store.record_account_sync(account_id, result)
        return result

    def copy_gate_equity_to_new_experiment(self, account_id: str) -> dict[str, Any]:
        """Explicit one-time helper: copy a numeric equity value into an experiment with no cycles."""

        experiment = self.store.experiment()
        cycles = self.store.list_cycles(experiment["experiment_id"], limit=1)
        if cycles:
            raise PaperTradingError("starting balance can only be copied into a new experiment without cycles")
        sync = self.store.latest_account_sync(account_id)
        equity = ((sync or {}).get("balance") or {}).get("equity")
        if not isinstance(equity, (int, float)) or equity < 1:
            raise PaperTradingError("sync the Gate account first; no usable equity value is available")
        config = dict(experiment["config"])
        config["starting_balance_usdt"] = round(float(equity), 8)
        self.store.save_experiment(config)
        self.store.record_runtime_event(
            experiment["experiment_id"],
            None,
            "starting_balance_copied_from_gate",
            {"account_id": account_id, "value_only": True, "starting_balance_usdt": config["starting_balance_usdt"]},
        )
        return self.store.experiment()

    def resume(self) -> dict[str, Any]:
        return self.start()

    def stop(self) -> dict[str, Any]:
        return self.store.set_status("stopped")

    def test_data_source(self, mode: str) -> dict[str, Any]:
        symbols = self.store.experiment()["config"]["symbols"]
        if mode == "fixture":
            provider = FixtureFuturesMarketDataProvider()
            support = provider.validate_symbols(symbols)
            sample = provider.fetch_snapshot(support["supported"][0], self._clock())
            return {
                "ok": True,
                "provider": provider.provider_id,
                "data_origin": sample.data_origin,
                "supported_symbols": support["supported"],
                "excluded_symbols": support["excluded"],
                "data_cutoff": sample.data_cutoff,
            }
        if mode not in {"binance_usdm", "gate_usdt"}:
            raise PaperTradingError("market-data mode is unsupported")
        provider = (
            BinanceUsdMFuturesMarketDataProvider()
            if mode == "binance_usdm"
            else self._market({"market_data_mode": "gate_usdt"})
        )
        support = provider.validate_symbols(symbols)
        if not support["supported"]:
            raise PaperTradingError("no configured symbol is supported by the public futures venue")
        sample = provider.fetch_snapshot(support["supported"][0], self._clock())
        return {
            "ok": True,
            "provider": provider.provider_id,
            "data_origin": sample.data_origin,
            "supported_symbols": support["supported"],
            "excluded_symbols": support["excluded"],
            "data_cutoff": sample.data_cutoff,
        }

    def warm_up_market_history(
        self,
        *,
        profile: str = "EXP-001",
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Opt-in local history preload; tests and fixture mode make no network request."""
        if profile not in WARMUP_PROFILES:
            raise PaperTradingError("warm-up profile is unsupported")
        experiment = self.store.experiment()
        if experiment["status"] != "stopped":
            raise PaperTradingError("stop the runtime before warming up market history")
        config = experiment["config"]
        current_time = (as_of or self._clock()).astimezone(timezone.utc)
        if current_time > self._clock().astimezone(timezone.utc) + timedelta(seconds=1):
            raise PaperTradingError("warm-up cutoff cannot be in the future")
        provider = self._market(config)
        symbol_validator = getattr(provider, "validate_symbols", None)
        fetch_history = getattr(provider, "fetch_history", None)
        if not callable(symbol_validator) or not callable(fetch_history):
            raise PaperTradingError("selected market provider does not support history warm-up")
        support = symbol_validator(config["symbols"])
        lanes = []
        failures = []
        for excluded in support["excluded"]:
            self.store.record_runtime_event(
                experiment["experiment_id"],
                None,
                "warmup_symbol_excluded",
                excluded,
            )
        for symbol in support["supported"]:
            for interval, bars in WARMUP_PROFILES[profile].items():
                cutoff = floor_time(current_time, interval)
                start = cutoff - timedelta(
                    seconds=bars * INTERVAL_SECONDS[interval]
                )
                try:
                    candles = fetch_history(
                        symbol,
                        interval,
                        bars=bars,
                        as_of=current_time,
                    )
                    if (
                        not candles
                        or candles[0]["open_time"] != iso_utc(start)
                        or candles[-1]["close_time"] != iso_utc(cutoff)
                    ):
                        raise PaperTradingError(
                            "warm-up provider returned a different closed-bar range"
                        )
                    lanes.append(
                        self.store.save_market_history_lane(
                            experiment_id=experiment["experiment_id"],
                            provider_id=provider.provider_id,
                            symbol=symbol,
                            interval=interval,
                            candles=candles,
                            requested_bars=bars,
                            start_time=iso_utc(start),
                            data_cutoff=iso_utc(cutoff),
                            data_origin=MARKET_DATA_ORIGINS[config["market_data_mode"]],
                        )
                    )
                except PaperTradingError:
                    failure = {
                        "symbol": symbol,
                        "interval": interval,
                        "requested_bars": bars,
                        "error_code": "history_warmup_failed",
                    }
                    failures.append(failure)
                    self.store.record_runtime_event(
                        experiment["experiment_id"],
                        None,
                        "warmup_lane_failed",
                        failure,
                    )
        retention_days = config["market_data_retention_days"]
        if retention_days is not None:
            self.store.prune_market_history(
                provider_id=provider.provider_id,
                older_than=current_time - timedelta(days=retention_days),
            )
        result = {
            "profile": profile,
            "profile_version": "paper-warmup.exp001.v1",
            "experiment_id": experiment["experiment_id"],
            "provider_id": provider.provider_id,
            "data_origin": MARKET_DATA_ORIGINS[config["market_data_mode"]],
            "as_of": iso_utc(current_time),
            "supported_symbols": support["supported"],
            "excluded_symbols": support["excluded"],
            "requested_lane_count": len(support["supported"])
            * len(WARMUP_PROFILES[profile]),
            "completed_lane_count": len(lanes),
            "retrieved_bars": sum(lane["retrieved_bars"] for lane in lanes),
            "inserted_bars": sum(lane["inserted_bars"] for lane in lanes),
            "status": "failed"
            if not support["supported"] or (failures and not lanes)
            else "partial"
            if failures or support["excluded"]
            else "complete",
            "failures": failures,
            "history": self.store.market_history_status(experiment["experiment_id"]),
        }
        self.store.record_runtime_event(
            experiment["experiment_id"],
            None,
            "warmup_completed",
            {
                "profile": profile,
                "status": result["status"],
                "completed_lane_count": len(lanes),
                "retrieved_bars": result["retrieved_bars"],
            },
        )
        return result

    def metrics(self) -> dict[str, Any]:
        return PaperRuntimeReports(self).metrics()

    def dashboard(self) -> dict[str, Any]:
        return PaperRuntimeReports(self).dashboard()

    def evaluation(self) -> dict[str, Any]:
        return PaperRuntimeReports(self).evaluation()

    def cost_overview(self) -> dict[str, Any]:
        return PaperRuntimeReports(self).cost_overview()

    def economics(self) -> dict[str, Any]:
        return PaperRuntimeReports(self).economics()

    def export_bundle(self) -> tuple[bytes, str]:
        return PaperRuntimeReports(self).export_bundle()


class PaperScheduler:
    """Two local workers: closed-candle analysis cycles and model-free bar monitoring."""

    def __init__(self, runtime: PaperRuntime, *, poll_seconds: float = 5.0) -> None:
        self.runtime = runtime
        self.poll_seconds = max(1.0, float(poll_seconds))
        self._shutdown = threading.Event()
        self._worker_lock = threading.RLock()
        self._cycle_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None

    @property
    def store(self) -> PaperStore:
        return self.runtime.store

    @property
    def _clock(self) -> Callable[[], datetime]:
        return self.runtime._clock

    def _ensure_workers(self) -> None:
        with self._worker_lock:
            if self._shutdown.is_set():
                return
            if self._cycle_thread is None or not self._cycle_thread.is_alive():
                self._cycle_thread = threading.Thread(
                    target=self._cycle_loop,
                    name="paper-futures-scheduler",
                    daemon=True,
                )
                self._cycle_thread.start()
            if self._monitor_thread is None or not self._monitor_thread.is_alive():
                self._monitor_thread = threading.Thread(
                    target=self._monitor_loop,
                    name="paper-futures-position-monitor",
                    daemon=True,
                )
                self._monitor_thread.start()

    def resume_on_startup(self) -> dict[str, Any]:
        # Recovery runs before any worker starts, so no autonomous action sees unreconciled state.
        self.last_recovery = self.runtime.recover_on_startup()
        experiment = self.runtime.store.experiment()
        if experiment["status"] == "running" and not experiment["config"]["auto_resume"]:
            self.runtime.store.set_status("paused")
        self._ensure_workers()
        return self.runtime.store.experiment()

    def start(self) -> dict[str, Any]:
        result = self.runtime.start()
        self._ensure_workers()
        return result

    def pause(self) -> dict[str, Any]:
        return self.runtime.pause()

    def resume(self) -> dict[str, Any]:
        result = self.runtime.resume()
        self._ensure_workers()
        return result

    def stop(self) -> dict[str, Any]:
        return self.runtime.stop()

    def cycle_tick(self, now: datetime | None = None) -> dict[str, Any]:
        """One scheduler decision. The slot is persisted before any cycle runs, so a restart
        within the slot never repeats it; slots missed during sleep/downtime are recorded as
        SKIPPED_GAP and never back-filled with invented observations."""

        res = self.runtime.resilience
        experiment = self.runtime.store.experiment()
        res.heartbeat("scheduler", detail={"status": experiment["status"]})
        if experiment["status"] != "running":
            return {"status": "idle"}
        if self.runtime.portfolio.kill_switch()["level"] == "FULL_AUTOMATION_HALT":
            return {"status": "halted"}
        config = experiment["config"]
        experiment_id = experiment["experiment_id"]
        now = (now or self.runtime._clock()).astimezone(timezone.utc)
        close = floor_time(now, "15m")
        due = close + timedelta(seconds=config["schedule_delay_seconds"])
        if now < due:
            return {"status": "waiting"}
        slot = iso_utc(close)
        prior = res.slot_status(experiment_id, slot)
        if prior == "DONE":
            return {"status": "already_done", "slot": slot}
        # An ATTEMPTED slot was interrupted (shutdown/crash): re-running is safe because each
        # (experiment, symbol, candle) cycle is unique and a consumed AI call is never repeated.
        gap = [] if prior == "ATTEMPTED" else missed_slots(res.last_slot(experiment_id), slot)
        if gap:
            listed = gap[: res.settings["max_missed_slots_listed"]]
            for missed in listed:
                res.record_slot(experiment_id, missed, "SKIPPED_GAP", {"reason": "process down or machine asleep"})
            res.open_incident("SCHEDULER_GAP", severity="WARNING", experiment_id=experiment_id, resolved=True,
                              summary=f"{len(gap)} scheduled 15m slot(s) missed; not back-filled (no invented observations)",
                              detail={"first": gap[0], "last": gap[-1], "count": len(gap)})
        lateness = (now - due).total_seconds()
        if lateness > res.settings["scheduler_lag_seconds"]:
            res.open_incident("SCHEDULER_LAG", severity="WARNING", experiment_id=experiment_id, resolved=True,
                              summary=f"slot {slot} started {int(lateness)}s late; the cycle uses the closed candle only",
                              detail={"slot": slot, "late_seconds": lateness})
        res.record_slot(experiment_id, slot, "ATTEMPTED", {"symbols": config["symbols"]})
        outcomes: dict[str, str] = {}
        for symbol in config["symbols"]:
            if self._shutdown.is_set():
                outcomes[symbol] = "shutdown"
                break
            try:
                cycle = self.runtime.run_cycle(symbol, as_of=now, manual=False)
                outcomes[symbol] = str((cycle or {}).get("status", "ok"))
            except PaperTradingError as exc:
                outcomes[symbol] = f"error: {str(exc)[:80]}"
        interrupted = any(value == "shutdown" for value in outcomes.values())
        res.record_slot(experiment_id, slot, "ATTEMPTED" if interrupted else "DONE", {"outcomes": outcomes})
        return {"status": "interrupted" if interrupted else "ran", "slot": slot, "missed": len(gap), "outcomes": outcomes}

    def monitor_tick(self, now: datetime | None = None) -> dict[str, Any]:
        res = self.runtime.resilience
        experiment = self.runtime.store.experiment()
        interval = int(experiment["config"]["monitor_interval_seconds"])
        now = (now or self.runtime._clock()).astimezone(timezone.utc)
        previous = res.heartbeats().get("monitor", {}).get("last_ok_at")
        if previous:
            silence = (now - parse_utc(previous, "monitor.last_ok_at")).total_seconds()
            if silence > res.settings["monitor_gap_multiplier"] * max(interval, 5):
                res.open_incident("MONITOR_GAP", severity="WARNING", experiment_id=experiment["experiment_id"], resolved=True,
                                  summary=f"monitor silent for {int(silence)}s (sleep/downtime); catching up from real closed bars",
                                  detail={"silence_seconds": silence, "since": previous})
        self.runtime.monitor_once(as_of=now)
        # Spot limits/plans, management metadata, post-trade reviews, the deterministic
        # position-review queue (no per-tick AI), lifecycle reviews, snapshots, and attention.
        summary = self.runtime.portfolio.after_monitor(now=now)
        stream = self.runtime.live_stream
        if stream is not None:
            status = stream.status()
            counters = status.get("counters") or {}
            seen = (res.heartbeats().get("feed") or {}).get("detail") or {}
            live = str(status.get("state") or "").upper() == "LIVE"
            reason = status.get("last_error") or status.get("reason")
            if not live:
                res.open_incident("FEED_STALE", severity="WARNING", dedupe_key="feed", experiment_id=experiment["experiment_id"],
                                  summary=f"market feed {status.get('state')}: {reason or 'no detail'}; stale-feed guards stay authoritative",
                                  detail={"state": status.get("state"), "reason": reason})
            else:
                res.resolve("feed", "market feed live again")
            new_gap_bars = int(counters.get("gap_fill_bars", 0)) - int(seen.get("gap_fill_bars", 0))
            if seen and new_gap_bars > 0:
                res.open_incident("FEED_GAP", severity="INFO", experiment_id=experiment["experiment_id"], resolved=True,
                                  summary=f"reconnected and REST gap-filled {new_gap_bars} closed bar(s)",
                                  detail={"reconnects": counters.get("reconnects"), "gap_fill_bars": counters.get("gap_fill_bars")})
            res.heartbeat("feed", ok=live, error=reason,
                          detail={"gap_fill_bars": counters.get("gap_fill_bars", 0), "reconnects": counters.get("reconnects", 0),
                                  "dropped": counters.get("dropped", 0)})
        retention_age = res.heartbeat_age("retention")
        if retention_age is None or retention_age > 86_400:
            summary["retention"] = compact_storage(self.runtime.store, experiment["experiment_id"], now=now)
            res.heartbeat("retention", detail=summary["retention"])
        res.heartbeat("monitor", detail={k: v for k, v in summary.items() if isinstance(v, (int, bool, str))})
        res.resolve("monitor", "monitor tick succeeded")
        return summary

    def _record_loop_error(self, name: str, exc: BaseException) -> None:
        try:
            self.runtime.resilience.heartbeat(name, ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
            self.runtime.resilience.open_incident(
                "SCHEDULER_FAILURE" if name == "scheduler" else "MONITOR_FAILURE", severity="WARNING", dedupe_key=name,
                summary=f"{name} loop error: {type(exc).__name__}", detail={"error": str(exc)[:300]},
            )
        except Exception:
            pass  # the database itself may be failing; the loop keeps running and retries

    def _cycle_loop(self) -> None:
        while not self._shutdown.wait(self.poll_seconds):
            try:
                self.cycle_tick()
            except Exception as exc:  # never kill the scheduler thread; record and back off
                self._record_loop_error("scheduler", exc)
                self._shutdown.wait(self.poll_seconds)

    def _monitor_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                interval = self.runtime.store.experiment()["config"]["monitor_interval_seconds"]
                self.monitor_tick()
            except Exception as exc:
                self._record_loop_error("monitor", exc)
                interval = 30
            self._shutdown.wait(max(5, int(interval)))

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop accepting new work, let bounded in-flight work finish, then mark a clean stop."""

        self._shutdown.set()
        for worker in (self._cycle_thread, self._monitor_thread):
            if worker is not None and worker.is_alive():
                worker.join(timeout=timeout)
        try:
            self.runtime.mark_clean_shutdown()
        except Exception:
            pass


class PaperRuntimeReports:
    """Read-only metrics, dashboard, and export projections over persisted runtime state."""

    def __init__(self, runtime: PaperRuntime) -> None:
        self.runtime = runtime
        self.store = runtime.store
        self._clock = runtime._clock

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float | None:
        if not values:
            return None
        ordered = sorted(float(value) for value in values)
        index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
        return ordered[index]

    def _cohort_metrics(self, experiment_id: str, cohort: str) -> dict[str, Any]:
        positions = self.store.list_positions(experiment_id, cohort=cohort)
        closed = [
            position
            for position in positions
            if position["status"] == "closed" and position["closed_pnl_recorded"]
        ]
        wallet = self.store.wallet_summary(experiment_id, cohort)
        closed_pnl = [float(position["realized_pnl"] or 0) for position in closed]
        gains = sum(value for value in closed_pnl if value > 0)
        losses = -sum(value for value in closed_pnl if value < 0)
        equity = self.store.equity_series(experiment_id, cohort)
        peak = -math.inf
        max_drawdown = 0.0
        for point in equity:
            peak = max(peak, float(point["equity"]))
            if peak > 0:
                max_drawdown = max(
                    max_drawdown,
                    (peak - float(point["equity"])) / peak,
                )
        partial_realized = sum(
            float(position["realized_gross"])
            - (float(position["entry_fee"]) - float(position["entry_fee_remaining"]))
            - float(position["exit_fees"])
            - float(position["funding_paid"])
            for position in positions
            if position["status"] == "open"
        )
        total_realized = sum(closed_pnl) + partial_realized
        return {
            "cohort": cohort,
            "starting_balance_usdt": float(wallet["starting_balance"]),
            "cash_balance_usdt": float(wallet["cash_balance"]),
            "unrealized_pnl_usdt": float(wallet["unrealized_pnl"]),
            "ending_equity_usdt": float(wallet["equity"]),
            "net_pnl_usdt": float(wallet["equity"]) - float(wallet["starting_balance"]),
            "realized_pnl_usdt": total_realized,
            "closed_trade_count": len(closed),
            "win_count": sum(1 for value in closed_pnl if value > 0),
            "loss_count": sum(1 for value in closed_pnl if value < 0),
            "win_rate": sum(1 for value in closed_pnl if value > 0) / len(closed_pnl)
            if closed_pnl
            else None,
            "expectancy_usdt": sum(closed_pnl) / len(closed_pnl) if closed_pnl else None,
            "profit_factor": gains / losses if losses > 0 else None,
            "max_drawdown": max_drawdown if len(equity) > 1 else None,
            "open_position_count": sum(1 for position in positions if position["status"] == "open"),
            "fees_usdt": sum(float(position["entry_fee"]) + float(position["exit_fees"]) for position in positions),
            "funding_paid_usdt": sum(float(position["funding_paid"]) for position in positions),
            "slippage_drag_usdt": sum(float(position["slippage_paid"]) for position in positions),
            "margin_used_usdt": float(wallet["margin_used"]),
            "available_margin_usdt": float(wallet["available_margin"]),
            "sample_denominator": len(closed_pnl),
            "data_origin": equity[-1]["data_origin"] if equity else "NO_CYCLE",
        }

    @staticmethod
    def _bucket_trade_metrics(
        closed_trades: list[dict[str, Any]],
        *,
        key_field: str,
        key_name: str,
        cycle_origins: dict[str, str],
        initial_equity: float,
    ) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for trade in closed_trades:
            key = trade.get(key_field) or "unknown"
            groups.setdefault(str(key), []).append(trade)
        results = []
        for key, trades in sorted(groups.items()):
            chronological = sorted(
                trades,
                key=lambda trade: (trade.get("closed_at") or "", trade["position_id"]),
            )
            pnl = sum(
                float(trade["realized_pnl"] or 0) for trade in chronological
            )
            origins = {
                cycle_origins.get(trade["cycle_id"], "UNKNOWN")
                for trade in chronological
            }
            equity = float(initial_equity)
            peak = equity
            max_drawdown = 0.0
            for trade in chronological:
                equity += float(trade["realized_pnl"] or 0)
                peak = max(peak, equity)
                if peak > 0:
                    max_drawdown = max(max_drawdown, (peak - equity) / peak)
            results.append(
                {
                    key_name: key,
                    "closed_trade_count": len(chronological),
                    "net_pnl_usdt": pnl,
                    "expectancy_usdt": (
                        pnl / len(chronological) if chronological else None
                    ),
                    "max_drawdown": max_drawdown if chronological else None,
                    "data_origin": next(iter(origins)) if len(origins) == 1 else "MIXED",
                }
            )
        return results

    def metrics(self) -> dict[str, Any]:
        experiment = self.store.experiment()
        experiment_id = experiment["experiment_id"]
        config = experiment["config"]
        records = self.store.export_records(experiment_id)
        primary = self._cohort_metrics(experiment_id, "primary")
        cycles = records["cycles"]
        closed = [
            position
            for position in records["positions"]
            if position["cohort"] == "primary"
            and position["status"] == "closed"
            and position["closed_pnl_recorded"]
        ]
        cycle_origins = {
            cycle["cycle_id"]: cycle["payload"].get("data_origin", "UNKNOWN")
            for cycle in cycles
        }
        by_asset = self._bucket_trade_metrics(
            closed,
            key_field="symbol",
            key_name="symbol",
            cycle_origins=cycle_origins,
            initial_equity=float(config["starting_balance_usdt"]),
        )
        by_regime = self._bucket_trade_metrics(
            closed,
            key_field="market_regime",
            key_name="regime",
            cycle_origins=cycle_origins,
            initial_equity=float(config["starting_balance_usdt"]),
        )
        blocks = [event for event in records["risk_events"] if event["status"] == "blocked"]
        eligible = sum(
            1
            for cycle in cycles
            if cycle["payload"].get("features", {}).get("gate_eligible") is True
        )
        hybrid_cases = []
        for cycle in cycles:
            payload = cycle["payload"]
            decision = payload.get("arms", {}).get("hybrid")
            route = decision.get("escalation") if isinstance(decision, dict) else None
            if (
                not isinstance(decision, dict)
                or payload.get("features", {}).get("gate_eligible") is not True
                or not isinstance(route, dict)
            ):
                continue
            jev_status = payload.get("jev_status")
            route_reasons = route.get("reasons")
            fallback_route = isinstance(route_reasons, list) and any(
                reason
                in {
                    "jev_disabled_gpt_fallback",
                    "jev_unavailable_gpt_fallback",
                }
                for reason in route_reasons
            )
            if jev_status == "completed" or fallback_route:
                hybrid_cases.append(decision)
        escalated = sum(
            1
            for decision in hybrid_cases
            if (decision.get("escalation") or {}).get("escalate") is True
        )
        calls = records["ai_calls"]
        successful_calls = [call for call in calls if call["status"] == "ok"]
        latencies = [
            float(call["latency_ms"])
            for call in successful_calls
            if call["latency_ms"] is not None
        ]
        cycle_latencies = [
            float(cycle["payload"]["cycle_latency_ms"])
            for cycle in cycles
            if isinstance(cycle["payload"].get("cycle_latency_ms"), (int, float))
        ]
        expected_slot_indices: set[int] = set()
        running_since: datetime | None = None
        status_events = self.store.runtime_status_events(experiment_id)
        delay = int(config["schedule_delay_seconds"])
        now = self._clock().astimezone(timezone.utc)

        def collect_due_slots(start: datetime, end: datetime) -> None:
            start_index = math.ceil(
                (start.timestamp() - delay) / INTERVAL_SECONDS["15m"]
            )
            end_index = math.floor(
                (end.timestamp() - delay) / INTERVAL_SECONDS["15m"]
            )
            if end_index >= start_index:
                expected_slot_indices.update(range(start_index, end_index + 1))
            current_closed_slot = math.floor(
                start.timestamp() / INTERVAL_SECONDS["15m"]
            )
            current_due_at = current_closed_slot * INTERVAL_SECONDS["15m"] + delay
            if (
                start.timestamp() >= current_due_at
                and current_closed_slot * INTERVAL_SECONDS["15m"] <= end.timestamp()
            ):
                expected_slot_indices.add(current_closed_slot)

        for status_event in status_events:
            changed_at = parse_utc(status_event["created_at"], "runtime_status.created_at")
            status = status_event.get("status")
            if status == "running" and running_since is None:
                running_since = changed_at
            elif status != "running" and running_since is not None:
                collect_due_slots(running_since, changed_at)
                running_since = None
        if running_since is not None:
            collect_due_slots(running_since, now)
        cycles_expected = len(expected_slot_indices) * len(config["symbols"])
        status_counts: dict[str, int] = {}
        for cycle in cycles:
            status_counts[cycle["status"]] = status_counts.get(cycle["status"], 0) + 1
        cycle_completed = status_counts.get("complete", 0)
        cycle_failed = status_counts.get("failed", 0)
        cycle_interrupted = status_counts.get("interrupted", 0)
        cycle_processing = status_counts.get("processing", 0)
        schedule_skipped = max(
            0,
            cycles_expected
            - cycle_completed
            - cycle_failed
            - cycle_interrupted
            - cycle_processing,
        )
        signal_skipped = sum(
            1
            for cycle in cycles
            if cycle["status"] == "complete"
            and cycle["payload"].get("quant_gate", {}).get("eligible") is False
        )
        duplicate_preventions = sum(
            1
            for event in records["events"]
            if event["event_type"] == "duplicate_cycle_prevented"
        )
        operational_events = records["events"]
        external_calls = [
            call for call in calls if not call["provider_kind"].startswith("fixture_")
        ]
        unpriced_calls = [
            call for call in external_calls if call["cost_estimate"] is None
        ]
        if not external_calls:
            total_ai_cost = None
            cost_status = "fixture_only" if successful_calls else "no_calls"
        elif unpriced_calls:
            total_ai_cost = None
            cost_status = "unpriced"
        else:
            total_ai_cost = sum(
                float(call["cost_estimate"] or 0) for call in external_calls
            )
            cost_status = "priced"
        token_counts = {
            key: {
                "known_tokens": sum(
                    int(call[field])
                    for call in calls
                    if isinstance(call[field], int)
                ),
                "known_call_count": sum(
                    1 for call in calls if isinstance(call[field], int)
                ),
                "total_call_count": len(calls),
            }
            for key, field in (
                ("input", "input_tokens"),
                ("output", "output_tokens"),
                ("reasoning", "reasoning_tokens"),
            )
        }
        grouped_calls: dict[str, list[dict[str, Any]]] = {}
        for call in calls:
            grouped_calls.setdefault(call["provider_id"], []).append(call)
        provider_usage = []
        for provider_id, provider_calls in sorted(grouped_calls.items()):
            provider_unpriced = any(
                not call["provider_kind"].startswith("fixture_")
                and call["cost_estimate"] is None
                for call in provider_calls
            )
            provider_usage.append(
                {
                    "provider_id": provider_id,
                    "provider_kind": provider_calls[0]["provider_kind"],
                    "model": provider_calls[0]["model"],
                    "call_count": len(provider_calls),
                    "successful_call_count": sum(
                        1 for call in provider_calls if call["status"] == "ok"
                    ),
                    "input_tokens": sum(
                        int(call["input_tokens"])
                        for call in provider_calls
                        if isinstance(call["input_tokens"], int)
                    ),
                    "output_tokens": sum(
                        int(call["output_tokens"])
                        for call in provider_calls
                        if isinstance(call["output_tokens"], int)
                    ),
                    "latency_p50_ms": self._percentile(
                        [
                            float(call["latency_ms"])
                            for call in provider_calls
                            if call["latency_ms"] is not None
                        ],
                        0.5,
                    ),
                    "latency_p95_ms": self._percentile(
                        [
                            float(call["latency_ms"])
                            for call in provider_calls
                            if call["latency_ms"] is not None
                        ],
                        0.95,
                    ),
                    "cost_estimate_usd": (
                        None
                        if provider_unpriced
                        else sum(float(call["cost_estimate"] or 0) for call in provider_calls)
                    ),
                    "pricing_versions": sorted(
                        {
                            call["pricing_version"]
                            for call in provider_calls
                            if call["pricing_version"] is not None
                        }
                    ),
                }
            )
        arm_metrics = {}
        for arm in config["evaluation_arms"]:
            arm_metrics[arm] = self._cohort_metrics(experiment_id, f"arm-{arm}")
        shadow_metrics = [
            self._cohort_metrics(experiment_id, f"x{leverage}")
            for leverage in config["shadow_leverage"]
        ]
        equity_reconciles = math.isclose(
            float(primary["ending_equity_usdt"]),
            float(primary["cash_balance_usdt"]) + float(primary["unrealized_pnl_usdt"]),
            rel_tol=0,
            abs_tol=1e-8,
        )
        closed_trade_pnl = sum(float(position["realized_pnl"] or 0) for position in closed)
        pnl_by_asset: dict[str, float] = {}
        for position in closed:
            pnl_by_asset[position["symbol"]] = pnl_by_asset.get(position["symbol"], 0.0) + float(
                position["realized_pnl"] or 0
            )
        grouped_closed_pnl = sum(pnl_by_asset.values())
        grouped_asset_pnl = sum(row["net_pnl_usdt"] for row in by_asset)
        grouped_regime_pnl = sum(row["net_pnl_usdt"] for row in by_regime)
        asset_pnl_reconciles = math.isclose(
            closed_trade_pnl, grouped_asset_pnl, rel_tol=0, abs_tol=1e-8
        )
        regime_pnl_reconciles = math.isclose(
            closed_trade_pnl, grouped_regime_pnl, rel_tol=0, abs_tol=1e-8
        )
        closed_pnl_reconciles = math.isclose(
            closed_trade_pnl, grouped_closed_pnl,
            rel_tol=0,
            abs_tol=1e-8,
        ) and asset_pnl_reconciles and regime_pnl_reconciles
        return {
            "schema_version": "paper-futures-metrics.v1",
            "data_origin": (
                records["equity"][-1]["data_origin"] if records["equity"] else "NO_CYCLE"
            ),
            "sample_denominators": {
                "cycles": len(cycles),
                "eligible_quant_cases": eligible,
                "hybrid_escalation_cases": len(hybrid_cases),
                "closed_primary_trades": len(closed),
                "shadow_closed_trades": sum(
                    metric["closed_trade_count"] for metric in shadow_metrics
                ),
            },
            "scheduler_cycles": {
                "expected": cycles_expected,
                "completed": cycle_completed,
                "failed": cycle_failed,
                "interrupted": cycle_interrupted,
                "processing": cycle_processing,
                "schedule_skipped": schedule_skipped,
                "signal_skipped": signal_skipped,
                "duplicate_preventions": duplicate_preventions,
                "running_interval_basis": "persisted runtime status transitions and 15m schedule",
            },
            "operational": {
                "market_data_failure_count": sum(
                    1
                    for event in operational_events
                    if event["event_type"] == "market_data_failure"
                ),
                "monitor_failure_count": sum(
                    1
                    for event in operational_events
                    if event["event_type"] == "monitor_error"
                ),
                "provider_error_count": sum(1 for call in calls if call["status"] == "failed"),
                "restart_recovery_count": sum(
                    1 for event in operational_events if event["event_type"] == "cycle_interrupted"
                ),
                "kill_switch_event_count": sum(
                    1 for event in operational_events if event["event_type"] == "kill_switch"
                ),
            },
            "portfolio": primary,
            "by_asset": by_asset,
            "by_regime": by_regime,
            "bucket_drawdown_convention": (
                "Each primary asset/regime bucket starts with the configured "
                "EXP-001 starting balance; closed net PnL is applied in "
                "chronological order and drawdown is peak-to-trough divided "
                "by peak. Bucket curves are independent, not portfolio curves."
            ),
            "risk_block_count": len(blocks),
            "risk_approval_count": sum(
                1 for event in records["risk_events"] if event["status"] == "approved"
            ),
            "jev_calls": sum(1 for call in calls if call["path"] == "jev"),
            "gpt_calls": sum(1 for call in calls if call["path"].startswith("gpt:")),
            "escalation_count": escalated,
            "escalation_rate": escalated / len(hybrid_cases) if hybrid_cases else None,
            "escalation_rate_numerator": escalated,
            "escalation_rate_denominator": len(hybrid_cases),
            "escalation_rate_definition": (
                "router-required deep reasoning divided by quant-gated Hybrid cases "
                "with completed Jev decisions or explicit GPT fallback; it measures "
                "routing requirement, not provider call success"
            ),
            "luna_avoided_rate_definition": (
                "one minus escalation_rate; cases routed through the Jev fast path"
            ),
            "luna_avoided_rate": (
                (len(hybrid_cases) - escalated) / len(hybrid_cases)
                if hybrid_cases
                else None
            ),
            "ai_cost_estimate_usd": total_ai_cost,
            "ai_cost_status": cost_status,
            "ai_cost_per_eligible_case_usd": (
                total_ai_cost / eligible
                if total_ai_cost is not None and eligible > 0
                else None
            ),
            "ai_cost_per_closed_trade_usd": (
                total_ai_cost / len(closed)
                if total_ai_cost is not None and closed
                else None
            ),
            "ai_token_usage": token_counts,
            "provider_usage": provider_usage,
            "latency_ms": {
                "ai_p50": self._percentile(latencies, 0.5),
                "ai_p95": self._percentile(latencies, 0.95),
                "cycle_p50": self._percentile(cycle_latencies, 0.5),
                "cycle_p95": self._percentile(cycle_latencies, 0.95),
                "ai_sample_count": len(latencies),
                "cycle_sample_count": len(cycle_latencies),
            },
            "decision_quality": None,
            "decision_quality_status": (
                "not_evaluated_until_future_market_outcomes_are_observed"
            ),
            "arms": arm_metrics,
            "shadow_leverage": shadow_metrics,
            "reconciliation": {
                "ending_equity_equals_cash_plus_unrealized": equity_reconciles,
                "closed_pnl_matches_closed_trade_ledger": closed_pnl_reconciles,
                "closed_trade_net_pnl_usdt": closed_trade_pnl,
                "by_asset_pnl_reconciles": asset_pnl_reconciles,
                "by_regime_pnl_reconciles": regime_pnl_reconciles,
                "all_cohort_closed_trade_count": sum(
                    1 for position in records["positions"]
                    if position["status"] == "closed" and position["closed_pnl_recorded"]
                ),
                "all_cohort_closed_trade_net_pnl_usdt": sum(
                    float(position["realized_pnl"] or 0)
                    for position in records["positions"]
                    if position["status"] == "closed" and position["closed_pnl_recorded"]
                ),
                "configured_mode": config["execution_mode"],
                "real_money_execution": False,
            },
        }

    @staticmethod
    def _cycle_routing(
        cycle: dict[str, Any],
        execution_metadata: dict[str, Any],
        configured_primary_arm: str,
    ) -> dict[str, Any]:
        gate = cycle.get("quant_gate")
        if isinstance(gate, dict) and isinstance(gate.get("eligible"), bool):
            quant_gate_decision = "PASS" if gate["eligible"] else "BLOCKED"
            raw_strength = gate.get("strength")
            signal_strength = (
                float(raw_strength)
                if isinstance(raw_strength, (int, float))
                and not isinstance(raw_strength, bool)
                and math.isfinite(float(raw_strength))
                else None
            )
        else:
            quant_gate_decision = "UNKNOWN"
            signal_strength = None

        jev_vector = cycle.get("jev_vector")
        if isinstance(jev_vector, dict):
            answers = jev_vector.get("answers") or {}
            regime_answer = answers.get("market_regime") or {}
            regime = regime_answer.get("value")
            arm_decision = (cycle.get("arms") or {}).get("jev")
            if isinstance(arm_decision, dict) and isinstance(
                arm_decision.get("decision"), str
            ):
                jev_decision_value = arm_decision["decision"]
            elif isinstance(regime, str):
                jev_decision_value = regime
            else:
                jev_decision_value = "UNKNOWN"
            raw_confidence = regime_answer.get("confidence")
            confidence = (
                float(raw_confidence)
                if isinstance(raw_confidence, (int, float))
                and not isinstance(raw_confidence, bool)
                and math.isfinite(float(raw_confidence))
                else None
            )
            direction = (
                "long" if regime == "bull" else "short" if regime == "bear" else None
            )
        else:
            jev_decision_value = "UNKNOWN"
            confidence = None
            direction = None

        primary = cycle.get("primary_decision") or {}
        route = primary.get("escalation")
        route_reasons = route.get("reasons") if isinstance(route, dict) else None
        route_was_evaluated = (
            isinstance(route, dict)
            and isinstance(route.get("escalate"), bool)
            and not (
                isinstance(route_reasons, list)
                and any(
                    reason
                    in {
                        "signal_gate",
                        "jev_disabled_fail_closed",
                        "jev_unavailable_fail_closed",
                    }
                    for reason in route_reasons
                )
            )
        )
        escalation_required = (
            route["escalate"] if route_was_evaluated else None
        )
        risk = cycle.get("risk")
        risk_evaluated = (
            cycle.get("status") == "complete"
            and isinstance(risk, dict)
            and isinstance(risk.get("allowed"), bool)
            and risk.get("code") != "NO_INTENT"
            and risk.get("code") != "NOT_EVALUATED"
        )
        if isinstance(risk, dict):
            risk_decision = {
                "approved": risk["allowed"] if risk_evaluated else None,
                "code": risk.get("code") or "NOT_EVALUATED",
                "reason": risk.get("reason") or "Risk decision reason unavailable.",
            }
        else:
            risk_decision = {
                "approved": None,
                "code": "NOT_EVALUATED",
                "reason": "Cycle did not reach deterministic risk evaluation.",
            }

        primary_arm = cycle.get("primary_arm") or configured_primary_arm
        if primary_arm not in {"luna", "luna_skill", "hybrid"}:
            luna_result = None
        else:
            ai_path = primary.get("ai_path")
            matching_calls = [
                call
                for call in execution_metadata.get("ai_calls", [])
                if isinstance(ai_path, str) and call.get("path") == ai_path
            ]
            if cycle.get("status") != "complete":
                luna_result = {"status": "not_evaluated", "decision": None}
            elif matching_calls:
                if matching_calls[-1].get("status") == "ok":
                    luna_result = {
                        "status": "invoked",
                        "decision": primary.get("decision"),
                    }
                else:
                    luna_result = {"status": "failed", "decision": None}
            elif ai_path in {"blocked_escalation", "jev_fast_path"} or (
                route_was_evaluated and route.get("escalate") is False
            ):
                luna_result = {"status": "not_invoked", "decision": None}
            else:
                luna_result = {"status": "not_evaluated", "decision": None}

        order_status_counts = execution_metadata.get("order_status_counts", {})
        order_count = int(execution_metadata.get("order_count", 0))
        fill_count = int(execution_metadata.get("fill_count", 0))
        if cycle.get("status") in {"processing", "interrupted", "failed"}:
            execution_status = "NOT_EVALUATED"
        elif fill_count > 0 and order_status_counts.get("pending", 0) > 0:
            execution_status = "PARTIAL"
        elif fill_count > 0:
            execution_status = "FILLED"
        elif order_status_counts.get("pending", 0) > 0:
            execution_status = "PENDING"
        elif order_count > 0:
            execution_status = "REJECTED"
        elif risk_decision["approved"] is None:
            execution_status = (
                "NOT_SUBMITTED"
                if risk_decision["code"] == "NO_INTENT"
                else "NOT_EVALUATED"
            )
        elif risk_decision["approved"] is False:
            execution_status = "BLOCKED"
        else:
            execution_status = "NO_ORDER"

        return {
            "quant_gate": {
                "decision": quant_gate_decision,
                "signal_strength": signal_strength,
            },
            "jev_decision": {
                "decision": jev_decision_value,
                "confidence": confidence,
                "direction": direction,
            },
            "escalation_required": escalation_required,
            "luna_result": luna_result,
            "risk_decision": risk_decision,
            "paper_execution": {
                "status": execution_status,
                "order_count": order_count,
                "fill_count": fill_count,
            },
        }

    def dashboard(self) -> dict[str, Any]:
        experiment = self.store.experiment()
        experiment_id = experiment["experiment_id"]
        config = experiment["config"]
        now = self._clock().astimezone(timezone.utc)
        current_slot = floor_time(now, "15m")
        next_slot = current_slot + timedelta(minutes=15, seconds=config["schedule_delay_seconds"])
        cycles = self.store.list_cycles(experiment_id, limit=30)
        execution_metadata = self.store.cycle_execution_metadata(
            experiment_id, [cycle["cycle_id"] for cycle in cycles]
        )
        for cycle in cycles:
            cycle["routing"] = self._cycle_routing(
                cycle,
                execution_metadata.get(cycle["cycle_id"], {}),
                config["primary_arm"],
            )
        activity = self.store.activity(experiment_id, limit=40)
        return {
            "experiment": {
                "experiment_id": experiment_id,
                "status": experiment["status"],
                "created_at": experiment["created_at"],
                "updated_at": experiment["updated_at"],
                "config": config,
            },
            "data_safety": {
                "execution_mode": "PAPER",
                "real_money_execution": False,
                "live_exchange_order_endpoint": False,
                "market_data_mode": config["market_data_mode"],
                "origin": (
                    cycles[0].get("data_origin", "NO_CYCLE") if cycles else "NO_CYCLE"
                ),
            },
            "providers": self.store.list_providers(),
            "next_cycle_at": iso_utc(next_slot),
            "metrics": self.metrics(),
            "positions": self.store.list_positions(experiment_id, cohort="primary"),
            "equity": self.store.equity_series(experiment_id, "primary", limit=250),
            "cycles": cycles,
            "risk_events": self.store.risk_events(experiment_id, limit=50),
            "activity": activity,
            "ai_cost": self.cost_overview(),
            "economics": self.economics(),
            "market_stream": self.market_stream_status(),
            "exchange_accounts": self.store.list_exchange_accounts(),
            "real_integration": self.store.latest_integration_check(),
            "live_execution": {
                "gate_write_execution": False,
                "status": "BLOCKED_BY_DESIGN",
                "adapter": "DisabledLiveExecutionAdapter",
            },
        }

    def market_stream_status(self) -> dict[str, Any] | None:
        stream = getattr(self.runtime, "live_stream", None)
        if stream is None:
            return None
        stream.refresh_state()
        return stream.status()

    def cost_overview(self) -> dict[str, Any]:
        experiment = self.store.experiment()
        experiment_id = experiment["experiment_id"]
        config = experiment["config"]
        ledger = self.store.cost_ledger
        usage = ledger.usage_events(experiment_id)
        budget_events = ledger.budget_events(experiment_id)
        book = ledger.price_book()
        providers = self.store.providers_internal()
        coverage = []
        for provider in providers:
            if provider["kind"].startswith("fixture_"):
                continue
            price = ledger.current_price(provider["kind"], provider["model"])
            coverage.append(
                {
                    "provider_id": provider["provider_id"],
                    "provider_kind": provider["kind"],
                    "model": provider["model"],
                    "price_known": price is not None,
                    "price_book_version": price["version"] if price else None,
                    "status": "PRICED" if price else "UNKNOWN_PRICE_FAIL_CLOSED",
                }
            )
        return {
            "budget_status": ledger.budget_status(experiment_id, config["ai_budget"]),
            "cost_fx": config["cost_fx"],
            "price_book": book,
            "price_book_current_versions": sorted({row["version"] for row in book}),
            "price_coverage": coverage,
            "providers": provider_ledger_summary(usage, budget_events),
            "recent_budget_events": [
                {**row, "details": _loads(row["details_json"], {})} for row in budget_events[:40]
            ],
            "recent_usage": usage[-40:],
        }

    def economics(self) -> dict[str, Any]:
        experiment = self.store.experiment()
        experiment_id = experiment["experiment_id"]
        records = self.store.export_records(experiment_id)
        return economic_summary(
            positions=records["positions"],
            usage_events=self.store.cost_ledger.usage_events(experiment_id),
            cycles=records["cycles"],
            fx=experiment["config"]["cost_fx"],
            starting_balance_usdt=float(experiment["config"]["starting_balance_usdt"]),
        )

    def evaluation(self) -> dict[str, Any]:
        metrics = self.metrics()
        return {
            "experiment_id": self.store.experiment()["experiment_id"],
            "data_origin": metrics["data_origin"],
            "metrics": metrics,
            "arms": metrics["arms"],
            "shadow_leverage": metrics["shadow_leverage"],
            "disclaimer": (
                "Decision quality is not scored until separate future market outcomes are observed. "
                "Fixture results are synthetic mechanics checks, not market-performance evidence."
            ),
        }

    @staticmethod
    def _csv_bytes(rows: list[dict[str, Any]], fields: list[str]) -> bytes:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, sort_keys=True, separators=(",", ":"))
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )
        return stream.getvalue().encode("utf-8")

    def export_bundle(self) -> tuple[bytes, str]:
        experiment = self.store.experiment()
        experiment_id = experiment["experiment_id"]
        records = self.store.export_records(experiment_id)
        history_status = self.store.market_history_status(experiment_id)
        metrics = self.metrics()
        public_providers = self.store.list_providers()
        safe_config = {
            **experiment["config"],
            "providers": public_providers,
            "credential_values_included": False,
            "credential_references_included": False,
        }
        created_at = iso_utc(self._clock().astimezone(timezone.utc))
        files: dict[str, bytes] = {}
        manifest = {
            "schema_version": "paper-futures-export.v1",
            "experiment_id": experiment_id,
            "created_at": created_at,
            "execution_mode": "PAPER",
            "market_data_modes": sorted(
                {
                    cycle["payload"].get("data_origin", "unknown")
                    for cycle in records["cycles"]
                }
            ),
            "contains_provider_credential_values": False,
            "contains_credential_references": False,
            "metric_denominators": metrics["sample_denominators"],
            "reconciliation": metrics["reconciliation"],
            "warmup_lanes": len(history_status["runs"]),
            "warmup_requested_bars": sum(
                row["retrieved_bars"] for row in history_status["runs"]
            ),
            "market_history_lanes": len(history_status["lanes"]),
            "market_history_stored_bars": sum(
                row["stored_bars"] for row in history_status["lanes"]
            ),
        }
        files["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        files["config.json"] = json.dumps(safe_config, indent=2, sort_keys=True).encode("utf-8")
        files["metrics.json"] = json.dumps(metrics, indent=2, sort_keys=True).encode("utf-8")

        closed_trades = [
            position
            for position in records["positions"]
            if position["status"] == "closed" and position["closed_pnl_recorded"]
        ]
        files["trades.csv"] = self._csv_bytes(
            closed_trades,
            [
                "position_id",
                "cycle_id",
                "symbol",
                "market_regime",
                "regime_source",
                "cohort",
                "source_arm",
                "side",
                "opened_quantity",
                "entry_price",
                "exit_price",
                "leverage",
                "entry_fee",
                "exit_fees",
                "funding_paid",
                "slippage_paid",
                "realized_pnl",
                "exit_reason",
                "opened_at",
                "closed_at",
            ],
        )
        files["positions.csv"] = self._csv_bytes(
            records["positions"],
            [
                "position_id",
                "cycle_id",
                "symbol",
                "market_regime",
                "regime_source",
                "cohort",
                "is_shadow",
                "source_arm",
                "side",
                "quantity",
                "opened_quantity",
                "entry_price",
                "mark_price",
                "stop_price",
                "target_price",
                "leverage",
                "margin",
                "liquidation_price",
                "entry_fee",
                "exit_fees",
                "funding_paid",
                "slippage_paid",
                "status",
                "opened_at",
                "closed_at",
                "exit_price",
                "exit_reason",
                "realized_pnl",
            ],
        )
        files["equity.csv"] = self._csv_bytes(
            records["equity"],
            [
                "experiment_id",
                "cohort",
                "as_of",
                "cash_balance",
                "unrealized_pnl",
                "equity",
                "margin_used",
                "available_margin",
                "realized_pnl",
                "data_origin",
            ],
        )
        files["fills.csv"] = self._csv_bytes(
            records["fills"],
            [
                "fill_id",
                "cycle_id",
                "order_id",
                "position_id",
                "cohort",
                "side",
                "quantity",
                "price",
                "fee",
                "slippage_cost",
                "as_of",
            ],
        )
        files["orders.csv"] = self._csv_bytes(
            records["orders"],
            [
                "order_id",
                "cycle_id",
                "position_id",
                "symbol",
                "cohort",
                "side",
                "order_type",
                "reduce_only",
                "quantity",
                "filled_quantity",
                "limit_price",
                "last_checked_bar",
                "status",
                "risk_json",
                "created_at",
            ],
        )
        files["wallets.csv"] = self._csv_bytes(
            records["wallets"],
            [
                "experiment_id",
                "cohort",
                "starting_balance",
                "cash_balance",
            ],
        )

        def group_pnl(key_fn: Callable[[dict[str, Any]], str]) -> list[dict[str, Any]]:
            grouped: dict[tuple[str, str], dict[str, float]] = {}
            for trade in closed_trades:
                key = (key_fn(trade), trade["cohort"])
                row = grouped.setdefault(key, {"closed_trade_count": 0, "net_pnl_usdt": 0.0})
                row["closed_trade_count"] += 1
                row["net_pnl_usdt"] += float(trade["realized_pnl"] or 0)
            return [
                {"group": key, "cohort": cohort, **row}
                for (key, cohort), row in sorted(grouped.items())
            ]

        files["pnl-by-day.csv"] = self._csv_bytes(
            group_pnl(lambda item: str(item["closed_at"])[:10]),
            ["group", "cohort", "closed_trade_count", "net_pnl_usdt"],
        )
        files["pnl-by-asset.csv"] = self._csv_bytes(
            group_pnl(lambda item: item["symbol"]),
            ["group", "cohort", "closed_trade_count", "net_pnl_usdt"],
        )
        files["pnl-by-regime.csv"] = self._csv_bytes(
            group_pnl(lambda item: item.get("market_regime") or "unknown"),
            ["group", "cohort", "closed_trade_count", "net_pnl_usdt"],
        )
        leverage_rows = []
        leverage_groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for trade in closed_trades:
            leverage_groups.setdefault(
                (int(trade["leverage"]), trade["cohort"]), []
            ).append(trade)
        for (leverage, cohort), trades in sorted(leverage_groups.items()):
            leverage_rows.append(
                {
                    "leverage": leverage,
                    "cohort": cohort,
                    "closed_trade_count": len(trades),
                    "net_pnl_usdt": sum(float(item["realized_pnl"] or 0) for item in trades),
                    "data_origin": metrics["data_origin"],
                }
            )
        files["pnl-by-leverage.csv"] = self._csv_bytes(
            leverage_rows,
            ["leverage", "cohort", "closed_trade_count", "net_pnl_usdt", "data_origin"],
        )

        cycle_payloads = [cycle["payload"] for cycle in records["cycles"]]
        jsonl = lambda records: ("\n".join(_json(item) for item in records) + ("\n" if records else "")).encode("utf-8")
        files["decisions.jsonl"] = jsonl(
            [
                {
                    "cycle_id": cycle.get("cycle_id"),
                    "symbol": cycle.get("symbol"),
                    "cycle_slot": cycle.get("cycle_slot"),
                    "data_cutoff": cycle.get("data_cutoff"),
                    "snapshot_hash": cycle.get("snapshot_hash"),
                    "decision_input_hash": cycle.get("decision_input_hash"),
                    "primary_arm": cycle.get("primary_arm"),
                    "primary_decision": cycle.get("primary_decision"),
                    "arms": cycle.get("arms"),
                    "risk": cycle.get("risk"),
                    "executions": cycle.get("executions"),
                    "data_origin": cycle.get("data_origin"),
                }
                for cycle in cycle_payloads
            ]
        )
        files["market-snapshots.jsonl"] = jsonl(
            [
                {
                    "cycle_id": cycle.get("cycle_id"),
                    "symbol": cycle.get("symbol"),
                    "cycle_slot": cycle.get("cycle_slot"),
                    "as_of": cycle.get("as_of"),
                    "data_cutoff": cycle.get("data_cutoff"),
                    "snapshot_hash": cycle.get("snapshot_hash"),
                    "decision_input_hash": cycle.get("decision_input_hash"),
                    "data_origin": cycle.get("data_origin"),
                    "snapshot": cycle.get("market_snapshot"),
                }
                for cycle in cycle_payloads
                if cycle.get("market_snapshot") is not None
            ]
        )
        files["signals.jsonl"] = jsonl(
            [
                {
                    "cycle_id": cycle.get("cycle_id"),
                    "symbol": cycle.get("symbol"),
                    "snapshot_hash": cycle.get("snapshot_hash"),
                    "features": cycle.get("features"),
                    "quant_gate": cycle.get("quant_gate"),
                    "data_origin": cycle.get("data_origin"),
                }
                for cycle in cycle_payloads
            ]
        )
        files["jev-decisions.jsonl"] = jsonl(
            [
                {
                    "cycle_id": cycle.get("cycle_id"),
                    "symbol": cycle.get("symbol"),
                    "snapshot_hash": cycle.get("snapshot_hash"),
                    "decision_vector": cycle.get("jev_vector"),
                }
                for cycle in cycle_payloads
                if cycle.get("jev_vector") is not None
            ]
        )
        files["escalation-events.jsonl"] = jsonl(
            [
                event
                for cycle in cycle_payloads
                for event in cycle.get("escalation_events", [])
            ]
        )
        risk_rows = [
            {
                key: event.get(key)
                for key in (
                    "event_id",
                    "cycle_id",
                    "symbol",
                    "cohort",
                    "status",
                    "code",
                    "reason",
                    "created_at",
                )
            }
            for event in records["risk_events"]
        ]
        files["risk-events.csv"] = self._csv_bytes(
            risk_rows,
            [
                "event_id",
                "cycle_id",
                "symbol",
                "cohort",
                "status",
                "code",
                "reason",
                "created_at",
            ],
        )
        files["ai-usage.csv"] = self._csv_bytes(
            records["ai_calls"],
            [
                "cycle_id",
                "provider_id",
                "provider_kind",
                "path",
                "model",
                "status",
                "error_code",
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "latency_ms",
                "cost_estimate",
                "pricing_version",
                "observed_at",
            ],
        )
        provider_cost_rows = []
        groups: dict[str, list[dict[str, Any]]] = {}
        for call in records["ai_calls"]:
            groups.setdefault(call["provider_id"], []).append(call)
        for provider_id, group in sorted(groups.items()):
            priced = all(
                call["cost_estimate"] is not None
                or call["provider_kind"].startswith("fixture_")
                for call in group
            )
            provider_cost_rows.append(
                {
                    "provider_id": provider_id,
                    "call_count": len(group),
                    "priced_call_count": sum(
                        1 for call in group if call["cost_estimate"] is not None
                    ),
                    "cost_estimate_usd": (
                        sum(float(call["cost_estimate"] or 0) for call in group)
                        if priced
                        else None
                    ),
                    "pricing_versions": sorted(
                        {
                            call["pricing_version"]
                            for call in group
                            if call["pricing_version"] is not None
                        }
                    ),
                }
            )
        files["ai-cost-by-provider.csv"] = self._csv_bytes(
            provider_cost_rows,
            [
                "provider_id",
                "call_count",
                "priced_call_count",
                "cost_estimate_usd",
                "pricing_versions",
            ],
        )
        files["latency.csv"] = self._csv_bytes(
            [
                {
                    "cycle_id": cycle.get("cycle_id"),
                    "symbol": cycle.get("symbol"),
                    "path": "cycle",
                    "latency_ms": cycle.get("cycle_latency_ms"),
                    "as_of": cycle.get("as_of"),
                }
                for cycle in cycle_payloads
            ]
            + [
                {
                    "cycle_id": call["cycle_id"],
                    "symbol": None,
                    "path": call["path"],
                    "latency_ms": call["latency_ms"],
                    "as_of": call["observed_at"],
                }
                for call in records["ai_calls"]
            ],
            ["cycle_id", "symbol", "path", "latency_ms", "as_of"],
        )
        files["market-history.csv"] = self._csv_bytes(
            records["market_history_runs"],
            [
                "run_id",
                "provider_id",
                "symbol",
                "interval",
                "requested_bars",
                "retrieved_bars",
                "inserted_bars",
                "start_time",
                "data_cutoff",
                "data_origin",
                "content_sha256",
                "status",
                "error_code",
                "created_at",
            ],
        )
        files["warmup-bars.jsonl"] = jsonl(records["market_history_bars"])
        ledger = self.store.cost_ledger
        usage_events = ledger.usage_events(experiment_id)
        budget_events = ledger.budget_events(experiment_id)
        cost = self.cost_overview()
        economics = self.economics()
        ledger_fields = [
            "usage_event_id",
            "experiment_id",
            "cycle_id",
            "symbol",
            "arm",
            "provider_id",
            "provider_kind",
            "model",
            "returned_model",
            "reasoning_effort",
            "call_type",
            "started_at",
            "completed_at",
            "latency_ms",
            "status",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "price_id",
            "price_book_version",
            "estimated_cost_usd",
            "billed_cost_usd",
            "cost_status",
            "reservation_id",
            "provider_request_id",
            "provider_response_id",
            "error_code",
            "real_external_call",
            "decision",
        ]
        files["ai-cost-ledger.csv"] = self._csv_bytes(usage_events, ledger_fields)
        files["ai-budget-events.csv"] = self._csv_bytes(
            [
                {**row, "details": _loads(row["details_json"], {})}
                for row in reversed(budget_events)
            ],
            [
                "event_id",
                "cycle_id",
                "provider_id",
                "call_type",
                "event_type",
                "scope",
                "code",
                "limit_action",
                "policy_version",
                "details",
                "created_at",
            ],
        )
        files["ai-budget-reservations.csv"] = self._csv_bytes(
            ledger.reservations(experiment_id),
            [
                "reservation_id",
                "cycle_id",
                "provider_id",
                "provider_kind",
                "call_type",
                "reserved_usd",
                "charged_usd",
                "status",
                "price_id",
                "created_at",
                "settled_at",
            ],
        )
        files["provider-price-book.json"] = json.dumps(
            {
                "schema_version": "ai-price-book.v1",
                "entries": cost["price_book"],
                "coverage": cost["price_coverage"],
                "note": "Append-only versions; unknown price is reported as unavailable, never zero.",
            },
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        files["economic-pnl.csv"] = self._csv_bytes(
            [
                {
                    "trading_currency": economics["trading_currency"],
                    "ai_cost_currency": economics["ai_cost_currency"],
                    **economics["trading"],
                    **{
                        key: value
                        for key, value in economics["ai_cost"].items()
                        if not isinstance(value, dict)
                    },
                    "fx_mode": economics["fx"]["mode"],
                    "usdt_per_usd": economics["fx"].get("usdt_per_usd"),
                    "fx_source": economics["fx"].get("source"),
                    "ai_cost_usdt": economics["ai_cost_usdt"],
                    "net_economic_pnl_usdt": economics["net_economic_pnl_usdt"],
                    "net_economic_unavailable_reason": economics["net_economic_unavailable_reason"],
                    **{
                        key: value
                        for key, value in economics["kpis"].items()
                        if not isinstance(value, dict)
                    },
                }
            ],
            [
                "trading_currency",
                "ai_cost_currency",
                "gross_trading_pnl_usdt",
                "fees_usdt",
                "funding_usdt",
                "slippage_usdt",
                "realized_net_pnl_usdt",
                "unrealized_pnl_usdt",
                "net_trading_pnl_usdt",
                "closed_trades",
                "winning_trades",
                "jev_cost_usd",
                "gpt_cost_usd",
                "total_ai_cost_usd",
                "paid_calls",
                "calls_with_unavailable_cost",
                "complete",
                "gpt_escalation_cost_usd",
                "cost_on_no_trade_usd",
                "fx_mode",
                "usdt_per_usd",
                "fx_source",
                "ai_cost_usdt",
                "net_economic_pnl_usdt",
                "net_economic_unavailable_reason",
                "ai_cost_per_analysis_usd",
                "ai_cost_per_eligible_case_usd",
                "ai_cost_per_trade_usd",
                "ai_cost_per_winning_trade_usd",
                "ai_cost_pct_of_gross_profit",
                "ai_cost_pct_of_net_trading_pnl",
                "net_economic_expectancy_per_trade_usdt",
                "net_economic_return_on_capital",
            ],
        )
        files["economics.json"] = json.dumps(economics, indent=2, sort_keys=True).encode("utf-8")
        files["market-stream-health.csv"] = self._csv_bytes(
            [
                {
                    **{k: row[k] for k in ("health_id", "provider", "event", "state", "observed_at")},
                    "counters": row["payload"].get("counters"),
                    "symbols": row["payload"].get("symbols"),
                    "detail": {
                        key: value
                        for key, value in row["payload"].items()
                        if key not in {"counters", "symbols", "observed_at", "state", "event"}
                    },
                }
                for row in self.store.stream_health()
            ],
            ["health_id", "provider", "event", "state", "observed_at", "counters", "symbols", "detail"],
        )
        provider_validation = [
            {
                "provider_id": provider["provider_id"],
                "kind": provider["kind"],
                "model": provider["model"],
                "reasoning_effort": provider["reasoning_effort"],
                "last_validation_status": provider["last_validation_status"],
                "last_validated_at": provider["last_validated_at"],
                "last_validation_latency_ms": provider["last_validation_latency_ms"],
                "last_validation": provider.get("last_validation"),
            }
            for provider in public_providers
        ]
        files["provider-validation.json"] = json.dumps(
            provider_validation, indent=2, sort_keys=True
        ).encode("utf-8")
        integration = self.store.latest_integration_check()
        files["real-integration-summary.json"] = json.dumps(
            integration
            or {"status": "NOT_RUN", "note": "python3 -m crypto_eval real-integration-check has not been run"},
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        account_syncs = self.store.account_syncs()
        if account_syncs:
            files["gate-account-sync-summary.json"] = json.dumps(
                account_syncs[-20:], indent=2, sort_keys=True
            ).encode("utf-8")
        portfolio_files = self.runtime.portfolio.export_files()

        def csv_rows(rows: list[dict[str, Any]]) -> bytes:
            fields = sorted({key for row in rows for key in row}) if rows else ["empty"]
            return self._csv_bytes(rows, fields)

        files["portfolio-snapshots.csv"] = csv_rows(portfolio_files["portfolio_snapshots"])
        files["spot-wallets.csv"] = csv_rows(portfolio_files["spot_wallets"])
        files["spot-holdings.csv"] = csv_rows(portfolio_files["spot_holdings"])
        files["spot-orders.csv"] = csv_rows(portfolio_files["spot_orders"])
        files["spot-fills.csv"] = csv_rows(portfolio_files["spot_fills"])
        files["position-plans.jsonl"] = jsonl(portfolio_files["position_plans"])
        files["position-replans.jsonl"] = jsonl(portfolio_files["position_replans"])
        files["management-events.jsonl"] = jsonl(portfolio_files["management_events"])
        files["activity.csv"] = csv_rows(portfolio_files["activity"])
        files["attention-events.csv"] = csv_rows(portfolio_files["attention"])
        files["post-trade-reviews.jsonl"] = jsonl(portfolio_files["post_trade_reviews"])
        files["improvement-hypotheses.jsonl"] = jsonl(portfolio_files["hypotheses"])
        files["brain-decisions.jsonl"] = jsonl(portfolio_files["brain_decisions"])
        files["strategy-tournament.csv"] = csv_rows(
            [
                {key: value for key, value in arm.items() if not isinstance(value, (list, dict))}
                for arm in portfolio_files["tournament"]["arms"]
            ]
        )
        files["strategy-tournament.json"] = json.dumps(
            portfolio_files["tournament"], indent=2, sort_keys=True, default=str
        ).encode("utf-8")
        files["safety-events.csv"] = csv_rows(
            [{**{k: v for k, v in event.items() if k != "detail"}, "detail": event["detail"]} for event in portfolio_files["safety_events"]]
        )
        files["execution-plans.jsonl"] = jsonl(portfolio_files["execution_plans"])
        files["market-safety-states.csv"] = csv_rows(portfolio_files["market_safety_states"])
        files["kill-switch.json"] = json.dumps(portfolio_files["kill_switch"], indent=2, sort_keys=True).encode("utf-8")
        files["lifecycle-states.csv"] = csv_rows(portfolio_files["lifecycle_states"])
        files["lifecycle-events.jsonl"] = jsonl(portfolio_files["lifecycle_events"])
        files["lifecycle-benchmarks.jsonl"] = jsonl(portfolio_files["lifecycle_benchmarks"])
        files["portfolio-settings.json"] = json.dumps(
            portfolio_files["settings"], indent=2, sort_keys=True
        ).encode("utf-8")
        manifest["portfolio_os"] = {
            "schema_versions": {
                "portfolio_state": "portfolio-state.v1",
                "unified_position_view": "unified-position-view.v1",
                "paper_order_view": "paper-order-view.v1",
                "position_replan_proposal": "position-replan-proposal.v1",
                "activity_event": "activity-event.v1",
                "attention_event": "attention-event.v1",
                "post_trade_review": "post-trade-review.v1",
                "strategy_tournament": "strategy-tournament.v1",
            },
            "row_counts": {
                key: len(value) for key, value in portfolio_files.items() if isinstance(value, list)
            },
            "spot_real_writes": False,
        }
        manifest["ai_cost"] = {
            "providers": [
                {
                    key: row[key]
                    for key in (
                        "provider_id",
                        "provider_kind",
                        "model",
                        "returned_models",
                        "reasoning_effort",
                        "calls",
                        "estimated_cost_usd",
                        "price_book_versions",
                    )
                }
                for row in cost["providers"]
            ],
            "budget": experiment["config"]["ai_budget"],
            "budget_limit_action": experiment["config"]["ai_budget"]["limit_action"],
            "price_book_versions": cost["price_book_current_versions"],
            "cost_fx_policy": experiment["config"]["cost_fx"],
        }
        manifest["market_provider"] = {
            "mode": experiment["config"]["market_data_mode"],
            "origin": MARKET_DATA_ORIGINS[experiment["config"]["market_data_mode"]],
        }
        manifest["real_integration_status"] = (integration or {}).get("status", "NOT_RUN")
        manifest["gate_live_write_execution"] = "BLOCKED_BY_DESIGN"
        report = [
            "# EXP-001 PAPER futures research export",
            "",
            f"- Generated: {created_at}",
            f"- Data origin: {metrics['data_origin']}",
            "- Execution mode: PAPER; live exchange order endpoints are not present.",
            "- Fixture results, when present, are synthetic mechanics checks only.",
            "- Decision quality is not scored until later, separate outcomes are observed.",
            "",
            "## Reconciled portfolio totals",
            "",
            f"- Starting balance: {metrics['portfolio']['starting_balance_usdt']:.8f} USDT",
            f"- Ending equity: {metrics['portfolio']['ending_equity_usdt']:.8f} USDT",
            f"- Realized PnL: {metrics['portfolio']['realized_pnl_usdt']:.8f} USDT",
            f"- Closed primary trades: {metrics['portfolio']['closed_trade_count']}",
            f"- Equity reconciliation: {metrics['reconciliation']['ending_equity_equals_cash_plus_unrealized']}",
            f"- Closed PnL reconciliation: {metrics['reconciliation']['closed_pnl_matches_closed_trade_ledger']}",
            f"- Exported files: {len(files) + 1}",
            f"- Warm-up lanes: {len(history_status['runs'])}",
            "- Warm-up bars requested: "
            f"{sum(row['retrieved_bars'] for row in history_status['runs'])}",
            "- Total point-in-time bars archived: "
            f"{sum(row['stored_bars'] for row in history_status['lanes'])}",
        ]
        files["summary.md"] = ("\n".join(report) + "\n").encode("utf-8")
        manifest["files"] = sorted([*files, "manifest.json"])
        manifest["file_sha256"] = {
            name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items()) if name != "manifest.json"
        }
        resolver = self.runtime.resolver
        markers: list[bytes] = []
        for provider in self.store.providers_internal():
            if provider.get("kind", "").startswith("fixture_"):
                continue
            value, _source = resolver.resolve(
                secret_id=provider.get("credential_secret"), env_name=provider.get("credential_env")
            )
            if value and len(value) >= 8:
                markers.append(value.encode("utf-8"))
        for account in self.store.list_exchange_accounts():
            internal = self.store.exchange_account_internal(account["account_id"])
            for secret_id in (internal["key_secret_id"], internal["secret_secret_id"]):
                value, _source = resolver.resolve(secret_id=secret_id, env_name=None)
                if value and len(value) >= 8:
                    markers.append(value.encode("utf-8"))
        leaked = sorted({name for name, data in files.items() for marker in markers if marker in data})
        if leaked:
            raise PaperTradingError("export refused: a configured credential value appeared in the bundle")
        manifest["secret_scan"] = {
            "scanned_files": len(files),
            "configured_credentials_checked": len(markers),
            "credential_values_found": False,
        }
        files["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, data in sorted(files.items()):
                bundle.writestr(name, data)
        return archive.getvalue(), f"{experiment_id.lower()}-paper-export.zip"
