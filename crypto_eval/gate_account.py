"""Read-only Gate account mirror and a live execution adapter that is disabled by construction.

The authenticated client enforces an HTTP method allowlist (GET only) and an exact
endpoint allowlist *before* any bytes reach the transport. Any mutating operation —
order create/amend/cancel, leverage/margin changes, transfers, withdrawals — raises
``LiveExecutionBlocked`` without constructing a network request.

The mirror is context only. It is never merged with the PAPER wallet and the PAPER
engine has no code path into it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from .paper_contracts import PaperTradingError, iso_utc


GATE_LIVE_BASE = "https://api.gateio.ws/api/v4"
GATE_TESTNET_BASE = "https://fx-api-testnet.gateio.ws/api/v4"
READ_ONLY_METHODS = frozenset({"GET"})
# path → allowed query keys
READ_ONLY_ENDPOINTS: dict[str, frozenset[str]] = {
    "/account/detail": frozenset(),
    "/futures/usdt/accounts": frozenset(),
    "/futures/usdt/positions": frozenset({"holding", "limit", "offset"}),
    "/futures/usdt/orders": frozenset({"contract", "status", "limit", "offset", "last_id"}),
    "/futures/usdt/my_trades": frozenset({"contract", "limit", "offset", "last_id"}),
    # Spot wallet mirror for Holdings: balances and the user's own fills (GET only).
    "/spot/accounts": frozenset({"currency"}),
    "/spot/my_trades": frozenset({"currency_pair", "limit", "page", "from", "to"}),
}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
ACCOUNT_SOURCES = {"live": "GATE_LIVE_READONLY", "testnet": "GATE_TESTNET_READONLY"}


class LiveExecutionBlocked(PaperTradingError):
    """A real-exchange write was attempted; it is blocked by design before transport."""


class GateAccountError(PaperTradingError):
    """Read-only account call failed; message never includes credentials or bodies.

    ``status`` (HTTP code), ``label`` (Gate's machine error code, e.g. ``INVALID_KEY``, validated
    as an uppercase token) and ``kind`` (http, timeout, network, invalid) are safe diagnostics.
    """

    def __init__(
        self, message: str, *, status: int | None = None, label: str | None = None, kind: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.label = label
        self.kind = kind


_ERROR_LABEL_RE = re.compile(r"[A-Z][A-Z0-9_]{1,63}")


def _error_label(exc: urllib.error.HTTPError) -> str | None:
    """Gate's ``label`` from an error body; only a bare uppercase token survives, never body text."""

    try:
        body = json.loads(exc.read(65536))
    except Exception:
        return None
    label = body.get("label") if isinstance(body, dict) else None
    return label if isinstance(label, str) and _ERROR_LABEL_RE.fullmatch(label) else None


SignedTransport = Callable[[str, str, dict[str, str], float], bytes]


def _signed_get(method: str, url: str, headers: dict[str, str], timeout: float) -> bytes:
    if method != "GET":  # defense in depth; the client already refused
        raise LiveExecutionBlocked("non-GET Gate requests are blocked by design")
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_RESPONSE_BYTES + 1)


def gate_signature(secret: str, method: str, path: str, query: str, body: bytes, timestamp: str) -> str:
    body_hash = hashlib.sha512(body).hexdigest()
    payload = f"{method}\n{path}\n{query}\n{body_hash}\n{timestamp}"
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha512).hexdigest()


