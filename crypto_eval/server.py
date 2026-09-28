"""Loopback-only HTTP server for the same-origin local research frontend."""

from __future__ import annotations

import ipaddress
import json
import posixpath
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .contracts import EvaluationError
from .forward import (
    ForwardRunNotFoundError,
    ForwardRuntimeService,
    OutcomeNotReadyError,
)
from .market_data import MarketDataError
from .openai_runner import MissingOpenAICredentialsError, OpenAIResponsesError


MAX_REQUEST_BYTES = 32 * 1024
ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = (ROOT / "frontend").resolve()
DECISION_SCHEMA_PATH = (ROOT / "schemas" / "decision-state.schema.json").resolve()
SCORE_PATH = re.compile(r"^/api/forward/([^/]+)/score$")


def _loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _handler_for(runtime: ForwardRuntimeService) -> type[SimpleHTTPRequestHandler]:
    class RuntimeRequestHandler(SimpleHTTPRequestHandler):
        server_version = "CryptoResearchRuntime/1.0"

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(ROOT), **kwargs)

        def end_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self'; script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
            if self.path.startswith("/api/"):
                self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            try:
                if path == "/api/status":
                    self._send_json(200, runtime.status())
                    return
                if path == "/api/evaluations":
                    self._send_json(
                        200,
                        {
                            "schema_version": "crypto-eval.forward-list.v1",
                            "runs": runtime.list_runs(),
                        },
                    )
                    return
                if path.startswith("/api/"):
                    self._send_json(
                        404,
                        {
                            "error": {
                                "code": "not_found",
                                "message": "API endpoint was not found",
                            }
                        },
                    )
                    return
                if path in {"/", "/frontend"}:
                    self.send_response(302)
                    self.send_header("Location", "/frontend/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                decoded_path = posixpath.normpath(unquote(path))
                if decoded_path in {"/", "/frontend"}:
                    decoded_path = "/frontend/"
                requested_path = (ROOT / decoded_path.lstrip("/")).resolve()
                in_frontend = requested_path == FRONTEND_ROOT or (
                    FRONTEND_ROOT in requested_path.parents
                )
                if not in_frontend and requested_path != DECISION_SCHEMA_PATH:
                    self._send_json(
                        404,
                        {
                            "error": {
                                "code": "not_found",
                                "message": "static resource was not found",
                            }
                        },
                    )
                    return
                super().do_GET()
            except Exception:
                self._send_json(
                    500,
                    {
                        "error": {
                            "code": "runtime_failure",
                            "message": "local runtime could not complete the request",
                        }
                    },
                )

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            try:
                self._validate_same_origin()
                if path == "/api/analyze":
                    result = runtime.create(self._read_json_body())
                    self._send_json(201, result)
                    return
                match = SCORE_PATH.fullmatch(path)
                if match:
                    self._read_optional_empty_body()
                    result = runtime.score(match.group(1))
                    self._send_json(200, result)
                    return
                self._send_json(
                    404,
                    {
                        "error": {
                            "code": "not_found",
                            "message": "API endpoint was not found",
                        }
                    },
                )
            except MissingOpenAICredentialsError as exc:
                self._send_error_json(503, "model_credentials_missing", str(exc))
            except OutcomeNotReadyError as exc:
                self._send_error_json(409, "horizon_not_closed", str(exc))
            except ForwardRunNotFoundError as exc:
                self._send_error_json(404, "forward_run_not_found", str(exc))
            except MarketDataError as exc:
                self._send_error_json(502, "market_data_unavailable", str(exc))
            except OpenAIResponsesError as exc:
                self._send_error_json(502, "model_request_failed", str(exc))
            except EvaluationError as exc:
                self._send_error_json(400, "invalid_request", str(exc))
            except _RequestBodyError as exc:
                self._send_error_json(exc.status_code, exc.code, str(exc))
            except Exception:
                self._send_error_json(
                    500,
                    "runtime_failure",
                    "local runtime could not complete the request",
                )

        def _validate_same_origin(self) -> None:
            origin = self.headers.get("Origin")
            if not origin:
                return
            parsed_origin = urlsplit(origin)
            host_header = self.headers.get("Host", "")
            if (
                parsed_origin.scheme != "http"
                or parsed_origin.netloc.lower() != host_header.lower()
                or not parsed_origin.hostname
                or not _loopback_host(parsed_origin.hostname)
            ):
                raise _RequestBodyError(
                    403,
                    "cross_origin_request",
                    "runtime API accepts same-origin loopback requests only",
                )

        def _read_optional_empty_body(self) -> None:
            length = self._content_length()
            if length > MAX_REQUEST_BYTES:
                raise _RequestBodyError(413, "request_too_large", "request body is too large")
            if length > 0:
                body = self.rfile.read(length)
                if body.strip():
                    raise _RequestBodyError(
                        400,
                        "unexpected_body",
                        "score endpoint does not accept a client-supplied outcome",
                    )

        def _read_json_body(self) -> dict[str, Any]:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise _RequestBodyError(
                    415,
                    "unsupported_media_type",
                    "request content type must be application/json",
                )
            length = self._content_length()
            if length < 1:
                raise _RequestBodyError(400, "empty_body", "JSON request body is required")
            if length > MAX_REQUEST_BYTES:
                raise _RequestBodyError(413, "request_too_large", "request body is too large")
            try:
                value = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise _RequestBodyError(400, "invalid_json", "request body is not valid JSON") from None
            if not isinstance(value, dict):
                raise _RequestBodyError(400, "invalid_json", "request body must be a JSON object")
            return value

        def _content_length(self) -> int:
            raw = self.headers.get("Content-Length")
            if raw is None:
                if self.headers.get("Transfer-Encoding"):
                    raise _RequestBodyError(
                        400,
                        "invalid_content_length",
                        "chunked request bodies are not supported",
                    )
                return 0
            if not raw.isdigit():
                raise _RequestBodyError(400, "invalid_content_length", "content length is invalid")
            return int(raw)

        def _send_error_json(self, status: int, code: str, message: str) -> None:
            self._send_json(status, {"error": {"code": code, "message": message}})

        def _send_json(self, status: int, value: Any) -> None:
            try:
                payload = json.dumps(
                    value,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError):
                status = 500
                payload = (
                    b'{"error":{"code":"runtime_failure",'
                    b'"message":"local runtime could not serialize the response"}}'
                )
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return RuntimeRequestHandler


class _RequestBodyError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class LocalRuntimeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_runtime_server(
    runtime: ForwardRuntimeService,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> LocalRuntimeHTTPServer:
    if not _loopback_host(host):
        raise EvaluationError("local runtime must bind to a loopback interface")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise EvaluationError("server port must be between 0 and 65535")
    return LocalRuntimeHTTPServer((host, port), _handler_for(runtime))


def serve_runtime(
    runtime: ForwardRuntimeService,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    server = create_runtime_server(runtime, host=host, port=port)
    address, actual_port = server.server_address[:2]
    print(f"Local research runtime listening at http://{address}:{actual_port}/frontend/")
    try:
        server.serve_forever()
    finally:
        server.server_close()
