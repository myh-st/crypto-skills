"""Exchange-neutral market-instrument catalog (Gate Spot + Gate USDT perpetual).

Production trading selection comes from exchange metadata, never a hard-coded list.
Only unauthenticated public GET requests are made. Metadata and ticker statistics are
cached server-side with explicit freshness; unknown, delisted, untradable, or stale
instruments fail closed for new PAPER orders.

A deterministic fixture source mirrors the same contract for offline CI and fixture
experiments. Fixture instruments are labeled ``FIXTURE`` and never presented as live.
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
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .gate_market import (
    GATE_API_BASE_URL,
    GATE_HOSTS,
    GateUsdtFuturesMarketDataProvider,
    _http_get,
    from_gate_contract,
    normalize_ticker,
)
from .paper_contracts import PaperTradingError, iso_utc, parse_utc
from .paper_market import (
    INTERVAL_SECONDS,
    MAX_HISTORY_BARS,
    SYMBOL_BASE_PRICES,
    FixtureFuturesMarketDataProvider,
    MarketDataError,
    MarketSnapshot,
    _validate_candle_lane,
    floor_time,
)


INSTRUMENT_SCHEMA_VERSION = "market-instrument.v1"
MARKET_TYPES = ("spot", "perpetual")
SPOT_DATA_ORIGIN = "GATE_SPOT_PUBLIC"
PERP_DATA_ORIGIN = "GATE_USDT_PUBLIC"
FIXTURE_ORIGIN = "FIXTURE"
SPOT_PROVIDER_ID = "gate-spot-public"
METADATA_TTL_SECONDS = 900
STATS_TTL_SECONDS = 30
METADATA_HARD_MAX_AGE_SECONDS = 6 * 3600
QUOTE_MAX_AGE_SECONDS = 30
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
SPOT_CHART_INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
INSTRUMENT_ID_RE = re.compile(r"(gate|fixture):(spot|perpetual):([A-Z0-9]{1,20}_[A-Z0-9]{2,10})")


class CatalogError(PaperTradingError):
    """The catalog could not provide an exchange-backed, tradable instrument."""


def instrument_id(exchange: str, market_type: str, exchange_symbol: str) -> str:
    return f"{exchange}:{market_type}:{exchange_symbol}"


def parse_instrument_id(value: Any) -> tuple[str, str, str]:
    if not isinstance(value, str) or not INSTRUMENT_ID_RE.fullmatch(value.strip()):
        raise CatalogError("instrument_id is invalid")
    exchange, market_type, symbol = value.strip().split(":")
    return exchange, market_type, symbol


def neutral_symbol(exchange_symbol: str) -> str:
    """``BTC_USDT`` -> ``BTCUSDT`` (the runtime's exchange-neutral symbol)."""

    return exchange_symbol.replace("_", "")


def _num(value: Any, *, positive: bool = False) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or (positive and result <= 0):
        return None
    return result


def _spread(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 10_000


def _liquidity_state(volume_quote: float | None, spread_bps: float | None) -> str:
    if volume_quote is None:
        return "unknown"
    if volume_quote >= 5_000_000 and (spread_bps is None or spread_bps <= 10):
        return "high"
    if volume_quote >= 250_000 and (spread_bps is None or spread_bps <= 50):
        return "medium"
    return "low"


def normalize_spot_pair(
    raw: Any,
    ticker: dict[str, Any] | None,
    *,
    refreshed_at: str,
    exchange: str = "gate",
    data_origin: str = SPOT_DATA_ORIGIN,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
        raise CatalogError("spot pair metadata is malformed")
    pair = raw["id"].strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{1,20}_[A-Z0-9]{2,10}", pair):
        raise CatalogError("spot pair id is malformed")
    base = str(raw.get("base") or pair.split("_")[0]).upper()
    quote = str(raw.get("quote") or pair.split("_")[1]).upper()
    trade_status = str(raw.get("trade_status") or "untradable")
    status = {
        "tradable": "tradable",
        "buyable": "buy_only",
        "sellable": "sell_only",
    }.get(trade_status, "untradable")
    moment = now or datetime.now(timezone.utc)
    delisting = raw.get("delisting_time")
    if isinstance(delisting, (int, float)) and delisting > 0:
        if datetime.fromtimestamp(float(delisting), tz=timezone.utc) <= moment + timedelta(days=1):
            status = "delisting"
    precision = raw.get("precision")
    amount_precision = raw.get("amount_precision")
    price_tick = 10 ** (-int(precision)) if isinstance(precision, int) and 0 <= precision <= 18 else None
    quantity_step = (
        10 ** (-int(amount_precision)) if isinstance(amount_precision, int) and 0 <= amount_precision <= 18 else None
    )
    ticker = ticker or {}
    bid = _num(ticker.get("highest_bid"), positive=True)
    ask = _num(ticker.get("lowest_ask"), positive=True)
    last = _num(ticker.get("last"), positive=True)
    change = _num(ticker.get("change_percentage"))
    volume_quote = _num(ticker.get("quote_volume"))
    spread = _spread(bid, ask)
    fee = _num(raw.get("fee"))
    return {
        "schema_version": INSTRUMENT_SCHEMA_VERSION,
        "instrument_id": instrument_id(exchange, "spot", pair),
        "exchange": exchange,
        "market_type": "spot",
        "exchange_symbol": pair,
        "symbol": neutral_symbol(pair),
        "display_symbol": f"{base}/{quote}",
        "base": base,
        "quote": quote,
        "settle": None,
        "status": status,
        "tradable": status == "tradable",
        "price_tick": price_tick,
        "quantity_step": quantity_step,
        "min_quantity": _num(raw.get("min_base_amount")),
        "max_quantity": _num(raw.get("max_base_amount")),
        "min_notional": _num(raw.get("min_quote_amount")),
        "max_notional": _num(raw.get("max_quote_amount")),
        "min_leverage": None,
        "max_leverage": None,
        "maintenance_rate": None,
        "funding_rate": None,
        "taker_fee_rate": None if fee is None else fee / 100,
        "maker_fee_rate": None if fee is None else fee / 100,
        "last_price": last,
        "mark_price": None,
        "index_price": None,
        "best_bid": bid,
        "best_ask": ask,
        "volume_24h_quote": volume_quote,
        "change_24h": None if change is None else change / 100,
        "spread_bps": spread,
        "liquidity": _liquidity_state(volume_quote, spread),
        "refreshed_at": refreshed_at,
        "data_origin": data_origin,
    }


def normalize_perpetual(
    contract: dict[str, Any],
    ticker: dict[str, Any] | None,
    *,
    refreshed_at: str,
    exchange: str = "gate",
    data_origin: str = PERP_DATA_ORIGIN,
) -> dict[str, Any]:
    """Normalize a ``gate_market.normalize_contract`` row plus a normalized ticker."""

    exchange_symbol = contract["exchange_contract"]
    base = exchange_symbol.split("_")[0]
    if contract.get("in_delisting"):
        status = "delisting"
    elif contract.get("status") == "trading":
        status = "tradable"
    else:
        status = "untradable"
    ticker = ticker or {}
    bid = ticker.get("best_bid")
    ask = ticker.get("best_ask")
    spread = _spread(bid, ask)
    change = ticker.get("change_24h_pct")
    volume_quote = ticker.get("volume_24h_quote")
    size = contract.get("contract_size")
    min_quantity = contract.get("min_quantity")
    max_contracts = contract.get("order_size_max_contracts")
    return {
        "schema_version": INSTRUMENT_SCHEMA_VERSION,
        "instrument_id": instrument_id(exchange, "perpetual", exchange_symbol),
        "exchange": exchange,
        "market_type": "perpetual",
        "exchange_symbol": exchange_symbol,
        "symbol": contract["symbol"],
        "display_symbol": f"{base}/USDT Perp",
        "base": base,
        "quote": "USDT",
        "settle": "USDT",
        "status": status,
        "tradable": status == "tradable",
        "price_tick": contract.get("price_tick"),
        "quantity_step": size,
        "min_quantity": min_quantity,
        "max_quantity": None if max_contracts is None or size is None else max_contracts * size,
        "min_notional": None,
        "max_notional": None,
        "min_leverage": contract.get("leverage_min"),
        "max_leverage": contract.get("leverage_max"),
        "maintenance_rate": contract.get("maintenance_rate"),
        "funding_rate": ticker.get("funding_rate"),
        "funding_next_apply": contract.get("funding_next_apply"),
        "taker_fee_rate": contract.get("taker_fee_rate"),
        "maker_fee_rate": contract.get("maker_fee_rate"),
        "last_price": ticker.get("last_price"),
        "mark_price": ticker.get("mark_price"),
        "index_price": ticker.get("index_price"),
        "best_bid": bid,
        "best_ask": ask,
        "volume_24h_quote": volume_quote,
        "change_24h": None if change is None else change / 100,
        "spread_bps": spread,
        "liquidity": _liquidity_state(volume_quote, spread),
        "refreshed_at": refreshed_at,
        "data_origin": data_origin,
    }


# ---------------------------------------------------------------------------
# Gate Spot public REST
# ---------------------------------------------------------------------------


def _spot_candle(raw: Any, interval: str) -> dict[str, Any] | None:
    """Gate spot row [t, quote_vol, close, high, low, open, base_vol, window_closed]."""

    if not isinstance(raw, list) or len(raw) < 7:
        raise MarketDataError("Gate spot candle is malformed")
    seconds = SPOT_CHART_INTERVALS[interval]
    try:
        opened_ts = int(raw[0])
    except (TypeError, ValueError):
        raise MarketDataError("Gate spot candle timestamp is malformed") from None
    if opened_ts % seconds:
        raise MarketDataError("Gate spot candle is not aligned to its interval")
    opened = datetime.fromtimestamp(opened_ts, tz=timezone.utc)
    values = {
        "open": _num(raw[5], positive=True),
        "high": _num(raw[3], positive=True),
        "low": _num(raw[4], positive=True),
        "close": _num(raw[2], positive=True),
        "volume": _num(raw[6]),
    }
    if any(value is None for value in values.values()):
        raise MarketDataError("Gate spot candle values are not finite")
    if values["high"] < max(values["open"], values["close"], values["low"]) or values["low"] > min(
        values["open"], values["close"], values["high"]
    ):
        raise MarketDataError("Gate spot candle values are inconsistent")
    closed_flag = str(raw[7]).lower() == "true" if len(raw) > 7 else None
    return {
        "open_time": iso_utc(opened),
        "close_time": iso_utc(opened + timedelta(seconds=seconds)),
        **values,
        "quote_volume": _num(raw[1]),
        "exchange_closed": closed_flag,
    }


class GateSpotMarketDataProvider:
    """Unauthenticated Gate Spot REST adapter (GET only, approved host allowlist)."""

    provider_id = SPOT_PROVIDER_ID
    data_origin = SPOT_DATA_ORIGIN

    def __init__(
        self,
        *,
        base_url: str = GATE_API_BASE_URL,
        timeout: float = 10.0,
        transport: Callable[[str, float], bytes] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "https" or parsed.hostname not in GATE_HOSTS:
            raise MarketDataError("public spot endpoint must use an approved Gate host")
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self._transport = transport or _http_get
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._pairs: dict[str, dict[str, Any]] = {}
        self._pairs_at = 0.0
        self.request_count = 0

    def _json_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        self.request_count += 1
        try:
            response = self._transport(url, self.timeout)
        except urllib.error.HTTPError as exc:
            raise MarketDataError(f"Gate spot request returned HTTP {exc.code}") from None
        except (TimeoutError, socket.timeout):
            raise MarketDataError("Gate spot request timed out") from None
        except Exception:
            raise MarketDataError("Gate spot request failed") from None
        if not isinstance(response, (bytes, bytearray)) or len(response) > MAX_RESPONSE_BYTES:
            raise MarketDataError("Gate spot response is invalid")
        try:
            return json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MarketDataError("Gate spot response is invalid JSON") from None

    def currency_pairs(self, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._pairs and not refresh and time.monotonic() - self._pairs_at < METADATA_TTL_SECONDS:
                return self._pairs
        rows = self._json_get("/spot/currency_pairs")
        if not isinstance(rows, list) or not rows:
            raise MarketDataError("Gate spot currency pairs are malformed")
        pairs = {
            str(row["id"]).upper(): row
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }
        with self._lock:
            self._pairs = pairs
            self._pairs_at = time.monotonic()
        return pairs

    def pair(self, exchange_symbol: str) -> dict[str, Any]:
        pairs = self.currency_pairs()
        key = exchange_symbol.strip().upper()
        if key not in pairs:
            raise MarketDataError("pair is not a Gate spot currency pair")
        return pairs[key]

    def tickers(self) -> dict[str, dict[str, Any]]:
        rows = self._json_get("/spot/tickers")
        if not isinstance(rows, list):
            raise MarketDataError("Gate spot tickers are malformed")
        return {
            str(row["currency_pair"]).upper(): row
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("currency_pair"), str)
        }

    def ticker(self, exchange_symbol: str) -> dict[str, Any]:
        rows = self._json_get("/spot/tickers", {"currency_pair": exchange_symbol})
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise MarketDataError("Gate spot ticker is empty")
        return rows[0]

    def book_top(self, exchange_symbol: str) -> dict[str, Any]:
        book = self._json_get(
            "/spot/order_book", {"currency_pair": exchange_symbol, "limit": 1, "with_id": "true"}
        )
        try:
            bid = _num(book["bids"][0][0], positive=True)
            ask = _num(book["asks"][0][0], positive=True)
            observed = datetime.fromtimestamp(int(book["current"]) / 1000, tz=timezone.utc)
        except (KeyError, IndexError, TypeError, ValueError, OverflowError, OSError):
            raise MarketDataError("Gate spot order book is malformed") from None
        if bid is None or ask is None or ask < bid:
            raise MarketDataError("Gate spot order book is crossed or empty")
        return {
            "best_bid": bid,
            "best_ask": ask,
            "mid_price": (bid + ask) / 2,
            "spread_bps": _spread(bid, ask),
            "observed_at": iso_utc(observed),
        }

    def quote(self, exchange_symbol: str) -> dict[str, Any]:
        book = self.book_top(exchange_symbol)
        ticker = self.ticker(exchange_symbol)
        return {
            **book,
            "last_price": _num(ticker.get("last"), positive=True),
            "change_24h": None if _num(ticker.get("change_percentage")) is None else _num(ticker.get("change_percentage")) / 100,
            "volume_24h_quote": _num(ticker.get("quote_volume")),
            "source": "gate_spot_rest",
            "data_origin": SPOT_DATA_ORIGIN,
        }

    def fetch_candles(
        self, exchange_symbol: str, interval: str, *, bars: int, as_of: datetime
    ) -> list[dict[str, Any]]:
        """Closed candles only, ending at the last window that closed by ``as_of``."""

        if interval not in SPOT_CHART_INTERVALS:
            raise MarketDataError("unsupported Gate spot candle interval")
        seconds = SPOT_CHART_INTERVALS[interval]
        timestamp = int(as_of.astimezone(timezone.utc).timestamp())
        cutoff = timestamp - timestamp % seconds
        start = cutoff - bars * seconds
        result: dict[str, dict[str, Any]] = {}
        cursor = start
        while cursor < cutoff:
            page_end = min(cutoff, cursor + seconds * 900)
            rows = self._json_get(
                "/spot/candlesticks",
                {
                    "currency_pair": exchange_symbol,
                    "interval": interval,
                    "from": cursor,
                    "to": page_end - seconds,
                },
            )
            if not isinstance(rows, list):
                raise MarketDataError("Gate spot candlesticks are malformed")
            for row in rows:
                candle = _spot_candle(row, interval)
                opened = int(parse_utc(candle["open_time"], "spot.open_time").timestamp())
                if cursor <= opened < page_end and opened + seconds <= timestamp:
                    result[candle["open_time"]] = candle
            cursor = page_end
        return [result[key] for key in sorted(result)]

    def chart_candles(self, exchange_symbol: str, interval: str, *, limit: int) -> list[dict[str, Any]]:
        """Recent candles including the current (not yet closed) one, labeled per bar."""

        if interval not in SPOT_CHART_INTERVALS:
            raise MarketDataError("unsupported Gate spot candle interval")
        rows = self._json_get(
            "/spot/candlesticks",
            {"currency_pair": exchange_symbol, "interval": interval, "limit": max(10, min(1000, limit))},
        )
        if not isinstance(rows, list):
            raise MarketDataError("Gate spot candlesticks are malformed")
        now = int(self._clock().timestamp())
        seconds = SPOT_CHART_INTERVALS[interval]
        candles = []
        for row in rows:
            candle = _spot_candle(row, interval)
            opened = int(parse_utc(candle["open_time"], "spot.open_time").timestamp())
            closed = candle.pop("exchange_closed")
            candle["closed"] = bool(closed) if closed is not None else opened + seconds <= now
            candle["source"] = "gate_spot_rest"
            candles.append(candle)
        return sorted(candles, key=lambda item: item["open_time"])

    def _lane(self, exchange_symbol: str, interval: str, bars: int, as_of: datetime) -> list[dict[str, Any]]:
        keys = ("open_time", "close_time", "open", "high", "low", "close", "volume")
        return [{key: candle[key] for key in keys} for candle in self.fetch_candles(exchange_symbol, interval, bars=bars, as_of=as_of)]

    def fetch_snapshot(self, exchange_symbol: str, as_of: datetime) -> MarketSnapshot:
        as_of = as_of.astimezone(timezone.utc)
        cutoff = floor_time(as_of, "15m")
        quote = None
        try:
            quote = self.quote(exchange_symbol)
        except MarketDataError:
            quote = None
        snapshot = MarketSnapshot(
            symbol=neutral_symbol(exchange_symbol),
            as_of=iso_utc(as_of),
            data_cutoff=iso_utc(as_of),
            data_origin=SPOT_DATA_ORIGIN,
            candles_15m=self._lane(exchange_symbol, "15m", 64, cutoff),
            candles_1h=self._lane(exchange_symbol, "1h", 48, cutoff),
            candles_4h=self._lane(exchange_symbol, "4h", 36, cutoff),
            candles_1m=self._lane(exchange_symbol, "1m", 60, as_of),
            funding_rate=None,
            funding_observed_at=None,
            open_interest_change_1h=None,
            open_interest_observed_at=None,
            spread_bps=None if quote is None else quote.get("spread_bps"),
            market_context=None
            if quote is None
            else {
                "source": quote["source"],
                "exchange": "gate",
                "market_type": "spot",
                "best_bid": quote["best_bid"],
                "best_ask": quote["best_ask"],
                "last_price": quote["last_price"],
                "book_observed_at": quote["observed_at"],
            },
        )
        return snapshot.validate()

    def fetch_monitor_bars(self, exchange_symbol: str, after: datetime, as_of: datetime) -> list[dict[str, Any]]:
        cutoff = floor_time(as_of, "1m")
        cursor = floor_time(after, "1m")
        if cursor < after:
            cursor += timedelta(minutes=1)
        if cutoff <= cursor:
            return []
        expected = int((cutoff - cursor).total_seconds() // 60)
        if expected > MAX_HISTORY_BARS:
            raise MarketDataError("spot monitor gap exceeds the supported 20000 closed bars")
        bars = [
            candle
            for candle in self._lane(exchange_symbol, "1m", expected, cutoff)
            if parse_utc(candle["open_time"], "spot.open") >= cursor
        ]
        if len(bars) != expected:
            raise MarketDataError("spot monitor recovery contains an incomplete one-minute path")
        return _validate_candle_lane(bars, "1m", cutoff, minimum=expected)


# ---------------------------------------------------------------------------
# Deterministic fixture sources (offline; labeled FIXTURE)
# ---------------------------------------------------------------------------

FIXTURE_SPOT_PAIRS = (
    ("BTC_USDT", "tradable", 6, 1, 0.00001, 3.0),
    ("ETH_USDT", "tradable", 4, 2, 0.0001, 3.0),
    ("SOL_USDT", "tradable", 3, 2, 0.001, 3.0),
    ("SUI_USDT", "tradable", 2, 4, 0.1, 1.0),
    ("SEI_USDT", "tradable", 1, 5, 1.0, 1.0),
    ("AVAX_USDT", "tradable", 3, 3, 0.01, 1.0),
    ("OLD_USDT", "untradable", 2, 4, 1.0, 1.0),
)
FIXTURE_PERP_SYMBOLS = ("BTC_USDT", "ETH_USDT", "SOL_USDT", "SUI_USDT", "SEI_USDT", "AVAX_USDT", "OLD_USDT")


class FixtureSpotMarketDataProvider:
    """Seeded synthetic spot data sharing the futures fixture price path; labeled FIXTURE."""

    provider_id = "deterministic-spot-fixture"
    data_origin = FIXTURE_ORIGIN

    def __init__(self, *, clock: Callable[[], datetime] | None = None, future_path: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._futures = FixtureFuturesMarketDataProvider(future_path=future_path)
        self.request_count = 0

    def currency_pairs(self, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
        return {
            pair: {
                "id": pair,
                "base": pair.split("_")[0],
                "quote": pair.split("_")[1],
                "fee": "0.1",
                "min_base_amount": str(min_base),
                "min_quote_amount": str(min_quote),
                "max_base_amount": "1000000",
                "max_quote_amount": "5000000",
                "amount_precision": amount_precision,
                "precision": precision,
                "trade_status": status,
            }
            for pair, status, amount_precision, precision, min_base, min_quote in FIXTURE_SPOT_PAIRS
        }

    def pair(self, exchange_symbol: str) -> dict[str, Any]:
        pairs = self.currency_pairs()
        if exchange_symbol not in pairs:
            raise MarketDataError("pair is not a fixture spot pair")
        return pairs[exchange_symbol]

    def _last(self, exchange_symbol: str, as_of: datetime | None = None) -> float:
        snapshot = self._futures.fetch_snapshot(neutral_symbol(exchange_symbol), as_of or self._clock())
        return float(snapshot.candles_1m[-1]["close"])

    def tickers(self) -> dict[str, dict[str, Any]]:
        result = {}
        for pair, *_ in FIXTURE_SPOT_PAIRS:
            last = self._last(pair)
            result[pair] = {
                "currency_pair": pair,
                "last": str(last),
                "highest_bid": str(last * 0.9999),
                "lowest_ask": str(last * 1.0001),
                "change_percentage": "1.25",
                "quote_volume": "1000000",
            }
        return result

    def ticker(self, exchange_symbol: str) -> dict[str, Any]:
        return self.tickers()[exchange_symbol]

    def quote(self, exchange_symbol: str) -> dict[str, Any]:
        last = self._last(exchange_symbol)
        bid, ask = last * 0.9999, last * 1.0001
        return {
            "best_bid": bid,
            "best_ask": ask,
            "mid_price": (bid + ask) / 2,
            "spread_bps": _spread(bid, ask),
            "last_price": last,
            "change_24h": 0.0125,
            "volume_24h_quote": 1_000_000.0,
            "observed_at": iso_utc(self._clock()),
            "source": "fixture",
            "data_origin": FIXTURE_ORIGIN,
        }

    def fetch_snapshot(self, exchange_symbol: str, as_of: datetime) -> MarketSnapshot:
        snapshot = self._futures.fetch_snapshot(neutral_symbol(exchange_symbol), as_of)
        return MarketSnapshot(
            **{**snapshot.__dict__, "funding_rate": None, "funding_observed_at": None,
               "open_interest_change_1h": None, "open_interest_observed_at": None}
        ).validate()

    def fetch_monitor_bars(self, exchange_symbol: str, after: datetime, as_of: datetime) -> list[dict[str, Any]]:
        return self._futures.fetch_monitor_bars(neutral_symbol(exchange_symbol), after, as_of)

    def chart_candles(self, exchange_symbol: str, interval: str, *, limit: int) -> list[dict[str, Any]]:
        lane_interval = interval if interval in INTERVAL_SECONDS else "15m"
        bars = self._futures._lane(neutral_symbol(exchange_symbol), lane_interval, max(10, min(500, limit)), self._clock())
        return [{**bar, "closed": True, "source": "fixture"} for bar in bars]


class FixturePerpetualCatalogSource:
    """Contract metadata + ticker stats for fixture perpetuals, same shape as Gate."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._futures = FixtureFuturesMarketDataProvider()

    def contracts(self, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
        result = {}
        for contract in FIXTURE_PERP_SYMBOLS:
            symbol = from_gate_contract(contract)
            result[symbol] = {
                "symbol": symbol,
                "exchange_contract": contract,
                "status": "delisted" if contract == "OLD_USDT" else "trading",
                "in_delisting": contract == "OLD_USDT",
                "settle": "USDT",
                "contract_size": 0.0001 if contract == "BTC_USDT" else 0.01,
                "order_size_min_contracts": 1,
                "order_size_max_contracts": 1_000_000,
                "min_quantity": 0.0001 if contract == "BTC_USDT" else 0.01,
                "price_tick": 0.1 if contract in {"BTC_USDT", "ETH_USDT"} else 0.0001,
                "leverage_min": 1,
                "leverage_max": 100,
                "maintenance_rate": 0.005,
                "taker_fee_rate": 0.0004,
                "maker_fee_rate": 0.0002,
                "funding_next_apply": None,
            }
        return result

    def all_tickers(self) -> dict[str, dict[str, Any]]:
        now = self._clock()
        result = {}
        for contract in FIXTURE_PERP_SYMBOLS:
            symbol = from_gate_contract(contract)
            last = float(self._futures.fetch_snapshot(symbol, now).candles_1m[-1]["close"])
            result[symbol] = {
                "symbol": symbol,
                "last_price": last,
                "mark_price": last,
                "index_price": last,
                "funding_rate": 0.0001,
                "change_24h_pct": 0.8,
                "volume_24h_quote": 2_000_000.0,
                "best_bid": last * 0.9999,
                "best_ask": last * 1.0001,
            }
        return result


def gate_perpetual_tickers(provider: GateUsdtFuturesMarketDataProvider) -> dict[str, dict[str, Any]]:
    rows = provider._json_get("/futures/usdt/tickers")
    if not isinstance(rows, list):
        raise MarketDataError("Gate futures tickers are malformed")
    observed = datetime.now(timezone.utc)
    result = {}
    for row in rows:
        try:
            ticker = normalize_ticker(row, observed_at=observed)
        except (MarketDataError, PaperTradingError):
            continue
        result[ticker["symbol"]] = ticker
    return result


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class MarketCatalog:
    """Cached, exchange-backed instrument catalog with explicit freshness.

    ``source`` is ``gate`` (real public metadata) or ``fixture`` (offline, labeled).
    """

    def __init__(
        self,
        *,
        source: str = "gate",
        spot_provider: Any | None = None,
        perpetual_provider: Any | None = None,
        live_stream: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if source not in {"gate", "fixture"}:
            raise CatalogError("catalog source must be gate or fixture")
        self.source = source
        self.exchange = source
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.live_stream = live_stream
        if source == "gate":
            self.spot = spot_provider or GateSpotMarketDataProvider()
            self.perpetual = perpetual_provider or GateUsdtFuturesMarketDataProvider(live_state=live_stream)
        else:
            self.spot = spot_provider or FixtureSpotMarketDataProvider(clock=self._clock)
            self.perpetual = perpetual_provider or FixturePerpetualCatalogSource(clock=self._clock)
        self._lock = threading.RLock()
        self._cache: dict[str, dict[str, Any]] = {
            market_type: {"instruments": {}, "metadata_at": None, "stats_at": None, "error": None}
            for market_type in MARKET_TYPES
        }

    @property
    def data_origin(self) -> str:
        return FIXTURE_ORIGIN if self.source == "fixture" else "GATE_PUBLIC"

    def _now(self) -> datetime:
        return self._clock().astimezone(timezone.utc)

    def _age(self, stamp: datetime | None) -> float | None:
        return None if stamp is None else max(0.0, (self._now() - stamp).total_seconds())

    def _load(self, market_type: str) -> dict[str, dict[str, Any]]:
        now = self._now()
        refreshed_at = iso_utc(now)
        if market_type == "spot":
            pairs = self.spot.currency_pairs(refresh=True)
            try:
                tickers = self.spot.tickers()
            except MarketDataError:
                tickers = {}
            origin = FIXTURE_ORIGIN if self.source == "fixture" else SPOT_DATA_ORIGIN
            result = {}
            for pair, raw in pairs.items():
                try:
                    item = normalize_spot_pair(
                        raw, tickers.get(pair), refreshed_at=refreshed_at, exchange=self.exchange,
                        data_origin=origin, now=now,
                    )
                except CatalogError:
                    continue
                result[item["instrument_id"]] = item
            return result
        contracts = self.perpetual.contracts(refresh=True)
        try:
            tickers = (
                self.perpetual.all_tickers()
                if hasattr(self.perpetual, "all_tickers")
                else gate_perpetual_tickers(self.perpetual)
            )
        except MarketDataError:
            tickers = {}
        origin = FIXTURE_ORIGIN if self.source == "fixture" else PERP_DATA_ORIGIN
        result = {}
        for symbol, contract in contracts.items():
            item = normalize_perpetual(
                contract, tickers.get(symbol), refreshed_at=refreshed_at, exchange=self.exchange, data_origin=origin
            )
            result[item["instrument_id"]] = item
        return result

    def refresh(self, market_type: str, *, force: bool = False) -> dict[str, Any]:
        if market_type not in MARKET_TYPES:
            raise CatalogError("market_type must be spot or perpetual")
        with self._lock:
            entry = self._cache[market_type]
            age = self._age(entry["stats_at"])
            if not force and entry["instruments"] and age is not None and age < STATS_TTL_SECONDS:
                return entry
            try:
                instruments = self._load(market_type)
                if not instruments:
                    raise MarketDataError("exchange returned no instruments")
                entry["instruments"] = instruments
                entry["metadata_at"] = self._now()
                entry["stats_at"] = self._now()
                entry["error"] = None
            except PaperTradingError as exc:
                entry["error"] = str(exc)[:200]
                if not entry["instruments"]:
                    raise CatalogError(f"{market_type} catalog is unavailable: {entry['error']}") from None
            return entry

    def freshness(self, market_type: str) -> dict[str, Any]:
        entry = self._cache[market_type]
        metadata_age = self._age(entry["metadata_at"])
        return {
            "market_type": market_type,
            "source": self.source,
            "metadata_refreshed_at": None if entry["metadata_at"] is None else iso_utc(entry["metadata_at"]),
            "metadata_age_seconds": metadata_age,
            "stale": metadata_age is None or metadata_age > METADATA_TTL_SECONDS * 2,
            "fail_closed": metadata_age is None or metadata_age > METADATA_HARD_MAX_AGE_SECONDS,
            "last_error": entry["error"],
            "instrument_count": len(entry["instruments"]),
        }

    def list(
        self,
        market_type: str,
        *,
        query: str = "",
        quote: str | None = None,
        tradable_only: bool = False,
        limit: int = 200,
        sort: str = "volume",
    ) -> dict[str, Any]:
        entry = self.refresh(market_type)
        items = list(entry["instruments"].values())
        needle = query.strip().upper().replace("/", "").replace("_", "").replace("-", "")
        if needle:
            items = [
                item
                for item in items
                if needle in item["symbol"] or needle in item["base"] or needle == item["exchange_symbol"].replace("_", "")
            ]
        if quote:
            items = [item for item in items if item["quote"] == quote.upper()]
        if tradable_only:
            items = [item for item in items if item["tradable"]]

        def volume_key(item: dict[str, Any]) -> tuple:
            exact = 0 if needle and item["base"] == needle else 1
            return (exact, not item["tradable"], -(item.get("volume_24h_quote") or 0.0), item["symbol"])

        if sort == "change":
            items.sort(key=lambda item: -(item.get("change_24h") or -math.inf))
        elif sort == "symbol":
            items.sort(key=lambda item: item["symbol"])
        else:
            items.sort(key=volume_key)
        limit = max(1, min(int(limit), 1000))
        return {
            "market_type": market_type,
            "exchange": self.exchange,
            "data_origin": self.data_origin,
            "freshness": self.freshness(market_type),
            "total": len(items),
            "instruments": items[:limit],
        }

    def get(self, instrument: str) -> dict[str, Any]:
        exchange, market_type, _symbol = parse_instrument_id(instrument)
        if exchange != self.exchange:
            raise CatalogError("instrument belongs to a different exchange source")
        entry = self.refresh(market_type)
        found = entry["instruments"].get(instrument)
        if found is None:
            raise CatalogError("instrument is not in the exchange catalog")
        return {**found, "freshness": self.freshness(market_type)}

    def by_symbol(self, market_type: str, symbol: str) -> dict[str, Any]:
        entry = self.refresh(market_type)
        normalized = symbol.strip().upper()
        for item in entry["instruments"].values():
            if item["symbol"] == normalized:
                return item
        raise CatalogError("symbol is not in the exchange catalog")

    def require_tradable(self, instrument: str, *, side: str | None = None) -> dict[str, Any]:
        """Fail closed for unknown, delisted, untradable, or stale-metadata instruments."""

        item = self.get(instrument)
        freshness = item["freshness"]
        if freshness["fail_closed"]:
            raise CatalogError("CATALOG_STALE: exchange metadata is too old for new orders")
        status = item["status"]
        if status == "tradable":
            return item
        if status == "buy_only" and side in {"buy", "long"}:
            return item
        if status == "sell_only" and side in {"sell"}:
            return item
        raise CatalogError(f"INSTRUMENT_NOT_TRADABLE: {item['display_symbol']} is {status}")

    def quote(self, instrument: str) -> dict[str, Any]:
        """A fresh top-of-book quote; raises when no fresh observation exists."""

        exchange, market_type, exchange_symbol = parse_instrument_id(instrument)
        if exchange != self.exchange:
            raise CatalogError("instrument belongs to a different exchange source")
        now = self._now()
        if market_type == "spot":
            quote = self.spot.quote(exchange_symbol)
        else:
            symbol = neutral_symbol(exchange_symbol)
            quote = None
            if self.source == "gate" and self.live_stream is not None:
                try:
                    state = self.live_stream.symbol_state(symbol)
                except Exception:
                    state = {}
                book = state.get("book") or {}
                ticker = state.get("ticker") or {}
                if state.get("fresh") and book.get("best_bid") and book.get("best_ask"):
                    quote = {
                        "best_bid": book["best_bid"],
                        "best_ask": book["best_ask"],
                        "mid_price": (book["best_bid"] + book["best_ask"]) / 2,
                        "spread_bps": _spread(book["best_bid"], book["best_ask"]),
                        "last_price": ticker.get("last_price"),
                        "mark_price": ticker.get("mark_price"),
                        "funding_rate": ticker.get("funding_rate"),
                        "observed_at": book.get("observed_at") or iso_utc(now),
                        "source": "gate_ws",
                        "data_origin": PERP_DATA_ORIGIN,
                    }
            if quote is None and self.source == "gate":
                book = self.perpetual.fetch_book_top(symbol)
                ticker = self.perpetual.fetch_ticker(symbol)
                quote = {
                    "best_bid": book["best_bid"],
                    "best_ask": book["best_ask"],
                    "mid_price": book["mid_price"],
                    "spread_bps": book["spread_bps"],
                    "last_price": ticker.get("last_price"),
                    "mark_price": ticker.get("mark_price"),
                    "funding_rate": ticker.get("funding_rate"),
                    "observed_at": iso_utc(now),
                    "source": "gate_rest",
                    "data_origin": PERP_DATA_ORIGIN,
                }
            if quote is None:
                snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot(symbol, now)
                last = float(snapshot.candles_1m[-1]["close"])
                quote = {
                    "best_bid": last * 0.9999,
                    "best_ask": last * 1.0001,
                    "mid_price": last,
                    "spread_bps": _spread(last * 0.9999, last * 1.0001),
                    "last_price": last,
                    "mark_price": last,
                    "funding_rate": 0.0001,
                    "observed_at": iso_utc(now),
                    "source": "fixture",
                    "data_origin": FIXTURE_ORIGIN,
                }
        observed = parse_utc(quote["observed_at"], "quote.observed_at")
        age = max(0.0, (now - observed).total_seconds())
        quote["age_seconds"] = age
        quote["fresh"] = age <= QUOTE_MAX_AGE_SECONDS
        quote["instrument_id"] = instrument
        quote["market_type"] = market_type
        return quote
