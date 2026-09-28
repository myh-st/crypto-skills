"""Backend-owned Gate USDT futures live stream with resilience and a local event fan-out.

The browser never talks to Gate. It consumes normalized events from this process via
SSE. Candles are only ever real exchange observations: REST warm-up/gap-fill plus WS
updates. No continuation candle is synthesized when data is missing.
"""

from __future__ import annotations

import json
import queue
import random
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable

from .gate_market import (
    CHART_INTERVALS,
    GATE_FUTURES_WS_URL,
    GATE_INTERVAL_SECONDS,
    GateUsdtFuturesMarketDataProvider,
    normalize_book_ticker,
    normalize_candle,
    normalize_ticker,
    to_gate_contract,
    from_gate_contract,
)
from .paper_contracts import PaperTradingError, iso_utc, parse_utc
from .ws_client import WebSocketConnection, WebSocketError


STREAM_STATES = ("OFFLINE", "CONNECTING", "LIVE", "STALE", "RECONNECTING")
MAX_CANDLES_PER_LANE = 1500
BOOK_EVENT_MIN_INTERVAL = 0.25


class GateLiveMarketStream:
    def __init__(
        self,
        symbols: list[str],
        *,
        rest: GateUsdtFuturesMarketDataProvider | None = None,
        intervals: tuple[str, ...] = CHART_INTERVALS,
        ws_url: str = GATE_FUTURES_WS_URL,
        ws_factory: Callable[[str], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
        stale_after_seconds: float = 20.0,
        heartbeat_seconds: float = 10.0,
        silence_reconnect_seconds: float = 45.0,
        max_backoff_seconds: float = 60.0,
        history_bars: int = 300,
        reconcile_seconds: float = 300.0,
        health_sink: Callable[[dict[str, Any]], None] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.symbols = [symbol.strip().upper() for symbol in symbols]
        self.intervals = tuple(intervals)
        self.rest = rest or GateUsdtFuturesMarketDataProvider()
        self.rest.live_state = self
        self.ws_url = ws_url
        self._ws_factory = ws_factory or (lambda url: WebSocketConnection(url, timeout=5.0).connect())
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.stale_after_seconds = stale_after_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.silence_reconnect_seconds = silence_reconnect_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.history_bars = history_bars
        self.reconcile_seconds = reconcile_seconds
        self._health_sink = health_sink
        self._sleep = sleep
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws: Any | None = None
        self._subscribers: list[queue.Queue] = []
        self._candles: dict[tuple[str, str], OrderedDict[str, dict[str, Any]]] = {}
        self._tickers: dict[str, dict[str, Any]] = {}
        self._books: dict[str, dict[str, Any]] = {}
        self._book_seq: dict[str, int] = {}
        self._last_update: dict[str, float] = {}
        self._last_book_publish: dict[str, float] = {}
        self.state = "OFFLINE"
        self.ever_connected = False
        self.connected_since: str | None = None
        self.last_message_at: str | None = None
        self.last_error: str | None = None
        self.counters = {
            "messages": 0,
            "candle_updates": 0,
            "ticker_updates": 0,
            "book_updates": 0,
            "reconnects": 0,
            "errors": 0,
            "duplicates": 0,
            "dropped": 0,
            "gap_fills": 0,
            "gap_fill_bars": 0,
            "rest_reconciles": 0,
            "pings": 0,
            "pongs": 0,
        }

    # ---- lifecycle -------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="gate-futures-stream", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._set_state("OFFLINE", reason="stopped")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_symbols(self, symbols: list[str]) -> None:
        normalized = [symbol.strip().upper() for symbol in symbols]
        with self._lock:
            if normalized == self.symbols:
                return
            self.symbols = normalized
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    # ---- state/observers ------------------------------------------
    def _now(self) -> float:
        return self._clock().timestamp()

    def _set_state(self, state: str, *, reason: str | None = None) -> None:
        with self._lock:
            if state == self.state:
                return
            previous = self.state
            self.state = state
        self._publish({"type": "status", "status": self.status()})
        self._health({"event": "state_change", "from": previous, "to": state, "reason": reason})

    def _health(self, extra: dict[str, Any]) -> None:
        if self._health_sink is None:
            return
        try:
            self._health_sink(
                {
                    "observed_at": iso_utc(self._clock()),
                    "state": self.state,
                    **extra,
                    "counters": dict(self.counters),
                    "symbols": {symbol: self.freshness(symbol) for symbol in self.symbols},
                }
            )
        except Exception:
            pass

    def freshness(self, symbol: str) -> dict[str, Any]:
        last = self._last_update.get(symbol)
        age = None if last is None else max(0.0, self._now() - last)
        return {
            "fresh": age is not None and age <= self.stale_after_seconds and self.state in {"LIVE", "STALE"},
            "age_seconds": age,
        }

    def refresh_state(self) -> str:
        with self._lock:
            if self.state not in {"LIVE", "STALE"}:
                return self.state
            fresh = all(self.freshness(symbol)["fresh"] for symbol in self.symbols)
        self._set_state("LIVE" if fresh else "STALE", reason="freshness")
        return self.state

    def symbol_state(self, symbol: str) -> dict[str, Any]:
        normalized = symbol.strip().upper()
        with self._lock:
            freshness = self.freshness(normalized)
            return {
                "symbol": normalized,
                "stream_state": self.state,
                **freshness,
                "ticker": dict(self._tickers[normalized]) if normalized in self._tickers else None,
                "book": dict(self._books[normalized]) if normalized in self._books else None,
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "provider": "gate_usdt_futures",
                "ws_url": self.ws_url,
                "state": self.state,
                "ever_connected": self.ever_connected,
                "connected_since": self.connected_since,
                "last_message_at": self.last_message_at,
                "last_error": self.last_error,
                "stale_after_seconds": self.stale_after_seconds,
                "symbols": {symbol: self.freshness(symbol) for symbol in self.symbols},
                "intervals": list(self.intervals),
                "counters": dict(self.counters),
                "synthetic_candles": False,
            }

    def subscribe(self, maxsize: int = 1000) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def _publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                self.counters["dropped"] += 1

    # ---- candles ---------------------------------------------------
    def candles(self, symbol: str, interval: str, *, limit: int = 500) -> list[dict[str, Any]]:
        key = (symbol.strip().upper(), interval)
        with self._lock:
            lane = self._candles.get(key)
            values = list(lane.values())[-limit:] if lane else []
        return [dict(item) for item in values]

    def _store_candle(self, symbol: str, interval: str, candle: dict[str, Any], *, closed: bool, source: str) -> bool:
        key = (symbol, interval)
        with self._lock:
            lane = self._candles.setdefault(key, OrderedDict())
            existing = lane.get(candle["open_time"])
            if existing is not None and existing.get("closed") and source == "ws" and not closed:
                # A confirmed closed candle is never reopened by a late partial update.
                self.counters["duplicates"] += 1
                return False
            record = {**candle, "closed": bool(closed), "source": source}
            if existing is not None and {k: v for k, v in existing.items() if k != "source"} == {
                k: v for k, v in record.items() if k != "source"
            }:
                self.counters["duplicates"] += 1
                return False
            lane[candle["open_time"]] = record
            if existing is None:
                lane_items = sorted(lane.items())
                lane.clear()
                lane.update(lane_items[-MAX_CANDLES_PER_LANE:])
                # Any earlier still-open candle is now superseded by a newer window.
                for open_time, item in lane.items():
                    if open_time < candle["open_time"] and not item.get("closed"):
                        item["closed"] = True
                        item["source"] = f"{item['source']}+rollover"
        return True

    def backfill(self, *, gap_fill: bool) -> int:
        """REST warm-up/gap-fill of closed candles; returns bars stored."""

        stored = 0
        now = self._clock()
        with self._lock:
            symbols = list(self.symbols)
        for symbol in symbols:
            for interval in self.intervals:
                bars = self.history_bars
                with self._lock:
                    lane = self._candles.get((symbol, interval))
                    last_closed = next(
                        (k for k in reversed(lane) if lane[k].get("closed")), None
                    ) if lane else None
                if gap_fill and last_closed is not None:
                    elapsed = (now - parse_utc(last_closed, "candle.open_time")).total_seconds()
                    bars = max(2, min(self.history_bars, int(elapsed // GATE_INTERVAL_SECONDS[interval]) + 2))
                try:
                    candles = self.rest.fetch_candles(symbol, interval, bars=bars, as_of=now)
                except PaperTradingError:
                    self.counters["errors"] += 1
                    continue
                for candle in candles:
                    if self._store_candle(symbol, interval, candle, closed=True, source="rest"):
                        stored += 1
        if gap_fill:
            self.counters["gap_fills"] += 1
            self.counters["gap_fill_bars"] += stored
            self._health({"event": "gap_fill", "bars": stored})
        return stored

    # ---- message handling -----------------------------------------
    def handle_message(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            self.counters["errors"] += 1
            return
        if not isinstance(message, dict):
            return
        self.counters["messages"] += 1
        self.last_message_at = iso_utc(self._clock())
        channel = message.get("channel")
        event = message.get("event")
        if channel == "futures.pong":
            self.counters["pongs"] += 1
            return
        if event == "subscribe":
            result = message.get("result")
            if isinstance(result, dict) and result.get("status") not in {None, "success"}:
                self.last_error = "subscription rejected"
                self.counters["errors"] += 1
            return
        if message.get("error"):
            self.counters["errors"] += 1
            self.last_error = "stream error message"
            return
        if event not in {"update", "all"}:
            return
        result = message.get("result")
        try:
            if channel == "futures.candlesticks":
                for item in result if isinstance(result, list) else [result]:
                    name = str(item.get("n", ""))
                    interval, _, contract = name.partition("_")
                    if interval not in GATE_INTERVAL_SECONDS:
                        continue
                    symbol = from_gate_contract(contract)
                    contract_size = self.rest.contract(symbol)["contract_size"]
                    candle = normalize_candle(item, interval, contract_size=contract_size)
                    closed = bool(item.get("w"))
                    if self._store_candle(symbol, interval, candle, closed=closed, source="ws"):
                        self.counters["candle_updates"] += 1
                        self._last_update[symbol] = self._now()
                        self._publish(
                            {"type": "candle", "symbol": symbol, "interval": interval, "candle": {**candle, "closed": closed}}
                        )
            elif channel == "futures.tickers":
                for item in result if isinstance(result, list) else [result]:
                    ticker = normalize_ticker(item, observed_at=self._clock())
                    with self._lock:
                        previous = self._tickers.get(ticker["symbol"], {})
                        merged = {**previous, **{k: v for k, v in ticker.items() if v is not None}}
                        self._tickers[ticker["symbol"]] = merged
                    self.counters["ticker_updates"] += 1
                    self._last_update[ticker["symbol"]] = self._now()
                    self._publish({"type": "ticker", "symbol": ticker["symbol"], "ticker": merged})
            elif channel == "futures.book_ticker":
                book = normalize_book_ticker(result)
                symbol = book["symbol"]
                sequence = book.get("update_id")
                if isinstance(sequence, int):
                    if sequence <= self._book_seq.get(symbol, -1):
                        self.counters["duplicates"] += 1
                        return
                    self._book_seq[symbol] = sequence
                with self._lock:
                    self._books[symbol] = book
                self.counters["book_updates"] += 1
                self._last_update[symbol] = self._now()
                last_publish = self._last_book_publish.get(symbol, 0.0)
                if time.monotonic() - last_publish >= BOOK_EVENT_MIN_INTERVAL:
                    self._last_book_publish[symbol] = time.monotonic()
                    self._publish({"type": "book", "symbol": symbol, "book": book})
        except (PaperTradingError, AttributeError, TypeError, KeyError):
            self.counters["errors"] += 1

    def _subscribe_all(self, ws: Any) -> None:
        now = int(self._now())
        contracts = [to_gate_contract(symbol) for symbol in self.symbols]
        for contract in contracts:
            for interval in self.intervals:
                ws.send_text(
                    json.dumps(
                        {"time": now, "channel": "futures.candlesticks", "event": "subscribe", "payload": [interval, contract]}
                    )
                )
            ws.send_text(
                json.dumps({"time": now, "channel": "futures.book_ticker", "event": "subscribe", "payload": [contract]})
            )
        ws.send_text(json.dumps({"time": now, "channel": "futures.tickers", "event": "subscribe", "payload": contracts}))

    def _pause(self, seconds: float) -> None:
        if self._sleep is not None:
            self._sleep(seconds)
        else:
            self._stop.wait(seconds)

    def _run(self) -> None:
        attempt = 0
        warmed = False
        while not self._stop.is_set():
            self._set_state("RECONNECTING" if self.ever_connected else "CONNECTING")
            ws = None
            try:
                self.rest.contracts()
                self.backfill(gap_fill=warmed)
                warmed = True
                ws = self._ws_factory(self.ws_url)
                self._ws = ws
                self._subscribe_all(ws)
                if self.ever_connected:
                    self.counters["reconnects"] += 1
                self.ever_connected = True
                self.connected_since = iso_utc(self._clock())
                attempt = 0
                self._set_state("LIVE")
                self._health({"event": "connected"})
                last_ping = time.monotonic()
                last_message = time.monotonic()
                last_reconcile = time.monotonic()
                while not self._stop.is_set():
                    raw = ws.recv()
                    if raw is not None:
                        last_message = time.monotonic()
                        self.handle_message(raw)
                    if time.monotonic() - last_ping >= self.heartbeat_seconds:
                        ws.send_text(json.dumps({"time": int(self._now()), "channel": "futures.ping"}))
                        self.counters["pings"] += 1
                        last_ping = time.monotonic()
                    if time.monotonic() - last_message > self.silence_reconnect_seconds:
                        raise WebSocketError("stream silent beyond the reconnect threshold")
                    if time.monotonic() - last_reconcile >= self.reconcile_seconds:
                        self.backfill(gap_fill=True)
                        self.counters["rest_reconciles"] += 1
                        last_reconcile = time.monotonic()
                    self.refresh_state()
            except (OSError, WebSocketError, PaperTradingError, ValueError) as exc:
                self.counters["errors"] += 1
                self.last_error = type(exc).__name__
            except Exception:
                self.counters["errors"] += 1
                self.last_error = "unexpected stream failure"
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
                self._ws = None
            if self._stop.is_set():
                break
            self._set_state("RECONNECTING" if self.ever_connected else "OFFLINE", reason=self.last_error)
            attempt += 1
            backoff = min(self.max_backoff_seconds, 2 ** min(attempt, 6))
            self._pause(backoff * (0.8 + 0.4 * random.random()))
        self._set_state("OFFLINE", reason="stopped")
