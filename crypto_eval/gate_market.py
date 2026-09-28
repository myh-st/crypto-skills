"""Gate.io USDT-margined perpetual futures public market data (REST).

Core contracts stay exchange-neutral (``BTCUSDT``); the Gate contract naming
(``BTC_USDT``) exists only inside this adapter. Only unauthenticated GET requests are
made here. Candles that have not closed by the requested cutoff are never returned.
"""

from __future__ import annotations

import json
import math
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .paper_contracts import iso_utc, parse_utc
from .paper_market import (
    INTERVAL_SECONDS,
    MAX_HISTORY_BARS,
    MarketDataError,
    MarketSnapshot,
    _validate_candle_lane,
    floor_time,
)


GATE_API_BASE_URL = "https://api.gateio.ws/api/v4"
GATE_TESTNET_API_BASE_URL = "https://fx-api-testnet.gateio.ws/api/v4"
GATE_FUTURES_WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
GATE_DATA_ORIGIN = "GATE_USDT_PUBLIC"
GATE_PROVIDER_ID = "gate-usdt-public"
GATE_HOSTS = {"api.gateio.ws", "fx-api-testnet.gateio.ws"}
GATE_PAGE_BARS = 1000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
CHART_INTERVALS = ("1m", "5m", "15m", "1h", "4h")
GATE_INTERVAL_SECONDS = {**INTERVAL_SECONDS, "5m": 300}

HttpGet = Callable[[str, float], bytes]