class ReadOnlyGateClient:
    """Signed GET-only Gate APIv4 client with an endpoint allowlist."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        environment: str = "live",
        timeout: float = 10.0,
        transport: SignedTransport | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if environment not in ACCOUNT_SOURCES:
            raise GateAccountError("Gate environment must be live or testnet")
        if not api_key or not api_secret:
            raise GateAccountError("Gate API key and secret are required for read-only sync")
        self._api_key = api_key
        self._api_secret = api_secret
        self.environment = environment
        self.base_url = GATE_LIVE_BASE if environment == "live" else GATE_TESTNET_BASE
        self.timeout = timeout
        self._transport = transport or _signed_get
        self._clock = clock or time.time
        self.request_log: list[dict[str, Any]] = []

    def request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        method = method.upper()
        if method not in READ_ONLY_METHODS:
            raise LiveExecutionBlocked(f"{method} to Gate is blocked by design; this branch is read-only")
        if path not in READ_ONLY_ENDPOINTS:
            raise LiveExecutionBlocked("Gate endpoint is not on the read-only allowlist")
        params = {key: value for key, value in (params or {}).items() if value is not None}
        if set(params) - READ_ONLY_ENDPOINTS[path]:
            raise LiveExecutionBlocked("Gate query parameter is not on the read-only allowlist")
        query = urllib.parse.urlencode(sorted(params.items()))
        timestamp = str(int(self._clock()))
        prefix = urllib.parse.urlsplit(self.base_url).path
        signature = gate_signature(self._api_secret, method, f"{prefix}{path}", query, b"", timestamp)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "KEY": self._api_key,
            "Timestamp": timestamp,
            "SIGN": signature,
        }
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        started = time.perf_counter()
        try:
            raw = self._transport(method, url, headers, self.timeout)
        except LiveExecutionBlocked:
            raise
        except urllib.error.HTTPError as exc:
            self.request_log.append({"method": method, "path": path, "status": exc.code})
            raise GateAccountError(
                f"Gate read-only request returned HTTP {exc.code}", status=exc.code, label=_error_label(exc), kind="http"
            ) from None
        except (TimeoutError, socket.timeout):
            raise GateAccountError("Gate read-only request timed out", kind="timeout") from None
        except (urllib.error.URLError, OSError):
            raise GateAccountError("Gate read-only request failed", kind="network") from None
        self.request_log.append(
            {"method": method, "path": path, "status": 200, "latency_ms": (time.perf_counter() - started) * 1000}
        )
        if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_RESPONSE_BYTES:
            raise GateAccountError("Gate read-only response is invalid", kind="invalid")
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise GateAccountError("Gate read-only response is invalid JSON", kind="invalid") from None

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params)


class ExchangeExecutionAdapter(Protocol):
    def place_order(self, **kwargs: Any) -> Any: ...

    def cancel_order(self, **kwargs: Any) -> Any: ...

    def amend_order(self, **kwargs: Any) -> Any: ...

    def set_leverage(self, **kwargs: Any) -> Any: ...


class DisabledLiveExecutionAdapter:
    """Interface placeholder for a future, separately reviewed live goal. Every call refuses."""

    write_execution = False

    def _blocked(self, operation: str) -> None:
        raise LiveExecutionBlocked(
            f"Gate live {operation} is BLOCKED BY DESIGN in this branch; PAPER execution only"
        )

    def place_order(self, **_: Any) -> Any:
        self._blocked("order placement")

    def cancel_order(self, **_: Any) -> Any:
        self._blocked("order cancellation")

    def amend_order(self, **_: Any) -> Any:
        self._blocked("order amendment")

    def set_leverage(self, **_: Any) -> Any:
        self._blocked("leverage change")

    def set_margin_mode(self, **_: Any) -> Any:
        self._blocked("margin-mode change")

    def transfer(self, **_: Any) -> Any:
        self._blocked("transfer")

    def withdraw(self, **_: Any) -> Any:
        self._blocked("withdrawal")


def exchange_capability(**overrides: Any) -> dict[str, Any]:
    capability = {
        "authenticated": False,
        "environment": None,
        "futures_read": False,
        "account_sync": False,
        "balance_sync": False,
        "positions_sync": False,
        "orders_read": False,
        "trades_read": False,
        "write_execution": False,
    }
    capability.update(overrides)
    capability["write_execution"] = False
    return capability


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _symbol(contract: Any) -> str | None:
    if isinstance(contract, str) and re.fullmatch(r"[A-Z0-9]{1,16}_USDT", contract):
        return contract.replace("_", "")
    return None


def normalize_futures_account(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise GateAccountError("Gate futures account response is malformed")
    return {
        "currency": raw.get("currency", "USDT"),
        "total": _float(raw.get("total")),
        "available": _float(raw.get("available")),
        "unrealised_pnl": _float(raw.get("unrealised_pnl")),
        "position_margin": _float(raw.get("position_margin")),
        "order_margin": _float(raw.get("order_margin")),
        "equity": (
            None
            if _float(raw.get("total")) is None
            else _float(raw.get("total")) + (_float(raw.get("unrealised_pnl")) or 0.0)
        ),
        "in_dual_mode": bool(raw.get("in_dual_mode")),
    }


def normalize_position(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    size = _float(raw.get("size"))
    if not size:
        return None
    return {
        "symbol": _symbol(raw.get("contract")),
        "exchange_contract": raw.get("contract"),
        "side": "long" if size > 0 else "short",
        "size_contracts": abs(size),
        "entry_price": _float(raw.get("entry_price")),
        "mark_price": _float(raw.get("mark_price")),
        "liquidation_price": _float(raw.get("liq_price")),
        "leverage": _float(raw.get("leverage")),
        "unrealised_pnl": _float(raw.get("unrealised_pnl")),
        "margin": _float(raw.get("margin")),
        "mode": raw.get("mode"),
    }


def normalize_order(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return {
        "order_id": str(raw.get("id")),
        "symbol": _symbol(raw.get("contract")),
        "size_contracts": _float(raw.get("size")),
        "left_contracts": _float(raw.get("left")),
        "price": _float(raw.get("price")),
        "fill_price": _float(raw.get("fill_price")),
        "status": raw.get("status"),
        "finish_as": raw.get("finish_as"),
        "reduce_only": bool(raw.get("is_reduce_only")),
        "created_at": (
            iso_utc(datetime.fromtimestamp(float(raw["create_time"]), tz=timezone.utc))
            if _float(raw.get("create_time"))
            else None
        ),
    }


def normalize_trade(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return {
        "trade_id": str(raw.get("id") or raw.get("trade_id")),
        "order_id": str(raw.get("order_id")),
        "symbol": _symbol(raw.get("contract")),
        "size_contracts": _float(raw.get("size")),
        "price": _float(raw.get("price")),
        "fee": _float(raw.get("fee")),
        "role": raw.get("role"),
        "created_at": (
            iso_utc(datetime.fromtimestamp(float(raw["create_time"]), tz=timezone.utc))
            if _float(raw.get("create_time"))
            else None
        ),
    }


_CURRENCY_RE = re.compile(r"[A-Z0-9]{1,20}")
_SPOT_PAIR_RE = re.compile(r"([A-Z0-9]{1,20})_([A-Z0-9]{2,10})")


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def normalize_spot_balance(raw: Any) -> dict[str, Any] | None:
    """One ``/spot/accounts`` row -> ``{currency, available, locked, total}``; None when malformed."""

    if not isinstance(raw, dict):
        return None
    currency = raw.get("currency")
    if not isinstance(currency, str) or not _CURRENCY_RE.fullmatch(currency.upper()):
        return None
    available = _float(raw.get("available"))
    locked = _float(raw.get("locked"))
    if not _finite(available) or not _finite(locked) or available < 0 or locked < 0:
        return None
    return {"currency": currency.upper(), "available": available, "locked": locked, "total": available + locked}


def normalize_spot_trade(raw: Any) -> dict[str, Any] | None:
    """One ``/spot/my_trades`` fill -> a typed fill; None when malformed (never guessed)."""

    if not isinstance(raw, dict):
        return None
    pair = raw.get("currency_pair")
    match = _SPOT_PAIR_RE.fullmatch(pair.upper()) if isinstance(pair, str) else None
    side = raw.get("side")
    amount = _float(raw.get("amount"))
    price = _float(raw.get("price"))
    if match is None or side not in {"buy", "sell"}:
        return None
    if not _finite(amount) or not _finite(price) or amount <= 0 or price <= 0:
        return None
    created_ms = _float(raw.get("create_time_ms"))
    if created_ms is None:
        seconds = _float(raw.get("create_time"))
        created_ms = None if seconds is None else seconds * 1000.0
    if not _finite(created_ms) or created_ms <= 0:
        return None
    fee = _float(raw.get("fee"))
    fee_currency = raw.get("fee_currency")
    fee_currency = fee_currency.upper() if isinstance(fee_currency, str) and fee_currency else None
    point_fee = _float(raw.get("point_fee"))
    gt_fee = _float(raw.get("gt_fee"))
    return {
        "trade_id": str(raw.get("id")),
        "order_id": None if raw.get("order_id") is None else str(raw.get("order_id")),
        "currency_pair": match.group(0),
        "base": match.group(1),
        "quote": match.group(2),
        "side": side,
        "role": raw.get("role"),
        "amount": amount,
        "price": price,
        "fee": fee if _finite(fee) and fee > 0 else 0.0,
        "fee_currency": fee_currency,
        "point_fee": point_fee if _finite(point_fee) and point_fee > 0 else 0.0,
        "gt_fee": gt_fee if _finite(gt_fee) and gt_fee > 0 else 0.0,
        "created_at_ms": created_ms,
        "created_at": iso_utc(datetime.fromtimestamp(created_ms / 1000.0, tz=timezone.utc)),
    }


def sync_read_only_account(client: ReadOnlyGateClient, *, include_trades: bool = True) -> dict[str, Any]:
    """Run the read-only sync; each capability is proven by a real GET or reported false."""

    result: dict[str, Any] = {
        "source": ACCOUNT_SOURCES[client.environment],
        "environment": client.environment,
        "synced_at": iso_utc(datetime.now(timezone.utc)),
        "account": None,
        "balance": None,
        "positions": [],
        "open_orders": [],
        "recent_orders": [],
        "recent_trades": [],
        "errors": {},
    }
    capability = exchange_capability(environment=client.environment)

    def attempt(name: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except GateAccountError as exc:
            result["errors"][name] = str(exc)
            return None

    detail = attempt("account_detail", lambda: client.get("/account/detail"))
    if isinstance(detail, dict):
        result["account"] = {"user_id_present": detail.get("user_id") is not None, "tier": detail.get("tier")}
        capability["account_sync"] = True
    balance = attempt("futures_account", lambda: client.get("/futures/usdt/accounts"))
    if isinstance(balance, dict):
        result["balance"] = normalize_futures_account(balance)
        capability["authenticated"] = True
        capability["futures_read"] = True
        capability["balance_sync"] = True
    positions = attempt("positions", lambda: client.get("/futures/usdt/positions", {"holding": "true"}))
    if isinstance(positions, list):
        result["positions"] = [item for item in (normalize_position(row) for row in positions) if item]
        capability["positions_sync"] = True
        capability["authenticated"] = True
    open_orders = attempt("open_orders", lambda: client.get("/futures/usdt/orders", {"status": "open"}))
    if isinstance(open_orders, list):
        result["open_orders"] = [item for item in (normalize_order(row) for row in open_orders) if item]
        capability["orders_read"] = True
    finished = attempt(
        "finished_orders", lambda: client.get("/futures/usdt/orders", {"status": "finished", "limit": 50})
    )
    if isinstance(finished, list):
        result["recent_orders"] = [item for item in (normalize_order(row) for row in finished) if item]
    if include_trades:
        trades = attempt("trades", lambda: client.get("/futures/usdt/my_trades", {"limit": 50}))
        if isinstance(trades, list):
            result["recent_trades"] = [item for item in (normalize_trade(row) for row in trades) if item]
            capability["trades_read"] = True
    result["capability"] = capability
    result["request_log"] = [
        {key: value for key, value in entry.items() if key in {"method", "path", "status", "latency_ms"}}
        for entry in client.request_log
    ]
    result["non_get_requests_emitted"] = sum(1 for entry in client.request_log if entry["method"] != "GET")
    return result
