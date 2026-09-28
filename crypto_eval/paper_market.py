"""Public USD-M futures data and reproducible, explicitly synthetic candles."""

from __future__ import annotations

import json
import math
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from .contracts import digest
from .paper_contracts import PaperTradingError, iso_utc, parse_utc


BINANCE_USDM_BASE_URL = "https://fapi.binance.com"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_HISTORY_BARS = 20_000
HISTORY_PAGE_SIZE = 1000
INTERVAL_SECONDS = {"1m": 60, "15m": 900, "1h": 3600, "4h": 14400}
WARMUP_PROFILES = {
    "EXP-001": {
        "1m": 7 * 24 * 60,
        "15m": 60 * 24 * 4,
        "1h": 90 * 24,
        "4h": 180 * 6,
    }
}
SYMBOL_BASE_PRICES = {
    "BTCUSDT": 62_000.0,
    "ETHUSDT": 3_100.0,
    "SOLUSDT": 145.0,
    "SUIUSDT": 1.25,
    "SEIUSDT": 0.34,
}


class MarketDataError(PaperTradingError):
    """Market data could not satisfy the PAPER runtime's point-in-time contract."""


HttpGet = Callable[[str, float], bytes]


def _http_get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "crypto-skills-paper/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_RESPONSE_BYTES + 1)


def floor_time(value: datetime, interval: str) -> datetime:
    if interval not in INTERVAL_SECONDS:
        raise MarketDataError("unsupported futures candle interval")
    seconds = INTERVAL_SECONDS[interval]
    timestamp = int(value.astimezone(timezone.utc).timestamp())
    return datetime.fromtimestamp(timestamp - timestamp % seconds, tz=timezone.utc)


