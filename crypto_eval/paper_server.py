"""Loopback-only HTTP host for the local PAPER futures research console."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import socket
import sys
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .contracts import EvaluationError
from .envfile import load_environment_file
from .gate_market import CHART_INTERVALS, GATE_DATA_ORIGIN, GateUsdtFuturesMarketDataProvider
from .gate_stream import GateLiveMarketStream
from .paper_contracts import PaperTradingError
from .paper_runtime import PaperRuntime, PaperScheduler, PaperStore
from .secret_store import CredentialResolver, default_secret_store


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOTS = {
    "/frontend/": REPOSITORY_ROOT / "frontend",
    "/schemas/": REPOSITORY_ROOT / "schemas",
}
MAX_REQUEST_BYTES = 1_000_000
DEFAULT_PORT = 8765


def default_database_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "crypto-skills"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "crypto-skills"
    return base / "paper-futures.sqlite3"


class PaperHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, runtime: PaperRuntime, scheduler: PaperScheduler):
        self.runtime = runtime
        self.scheduler = scheduler
        self._chart_provider: GateUsdtFuturesMarketDataProvider | None = None
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
            address = (address[0], address[1], 0, 0)
        super().__init__(address, handler)

    def handle_error(self, request, client_address) -> None:
        # Browser/EventSource disconnects are routine; never print request data or tracebacks.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        print("paper server: request failed safely", file=sys.stderr)


class PaperRequestHandler(BaseHTTPRequestHandler):
    server_version = "PaperFuturesResearch/1.0"
    sys_version = ""

    @property
    def runtime(self) -> PaperRuntime:
        return self.server.runtime  # type: ignore[attr-defined,return-value]

    @property
    def scheduler(self) -> PaperScheduler:
        return self.server.scheduler  # type: ignore[attr-defined,return-value]

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _headers(self, content_type: str, *, length: int | None = None) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'",
        )
        if length is not None:
            self.send_header("Content-Length", str(length))

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self._headers(content_type, length=len(body))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise PaperTradingError("requests must use application/json")
        raw_length = self.headers.get("Content-Length", "")
        if not raw_length.isdigit():
            raise PaperTradingError("request body length is invalid")
        length = int(raw_length)
        if length > MAX_REQUEST_BYTES:
            raise PaperTradingError("request body exceeds the supported size")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PaperTradingError("request body must be valid JSON") from None
        if not isinstance(value, dict):
            raise PaperTradingError("request body must be a JSON object")
        return value

    def _validate_request_origin(self) -> None:
        if self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
            raise PaperTradingError("cross-site requests are not accepted")
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and host and origin.rstrip("/") != f"http://{host}":
            raise PaperTradingError("cross-origin requests are not accepted")

    def _safe_error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _static(self, request_path: str) -> bool:
        if request_path == "/":
            request_path = "/frontend/"
        if request_path == "/frontend":
            request_path = "/frontend/"
        root = next(
            (
                directory
                for prefix, directory in STATIC_ROOTS.items()
                if request_path.startswith(prefix)
            ),
            None,
        )
        if root is None:
            return False
        suffix = urllib.parse.unquote(request_path[len("/frontend/") :]) if request_path.startswith(
            "/frontend/"
        ) else urllib.parse.unquote(request_path[len("/schemas/") :])
        if not suffix:
            suffix = "index.html"
        try:
            target = (root / suffix).resolve()
            target.relative_to(root.resolve())
        except (OSError, ValueError):
            self._safe_error(404, "resource not found")
            return True
        if not target.is_file():
            self._safe_error(404, "resource not found")
            return True
        try:
            data = target.read_bytes()
        except OSError:
            self._safe_error(404, "resource not found")
            return True
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type in {"text/html", "text/css", "application/javascript", "text/javascript"}:
            content_type += "; charset=utf-8"
        self._send_bytes(200, data, content_type)
        return True

    # ---- market data for the chart (backend-owned; Gate public only) ----
    @staticmethod
    def _stream_status(stream: GateLiveMarketStream) -> dict[str, Any]:
        stream.refresh_state()
        return stream.status()

    def _chart_symbol(self, query: dict[str, list[str]]) -> str:
        symbol = (query.get("symbol") or [""])[0].strip().upper()
        if not symbol.isalnum() or not 5 <= len(symbol) <= 20:
            raise PaperTradingError("symbol is invalid")
        return symbol

    def _chart_rest(self) -> GateUsdtFuturesMarketDataProvider:
        stream = self.runtime.live_stream
        if stream is not None:
            return stream.rest
        if self.server._chart_provider is None:  # type: ignore[attr-defined]
            self.server._chart_provider = GateUsdtFuturesMarketDataProvider()  # type: ignore[attr-defined]
        return self.server._chart_provider  # type: ignore[attr-defined]

    def _chart_candles(self, query: dict[str, list[str]]) -> dict[str, Any]:
        symbol = self._chart_symbol(query)
        interval = (query.get("interval") or ["1m"])[0]
        if interval not in CHART_INTERVALS:
            raise PaperTradingError("interval must be 1m, 5m, 15m, 1h, or 4h")
        raw_limit = (query.get("limit") or ["300"])[0]
        limit = int(raw_limit) if raw_limit.isdigit() else 300
        limit = max(10, min(1000, limit))
        stream = self.runtime.live_stream
        candles: list[dict[str, Any]] = []
        source = "gate_rest"
        if stream is not None and symbol in stream.symbols and interval in stream.intervals:
            candles = stream.candles(symbol, interval, limit=limit)
            source = "gate_ws+rest"
        if len(candles) < min(limit, 50):
            rest = self._chart_rest()
            closed = rest.fetch_candles(symbol, interval, bars=limit, as_of=datetime.now(timezone.utc))
            candles = [{**item, "closed": True, "source": "rest"} for item in closed]
            source = "gate_rest"
        return {
            "symbol": symbol,
            "interval": interval,
            "data_origin": GATE_DATA_ORIGIN,
            "source": source,
            "synthetic": False,
            "candles": candles,
            "stream": None if stream is None else self._stream_status(stream),
        }

    def _chart_ticker(self, query: dict[str, list[str]]) -> dict[str, Any]:
        symbol = self._chart_symbol(query)
        stream = self.runtime.live_stream
        if stream is not None and symbol in stream.symbols:
            state = stream.symbol_state(symbol)
            if state.get("ticker") or state.get("book"):
                return {"symbol": symbol, "source": "gate_ws", "data_origin": GATE_DATA_ORIGIN, **state}
        rest = self._chart_rest()
        return {
            "symbol": symbol,
            "source": "gate_rest",
            "data_origin": GATE_DATA_ORIGIN,
            "stream_state": "OFFLINE" if stream is None else stream.state,
            "fresh": False,
            "ticker": rest.fetch_ticker(symbol),
            "book": rest.fetch_book_top(symbol),
        }

    def _sse(self, query: dict[str, list[str]]) -> None:
        stream = self.runtime.live_stream
        if stream is None:
            self._safe_error(503, "live market stream is not running")
            return
        symbols = {
            item.strip().upper()
            for item in (query.get("symbols") or [",".join(stream.symbols)])[0].split(",")
            if item.strip()
        }
        interval = (query.get("interval") or ["1m"])[0]
        subscriber = stream.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def emit(event: dict[str, Any]) -> None:
            data = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
            self.wfile.write(f"event: {event['type']}\ndata: {data}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            emit({"type": "status", "status": self._stream_status(stream)})
            while True:
                try:
                    event = subscriber.get(timeout=5)
                except queue.Empty:
                    stream.refresh_state()
                    emit({"type": "status", "status": stream.status()})
                    continue
                if event.get("type") != "status":
                    if event.get("symbol") not in symbols:
                        continue
                    if event.get("type") == "candle" and event.get("interval") != interval:
                        continue
                emit(event)
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass
        finally:
            stream.unsubscribe(subscriber)

    def do_GET(self) -> None:
        split = urllib.parse.urlsplit(self.path)
        path = split.path
        query = urllib.parse.parse_qs(split.query)
        try:
            self._validate_request_origin()
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/frontend/")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            elif path == "/api/health":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "service": "paper-futures",
                        "execution_mode": "PAPER",
                        "real_money_execution": False,
                    },
                )
            elif path == "/api/experiment":
                experiment = self.runtime.store.experiment()
                self._send_json(
                    200,
                    {
                        "experiment": {
                            "experiment_id": experiment["experiment_id"],
                            "status": experiment["status"],
                            "config": experiment["config"],
                            "created_at": experiment["created_at"],
                            "updated_at": experiment["updated_at"],
                        }
                    },
                )
            elif path == "/api/providers":
                self._send_json(200, {"providers": self.runtime.store.list_providers()})
            elif path == "/api/market-data/status":
                experiment = self.runtime.store.experiment()
                self._send_json(
                    200,
                    {
                        "history": self.runtime.store.market_history_status(
                            experiment["experiment_id"]
                        )
                    },
                )
            elif path == "/api/dashboard":
                self._send_json(200, self.runtime.dashboard())
            elif path == "/api/evaluation":
                self._send_json(200, self.runtime.evaluation())
            elif path == "/api/activity":
                experiment = self.runtime.store.experiment()
                self._send_json(
                    200,
                    {
                        "activity": self.runtime.store.activity(
                            experiment["experiment_id"], limit=200
                        )
                    },
                )
            elif path == "/api/cost":
                self._send_json(200, self.runtime.cost_overview())
            elif path == "/api/economics":
                self._send_json(200, self.runtime.economics())
            elif path == "/api/price-book":
                self._send_json(200, {"entries": self.runtime.store.cost_ledger.price_book()})
            elif path == "/api/secret-store":
                store = self.runtime.resolver.store
                self._send_json(
                    200,
                    {"backend": store.backend, "persistent": bool(store.persistent), "plaintext_file": False},
                )
            elif path == "/api/exchange-accounts":
                self._send_json(
                    200,
                    {
                        "accounts": self.runtime.store.list_exchange_accounts(),
                        "write_execution": False,
                        "live_execution_status": "BLOCKED_BY_DESIGN",
                    },
                )
            elif path == "/api/market/status":
                stream = self.runtime.live_stream
                self._send_json(200, {"stream": None if stream is None else self._stream_status(stream)})
            elif path == "/api/market/candles":
                self._send_json(200, self._chart_candles(query))
            elif path == "/api/market/ticker":
                self._send_json(200, self._chart_ticker(query))
            elif path == "/api/market/stream":
                self._sse(query)
                return
            elif path == "/api/integration/latest":
                self._send_json(200, {"check": self.runtime.store.latest_integration_check()})
            elif path == "/api/export":
                body, filename = self.runtime.export_bundle()
                self._send_bytes(
                    200,
                    body,
                    "application/zip",
                    extra_headers={
                        "Content-Disposition": f'attachment; filename="{filename}"'
                    },
                )
            elif self._static(path):
                return
            else:
                self._safe_error(404, "resource not found")
        except PaperTradingError as exc:
            self._safe_error(400, str(exc))
        except (EvaluationError, OSError):
            self._safe_error(500, "request failed safely")
        except Exception:
            self._safe_error(500, "request failed safely")

    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        try:
            self._validate_request_origin()
            if path == "/api/experiment":
                body = self._read_json()
                result = self.runtime.store.save_experiment(body)
                self._send_json(
                    200,
                    {
                        "experiment": {
                            "experiment_id": result["experiment_id"],
                            "status": result["status"],
                            "config": result["config"],
                            "updated_at": result["updated_at"],
                        }
                    },
                )
                return
            if path == "/api/providers":
                body = self._read_json()
                provider = self.runtime.store.save_provider(body)
                self._send_json(200, {"provider": provider})
                return
            if path.startswith("/api/providers/") and path.endswith("/secret"):
                provider_id = urllib.parse.unquote(path[len("/api/providers/") : -len("/secret")].strip("/"))
                body = self._read_json()
                if set(body) != {"value"}:
                    raise PaperTradingError("secret requests contain only a value")
                self._send_json(200, {"secret": self.runtime.save_provider_secret(provider_id, body["value"])})
                return
            if path.startswith("/api/providers/") and path.endswith("/secret/delete"):
                provider_id = urllib.parse.unquote(
                    path[len("/api/providers/") : -len("/secret/delete")].strip("/")
                )
                self._send_json(200, {"secret": self.runtime.delete_provider_secret(provider_id)})
                return
            if path == "/api/price-book":
                body = self._read_json()
                self._send_json(200, {"entry": self.runtime.store.cost_ledger.add_price(body)})
                return
            if path == "/api/cost-controls":
                body = self._read_json()
                if set(body) - {"ai_budget", "cost_fx"}:
                    raise PaperTradingError("cost controls accept ai_budget and cost_fx only")
                experiment = self.runtime.store.update_cost_controls(
                    ai_budget=body.get("ai_budget"), cost_fx=body.get("cost_fx")
                )
                self._send_json(200, {"config": experiment["config"]})
                return
            if path == "/api/exchange-accounts":
                body = self._read_json()
                self._send_json(200, {"account": self.runtime.store.save_exchange_account(body)})
                return
            if path.startswith("/api/exchange-accounts/"):
                remainder = path[len("/api/exchange-accounts/") :].strip("/")
                account_id, _, action = remainder.partition("/")
                account_id = urllib.parse.unquote(account_id)
                if action == "secrets":
                    body = self._read_json()
                    if set(body) != {"api_key", "api_secret"}:
                        raise PaperTradingError("send api_key and api_secret only")
                    self._send_json(
                        200,
                        {"secrets": self.runtime.save_account_secrets(account_id, body["api_key"], body["api_secret"])},
                    )
                    return
                if action in {"test", "sync"}:
                    self._send_json(200, {"sync": self.runtime.sync_gate_account(account_id)})
                    return
                if action == "copy-equity":
                    experiment = self.runtime.copy_gate_equity_to_new_experiment(account_id)
                    self._send_json(200, {"config": experiment["config"]})
                    return
                self._safe_error(404, "resource not found")
                return
            if path.startswith("/api/providers/") and path.endswith("/test"):
                provider_id = urllib.parse.unquote(path[len("/api/providers/") : -len("/test")].strip("/"))
                result = self.runtime.test_provider(provider_id)
                self._send_json(200, {"result": result})
                return
            if path == "/api/runtime/start":
                self._send_json(200, {"experiment": self.scheduler.start()})
                return
            if path == "/api/runtime/pause":
                self._send_json(200, {"experiment": self.scheduler.pause()})
                return
            if path == "/api/runtime/resume":
                self._send_json(200, {"experiment": self.scheduler.resume()})
                return
            if path == "/api/runtime/stop":
                self._send_json(200, {"experiment": self.scheduler.stop()})
                return
            if path == "/api/runtime/cycle":
                body = self._read_json()
                symbol = body.get("symbol")
                manual = body.get("manual", True)
                if not isinstance(manual, bool):
                    raise PaperTradingError("manual must be boolean")
                if symbol is None:
                    cycles = self.runtime.run_symbol_cycles(manual=manual)
                    self._send_json(200, {"cycles": cycles})
                else:
                    if not isinstance(symbol, str):
                        raise PaperTradingError("symbol must be a string")
                    self._send_json(
                        200,
                        {"cycle": self.runtime.run_cycle(symbol, manual=manual)},
                    )
                return
            if path == "/api/runtime/monitor":
                self._send_json(200, {"events": self.runtime.monitor_once()})
                return
            if path == "/api/market-data/test":
                body = self._read_json()
                mode = body.get("mode")
                if not isinstance(mode, str):
                    raise PaperTradingError("mode must be fixture or binance_usdm")
                self._send_json(200, {"result": self.runtime.test_data_source(mode)})
                return
            if path == "/api/market-data/warm-up":
                body = self._read_json()
                profile = body.get("profile", "EXP-001")
                if not isinstance(profile, str):
                    raise PaperTradingError("profile must be EXP-001")
                self._send_json(
                    200,
                    {"result": self.runtime.warm_up_market_history(profile=profile)},
                )
                return
            if path.startswith("/api/positions/") and path.endswith("/reduce"):
                position_id = urllib.parse.unquote(
                    path[len("/api/positions/") : -len("/reduce")].strip("/")
                )
                body = self._read_json()
                fraction = body.get("fraction", 1.0)
                if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
                    raise PaperTradingError("fraction must be a number")
                self._send_json(
                    200,
                    {"result": self.runtime.close_or_reduce(position_id, fraction=float(fraction))},
                )
                return
            self._safe_error(404, "resource not found")
        except PaperTradingError as exc:
            self._safe_error(400, str(exc))
        except (EvaluationError, OSError):
            self._safe_error(500, "request failed safely")
        except Exception:
            self._safe_error(500, "request failed safely")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the loopback-only PAPER futures research console.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--database", type=Path, default=default_database_path())
    parser.add_argument("--no-live-stream", action="store_true", help="do not connect the Gate public WebSocket")
    return parser


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    database: str | Path | None = None,
    live_stream: bool = True,
) -> int:
    if host not in {"127.0.0.1", "::1"}:
        raise PaperTradingError("the local research server must bind to a loopback address")
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise PaperTradingError("port must be between 1024 and 65535")
    load_environment_file(REPOSITORY_ROOT / ".env")
    store = PaperStore(database or default_database_path())
    resolver = CredentialResolver(default_secret_store())
    stream = None
    if live_stream:
        symbols = store.experiment()["config"]["symbols"]
        stream = GateLiveMarketStream(symbols, health_sink=store.record_stream_health)
    runtime = PaperRuntime(store, resolver=resolver, live_stream=stream)
    if stream is not None:
        stream.start()
    scheduler = PaperScheduler(runtime)
    scheduler.resume_on_startup()
    server = PaperHTTPServer((host, port), PaperRequestHandler, runtime, scheduler)
    display_host = f"[{host}]" if ":" in host else host
    print(f"PAPER futures research console: http://{display_host}:{port}/")
    print("Execution mode is permanently PAPER; real-money order routes are not implemented.")
    print(f"Credential store: {resolver.store.backend}; Gate live stream: {'on' if stream else 'off'}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        scheduler.shutdown()
        if stream is not None:
            stream.stop()
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return serve(
            host=args.host, port=args.port, database=args.database, live_stream=not args.no_live_stream
        )
    except PaperTradingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
