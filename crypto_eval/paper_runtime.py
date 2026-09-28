"""Persistent PAPER portfolio, deterministic risk, aligned arms, and scheduler."""

from __future__ import annotations

import csv
import io
import json
import math
import sqlite3
import threading
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .contracts import canonical_json, digest
from .paper_ai import (
    AIProviderError,
    FixtureGPTProvider,
    FixtureJevProvider,
    JevAdapter,
    ResponsesAdapter,
    parse_gpt_intent,
    route_escalation,
)

from .paper_contracts import (
    DEFAULT_SHADOW_LEVERAGE,
    EXPERIMENT_ARMS,
    INTENT_SCHEMA_VERSION,
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
    floor_time,
)


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


def compute_features(
    snapshot: MarketSnapshot,
    *,
    minimum_signal_strength: float = 0.55,
    signal_gate_enabled: bool = True,
) -> dict[str, Any]:
    """Compute deterministic price/volume features from closed bars only."""

    snapshot.validate()
    bars = snapshot.candles_15m
    closes = [float(bar["close"]) for bar in bars]
    volumes = [float(bar["volume"]) for bar in bars]
    last = bars[-1]
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    ema12_1h = _ema([float(bar["close"]) for bar in snapshot.candles_1h], 12)
    ema26_1h = _ema([float(bar["close"]) for bar in snapshot.candles_1h], 26)
    ema12_4h = _ema([float(bar["close"]) for bar in snapshot.candles_4h], 12)
    ema26_4h = _ema([float(bar["close"]) for bar in snapshot.candles_4h], 26)
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
        self._seed()

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
            """
        )

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
            ):
                if name not in provider_columns:
                    self._db.execute(
                        f"ALTER TABLE providers ADD COLUMN {name} {declaration}"
                    )
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
            if cycles and config != current["config"]:
                raise PaperTradingError("an experiment with recorded cycles is frozen; create a new experiment")
            now = iso_utc(datetime.now(timezone.utc))
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
                    "WHERE experiment_id=? AND status='pending'",
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

    def list_providers(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT config_json, last_validation_status, last_validated_at, "
                "last_validation_latency_ms FROM providers ORDER BY provider_id"
            ).fetchall()
        result = []
        for row in rows:
            provider = _loads(row["config_json"], {})
            result.append(
                public_provider_config(
                    provider,
                    credential_present=bool(provider.get("credential_env")),
                    validation={
                        "status": row["last_validation_status"],
                        "validated_at": row["last_validated_at"],
                        "latency_ms": row["last_validation_latency_ms"],
                    },
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
        if isinstance(provider_id, str) and "credential_env" not in provider_value:
            existing_config = self.provider(provider_id)
            if existing_config is not None and not existing_config["kind"].startswith("fixture_"):
                provider_value["credential_env"] = existing_config.get("credential_env")
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
            if cycles and (existing is None or _loads(existing["config_json"]) != provider):
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
            credential_present=bool(provider.get("credential_env")),
            validation=validation,
        )

    def record_provider_validation(
        self,
        provider_id: str,
        *,
        status: str,
        latency_ms: float | None,
        error_code: str | None = None,
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
                "last_validation_latency_ms=?, last_validation_error_code=?, updated_at=? "
                "WHERE provider_id=?",
                (
                    status,
                    iso_utc(datetime.now(timezone.utc)),
                    None if latency_ms is None else float(latency_ms),
                    error_code if status == "failed" else None,
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
    ) -> dict[str, Any]:
        risk = dict(execution["risk"])
        status_row = db.execute(
            "SELECT status FROM experiments WHERE experiment_id=?",
            (experiment_id,),
        ).fetchone()
        if status_row is None or status_row["status"] != "running":
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
        expected_entry = float(intent["entry_price"])
        slippage_cost = quantity * (
            max(0.0, fill_price - expected_entry)
            if intent["side"] == "long"
            else max(0.0, expected_entry - fill_price)
        )
        db.execute(
            "INSERT OR IGNORE INTO positions(position_id, experiment_id, cycle_id, symbol, cohort, "
            "is_shadow, source_arm, side, quantity, opened_quantity, entry_price, mark_price, "
            "stop_price, target_price, leverage, margin, liquidation_price, entry_fee, "
            "entry_fee_remaining, slippage_paid, status, opened_at, last_funding_slot) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (
                position_id,
                experiment_id,
                order["cycle_id"],
                intent["symbol"],
                cohort,
                0 if cohort == "primary" else 1,
                intent["source_arm"],
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
        }

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
            order_id = f"{row['position_id']}:reduce:{as_of}"
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
    ) -> list[dict[str, Any]]:
        """Apply one closed execution bar once; no AI/provider is invoked here."""
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
                    db.execute(
                        "UPDATE positions SET mark_price=? WHERE position_id=? AND status='open'",
                        (c, current["position_id"]),
                    )
                    self._record_equity_locked(
                        db, experiment_id, current["cohort"], close_time, data_origin
                    )
        return completed

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
                f"{position['position_id']}:exit:{as_of}",
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
    ) -> None:
        self.store = store
        self._market_provider_override = market_provider
        self._jev_provider_override = jev_provider
        self._gpt_provider_override = gpt_provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.risk_engine = RiskEngine()
        self._cycle_lock = threading.RLock()

    def _market(self, config: dict[str, Any]) -> FuturesMarketDataProvider:
        if self._market_provider_override is not None:
            return self._market_provider_override
        if config["market_data_mode"] == "binance_usdm":
            return BinanceUsdMFuturesMarketDataProvider()
        return FixtureFuturesMarketDataProvider()

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
            return JevAdapter(provider_config), provider_config
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
            return ResponsesAdapter(provider_config), provider_config
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
        try:
            snapshot = self._market(config).fetch_snapshot(normalized_symbol, current_time).validate()
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
            features = compute_features(
                snapshot,
                minimum_signal_strength=config["minimum_signal_strength"],
                signal_gate_enabled=config["signal_gate_enabled"],
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
            jev_arms_selected = any(
                arm in {"jev", "quant_jev", "hybrid"} for arm in config["evaluation_arms"]
            )
            jev_disabled = jev_arms_selected and not config["jev_enabled"]
            jev_needed = config["jev_enabled"] and jev_arms_selected and (
                features["gate_eligible"]
                or portfolio["reassess_open_position"]
                or config["force_escalation"]
            )
            jev_vector = None
            ai_calls: list[dict[str, Any]] = []
            arm_results: dict[str, dict[str, Any]] = {}
            escalation_events: list[dict[str, Any]] = []
            gpt_cache: dict[tuple[str, bool], tuple[dict[str, Any] | None, str]] = {}
            jev_error = False
            jev_provider_config: dict[str, Any] | None = None
            if jev_needed:
                try:
                    provider, jev_provider_config = self._jev(config)
                    jev_vector = provider.evaluate(snapshot, features, portfolio)
                    if (
                        jev_vector.get("snapshot_hash") != snapshot.snapshot_hash
                        or jev_vector.get("schema_version") != "jev-decision-vector.v1"
                    ):
                        raise AIProviderError("Jev response did not match the frozen snapshot")
                    ai_calls.append(self._call_record(jev_provider_config, "jev", jev_vector))
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
                try:
                    provider, provider_config = self._gpt(config)
                    result = provider.generate_intent(
                        snapshot,
                        features,
                        portfolio,
                        jev_vector=vector,
                        source_arm=arm,
                        include_skill=include_skill,
                    )
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
                            if intent is None:
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
            executions: list[dict[str, Any]] = []
            risk_summary: dict[str, Any]
            if selected_intent is None:
                risk_summary = {
                    "allowed": False,
                    "code": "NO_INTENT",
                    "reason": primary["reason"],
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
                    config,
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
                executions.append(
                    {
                        "cohort": "primary",
                        "risk": primary_risk.to_dict(),
                        "intent": selected_intent.to_dict(),
                        "reference_price": float(features["market_mark_price"]),
                        "slippage_bps": config["slippage_bps"],
                        "fee_rate": config["taker_fee_rate"],
                        "as_of": snapshot.as_of,
                        "data_origin": snapshot.data_origin,
                    }
                )

            def add_arm_execution(arm_name: str, result: dict[str, Any]) -> None:
                raw_intent = result.get("intent")
                if raw_intent is None:
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
                decision = self.risk_engine.evaluate(
                    intent,
                    config,
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
                executions.append(
                    {
                        "cohort": cohort,
                        "risk": decision.to_dict(),
                        "intent": intent.to_dict(),
                        "reference_price": float(features["market_mark_price"]),
                        "slippage_bps": config["slippage_bps"],
                        "fee_rate": config["taker_fee_rate"],
                        "as_of": snapshot.as_of,
                        "data_origin": snapshot.data_origin,
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
                    shadow_config = dict(config)
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
                    executions.append(
                        {
                            "cohort": cohort,
                            "risk": shadow_risk.to_dict(),
                            "intent": selected_intent.to_dict(),
                            "reference_price": float(features["market_mark_price"]),
                            "slippage_bps": config["slippage_bps"],
                            "fee_rate": config["taker_fee_rate"],
                            "as_of": snapshot.as_of,
                            "data_origin": snapshot.data_origin,
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
                "market_snapshot": snapshot.to_dict(),
                "features": features,
                "quant_gate": gate,
                "jev_vector": jev_vector,
                "escalation_events": escalation_events,
                "arms": arm_results,
                "primary_arm": config["primary_arm"],
                "primary_decision": primary,
                "risk": risk_summary,
                "cycle_latency_ms": end_to_end_ms,
                "manual_cycle": bool(manual),
                "live_execution_enabled": False,
            }
            result = self.store.complete_cycle(
                cycle_id,
                payload,
                ai_calls=ai_calls,
                executions=executions,
                risk_events=[],
                data_origin=snapshot.data_origin,
            )
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
            except PaperTradingError:
                self.store.record_runtime_event(
                    experiment["experiment_id"],
                    None,
                    "monitor_error",
                    {"symbol": symbol, "code": "market_data_unavailable"},
                )
                continue
            funding_rates: dict[int, float | None] = {}
            for bar in bars:
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
                    data_origin="FIXTURE"
                    if config["market_data_mode"] == "fixture"
                    else "BINANCE_USDM_PUBLIC",
                )
                events.extend(result)
        return events

    def close_or_reduce(
        self,
        position_id: str,
        *,
        fraction: float = 1.0,
        as_of: datetime | None = None,
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
        market = self._market(config).fetch_snapshot(position["symbol"], current_time)
        mark = float(market.candles_1m[-1]["close"])
        side_sign = -1 if position["side"] == "long" else 1
        fill_price = mark * (1 + side_sign * config["slippage_bps"] / 10_000)
        return self.store.execute_reduce(
            experiment["experiment_id"],
            intent,
            risk,
            mark_price=fill_price,
            fee_rate=config["taker_fee_rate"],
            as_of=iso_utc(current_time),
            data_origin=market.data_origin,
            slippage_cost=abs(fill_price - mark) * float(risk.quantity),
        )

    def test_provider(self, provider_id: str) -> dict[str, Any]:
        provider = self.store.provider(provider_id)
        if provider is None:
            raise PaperTradingError("provider configuration was not found")
        if not provider.get("enabled"):
            raise PaperTradingError("provider is disabled")
        try:
            if provider["kind"] == "fixture_jev":
                result = FixtureJevProvider().test_connection()
            elif provider["kind"] == "fixture_gpt":
                result = FixtureGPTProvider().test_connection()
            elif provider["kind"] == "typesafe_jev":
                result = JevAdapter(provider).test_connection()
            elif provider["kind"] in {
                "openai_responses",
                "foundry_responses",
                "compatible_responses",
            }:
                result = ResponsesAdapter(provider).test_connection()
            else:
                raise PaperTradingError("provider kind is unsupported")
        except PaperTradingError:
            self.store.record_provider_validation(
                provider_id,
                status="failed",
                latency_ms=None,
                error_code="provider_validation_failed",
            )
            raise
        self.store.record_provider_validation(
            provider_id,
            status="passed",
            latency_ms=float(result["latency_ms"]),
        )
        return result

    def start(self) -> dict[str, Any]:
        config = self.store.experiment()["config"]
        if config["market_data_mode"] == "binance_usdm":
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
        if mode != "binance_usdm":
            raise PaperTradingError("market-data mode is unsupported")
        provider = BinanceUsdMFuturesMarketDataProvider()
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

    def metrics(self) -> dict[str, Any]:
        return PaperRuntimeReports(self).metrics()

    def dashboard(self) -> dict[str, Any]:
        return PaperRuntimeReports(self).dashboard()

    def evaluation(self) -> dict[str, Any]:
        return PaperRuntimeReports(self).evaluation()

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
        experiment = self.runtime.store.experiment()
        self.runtime.store.recover_interrupted_cycles(experiment["experiment_id"])
        if experiment["status"] == "running" and experiment["config"]["auto_resume"]:
            self._ensure_workers()
        elif experiment["status"] == "running":
            self.runtime.store.set_status("paused")
            self._ensure_workers()
        else:
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

    def _cycle_loop(self) -> None:
        last_attempted_slot: str | None = None
        while not self._shutdown.wait(self.poll_seconds):
            try:
                experiment = self.runtime.store.experiment()
                if experiment["status"] != "running":
                    continue
                config = experiment["config"]
                now = self.runtime._clock().astimezone(timezone.utc)
                close = floor_time(now, "15m")
                if now < close + timedelta(seconds=config["schedule_delay_seconds"]):
                    continue
                slot = iso_utc(close)
                if slot == last_attempted_slot:
                    continue
                last_attempted_slot = slot
                for symbol in config["symbols"]:
                    if self._shutdown.is_set():
                        break
                    try:
                        self.runtime.run_cycle(symbol, as_of=now, manual=False)
                    except PaperTradingError:
                        continue
            except Exception:
                self._shutdown.wait(self.poll_seconds)

    def _monitor_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                experiment = self.runtime.store.experiment()
                interval = experiment["config"]["monitor_interval_seconds"]
                self.runtime.monitor_once(as_of=self.runtime._clock().astimezone(timezone.utc))
            except Exception:
                interval = 30
            self._shutdown.wait(max(5, int(interval)))

    def shutdown(self, timeout: float = 5.0) -> None:
        self._shutdown.set()
        for worker in (self._cycle_thread, self._monitor_thread):
            if worker is not None and worker.is_alive():
                worker.join(timeout=timeout)


class PaperRuntimeReports:
    """Read-only metrics, dashboard, and export projections over persisted runtime state."""

    def __init__(self, runtime: PaperRuntime) -> None:
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
        blocks = [event for event in records["risk_events"] if event["status"] == "blocked"]
        eligible = sum(
            1
            for cycle in cycles
            if cycle["payload"].get("features", {}).get("gate_eligible") is True
        )
        hybrid_cases = [
            cycle["payload"].get("arms", {}).get("hybrid")
            for cycle in cycles
            if cycle["payload"].get("arms", {}).get("hybrid")
            and cycle["payload"].get("features", {}).get("gate_eligible") is True
        ]
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
        closed_pnl_reconciles = math.isclose(
            closed_trade_pnl, grouped_closed_pnl,
            rel_tol=0,
            abs_tol=1e-8,
        )
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
            "risk_block_count": len(blocks),
            "risk_approval_count": sum(
                1 for event in records["risk_events"] if event["status"] == "approved"
            ),
            "jev_calls": sum(1 for call in calls if call["path"] == "jev"),
            "gpt_calls": sum(1 for call in calls if call["path"].startswith("gpt:")),
            "escalation_count": escalated,
            "escalation_rate": escalated / len(hybrid_cases) if hybrid_cases else None,
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

    def dashboard(self) -> dict[str, Any]:
        experiment = self.store.experiment()
        experiment_id = experiment["experiment_id"]
        config = experiment["config"]
        now = self._clock().astimezone(timezone.utc)
        current_slot = floor_time(now, "15m")
        next_slot = current_slot + timedelta(minutes=15, seconds=config["schedule_delay_seconds"])
        cycles = self.store.list_cycles(experiment_id, limit=30)
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
        }

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
        ]
        files["summary.md"] = ("\n".join(report) + "\n").encode("utf-8")
        manifest["files"] = sorted([*files, "manifest.json"])
        files["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, data in sorted(files.items()):
                bundle.writestr(name, data)
        return archive.getvalue(), f"{experiment_id.lower()}-paper-export.zip"
