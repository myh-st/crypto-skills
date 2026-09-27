"""Read-only Binance Spot klines and immutable local candle archives."""

from __future__ import annotations

import json
import math
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .contracts import EvaluationError, _validate_candles, digest, iso_utc, parse_timestamp
from .io import read_json, write_json_new


BINANCE_PROVIDER_ID = "binance-public-spot"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
MAX_KLINE_LIMIT = 1000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

INTERVAL_SECONDS = {
    "1m": 60,
    "3m": 3 * 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "2h": 2 * 60 * 60,
    "4h": 4 * 60 * 60,
    "6h": 6 * 60 * 60,
    "8h": 8 * 60 * 60,
    "12h": 12 * 60 * 60,
    "1d": 24 * 60 * 60,
    "3d": 3 * 24 * 60 * 60,
    "1w": 7 * 24 * 60 * 60,
}


class MarketDataError(EvaluationError):
    """Raised when public market data cannot satisfy its declared contract."""


HttpGet = Callable[[str, float], bytes]


def _default_http_get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_RESPONSE_BYTES + 1)


def _as_utc(value: datetime | str, location: str) -> datetime:
    if isinstance(value, str):
        return parse_timestamp(value, location)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataError(f"{location} must be a timezone-aware timestamp")
    return value.astimezone(timezone.utc)


def _epoch_milliseconds(value: datetime) -> int:
    delta = value.astimezone(timezone.utc) - UTC_EPOCH
    whole_seconds = delta.days * 24 * 60 * 60 + delta.seconds
    return whole_seconds * 1000 + delta.microseconds // 1000


def _datetime_from_epoch_milliseconds(value: int) -> datetime:
    seconds, milliseconds = divmod(value, 1000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc) + timedelta(
        milliseconds=milliseconds
    )