def to_gate_contract(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{1,16}USDT", normalized):
        raise MarketDataError("Gate USDT perpetual adapter supports *USDT symbols only")
    return f"{normalized[:-4]}_USDT"


def from_gate_contract(contract: str) -> str:
    if not isinstance(contract, str) or not re.fullmatch(r"[A-Z0-9]{1,16}_USDT", contract):
        raise MarketDataError("Gate contract name is malformed")
    return contract.replace("_", "")


def _finite(value: Any, field: str, *, positive: bool = False, allow_none: bool = False) -> float | None:
    if value is None or value == "":
        if allow_none:
            return None
        raise MarketDataError(f"Gate {field} is missing")
    if isinstance(value, bool):
        raise MarketDataError(f"Gate {field} is not a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise MarketDataError(f"Gate {field} is not a finite number") from None
    if not math.isfinite(result) or (positive and result <= 0):
        raise MarketDataError(f"Gate {field} is not a finite number")
    return result


def _http_get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "crypto-skills-paper/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_RESPONSE_BYTES + 1)


def normalize_contract(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
        raise MarketDataError("Gate contract metadata is malformed")
    quanto = _finite(raw.get("quanto_multiplier"), "quanto_multiplier", positive=True)
    return {
        "symbol": from_gate_contract(raw["name"]),
        "exchange_contract": raw["name"],
        "status": raw.get("status") or ("delisting" if raw.get("in_delisting") else "trading"),
        "in_delisting": bool(raw.get("in_delisting")),
        "settle": "USDT",
        "contract_size": quanto,
        "order_size_min_contracts": _finite(raw.get("order_size_min"), "order_size_min", allow_none=True),
        "order_size_max_contracts": _finite(raw.get("order_size_max"), "order_size_max", allow_none=True),
        "min_quantity": (_finite(raw.get("order_size_min"), "order_size_min", allow_none=True) or 1) * quanto,
        "price_tick": _finite(raw.get("order_price_round"), "order_price_round", allow_none=True),
        "mark_price_tick": _finite(raw.get("mark_price_round"), "mark_price_round", allow_none=True),
        "leverage_min": _finite(raw.get("leverage_min"), "leverage_min", allow_none=True),
        "leverage_max": _finite(raw.get("leverage_max"), "leverage_max", allow_none=True),
        "maintenance_rate": _finite(raw.get("maintenance_rate"), "maintenance_rate", allow_none=True),
        "taker_fee_rate": _finite(raw.get("taker_fee_rate"), "taker_fee_rate", allow_none=True),
        "maker_fee_rate": _finite(raw.get("maker_fee_rate"), "maker_fee_rate", allow_none=True),
        "funding_interval_seconds": raw.get("funding_interval"),
        "funding_next_apply": (
            iso_utc(datetime.fromtimestamp(int(raw["funding_next_apply"]), tz=timezone.utc))
            if isinstance(raw.get("funding_next_apply"), int)
            else None
        ),
        "mark_type": raw.get("mark_type"),
    }


def normalize_candle(raw: Any, interval: str, *, contract_size: float) -> dict[str, Any]:
    """Gate REST (t=open seconds) or WS candle → exchange-neutral OHLCV with base volume."""

    if not isinstance(raw, dict):
        raise MarketDataError("Gate candle is malformed")
    seconds = GATE_INTERVAL_SECONDS[interval]
    try:
        opened_ts = int(raw["t"])
    except (KeyError, TypeError, ValueError):
        raise MarketDataError("Gate candle timestamp is malformed") from None
    if opened_ts % seconds:
        raise MarketDataError("Gate candle is not aligned to its interval")
    opened = datetime.fromtimestamp(opened_ts, tz=timezone.utc)
    contracts = _finite(raw.get("v", 0), "candle.volume")
    candle = {
        "open_time": iso_utc(opened),
        "close_time": iso_utc(opened + timedelta(seconds=seconds)),
        "open": _finite(raw.get("o"), "candle.open", positive=True),
        "high": _finite(raw.get("h"), "candle.high", positive=True),
        "low": _finite(raw.get("l"), "candle.low", positive=True),
        "close": _finite(raw.get("c"), "candle.close", positive=True),
        "volume": contracts * contract_size,
    }
    quote = raw.get("sum", raw.get("a"))
    if quote not in (None, ""):
        candle["quote_volume"] = _finite(quote, "candle.quote_volume")
    if (
        candle["high"] < max(candle["open"], candle["close"], candle["low"])
        or candle["low"] > min(candle["open"], candle["close"], candle["high"])
        or candle["volume"] < 0
    ):
        raise MarketDataError("Gate candle values are inconsistent")
    return candle


def normalize_ticker(raw: Any, *, observed_at: datetime) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("contract"), str):
        raise MarketDataError("Gate ticker is malformed")
    bid = _finite(raw.get("highest_bid"), "highest_bid", positive=True, allow_none=True)
    ask = _finite(raw.get("lowest_ask"), "lowest_ask", positive=True, allow_none=True)
    return {
        "symbol": from_gate_contract(raw["contract"]),
        "observed_at": iso_utc(observed_at),
        "last_price": _finite(raw.get("last"), "last", positive=True),
        "mark_price": _finite(raw.get("mark_price"), "mark_price", positive=True, allow_none=True),
        "index_price": _finite(raw.get("index_price"), "index_price", positive=True, allow_none=True),
        "funding_rate": _finite(raw.get("funding_rate"), "funding_rate", allow_none=True),
        "funding_rate_indicative": _finite(
            raw.get("funding_rate_indicative"), "funding_rate_indicative", allow_none=True
        ),
        "change_24h_pct": _finite(raw.get("change_percentage"), "change_percentage", allow_none=True),
        "volume_24h_quote": _finite(raw.get("volume_24h_quote"), "volume_24h_quote", allow_none=True),
        "volume_24h_base": _finite(raw.get("volume_24h_base"), "volume_24h_base", allow_none=True),
        "high_24h": _finite(raw.get("high_24h"), "high_24h", allow_none=True),
        "low_24h": _finite(raw.get("low_24h"), "low_24h", allow_none=True),
        "open_interest_contracts": _finite(raw.get("total_size"), "total_size", allow_none=True),
        "best_bid": bid,
        "best_ask": ask,
    }


def normalize_book_ticker(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("s"), str):
        raise MarketDataError("Gate book ticker is malformed")
    bid = _finite(raw.get("b"), "book.bid", positive=True)
    ask = _finite(raw.get("a"), "book.ask", positive=True)
    if ask < bid:
        raise MarketDataError("Gate book ticker is crossed")
    try:
        observed = datetime.fromtimestamp(int(raw["t"]) / 1000, tz=timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        raise MarketDataError("Gate book ticker timestamp is malformed") from None
    mid = (bid + ask) / 2
    return {
        "symbol": from_gate_contract(raw["s"]),
        "observed_at": iso_utc(observed),
        "update_id": raw.get("u"),
        "best_bid": bid,
        "best_bid_size": _finite(raw.get("B"), "book.bid_size", allow_none=True),
        "best_ask": ask,
        "best_ask_size": _finite(raw.get("A"), "book.ask_size", allow_none=True),
        "mid_price": mid,
        "spread_bps": (ask - bid) / mid * 10_000,
    }


OHLCV_KEYS = ("open_time", "close_time", "open", "high", "low", "close", "volume")


def ohlcv(candle: dict[str, Any]) -> dict[str, Any]:
    """Core candle contract used by snapshots, warm-up archives, and the monitor."""

    return {key: candle[key] for key in OHLCV_KEYS}


def spread_bps(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 10_000


class GateUsdtFuturesMarketDataProvider:
    """Unauthenticated Gate USDT perpetual REST adapter implementing FuturesMarketDataProvider."""

    provider_id = GATE_PROVIDER_ID
    data_origin = GATE_DATA_ORIGIN

    def __init__(
        self,
        *,
        base_url: str = GATE_API_BASE_URL,
        timeout: float = 10.0,
        transport: HttpGet | None = None,
        clock: Callable[[], datetime] | None = None,
        live_state: Any | None = None,
    ) -> None:
        if not 1 <= timeout <= 60:
            raise MarketDataError("public market-data timeout must be between 1 and 60 seconds")
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "https" or parsed.hostname not in GATE_HOSTS:
            raise MarketDataError("public futures endpoint must use an approved Gate host")
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self._transport = transport or _http_get
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._contracts: dict[str, dict[str, Any]] = {}
        self._contracts_at = 0.0
        self._lock = threading.Lock()
        self.live_state = live_state
        self.request_count = 0

    # ---- transport -------------------------------------------------
    def _json_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        self.request_count += 1
        try:
            response = self._transport(url, self.timeout)
        except urllib.error.HTTPError as exc:
            raise MarketDataError(f"Gate market-data request returned HTTP {exc.code}") from None
        except (TimeoutError, socket.timeout):
            raise MarketDataError("Gate market-data request timed out") from None
        except (urllib.error.URLError, OSError):
            raise MarketDataError("Gate market-data request failed") from None
        except Exception:
            raise MarketDataError("Gate market-data request failed") from None
        if not isinstance(response, (bytes, bytearray)) or len(response) > MAX_RESPONSE_BYTES:
            raise MarketDataError("Gate market-data response is invalid")
        try:
            return json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MarketDataError("Gate market-data response is invalid JSON") from None

    # ---- metadata --------------------------------------------------
    def contracts(self, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._contracts and not refresh and time.monotonic() - self._contracts_at < 900:
                return self._contracts
        rows = self._json_get("/futures/usdt/contracts")
        if not isinstance(rows, list):
            raise MarketDataError("Gate contract metadata is malformed")
        contracts: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                item = normalize_contract(row)
            except MarketDataError:
                continue
            contracts[item["symbol"]] = item
        if not contracts:
            raise MarketDataError("Gate contract metadata is empty")
        with self._lock:
            self._contracts = contracts
            self._contracts_at = time.monotonic()
        return contracts

    def contract(self, symbol: str) -> dict[str, Any]:
        normalized = symbol.strip().upper()
        contracts = self.contracts()
        if normalized not in contracts:
            raise MarketDataError("symbol is not a Gate USDT perpetual contract")
        return contracts[normalized]

    def validate_symbols(self, symbols: list[str]) -> dict[str, Any]:
        contracts = self.contracts(refresh=True)
        accepted: list[str] = []
        excluded: list[dict[str, str]] = []
        for symbol in dict.fromkeys(value.strip().upper() for value in symbols):
            item = contracts.get(symbol)
            if item is not None and item["status"] == "trading" and not item["in_delisting"]:
                accepted.append(symbol)
            else:
                excluded.append(
                    {"symbol": symbol, "reason": "not an active Gate USDT perpetual in live contract metadata"}
                )
        return {"supported": accepted, "excluded": excluded}

    # ---- candles ---------------------------------------------------
    def _candles_range(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Closed candles with open_time in [start, end); paged, contiguous, de-duplicated."""

        contract = self.contract(symbol)
        step = GATE_INTERVAL_SECONDS[interval]
        cursor = start
        result: dict[str, dict[str, Any]] = {}
        while cursor < end:
            page_end = min(end, cursor + timedelta(seconds=step * GATE_PAGE_BARS))
            rows = self._json_get(
                "/futures/usdt/candlesticks",
                {
                    "contract": contract["exchange_contract"],
                    "interval": interval,
                    "from": int(cursor.timestamp()),
                    "to": int(page_end.timestamp()) - step,
                },
            )
            if not isinstance(rows, list):
                raise MarketDataError("Gate candlestick response must be an array")
            for row in rows:
                candle = normalize_candle(row, interval, contract_size=contract["contract_size"])
                opened = parse_utc(candle["open_time"], "candle.open_time")
                if cursor <= opened < page_end:
                    result[candle["open_time"]] = candle
            cursor = page_end
        return [result[key] for key in sorted(result)]

    def fetch_candles(
        self, symbol: str, interval: str, *, bars: int, as_of: datetime
    ) -> list[dict[str, Any]]:
        if interval not in GATE_INTERVAL_SECONDS:
            raise MarketDataError("unsupported Gate candle interval")
        cutoff = _floor(as_of, interval)
        start = cutoff - timedelta(seconds=bars * GATE_INTERVAL_SECONDS[interval])
        return self._candles_range(symbol, interval, start, cutoff)

    def fetch_history(
        self,
        symbol: str,
        interval: str,
        *,
        bars: int,
        as_of: datetime,
    ) -> list[dict[str, Any]]:
        if interval not in INTERVAL_SECONDS:
            raise MarketDataError("unsupported futures history interval")
        if isinstance(bars, bool) or not isinstance(bars, int) or not 1 <= bars <= MAX_HISTORY_BARS:
            raise MarketDataError("history bars must be between 1 and 20000")
        cutoff = floor_time(as_of, interval)
        candles = [ohlcv(c) for c in self.fetch_candles(symbol, interval, bars=bars, as_of=as_of)]
        if len(candles) != bars:
            raise MarketDataError(f"futures {interval} warm-up history is incomplete")
        return _validate_candle_lane(candles, interval, cutoff, minimum=bars)

    def fetch_monitor_bars(
        self, symbol: str, after: datetime, as_of: datetime
    ) -> list[dict[str, Any]]:
        cutoff = floor_time(as_of, "1m")
        cursor = floor_time(after, "1m")
        if cursor < after:
            cursor += timedelta(minutes=1)
        if cutoff <= cursor:
            return []
        expected = int((cutoff - cursor).total_seconds() // 60)
        if expected > MAX_HISTORY_BARS:
            raise MarketDataError("monitor recovery gap exceeds the supported 20000 closed bars")
        candles = [ohlcv(c) for c in self._candles_range(symbol, "1m", cursor, cutoff)]
        if len(candles) != expected:
            raise MarketDataError("monitor recovery contains an incomplete one-minute path")
        return _validate_candle_lane(candles, "1m", cutoff, minimum=expected)

    # ---- tickers / funding / OI ------------------------------------
    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        contract = self.contract(symbol)
        rows = self._json_get("/futures/usdt/tickers", {"contract": contract["exchange_contract"]})
        if not isinstance(rows, list) or not rows:
            raise MarketDataError("Gate ticker response is empty")
        return normalize_ticker(rows[0], observed_at=self._clock())

    def fetch_book_top(self, symbol: str) -> dict[str, Any]:
        contract = self.contract(symbol)
        book = self._json_get(
            "/futures/usdt/order_book",
            {"contract": contract["exchange_contract"], "limit": 1, "with_id": "true"},
        )
        if not isinstance(book, dict) or not book.get("bids") or not book.get("asks"):
            raise MarketDataError("Gate order book response is malformed")
        try:
            observed_ms = int(float(book.get("current")) * 1000)
            raw = {
                "t": observed_ms,
                "s": contract["exchange_contract"],
                "b": book["bids"][0]["p"],
                "B": book["bids"][0]["s"],
                "a": book["asks"][0]["p"],
                "A": book["asks"][0]["s"],
                "u": book.get("id"),
            }
        except (KeyError, IndexError, TypeError, ValueError):
            raise MarketDataError("Gate order book response is malformed") from None
        return normalize_book_ticker(raw)

    def fetch_funding_history(self, symbol: str, *, limit: int = 10) -> list[dict[str, Any]]:
        contract = self.contract(symbol)
        rows = self._json_get(
            "/futures/usdt/funding_rate", {"contract": contract["exchange_contract"], "limit": limit}
        )
        if not isinstance(rows, list):
            raise MarketDataError("Gate funding history is malformed")
        result = []
        for row in rows:
            try:
                result.append(
                    {
                        "observed_at": iso_utc(datetime.fromtimestamp(int(row["t"]), tz=timezone.utc)),
                        "funding_rate": _finite(row["r"], "funding_rate"),
                    }
                )
            except (KeyError, TypeError, ValueError, MarketDataError):
                continue
        return sorted(result, key=lambda item: item["observed_at"])

    def fetch_funding_rate(self, symbol: str, as_of: datetime) -> float | None:
        try:
            history = self.fetch_funding_history(symbol, limit=10)
        except MarketDataError:
            return None
        moment = as_of.astimezone(timezone.utc)
        eligible = [
            item for item in history if parse_utc(item["observed_at"], "funding.observed_at") <= moment
        ]
        if not eligible:
            return None
        latest = eligible[-1]
        if (moment - parse_utc(latest["observed_at"], "funding.observed_at")).total_seconds() > 12 * 3600:
            return None
        return latest["funding_rate"]

    def _open_interest_change(self, symbol: str, as_of: datetime) -> tuple[float | None, str | None]:
        contract = self.contract(symbol)
        try:
            rows = self._json_get(
                "/futures/usdt/contract_stats",
                {"contract": contract["exchange_contract"], "interval": "1h", "limit": 4},
            )
        except MarketDataError:
            return None, None
        if not isinstance(rows, list):
            return None, None
        completed = []
        for row in rows:
            try:
                start = int(row["time"])
                value = float(row["open_interest"])
            except (KeyError, TypeError, ValueError):
                continue
            if start + 3600 <= as_of.timestamp() and math.isfinite(value) and value > 0:
                completed.append((start, value))
        completed.sort()
        if len(completed) < 2:
            return None, None
        observed = datetime.fromtimestamp(completed[-1][0], tz=timezone.utc)
        if (as_of - observed).total_seconds() > 3 * 3600:
            return None, None
        return completed[-1][1] / completed[-2][1] - 1, iso_utc(observed)

    # ---- snapshot --------------------------------------------------
    def fetch_snapshot(self, symbol: str, as_of: datetime) -> MarketSnapshot:
        normalized = symbol.strip().upper()
        as_of = as_of.astimezone(timezone.utc)
        contract = self.contract(normalized)
        cutoff = floor_time(as_of, "15m")
        candles_15m = [ohlcv(c) for c in self.fetch_candles(normalized, "15m", bars=64, as_of=cutoff)]
        candles_1h = [ohlcv(c) for c in self.fetch_candles(normalized, "1h", bars=48, as_of=cutoff)]
        candles_4h = [ohlcv(c) for c in self.fetch_candles(normalized, "4h", bars=36, as_of=cutoff)]
        candles_1m = [ohlcv(c) for c in self.fetch_candles(normalized, "1m", bars=60, as_of=as_of)]
        funding_rate = None
        funding_observed_at = None
        try:
            history = self.fetch_funding_history(normalized, limit=3)
            eligible = [
                item for item in history if parse_utc(item["observed_at"], "funding.observed_at") <= as_of
            ]
            if eligible and (
                as_of - parse_utc(eligible[-1]["observed_at"], "funding.observed_at")
            ).total_seconds() <= 12 * 3600:
                funding_rate = eligible[-1]["funding_rate"]
                funding_observed_at = eligible[-1]["observed_at"]
        except MarketDataError:
            pass
        oi_change, oi_observed = self._open_interest_change(normalized, as_of)
        context = self.market_context(normalized, contract)
        snapshot = MarketSnapshot(
            symbol=normalized,
            as_of=iso_utc(as_of),
            data_cutoff=iso_utc(as_of),
            data_origin=GATE_DATA_ORIGIN,
            candles_15m=candles_15m,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
            candles_1m=candles_1m,
            funding_rate=funding_rate,
            funding_observed_at=funding_observed_at,
            open_interest_change_1h=oi_change,
            open_interest_observed_at=oi_observed,
            spread_bps=context.get("spread_bps"),
            market_context=context,
        )
        return snapshot.validate()

    def market_context(self, symbol: str, contract: dict[str, Any] | None = None) -> dict[str, Any]:
        """Latest ticker/book context. Prefers the live WS state; falls back to REST."""

        contract = contract or self.contract(symbol)
        live = None
        if self.live_state is not None:
            try:
                live = self.live_state.symbol_state(symbol)
            except Exception:
                live = None
        if live and live.get("fresh") and live.get("book") and live.get("ticker"):
            book = live["book"]
            ticker = live["ticker"]
            source = "gate_ws"
        else:
            ticker = self.fetch_ticker(symbol)
            book = self.fetch_book_top(symbol)
            source = "gate_rest"
        return {
            "source": source,
            "exchange": "gate",
            "exchange_contract": contract["exchange_contract"],
            "last_price": ticker.get("last_price"),
            "mark_price": ticker.get("mark_price"),
            "index_price": ticker.get("index_price"),
            "ticker_observed_at": ticker.get("observed_at"),
            "best_bid": book.get("best_bid"),
            "best_ask": book.get("best_ask"),
            "book_observed_at": book.get("observed_at"),
            "spread_bps": book.get("spread_bps"),
            "funding_rate_current": ticker.get("funding_rate"),
            "change_24h_pct": ticker.get("change_24h_pct"),
            "volume_24h_quote": ticker.get("volume_24h_quote"),
            "contract_size": contract["contract_size"],
            "min_quantity": contract["min_quantity"],
            "price_tick": contract["price_tick"],
            "maintenance_rate": contract["maintenance_rate"],
            "leverage_max": contract["leverage_max"],
            "taker_fee_rate": contract["taker_fee_rate"],
            "maker_fee_rate": contract["maker_fee_rate"],
        }


def _floor(value: datetime, interval: str) -> datetime:
    seconds = GATE_INTERVAL_SECONDS[interval]
    timestamp = int(value.astimezone(timezone.utc).timestamp())
    return datetime.fromtimestamp(timestamp - timestamp % seconds, tz=timezone.utc)
