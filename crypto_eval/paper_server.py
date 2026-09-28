"""Loopback-only HTTP host for the local PAPER futures research console."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import socket
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .contracts import EvaluationError
from .paper_contracts import PaperTradingError
from .paper_runtime import PaperRuntime, PaperScheduler, PaperStore


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
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
            address = (address[0], address[1], 0, 0)
        super().__init__(address, handler)


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

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
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
    return parser


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    database: str | Path | None = None,
) -> int:
    if host not in {"127.0.0.1", "::1"}:
        raise PaperTradingError("the local research server must bind to a loopback address")
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise PaperTradingError("port must be between 1024 and 65535")
    store = PaperStore(database or default_database_path())
    runtime = PaperRuntime(store)
    scheduler = PaperScheduler(runtime)
    scheduler.resume_on_startup()
    server = PaperHTTPServer((host, port), PaperRequestHandler, runtime, scheduler)
    display_host = f"[{host}]" if ":" in host else host
    print(f"PAPER futures research console: http://{display_host}:{port}/")
    print("Execution mode is permanently PAPER; real-money order routes are not implemented.")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        scheduler.shutdown()
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return serve(host=args.host, port=args.port, database=args.database)
    except PaperTradingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