def _interval_floor(value: datetime, interval: str) -> datetime:
    seconds = INTERVAL_SECONDS[interval]
    timestamp = int(value.timestamp())
    if interval == "1w":
        monday_epoch = 4 * 24 * 60 * 60
        timestamp = ((timestamp - monday_epoch) // seconds) * seconds + monday_epoch
    else:
        timestamp = (timestamp // seconds) * seconds
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _symbol(value: str) -> str:
    if not isinstance(value, str):
        raise MarketDataError("symbol must be an uppercase Binance Spot symbol")
    normalized = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{5,20}", normalized):
        raise MarketDataError("symbol must contain only letters and digits (for example BTCUSDT)")
    return normalized


def _interval(value: str) -> tuple[str, int]:
    if not isinstance(value, str) or value not in INTERVAL_SECONDS:
        raise MarketDataError("unsupported Binance Spot interval")
    return value, INTERVAL_SECONDS[value]


def _integer(value: Any, location: str) -> int:
    if isinstance(value, bool):
        raise MarketDataError(f"{location} is malformed")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise MarketDataError(f"{location} is malformed")


def _finite_number(value: Any, location: str) -> float:
    if isinstance(value, bool):
        raise MarketDataError(f"{location} is malformed")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarketDataError(f"{location} is malformed") from exc
    if not math.isfinite(result):
        raise MarketDataError(f"{location} is not finite")
    return result


class BinanceSpotKlinesProvider:
    """Fetch public, unauthenticated Binance Spot OHLCV candles.

    Ranges use an inclusive start and an exclusive close boundary. For
    example, ``end=11:00Z`` requests bars that have closed by 11:00Z and sends
    Binance ``endTime=10:59:59.999Z`` so an in-progress 11:00 candle cannot
    enter the result.
    """

    provider_id = BINANCE_PROVIDER_ID

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        transport: HttpGet | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise MarketDataError("Binance request timeout must be a positive number")
        self.timeout = float(timeout)
        self._transport = transport or _default_http_get
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch_range(
        self,
        symbol: str,
        interval: str,
        start: datetime | str,
        end: datetime | str,
        *,
        limit: int = MAX_KLINE_LIMIT,
    ) -> list[dict[str, Any]]:
        """Fetch every closed candle in an interval-aligned point-in-time range."""

        normalized_symbol = _symbol(symbol)
        normalized_interval, interval_seconds = _interval(interval)
        start_time = _as_utc(start, "start")
        requested_end = _as_utc(end, "end")
        cutoff = _interval_floor(requested_end, normalized_interval)
        step_ms = interval_seconds * 1000
        start_ms = _epoch_milliseconds(start_time)
        cutoff_ms = _epoch_milliseconds(cutoff)

        if _interval_floor(start_time, normalized_interval) != start_time:
            raise MarketDataError("start must be aligned to the requested candle interval")
        if cutoff <= start_time:
            raise MarketDataError("requested range contains no fully closed candle")
        if requested_end < start_time:
            raise MarketDataError("end must be after start")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_KLINE_LIMIT
        ):
            raise MarketDataError(f"limit must be between 1 and {MAX_KLINE_LIMIT}")

        expected_count = (cutoff_ms - start_ms) // step_ms
        if expected_count <= 0 or (cutoff_ms - start_ms) % step_ms:
            raise MarketDataError("requested range is not aligned to the candle interval")

        candles: list[dict[str, Any]] = []
        expected_open_ms = start_ms
        cursor_ms = start_ms
        while cursor_ms < cutoff_ms:
            query = urllib.parse.urlencode(
                {
                    "symbol": normalized_symbol,
                    "interval": normalized_interval,
                    "startTime": cursor_ms,
                    "endTime": cutoff_ms - 1,
                    "timeZone": 0,
                    "limit": limit,
                }
            )
            url = f"{BINANCE_KLINES_URL}?{query}"
            try:
                payload = self._transport(url, self.timeout)
            except urllib.error.HTTPError as exc:
                raise MarketDataError(
                    f"Binance Spot klines returned HTTP {exc.code}"
                ) from None
            except (TimeoutError, socket.timeout):
                raise MarketDataError("Binance Spot klines request timed out") from None
            except urllib.error.URLError:
                raise MarketDataError("Binance Spot klines request failed") from None
            except Exception:
                raise MarketDataError("Binance Spot klines request failed") from None

            if not isinstance(payload, (bytes, bytearray)) or len(payload) > MAX_RESPONSE_BYTES:
                raise MarketDataError("Binance Spot klines returned an invalid response")
            try:
                rows = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise MarketDataError("Binance Spot klines returned invalid JSON") from None
            if not isinstance(rows, list):
                raise MarketDataError("Binance Spot klines response must be an array")
            if not rows:
                raise MarketDataError("Binance Spot klines are missing for the requested range")
            if len(rows) > limit:
                raise MarketDataError("Binance Spot klines exceeded the requested page limit")

            for index, raw in enumerate(rows):
                candle, open_ms = self._normalize_kline(
                    raw,
                    normalized_symbol,
                    normalized_interval,
                    interval_seconds,
                    cutoff,
                    f"klines[{index}]",
                )
                if open_ms < cursor_ms:
                    raise MarketDataError("Binance Spot klines are duplicated or out of order")
                if open_ms != expected_open_ms:
                    raise MarketDataError("Binance Spot klines are missing an interval")
                candles.append(candle)
                expected_open_ms += step_ms

            last_open_ms = _integer(rows[-1][0], "klines.last.open_time")
            next_cursor_ms = last_open_ms + step_ms
            if next_cursor_ms <= cursor_ms:
                raise MarketDataError("Binance Spot klines pagination did not advance")
            cursor_ms = next_cursor_ms
            if len(rows) < limit:
                break

        if expected_open_ms != cutoff_ms or len(candles) != expected_count:
            raise MarketDataError("Binance Spot klines are incomplete for the requested range")
        normalized = _validate_candles(
            candles,
            "Binance Spot klines",
            cutoff=cutoff,
            interval_seconds=interval_seconds,
            minimum_count=expected_count,
        )
        if normalized[-1]["close_time"] != iso_utc(cutoff):
            raise MarketDataError("Binance Spot klines are stale or do not reach the requested cutoff")
        return normalized

    def fetch_history(
        self,
        symbol: str,
        interval: str,
        *,
        bars: int = 200,
        as_of: datetime | str | None = None,
        max_staleness_intervals: float = 2.0,
    ) -> list[dict[str, Any]]:
        """Fetch a complete trailing window ending at the latest closed bar."""

        normalized_interval, interval_seconds = _interval(interval)
        if isinstance(bars, bool) or not isinstance(bars, int) or bars < 1:
            raise MarketDataError("bars must be a positive integer")
        if (
            isinstance(max_staleness_intervals, bool)
            or not isinstance(max_staleness_intervals, (int, float))
            or max_staleness_intervals <= 0
        ):
            raise MarketDataError("max_staleness_intervals must be a positive number")

        now = _as_utc(self._clock(), "clock")
        requested_as_of = now if as_of is None else _as_utc(as_of, "as_of")
        if requested_as_of > now:
            raise MarketDataError("as_of cannot be in the future")
        cutoff = _interval_floor(requested_as_of, normalized_interval)
        if as_of is None and (now - cutoff).total_seconds() > (
            interval_seconds * float(max_staleness_intervals)
        ):
            raise MarketDataError("latest completed Binance Spot candle is stale")
        start = cutoff - timedelta(seconds=bars * interval_seconds)
        candles = self.fetch_range(symbol, normalized_interval, start, cutoff)
        if len(candles) != bars:
            raise MarketDataError("Binance Spot history is incomplete")
        return candles

    @staticmethod
    def _normalize_kline(
        raw: Any,
        symbol: str,
        interval: str,
        interval_seconds: int,
        cutoff: datetime,
        location: str,
    ) -> tuple[dict[str, Any], int]:
        if not isinstance(raw, list) or len(raw) < 7:
            raise MarketDataError(f"{location} is malformed for {symbol} {interval}")
        open_ms = _integer(raw[0], f"{location}.open_time")
        close_ms = _integer(raw[6], f"{location}.close_time")
        interval_ms = interval_seconds * 1000
        expected_source_close = open_ms + interval_ms - 1
        if abs(close_ms - expected_source_close) > 2:
            raise MarketDataError(f"{location} does not match the declared interval")

        open_time = _datetime_from_epoch_milliseconds(open_ms)
        close_boundary = _datetime_from_epoch_milliseconds(open_ms + interval_ms)
        if close_boundary > cutoff:
            raise MarketDataError(f"{location} closes after the requested data cutoff")
        candle = {
            "open_time": iso_utc(open_time),
            "close_time": iso_utc(close_boundary),
            "open": _finite_number(raw[1], f"{location}.open"),
            "high": _finite_number(raw[2], f"{location}.high"),
            "low": _finite_number(raw[3], f"{location}.low"),
            "close": _finite_number(raw[4], f"{location}.close"),
            "volume": _finite_number(raw[5], f"{location}.volume"),
        }
        _validate_candles(
            [candle],
            location,
            cutoff=cutoff,
            interval_seconds=interval_seconds,
            minimum_count=1,
        )
        return candle, open_ms


def build_market_archive_record(
    provider_id: str,
    symbol: str,
    interval: str,
    requested_start: datetime | str,
    requested_end: datetime | str,
    candles: list[dict[str, Any]],
    retrieved_at: datetime | str,
) -> dict[str, Any]:
    """Create a secret-free, content-addressed record for normalized candles."""

    if provider_id != BINANCE_PROVIDER_ID:
        raise MarketDataError("unsupported market archive provider")
    normalized_symbol = _symbol(symbol)
    normalized_interval, interval_seconds = _interval(interval)
    start = _as_utc(requested_start, "requested_start")
    end = _as_utc(requested_end, "requested_end")
    retrieval_time = _as_utc(retrieved_at, "retrieved_at")
    cutoff = _interval_floor(end, normalized_interval)
    normalized = _validate_candles(
        candles,
        "archive.candles",
        cutoff=cutoff,
        interval_seconds=interval_seconds,
        minimum_count=1,
    )
    if _interval_floor(start, normalized_interval) != start:
        raise MarketDataError("requested_start must align to the candle interval")
    if normalized[0]["open_time"] != iso_utc(start):
        raise MarketDataError("archive candles do not start at requested_start")
    if normalized[-1]["close_time"] != iso_utc(cutoff):
        raise MarketDataError("archive candles do not reach the latest closed cutoff")
    expected_count = int((cutoff - start).total_seconds()) // interval_seconds
    if len(normalized) != expected_count:
        raise MarketDataError("archive candles are missing an interval")
    if retrieval_time < cutoff:
        raise MarketDataError("retrieved_at cannot precede the candle cutoff")

    content_hash = digest(normalized)
    identity = {
        "provider_id": provider_id,
        "symbol": normalized_symbol,
        "interval": normalized_interval,
        "requested_start": iso_utc(start),
        "data_cutoff": iso_utc(cutoff),
        "content_sha256": content_hash,
    }
    return {
        "schema_version": "crypto-market.archive.v1",
        "archive_id": f"archive-{digest(identity)[:24]}",
        **identity,
        "endpoint": BINANCE_KLINES_URL,
        "requested_end": iso_utc(end),
        "retrieved_at": iso_utc(retrieval_time),
        "candle_count": len(normalized),
        "candles": normalized,
    }


def validate_market_archive(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != "crypto-market.archive.v1":
        raise MarketDataError("market archive must use schema_version crypto-market.archive.v1")
    required = {
        "schema_version",
        "archive_id",
        "provider_id",
        "symbol",
        "interval",
        "requested_start",
        "requested_end",
        "data_cutoff",
        "retrieved_at",
        "endpoint",
        "candle_count",
        "content_sha256",
        "candles",
    }
    if set(value) != required:
        raise MarketDataError("market archive has missing or unsupported fields")
    if value["provider_id"] != BINANCE_PROVIDER_ID or value["endpoint"] != BINANCE_KLINES_URL:
        raise MarketDataError("market archive provider metadata is unsupported")
    normalized_symbol = _symbol(value["symbol"])
    normalized_interval, interval_seconds = _interval(value["interval"])
    start = _as_utc(value["requested_start"], "archive.requested_start")
    requested_end = _as_utc(value["requested_end"], "archive.requested_end")
    cutoff = _as_utc(value["data_cutoff"], "archive.data_cutoff")
    retrieved_at = _as_utc(value["retrieved_at"], "archive.retrieved_at")
    if cutoff != _interval_floor(requested_end, normalized_interval):
        raise MarketDataError("market archive cutoff does not match the requested range")
    if retrieved_at < cutoff:
        raise MarketDataError("market archive retrieved_at precedes its data cutoff")
    if _interval_floor(start, normalized_interval) != start:
        raise MarketDataError("market archive start is not interval-aligned")
    if (
        isinstance(value["candle_count"], bool)
        or not isinstance(value["candle_count"], int)
        or value["candle_count"] < 1
    ):
        raise MarketDataError("market archive candle_count is invalid")
    candles = _validate_candles(
        value["candles"],
        "archive.candles",
        cutoff=cutoff,
        interval_seconds=interval_seconds,
        minimum_count=1,
    )
    if candles[0]["open_time"] != iso_utc(start) or candles[-1]["close_time"] != iso_utc(cutoff):
        raise MarketDataError("market archive candles do not match their requested range")
    if len(candles) != value["candle_count"]:
        raise MarketDataError("market archive candle_count does not match its contents")
    if digest(candles) != value["content_sha256"]:
        raise MarketDataError("market archive content hash does not match its candles")
    identity = {
        "provider_id": BINANCE_PROVIDER_ID,
        "symbol": normalized_symbol,
        "interval": normalized_interval,
        "requested_start": iso_utc(start),
        "data_cutoff": iso_utc(cutoff),
        "content_sha256": value["content_sha256"],
    }
    if value["archive_id"] != f"archive-{digest(identity)[:24]}":
        raise MarketDataError("market archive id does not match its immutable contents")
    return value


def write_market_archive(record: dict[str, Any], archive_dir: Path) -> Path:
    """Persist an immutable archive; identical content reuses its existing file."""

    archive = validate_market_archive(record)
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"{archive['archive_id']}.json"
    if path.exists():
        existing = load_market_archive(path)
        if (
            existing["archive_id"] == archive["archive_id"]
            and existing["content_sha256"] == archive["content_sha256"]
        ):
            return path
        raise MarketDataError(f"market archive already exists with different contents: {path.name}")
    try:
        write_json_new(path, archive)
    except EvaluationError:
        if path.exists():
            existing = load_market_archive(path)
            if existing["content_sha256"] == archive["content_sha256"]:
                return path
        raise
    return path


def load_market_archive(path: Path) -> dict[str, Any]:
    return validate_market_archive(read_json(path))