def _finite(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise MarketDataError(f"{field} is not a valid finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise MarketDataError(f"{field} is not a valid finite number") from None
    if not math.isfinite(result) or (positive and result <= 0):
        raise MarketDataError(f"{field} is not a valid finite number")
    return result


def _normalize_candle(
    raw: Any,
    interval: str,
    cutoff: datetime,
    *,
    symbol: str,
) -> dict[str, Any]:
    seconds = INTERVAL_SECONDS[interval]
    if not isinstance(raw, list) or len(raw) < 7:
        raise MarketDataError(f"Binance USD-M {interval} response is malformed")
    try:
        opened_ms = int(raw[0])
        exchange_closed_ms = int(raw[6])
        opened = datetime.fromtimestamp(opened_ms / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        raise MarketDataError(f"Binance USD-M {interval} timestamp is malformed") from None
    expected_closed_ms = opened_ms + seconds * 1000 - 1
    if abs(exchange_closed_ms - expected_closed_ms) > 2:
        raise MarketDataError(f"Binance USD-M {interval} candle interval is inconsistent")
    closed = opened + timedelta(seconds=seconds)
    if closed > cutoff:
        raise MarketDataError(f"Binance USD-M {interval} candle is newer than the data cutoff")
    candle = {
        "open_time": iso_utc(opened),
        "close_time": iso_utc(closed),
        "open": _finite(raw[1], "candle.open", positive=True),
        "high": _finite(raw[2], "candle.high", positive=True),
        "low": _finite(raw[3], "candle.low", positive=True),
        "close": _finite(raw[4], "candle.close", positive=True),
        "volume": _finite(raw[5], "candle.volume"),
    }
    if (
        candle["high"] < max(candle["open"], candle["close"], candle["low"])
        or candle["low"] > min(candle["open"], candle["close"], candle["high"])
        or candle["volume"] < 0
    ):
        raise MarketDataError(f"Binance USD-M {interval} candle values are inconsistent")
    return candle


def _validate_candle_lane(
    candles: list[dict[str, Any]],
    interval: str,
    cutoff: datetime,
    *,
    minimum: int,
) -> list[dict[str, Any]]:
    if len(candles) < minimum:
        raise MarketDataError(f"futures {interval} history is incomplete")
    seconds = INTERVAL_SECONDS[interval]
    prior_close: datetime | None = None
    for candle in candles:
        close_time = parse_utc(candle["close_time"], "candle.close_time")
        open_time = parse_utc(candle["open_time"], "candle.open_time")
        if close_time > cutoff:
            raise MarketDataError(f"futures {interval} history exceeds its cutoff")
        if (close_time - open_time).total_seconds() != seconds:
            raise MarketDataError(f"futures {interval} candle interval is inconsistent")
        if prior_close is not None and open_time != prior_close:
            raise MarketDataError(f"futures {interval} history contains missing or duplicate bars")
        prior_close = close_time
    return candles


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    as_of: str
    data_cutoff: str
    data_origin: str
    candles_15m: list[dict[str, Any]]
    candles_1h: list[dict[str, Any]]
    candles_4h: list[dict[str, Any]]
    candles_1m: list[dict[str, Any]]
    funding_rate: float | None
    funding_observed_at: str | None
    open_interest_change_1h: float | None
    open_interest_observed_at: str | None
    spread_bps: float | None
    market_context: dict[str, Any] | None = None

    def validate(self) -> "MarketSnapshot":
        if not isinstance(self.symbol, str) or not self.symbol.isalnum() or self.symbol != self.symbol.upper():
            raise MarketDataError("snapshot symbol must be an uppercase contract symbol")
        as_of = parse_utc(self.as_of, "snapshot.as_of")
        cutoff = parse_utc(self.data_cutoff, "snapshot.data_cutoff")
        if cutoff > as_of:
            raise MarketDataError("snapshot data_cutoff cannot be later than as_of")
        if self.data_origin not in {"FIXTURE", "BINANCE_USDM_PUBLIC", "GATE_USDT_PUBLIC"}:
            raise MarketDataError("snapshot data origin is unsupported")
        for lane, interval, minimum in (
            (self.candles_15m, "15m", 30),
            (self.candles_1h, "1h", 20),
            (self.candles_4h, "4h", 12),
            (self.candles_1m, "1m", 1),
        ):
            _validate_candle_lane(lane, interval, cutoff, minimum=minimum)
        for field, observed in (
            ("funding_rate", self.funding_observed_at),
            ("open_interest_change_1h", self.open_interest_observed_at),
        ):
            value = getattr(self, field)
            if value is not None and (isinstance(value, bool) or not math.isfinite(value)):
                raise MarketDataError(f"snapshot {field} must be finite when available")
            if value is None and observed is not None:
                raise MarketDataError(f"snapshot {field} timestamp has no associated value")
            if value is not None and observed is None:
                raise MarketDataError(f"snapshot {field} requires its observation timestamp")
            if observed is not None and parse_utc(observed, f"snapshot.{field}.observed_at") > cutoff:
                raise MarketDataError(f"snapshot {field} observation is newer than the data cutoff")
            if observed is not None:
                age = (cutoff - parse_utc(observed, f"snapshot.{field}.observed_at")).total_seconds()
                max_age = 12 * 60 * 60 if field == "funding_rate" else 3 * 60 * 60
                if age > max_age:
                    raise MarketDataError(f"snapshot {field} observation is stale")
        if self.spread_bps is not None and (
            isinstance(self.spread_bps, bool)
            or not math.isfinite(self.spread_bps)
            or self.spread_bps < 0
        ):
            raise MarketDataError("snapshot spread_bps must be a finite non-negative number")
        return self

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if value.get("market_context") is None:
            # Keeps hashes of snapshots without live context identical to earlier versions.
            value.pop("market_context", None)
        return value

    @property
    def snapshot_hash(self) -> str:
        return digest(self.to_dict())


class FuturesMarketDataProvider(Protocol):
    provider_id: str

    def validate_symbols(self, symbols: list[str]) -> dict[str, Any]: ...

    def fetch_snapshot(self, symbol: str, as_of: datetime) -> MarketSnapshot: ...

    def fetch_funding_rate(self, symbol: str, as_of: datetime) -> float | None: ...

    def fetch_history(
        self, symbol: str, interval: str, *, bars: int, as_of: datetime
    ) -> list[dict[str, Any]]: ...

    def fetch_monitor_bars(
        self, symbol: str, after: datetime, as_of: datetime
    ) -> list[dict[str, Any]]: ...


class FixtureFuturesMarketDataProvider:
    """Seeded synthetic OHLCV feed. All returned data is labeled FIXTURE."""

    provider_id = "deterministic-paper-fixture"

    def __init__(self, *, future_path: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.future_path = future_path or {}

    @staticmethod
    def _base(symbol: str) -> float:
        if symbol in SYMBOL_BASE_PRICES:
            return SYMBOL_BASE_PRICES[symbol]
        return 10.0 + sum(ord(char) for char in symbol) / 100

    @classmethod
    def _lane(
        cls,
        symbol: str,
        interval: str,
        count: int,
        end: datetime,
        *,
        breakout: bool = False,
    ) -> list[dict[str, Any]]:
        seconds = INTERVAL_SECONDS[interval]
        base = cls._base(symbol)
        offset = sum(ord(char) for char in symbol) % 29
        series: list[dict[str, Any]] = []
        previous = base * (1 + offset * 0.0002)
        end_boundary = floor_time(end, interval)
        for index in range(count):
            close_time = end_boundary - timedelta(seconds=(count - index - 1) * seconds)
            open_price = previous
            drift = 0.00035 + ((index + offset) % 7 - 3) * 0.00003
            close_price = open_price * (1 + drift)
            if breakout and index == count - 1:
                prior_high = max(item["high"] for item in series[-20:])
                close_price = prior_high * 1.002
            high = max(open_price, close_price) * 1.0008
            low = min(open_price, close_price) * 0.9992
            volume = 90.0 + ((index * 11 + offset) % 17)
            if breakout and index == count - 1:
                volume *= 2.2
            series.append(
                {
                    "open_time": iso_utc(close_time - timedelta(seconds=seconds)),
                    "close_time": iso_utc(close_time),
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close_price,
                    "volume": volume,
                }
            )
            previous = close_price
        return series

    def fetch_snapshot(self, symbol: str, as_of: datetime) -> MarketSnapshot:
        symbol = symbol.strip().upper()
        cutoff = floor_time(as_of, "15m")
        candles_15m = self._lane(symbol, "15m", 64, cutoff, breakout=True)
        last_close = candles_15m[-1]["close"]
        one_minute = self._lane(symbol, "1m", 60, floor_time(as_of, "1m"))
        factor = last_close / one_minute[-1]["close"]
        for bar in one_minute:
            for field in ("open", "high", "low", "close"):
                bar[field] *= factor
        snapshot = MarketSnapshot(
            symbol=symbol,
            as_of=iso_utc(as_of),
            data_cutoff=iso_utc(as_of),
            data_origin="FIXTURE",
            candles_15m=candles_15m,
            candles_1h=self._lane(symbol, "1h", 48, cutoff),
            candles_4h=self._lane(symbol, "4h", 36, cutoff),
            candles_1m=one_minute,
            funding_rate=0.0001,
            funding_observed_at=iso_utc(cutoff),
            open_interest_change_1h=0.015,
            open_interest_observed_at=iso_utc(cutoff),
            spread_bps=2.0,
        )
        return snapshot.validate()

    def validate_symbols(self, symbols: list[str]) -> dict[str, Any]:
        return {
            "supported": list(dict.fromkeys(symbol.upper() for symbol in symbols)),
            "excluded": [],
        }

    def fetch_monitor_bars(
        self, symbol: str, after: datetime, as_of: datetime
    ) -> list[dict[str, Any]]:
        queued = self.future_path.get(symbol, [])
        if queued:
            return [
                dict(bar)
                for bar in queued
                if parse_utc(bar["close_time"], "fixture.future.close_time") > after
                and parse_utc(bar["close_time"], "fixture.future.close_time") <= as_of
            ]
        current = floor_time(as_of, "1m")
        first = floor_time(after, "1m") + timedelta(minutes=1)
        if first > current:
            return []
        candles: list[dict[str, Any]] = []
        previous = self._lane(symbol, "15m", 64, floor_time(after, "15m"), breakout=True)[-1]["close"]
        steps = int((current - first).total_seconds() // 60) + 1
        for index in range(steps):
            close_time = first + timedelta(minutes=index)
            opened = previous
            close = opened * (1 + (0.0001 if index % 3 else -0.00005))
            candles.append(
                {
                    "open_time": iso_utc(close_time - timedelta(minutes=1)),
                    "close_time": iso_utc(close_time),
                    "open": opened,
                    "high": max(opened, close) * 1.0002,
                    "low": min(opened, close) * 0.9998,
                    "close": close,
                    "volume": 3.0,
                }
            )
            previous = close
        return candles

    def fetch_funding_rate(self, symbol: str, as_of: datetime) -> float | None:
        return 0.0001

    def fetch_history(
        self,
        symbol: str,
        interval: str,
        *,
        bars: int,
        as_of: datetime,
    ) -> list[dict[str, Any]]:
        if interval not in INTERVAL_SECONDS:
            raise MarketDataError("unsupported fixture history interval")
        if (
            isinstance(bars, bool)
            or not isinstance(bars, int)
            or not 1 <= bars <= MAX_HISTORY_BARS
        ):
            raise MarketDataError("history bars must be between 1 and 20000")
        cutoff = floor_time(as_of, interval)
        return self._lane(symbol.strip().upper(), interval, bars, cutoff)


class BinanceUsdMFuturesMarketDataProvider:
    """Unauthenticated Binance USD-M futures read-only market-data adapter."""

    provider_id = "binance-usdm-public"

    def __init__(
        self,
        *,
        base_url: str = BINANCE_USDM_BASE_URL,
        timeout: float = 8.0,
        transport: HttpGet | None = None,
    ) -> None:
        if not 1 <= timeout <= 60:
            raise MarketDataError("public market-data timeout must be between 1 and 60 seconds")
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "https" or parsed.hostname != "fapi.binance.com":
            raise MarketDataError("public futures endpoint must use an approved Binance USD-M host")
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self._transport = transport or _http_get

    def _json_get(self, path: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}?{query}"
        try:
            response = self._transport(url, self.timeout)
        except urllib.error.HTTPError as exc:
            raise MarketDataError(f"Binance USD-M market-data request returned HTTP {exc.code}") from None
        except (TimeoutError, socket.timeout):
            raise MarketDataError("Binance USD-M market-data request timed out") from None
        except (urllib.error.URLError, OSError):
            raise MarketDataError("Binance USD-M market-data request failed") from None
        except Exception:
            raise MarketDataError("Binance USD-M market-data request failed") from None
        if not isinstance(response, (bytes, bytearray)) or len(response) > MAX_RESPONSE_BYTES:
            raise MarketDataError("Binance USD-M market-data response is invalid")
        try:
            return json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MarketDataError("Binance USD-M market-data response is invalid JSON") from None

    def validate_symbols(self, symbols: list[str]) -> dict[str, Any]:
        exchange_info = self._json_get("/fapi/v1/exchangeInfo", {})
        instruments = exchange_info.get("symbols") if isinstance(exchange_info, dict) else None
        if not isinstance(instruments, list):
            raise MarketDataError("Binance USD-M exchange metadata is malformed")
        supported = {
            instrument.get("symbol")
            for instrument in instruments
            if isinstance(instrument, dict)
            and instrument.get("status") == "TRADING"
            and instrument.get("contractType") == "PERPETUAL"
            and instrument.get("quoteAsset") == "USDT"
        }
        accepted = []
        excluded = []
        for symbol in dict.fromkeys(value.strip().upper() for value in symbols):
            if symbol in supported:
                accepted.append(symbol)
            else:
                excluded.append(
                    {
                        "symbol": symbol,
                        "reason": "not an active USDT-margined perpetual in exchange metadata",
                    }
                )
        return {"supported": accepted, "excluded": excluded}

    def _klines(
        self, symbol: str, interval: str, cutoff: datetime, count: int
    ) -> list[dict[str, Any]]:
        interval_cutoff = floor_time(cutoff, interval)
        raw_rows = self._json_get(
            "/fapi/v1/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "limit": count,
                "endTime": int(interval_cutoff.timestamp() * 1000) - 1,
            },
        )
        if not isinstance(raw_rows, list):
            raise MarketDataError("Binance USD-M klines response must be an array")
        candles = [_normalize_candle(row, interval, cutoff, symbol=symbol) for row in raw_rows]
        candles.sort(key=lambda item: item["open_time"])
        return _validate_candle_lane(
            candles,
            interval,
            cutoff,
            minimum=min(count, 30 if interval == "15m" else 20 if interval == "1h" else 12 if interval == "4h" else 1),
        )

    def fetch_history(
        self,
        symbol: str,
        interval: str,
        *,
        bars: int,
        as_of: datetime,
    ) -> list[dict[str, Any]]:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol.isalnum() or not 5 <= len(normalized_symbol) <= 20:
            raise MarketDataError("unsupported futures symbol")
        if interval not in INTERVAL_SECONDS:
            raise MarketDataError("unsupported futures history interval")
        if (
            isinstance(bars, bool)
            or not isinstance(bars, int)
            or not 1 <= bars <= MAX_HISTORY_BARS
        ):
            raise MarketDataError("history bars must be between 1 and 20000")
        cutoff = floor_time(as_of, interval)
        step_seconds = INTERVAL_SECONDS[interval]
        start = cutoff - timedelta(seconds=bars * step_seconds)
        cursor = start
        candles: list[dict[str, Any]] = []
        while cursor < cutoff:
            remaining = int((cutoff - cursor).total_seconds() // step_seconds)
            limit = min(HISTORY_PAGE_SIZE, remaining)
            raw_rows = self._json_get(
                "/fapi/v1/klines",
                {
                    "symbol": normalized_symbol,
                    "interval": interval,
                    "startTime": int(cursor.timestamp() * 1000),
                    "endTime": int(cutoff.timestamp() * 1000) - 1,
                    "limit": limit,
                },
            )
            if not isinstance(raw_rows, list) or not raw_rows:
                raise MarketDataError(f"futures {interval} warm-up history is incomplete")
            if len(raw_rows) > limit:
                raise MarketDataError(f"futures {interval} warm-up page exceeded its requested limit")
            page = [
                _normalize_candle(row, interval, cutoff, symbol=normalized_symbol)
                for row in raw_rows
            ]
            _validate_candle_lane(page, interval, cutoff, minimum=len(page))
            if parse_utc(page[0]["open_time"], f"{interval}.open_time") != cursor:
                raise MarketDataError(f"futures {interval} warm-up history has a missing interval")
            candles.extend(page)
            cursor = parse_utc(page[-1]["close_time"], f"{interval}.close_time")
            if len(page) < limit and cursor < cutoff:
                raise MarketDataError(f"futures {interval} warm-up history is incomplete")
        if len(candles) != bars:
            raise MarketDataError(f"futures {interval} warm-up returned an unexpected bar count")
        return _validate_candle_lane(candles, interval, cutoff, minimum=bars)

    def fetch_snapshot(self, symbol: str, as_of: datetime) -> MarketSnapshot:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol.isalnum() or not 5 <= len(normalized_symbol) <= 20:
            raise MarketDataError("unsupported futures symbol")
        cutoff = floor_time(as_of, "15m")
        candles_15m = self._klines(normalized_symbol, "15m", cutoff, 64)
        candles_1h = self._klines(normalized_symbol, "1h", cutoff, 48)
        candles_4h = self._klines(normalized_symbol, "4h", cutoff, 36)
        candles_1m = self._klines(
            normalized_symbol, "1m", floor_time(as_of, "1m"), 60
        )
        funding_rate = None
        funding_observed_at = None
        oi_change = None
        oi_observed_at = None
        try:
            premium = self._json_get("/fapi/v1/premiumIndex", {"symbol": normalized_symbol})
            observed = datetime.fromtimestamp(float(premium["time"]) / 1000, tz=timezone.utc)
            if observed <= as_of.astimezone(timezone.utc) and (
                as_of.astimezone(timezone.utc) - observed
            ).total_seconds() <= 12 * 60 * 60:
                funding_rate = _finite(premium["lastFundingRate"], "funding_rate")
                funding_observed_at = iso_utc(observed)
        except (KeyError, TypeError, ValueError, OSError, MarketDataError):
            pass
        try:
            oi_rows = self._json_get(
                "/futures/data/openInterestHist",
                {
                    "symbol": normalized_symbol,
                    "period": "1h",
                    "limit": 2,
                    "endTime": int(as_of.timestamp() * 1000) - 1,
                },
            )
            if isinstance(oi_rows, list):
                eligible = [
                    (datetime.fromtimestamp(float(row["timestamp"]) / 1000, tz=timezone.utc),
                     _finite(row["sumOpenInterest"], "open_interest"))
                    for row in oi_rows
                    if isinstance(row, dict)
                    and float(row.get("timestamp", as_of.timestamp() * 1000 + 1))
                    + 60 * 60 * 1000
                    <= as_of.timestamp() * 1000
                ]
                eligible.sort(key=lambda item: item[0])
                if (
                    len(eligible) >= 2
                    and eligible[-2][1] > 0
                    and (as_of - eligible[-1][0]).total_seconds() <= 3 * 60 * 60
                ):
                    oi_change = eligible[-1][1] / eligible[-2][1] - 1
                    oi_observed_at = iso_utc(eligible[-1][0])
        except (KeyError, TypeError, ValueError, OSError, MarketDataError):
            pass
        snapshot = MarketSnapshot(
            symbol=normalized_symbol,
            as_of=iso_utc(as_of),
            data_cutoff=iso_utc(as_of),
            data_origin="BINANCE_USDM_PUBLIC",
            candles_15m=candles_15m,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
            candles_1m=candles_1m,
            funding_rate=funding_rate,
            funding_observed_at=funding_observed_at,
            open_interest_change_1h=oi_change,
            open_interest_observed_at=oi_observed_at,
            spread_bps=None,
        )
        return snapshot.validate()

    def fetch_monitor_bars(
        self, symbol: str, after: datetime, as_of: datetime
    ) -> list[dict[str, Any]]:
        cutoff = floor_time(as_of, "1m")
        cursor = floor_time(after, "1m")
        if cursor < after:
            cursor += timedelta(minutes=1)
        if cutoff <= cursor:
            return []
        expected_count = int((cutoff - cursor).total_seconds() // 60)
        if expected_count > 20_000:
            raise MarketDataError("monitor recovery gap exceeds the supported 20000 closed bars")
        candles: list[dict[str, Any]] = []
        page_limit = 1000
        while cursor < cutoff:
            remaining = int((cutoff - cursor).total_seconds() // 60)
            limit = min(page_limit, remaining)
            raw_rows = self._json_get(
                "/fapi/v1/klines",
                {
                    "symbol": symbol.strip().upper(),
                    "interval": "1m",
                    "startTime": int(cursor.timestamp() * 1000),
                    "endTime": int(cutoff.timestamp() * 1000) - 1,
                    "limit": limit,
                },
            )
            if not isinstance(raw_rows, list) or not raw_rows:
                raise MarketDataError("monitor recovery is missing one-minute execution bars")
            if len(raw_rows) > limit:
                raise MarketDataError("monitor recovery exceeded the requested execution-bar limit")
            page = [
                _normalize_candle(row, "1m", cutoff, symbol=symbol.strip().upper())
                for row in raw_rows
            ]
            _validate_candle_lane(page, "1m", cutoff, minimum=len(page))
            if parse_utc(page[0]["open_time"], "monitor.open_time") != cursor:
                raise MarketDataError("monitor recovery contains a missing one-minute interval")
            candles.extend(page)
            cursor = parse_utc(page[-1]["close_time"], "monitor.close_time")
            if len(page) < limit and cursor < cutoff:
                raise MarketDataError("monitor recovery contains an incomplete bar history")
        if len(candles) != expected_count:
            raise MarketDataError("monitor recovery contains an incomplete one-minute path")
        return candles

    def fetch_funding_rate(self, symbol: str, as_of: datetime) -> float | None:
        try:
            records = self._json_get(
                "/fapi/v1/fundingRate",
                {
                    "symbol": symbol.strip().upper(),
                    "endTime": int(as_of.astimezone(timezone.utc).timestamp() * 1000),
                    "limit": 1,
                },
            )
            if not isinstance(records, list) or not records or not isinstance(records[-1], dict):
                return None
            record = records[-1]
            observed = datetime.fromtimestamp(float(record["fundingTime"]) / 1000, tz=timezone.utc)
            if observed > as_of.astimezone(timezone.utc):
                return None
            if (as_of.astimezone(timezone.utc) - observed).total_seconds() > 12 * 60 * 60:
                return None
            return _finite(record["fundingRate"], "funding_rate")
        except (KeyError, TypeError, ValueError, OSError, MarketDataError):
            return None
