"""Holdings: what the user actually holds on spot, from manual entries and a READ-ONLY Gate sync.

Decision support only. Nothing here places, amends or cancels an order, and nothing here can:
the Gate client is the GET-only, exact-allowlist ``ReadOnlyGateClient`` from ``gate_account.py``,
and the only authenticated paths used are ``/spot/accounts`` and ``/spot/my_trades``.

Cost basis is a moving average over the user's own ``{BASE}_USDT`` fills, walking back at most
365 days or 2,000 fills. A balance the fills do not explain (deposits, transfers, other quote
pairs) gets ``avg_source: "unknown"`` and a ``cost_basis_note``; a cost is never invented.
Prices come from Gate public spot tickers. Secrets never reach SQLite, responses or logs.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .contracts import canonical_json
from .gate_account import (
    GateAccountError,
    ReadOnlyGateClient,
    normalize_spot_balance,
    normalize_spot_trade,
)
from .paper_contracts import PaperTradingError, iso_utc
from .secret_store import scrub


HOLDINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings_manual(
    id TEXT PRIMARY KEY,
    base TEXT NOT NULL UNIQUE,
    qty REAL NOT NULL,
    avg_price REAL NOT NULL,
    opened_at TEXT,
    note TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holdings_gate_syncs(
    sync_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    payload_json TEXT NOT NULL,
    synced_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS holdings_gate_syncs_lookup_idx
    ON holdings_gate_syncs(account_id, sync_id DESC);
CREATE TABLE IF NOT EXISTS holdings_settings(
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

QUOTE = "USDT"
STABLECOINS = ("USDT", "USDC")
MAX_LOOKBACK_DAYS = 365
MAX_FILLS = 2000
TRADE_WINDOW_DAYS = 30  # Gate bounds a my_trades time range; walk back in windows of this size
TRADE_PAGE_LIMIT = 1000
MAX_PAGES_PER_WINDOW = 5
QTY_TOLERANCE_REL = 1e-3  # a balance within 0.1% of the traded quantity counts as explained
QTY_TOLERANCE_ABS = 1e-9
TICKER_TTL_SECONDS = 30.0
SYNC_INTERVAL_SECONDS = 15 * 60
SYNC_HISTORY_KEEP = 200
RULE_STATES = frozenset({"HOLD", "CASH", "WATCH"})
GATE_STATUSES = ("OK", "NOT_CONFIGURED", "ERROR")
DEFAULT_SETTINGS = {"share_holdings_with_ai": False}
MASK = "••••••••"
READ_ONLY_NOTE = (
    "Gate's API cannot prove a key is read-only without attempting a write, and this app never attempts one. "
    "On Gate, confirm the key was created with only the spot read-only permission (no trading, no withdrawal) "
    "and, if possible, an IP whitelist."
)
AUTH_ERROR_LABELS = frozenset({
    "INVALID_KEY", "INVALID_SIGNATURE", "INVALID_CREDENTIALS", "MISSING_REQUIRED_HEADER", "REQUEST_EXPIRED",
    "INVALID_TIMESTAMP", "IP_FORBIDDEN", "IP_NOT_ALLOWED", "KEY_EXPIRED",
})
PERMISSION_ERROR_LABELS = frozenset({"FORBIDDEN", "INVALID_KEY_PERMISSION", "ACCOUNT_LOCKED"})

BASE_RE = re.compile(r"[A-Z0-9]{1,20}")
ACCOUNT_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
MANUAL_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_NOTE_CHARS = 500
EPS = 1e-12

RuleStateProvider = Callable[[str], "str | None"]


def _now_iso(value: datetime) -> str:
    return iso_utc(value.astimezone(timezone.utc))


def _round(value: float | None, digits: int = 8) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0 else rounded


def _fmt(value: float) -> str:
    return f"{value:.8g}"


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PaperTradingError(f"{field} must be a number greater than 0")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise PaperTradingError(f"{field} must be a number greater than 0")
    return number


# ---------------------------------------------------------------- cost basis (pure)
def moving_average_basis(fills: list[dict[str, Any]], *, base: str, quote: str = QUOTE) -> dict[str, Any]:
    """Moving-average cost of ``base`` from normalized spot fills, oldest first.

    - buy: ``cost += amount*price`` (+ fee when the fee is in the quote); ``qty += amount``
      (- fee when the fee is in the base).
    - sell: ``realized += (price - avg)*amount - quote fee``; cost falls by ``avg*amount`` so the
      average is unchanged. A sell larger than the tracked quantity only realizes the matched part.
    - GT, point or other-currency fees cannot be converted: they are totalled in ``other_fees``.
    """

    qty = 0.0
    cost = 0.0
    realized = 0.0
    counted = 0
    unmatched_sell = 0.0
    other_fees: dict[str, float] = {}

    def add_other(currency: str, amount: float) -> None:
        if amount > 0:
            other_fees[currency] = other_fees.get(currency, 0.0) + amount

    ordered = sorted(
        (f for f in fills if f.get("base") == base and f.get("quote") == quote),
        key=lambda f: (f["created_at_ms"], str(f.get("trade_id"))),
    )
    for fill in ordered:
        counted += 1
        amount = float(fill["amount"])
        price = float(fill["price"])
        fee = float(fill.get("fee") or 0.0)
        fee_currency = fill.get("fee_currency")
        quote_fee = fee if fee_currency == quote else 0.0
        base_fee = fee if fee_currency == base else 0.0
        if fee > 0 and fee_currency not in {base, quote}:
            add_other(fee_currency or "UNKNOWN", fee)
        if fee_currency != "POINT":
            add_other("POINT", float(fill.get("point_fee") or 0.0))
        if fee_currency != "GT":
            add_other("GT", float(fill.get("gt_fee") or 0.0))
        if fill["side"] == "buy":
            cost += amount * price + quote_fee
            qty += amount - base_fee
        else:
            matched = min(amount, max(qty, 0.0))
            if matched > 0:
                avg = cost / qty
                realized += (price - avg) * matched
                cost -= avg * matched
                qty -= matched
            unmatched_sell += amount - matched
            realized -= quote_fee
            if base_fee > 0 and qty > EPS:
                consumed = min(base_fee, qty)
                avg = cost / qty
                cost -= avg * consumed
                realized -= avg * consumed  # coins paid as fee leave at their cost basis
                qty -= consumed
        if qty <= EPS:
            qty, cost = 0.0, 0.0
    return {
        "qty": qty,
        "cost": cost,
        "avg_price": cost / qty if qty > EPS else None,
        "realized": realized,
        "trades_counted": counted,
        "unmatched_sell_qty": unmatched_sell,
        "other_fees": other_fees,
    }


def reconcile_basis(
    base: str, balance_qty: float, basis: dict[str, Any], *, truncated: bool = False
) -> dict[str, Any]:
    """Compare a wallet balance with the traded quantity; unexplained balance -> unknown cost."""

    tracked = float(basis["qty"])
    avg = basis["avg_price"]
    tolerance = max(QTY_TOLERANCE_ABS, QTY_TOLERANCE_REL * max(balance_qty, tracked))
    notes: list[str] = []
    if tracked <= tolerance or avg is None:
        avg_source = "unknown"
        notes.append(
            f"No {base}_USDT buys in the last {MAX_LOOKBACK_DAYS} days explain this balance (deposit, transfer "
            "or another quote pair?). The cost is unknown; set a manual average price to track P&L."
        )
    elif balance_qty - tracked > tolerance:
        avg_source = "unknown"
        notes.append(
            f"Trades explain {_fmt(tracked)} of {_fmt(balance_qty)} {base} (average {_fmt(avg)} USDT); the other "
            f"{_fmt(balance_qty - tracked)} came from deposits or transfers, so the cost is unknown. "
            "Set a manual average price to track P&L."
        )
    else:
        avg_source = "gate_trades"
        if tracked - balance_qty > tolerance:
            notes.append(
                f"The balance is {_fmt(tracked - balance_qty)} {base} lower than the trades imply (withdrawal or "
                "transfer out?); the moving average is kept."
            )
    if basis["unmatched_sell_qty"] > tolerance:
        notes.append(
            f"{_fmt(basis['unmatched_sell_qty'])} {base} was sold without a matching buy in the look-back; "
            "realized P&L excludes it."
        )
    if basis["other_fees"]:
        fees = ", ".join(f"{_fmt(v)} {k}" for k, v in sorted(basis["other_fees"].items()))
        notes.append(f"Fees paid in {fees} are not converted to USDT and are excluded from the cost.")
    if truncated:
        notes.append(f"History is limited to the latest {MAX_FILLS} fills.")
    known = avg_source == "gate_trades"
    return {
        "qty": balance_qty,
        "avg_price": avg if known else None,
        "avg_source": avg_source,
        "realized_usdt": basis["realized"],
        "trades_counted": basis["trades_counted"],
        "cost_basis_note": " ".join(notes) or None,
    }


def alignment_for(rule_state: str | None, held: bool | None) -> str | None:
    """Holding vs the co-trader rule. WATCH means the rule holds nothing, like CASH."""

    if rule_state not in RULE_STATES or held is None:
        return None
    if held:
        return "aligned" if rule_state == "HOLD" else "holding_in_cash_state"
    return "not_held_in_hold_state" if rule_state == "HOLD" else "aligned"


def fetch_spot_fills(
    client: ReadOnlyGateClient,
    currency_pair: str,
    *,
    now_s: float,
    max_days: int = MAX_LOOKBACK_DAYS,
    max_fills: int = MAX_FILLS,
) -> tuple[list[dict[str, Any]], bool]:
    """Newest-first walk back in bounded windows until 365 days or 2,000 fills. GET only."""

    fills: dict[str, dict[str, Any]] = {}
    earliest = int(now_s) - max_days * 86400
    end = int(now_s)
    while end > earliest and len(fills) < max_fills:
        start = max(earliest, end - TRADE_WINDOW_DAYS * 86400)
        for page in range(1, MAX_PAGES_PER_WINDOW + 1):
            rows = client.get(
                "/spot/my_trades",
                {"currency_pair": currency_pair, "limit": TRADE_PAGE_LIMIT, "page": page, "from": start, "to": end},
            )
            if not isinstance(rows, list):
                raise GateAccountError("Gate spot trades response is malformed")
            for row in rows:
                fill = normalize_spot_trade(row)
                if fill is None or fill["currency_pair"] != currency_pair:
                    continue
                key = fill["trade_id"] if fill["trade_id"] not in {"None", ""} else (
                    f"{fill['created_at_ms']}:{fill['side']}:{fill['amount']}:{fill['price']}"
                )
                fills[key] = fill
            if len(rows) < TRADE_PAGE_LIMIT or len(fills) >= max_fills:
                break
        end = start
    ordered = sorted(fills.values(), key=lambda f: (f["created_at_ms"], f["trade_id"]))
    truncated = len(ordered) >= max_fills
    return ordered[-max_fills:], truncated


def _default_ticker_source() -> Callable[[], dict[str, dict[str, Any]]]:
    from .market_catalog import GateSpotMarketDataProvider

    return GateSpotMarketDataProvider().tickers


# ---------------------------------------------------------------- service
class Holdings:
    """Manual + Gate (read-only) spot holdings, priced with Gate public tickers.

    ``rule_state_provider`` is an optional callable ``base -> "HOLD"|"CASH"|"WATCH"|None``; the
    co-trader wires it later. Without it, ``rule_state`` and ``alignment`` are null.
    """

    def __init__(
        self,
        runtime: Any,
        *,
        rule_state_provider: RuleStateProvider | None = None,
        ticker_source: Callable[[], dict[str, dict[str, Any]]] | None = None,
        gate_transport: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.runtime = runtime
        self.store = runtime.store
        self.rule_state_provider = rule_state_provider
        self._ticker_source = ticker_source
        self._gate_transport = gate_transport
        self._clock = clock or getattr(runtime, "_clock", None) or (lambda: datetime.now(timezone.utc))
        self._sync_lock = threading.Lock()
        self._ticker_lock = threading.Lock()
        self._tickers: dict[str, dict[str, Any]] | None = None
        self._tickers_at = 0.0
        self._ticker_error: str | None = None

    # ---------------------------------------------------------- helpers
    def _now(self) -> datetime:
        return self._clock().astimezone(timezone.utc)

    def _query(self, sql: str, params: tuple = ()) -> list[Any]:
        return self.store._query(sql, params)

    @staticmethod
    def _json(value: Any) -> str:
        return canonical_json(value)

    @staticmethod
    def _loads(value: Any, fallback: Any) -> Any:
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback

    # ---------------------------------------------------------- settings
    def settings(self) -> dict[str, Any]:
        rows = self._query("SELECT key, value_json FROM holdings_settings")
        values = dict(DEFAULT_SETTINGS)
        for row in rows:
            if row["key"] in values:
                values[row["key"]] = self._loads(row["value_json"], values[row["key"]])
        values["share_holdings_with_ai"] = values["share_holdings_with_ai"] is True
        return values

    def update_settings(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict) or set(body) != {"share_holdings_with_ai"}:
            raise PaperTradingError("holdings settings accept share_holdings_with_ai only")
        value = body["share_holdings_with_ai"]
        if not isinstance(value, bool):
            raise PaperTradingError("share_holdings_with_ai must be boolean")
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO holdings_settings(key, value_json, updated_at) VALUES('share_holdings_with_ai', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (self._json(value), _now_iso(self._now())),
            )
        return {"share_holdings_with_ai": self.settings()["share_holdings_with_ai"]}

    # ---------------------------------------------------------- manual entries
    @staticmethod
    def _entry(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "base": row["base"],
            "qty": row["qty"],
            "avg_price": row["avg_price"],
            "opened_at": row["opened_at"],
            "note": row["note"],
            "updated_at": row["updated_at"],
        }

    def manual_entries(self) -> list[dict[str, Any]]:
        return [self._entry(row) for row in self._query("SELECT * FROM holdings_manual ORDER BY base")]

    def save_manual(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise PaperTradingError("manual holding must be an object")
        extra = set(body) - {"id", "base", "qty", "avg_price", "opened_at", "note"}
        if extra:
            raise PaperTradingError("manual holding contains unsupported fields")
        base = body.get("base")
        if not isinstance(base, str) or not BASE_RE.fullmatch(base):
            raise PaperTradingError("base must be 1-20 uppercase letters or digits, e.g. NEAR")
        if base in STABLECOINS:
            raise PaperTradingError("USDT and USDC are listed as cash, not as holdings")
        qty = _positive_number(body.get("qty"), "qty")
        avg_price = _positive_number(body.get("avg_price"), "avg_price")
        opened_at = body.get("opened_at")
        if opened_at is not None:
            if not isinstance(opened_at, str) or not opened_at.strip() or len(opened_at) > 40:
                raise PaperTradingError("opened_at must be an ISO date or date-time")
            try:
                datetime.fromisoformat(opened_at.strip().replace("Z", "+00:00"))
            except ValueError:
                raise PaperTradingError("opened_at must be an ISO date or date-time") from None
            opened_at = opened_at.strip()
        note = body.get("note")
        if note is not None:
            if not isinstance(note, str) or len(note) > MAX_NOTE_CHARS:
                raise PaperTradingError(f"note must be text of at most {MAX_NOTE_CHARS} characters")
            note = note.strip() or None
        entry_id = body.get("id")
        if entry_id is not None and (not isinstance(entry_id, str) or not MANUAL_ID_RE.fullmatch(entry_id)):
            raise PaperTradingError("id must be 1-64 letters, digits, '-' or '_'")
        now = _now_iso(self._now())
        with self.store.transaction() as db:
            same_base = db.execute("SELECT id FROM holdings_manual WHERE base=?", (base,)).fetchone()
            if entry_id is not None:
                if db.execute("SELECT id FROM holdings_manual WHERE id=?", (entry_id,)).fetchone() is None:
                    raise PaperTradingError("manual holding was not found")
                if same_base is not None and same_base["id"] != entry_id:
                    raise PaperTradingError(f"a manual holding for {base} already exists; edit that entry")
            else:
                # One manual entry per coin: adding an existing coin edits its entry.
                entry_id = same_base["id"] if same_base is not None else f"hm-{uuid.uuid4().hex[:12]}"
            db.execute(
                "INSERT INTO holdings_manual(id, base, qty, avg_price, opened_at, note, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET base=excluded.base, qty=excluded.qty, "
                "avg_price=excluded.avg_price, opened_at=excluded.opened_at, note=excluded.note, "
                "updated_at=excluded.updated_at",
                (entry_id, base, qty, avg_price, opened_at, note, now),
            )
            row = db.execute("SELECT * FROM holdings_manual WHERE id=?", (entry_id,)).fetchone()
        return self._entry(row)

    def delete_manual(self, entry_id: str) -> dict[str, Any]:
        if not isinstance(entry_id, str) or not MANUAL_ID_RE.fullmatch(entry_id):
            raise PaperTradingError("manual holding was not found")
        with self.store.transaction() as db:
            deleted = db.execute("DELETE FROM holdings_manual WHERE id=?", (entry_id,)).rowcount
        if not deleted:
            raise PaperTradingError("manual holding was not found")
        return {"deleted": True}

    # ---------------------------------------------------------- Gate (read-only)
    def _default_account_id(self) -> str | None:
        rows = self._query(
            "SELECT account_id FROM exchange_accounts WHERE exchange='gate' AND enabled=1 "
            "ORDER BY account_id='gate-main' DESC, account_id LIMIT 1"
        )
        return rows[0]["account_id"] if rows else None

    def _record_sync(self, account_id: str, status: str, error: str | None, payload: dict[str, Any]) -> None:
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO holdings_gate_syncs(account_id, status, error, payload_json, synced_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (account_id, status, error, self._json(payload), _now_iso(self._now())),
            )
            # Bounded history; always keep the newest OK snapshot.
            db.execute(
                "DELETE FROM holdings_gate_syncs WHERE account_id=? AND sync_id NOT IN ("
                "SELECT sync_id FROM holdings_gate_syncs WHERE account_id=? ORDER BY sync_id DESC LIMIT ?) "
                "AND sync_id != COALESCE((SELECT MAX(sync_id) FROM holdings_gate_syncs "
                "WHERE account_id=? AND status='OK'), -1)",
                (account_id, account_id, SYNC_HISTORY_KEEP, account_id),
            )

    def _gate_state(self) -> tuple[dict[str, Any], dict[str, Any] | None]:
        account_id = self._default_account_id()
        if account_id is None:
            return (
                {"account_id": None, "synced_at": None, "status": "NOT_CONFIGURED",
                 "error": "No Gate account is configured. Add a READ-ONLY spot API key in Settings > Exchange Accounts."},
                None,
            )
        last = self._query(
            "SELECT status, error, synced_at FROM holdings_gate_syncs WHERE account_id=? ORDER BY sync_id DESC LIMIT 1",
            (account_id,),
        )
        ok = self._query(
            "SELECT payload_json, synced_at FROM holdings_gate_syncs WHERE account_id=? AND status='OK' "
            "ORDER BY sync_id DESC LIMIT 1",
            (account_id,),
        )
        if not last:
            return (
                {"account_id": account_id, "synced_at": None, "status": "NOT_CONFIGURED",
                 "error": "The Gate account has not been synced yet; press Sync from Gate."},
                None,
            )
        status = last[0]["status"] if last[0]["status"] in GATE_STATUSES else "ERROR"
        snapshot = None
        if ok and status != "NOT_CONFIGURED":
            snapshot = self._loads(ok[0]["payload_json"], None)
        source = {
            "account_id": account_id,
            "synced_at": ok[0]["synced_at"] if ok else None,
            "status": status,
            "error": None if status == "OK" else last[0]["error"],
        }
        return source, snapshot if isinstance(snapshot, dict) else None

    def sync(self, account_id: str | None = None) -> dict[str, Any]:
        """Signed GET-only spot sync; returns the same payload as ``payload()``."""

        if account_id is not None:
            if not isinstance(account_id, str) or not ACCOUNT_ID_RE.fullmatch(account_id):
                raise PaperTradingError("account_id must be lowercase letters, digits or '-'")
            account = self.store.exchange_account_internal(account_id)
            if not account["enabled"]:
                raise PaperTradingError("exchange account is disabled")
        with self._sync_lock:
            self._sync_locked(account_id)
        return self.payload()

    def _sync_locked(self, account_id: str | None) -> None:
        account_id = account_id or self._default_account_id()
        if account_id is None:
            return
        try:
            client = self.runtime.gate_client(account_id, transport=self._gate_transport)
        except PaperTradingError:
            self._record_sync(
                account_id, "NOT_CONFIGURED",
                "Gate API key/secret are not stored for this account. Add a READ-ONLY spot key in "
                "Settings > Exchange Accounts.",
                {},
            )
            return
        secrets = [getattr(client, "_api_key", ""), getattr(client, "_api_secret", "")]
        try:
            snapshot = self._pull(client, secrets)
        except GateAccountError as exc:
            self._record_sync(account_id, "ERROR", scrub(str(exc), secrets), {})
            return
        except PaperTradingError:
            self._record_sync(account_id, "ERROR", "Gate spot request was refused by the read-only allowlist", {})
            return
        except Exception:
            self._record_sync(account_id, "ERROR", "Gate spot sync failed safely", {})
            return
        snapshot["account_id"] = account_id
        self._record_sync(account_id, "OK", None, snapshot)

    def _pull(self, client: ReadOnlyGateClient, secrets: list[str]) -> dict[str, Any]:
        raw = client.get("/spot/accounts")
        if not isinstance(raw, list):
            raise GateAccountError("Gate spot accounts response is malformed")
        balances = [b for b in (normalize_spot_balance(row) for row in raw) if b and b["total"] > 0]
        now_s = self._now().timestamp()
        coins: dict[str, Any] = {}
        cash = []
        for balance in sorted(balances, key=lambda b: b["currency"]):
            base = balance["currency"]
            if base in STABLECOINS:
                cash.append({"currency": base, "qty": balance["total"]})
                continue
            try:
                fills, truncated = fetch_spot_fills(client, f"{base}_{QUOTE}", now_s=now_s)
            except GateAccountError as exc:
                coins[base] = {
                    "qty": balance["total"], "avg_price": None, "avg_source": "unknown", "realized_usdt": None,
                    "trades_counted": 0,
                    "cost_basis_note": f"Trade history is unavailable ({scrub(str(exc), secrets)}); the cost is unknown.",
                }
                continue
            coins[base] = reconcile_basis(
                base, balance["total"], moving_average_basis(fills, base=base), truncated=truncated
            )
        return {"coins": coins, "cash": cash}

    def last_validation(self) -> dict[str, Any] | None:
        rows = self._query("SELECT value_json FROM holdings_settings WHERE key='gate_validation'")
        value = self._loads(rows[0]["value_json"], None) if rows else None
        return value if isinstance(value, dict) else None

    def validate_gate(self) -> dict[str, Any]:
        """One signed GET to ``/spot/accounts``: proves the key authenticates and can read spot.

        It never calls ``/spot/my_trades``, never syncs, and never attempts a write: Gate cannot prove a
        key is read-only without one, so ``read_only_note`` asks the user to confirm it on Gate.
        Only ``{ok, checked_at, error_code, account_id}`` is persisted.
        """

        checked_at = _now_iso(self._now())
        result: dict[str, Any] = {
            "ok": False,
            "checks": {"credentials_present": False, "auth": "skipped", "spot_read": "skipped"},
            "balances_nonzero": None,
            "key_hint": None,
            "checked_at": checked_at,
            "read_only_note": READ_ONLY_NOTE,
        }
        account_id = self._default_account_id()
        client = None
        if account_id is not None:
            try:
                client = self.runtime.gate_client(account_id, transport=self._gate_transport)
            except PaperTradingError:
                client = None
        if client is None:
            result["reason"] = "no_key"
            return result
        result["checks"]["credentials_present"] = True
        result["key_hint"] = {
            "account_id": account_id,
            "backend": self.runtime.resolver.store.backend,
            "api_key": MASK,
            "api_secret": MASK,
        }
        error_code = None
        try:
            raw = client.get("/spot/accounts")
            if not isinstance(raw, list):
                raise GateAccountError("Gate spot accounts response is malformed", kind="invalid")
            balances = [b for b in (normalize_spot_balance(row) for row in raw) if b and b["total"] > 0]
            result.update(ok=True, balances_nonzero=len(balances))
            result["checks"].update(auth="ok", spot_read="ok")
        except GateAccountError as exc:
            if exc.kind == "http":
                error_code = exc.label or f"HTTP_{exc.status}"
                if exc.label in AUTH_ERROR_LABELS or (exc.label is None and exc.status == 401):
                    result["checks"]["auth"] = "failed"
                elif exc.status == 403 or exc.label in PERMISSION_ERROR_LABELS:
                    result["checks"].update(auth="ok", spot_read="failed")
                else:
                    result["checks"]["spot_read"] = "failed"
            else:
                error_code = {"timeout": "TIMEOUT", "network": "NETWORK_ERROR"}.get(exc.kind or "", "INVALID_RESPONSE")
                result["checks"]["spot_read"] = "failed"
        except Exception:
            error_code = "UNEXPECTED_ERROR"
            result["checks"]["spot_read"] = "failed"
        if error_code is not None:
            result["error_code"] = error_code
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO holdings_settings(key, value_json, updated_at) VALUES('gate_validation', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (self._json({"ok": result["ok"], "checked_at": checked_at, "error_code": error_code,
                             "account_id": account_id}), checked_at),
            )
        return result

    def sync_if_due(self, interval_seconds: float = SYNC_INTERVAL_SECONDS) -> dict[str, Any] | None:
        """Scheduler hook: sync when a Gate account exists and the last attempt is older than the interval."""

        account_id = self._default_account_id()
        if account_id is None:
            return None
        rows = self._query(
            "SELECT synced_at FROM holdings_gate_syncs WHERE account_id=? ORDER BY sync_id DESC LIMIT 1", (account_id,)
        )
        if rows:
            last = datetime.fromisoformat(rows[0]["synced_at"].replace("Z", "+00:00"))
            if self._now() - last < timedelta(seconds=interval_seconds):
                return None
        return self.sync()

    # ---------------------------------------------------------- prices
    def _prices(self, bases: set[str]) -> dict[str, float]:
        if not bases:
            return {}
        with self._ticker_lock:
            fresh = self._tickers is not None and time.monotonic() - self._tickers_at < TICKER_TTL_SECONDS
            if not fresh:
                source = self._ticker_source or _default_ticker_source()
                self._ticker_source = source
                try:
                    tickers = source()
                    if not isinstance(tickers, dict):
                        raise PaperTradingError("Gate spot tickers are malformed")
                    self._tickers, self._ticker_error = tickers, None
                except Exception:
                    self._tickers, self._ticker_error = {}, "Gate spot tickers are unavailable"
                self._tickers_at = time.monotonic()
            tickers = self._tickers or {}
        prices = {}
        for base in bases:
            row = tickers.get(f"{base}_{QUOTE}")
            try:
                last = float(row["last"]) if isinstance(row, dict) else None
            except (TypeError, ValueError, KeyError):
                last = None
            if last is not None and math.isfinite(last) and last > 0:
                prices[base] = last
        return prices

    # ---------------------------------------------------------- merged view
    def _positions(self) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]] | None, int]:
        gate_source, snapshot = self._gate_state()
        manual = {entry["base"]: entry for entry in self.manual_entries()}
        coins = (snapshot or {}).get("coins") or {}
        positions: dict[str, dict[str, Any]] = {}
        for base, coin in coins.items():
            if not isinstance(coin, dict) or not coin.get("qty"):
                continue
            positions[base] = {
                "base": base, "qty": float(coin["qty"]), "qty_source": "gate", "avg_price": coin.get("avg_price"),
                "avg_source": coin.get("avg_source") or "unknown", "realized_usdt": coin.get("realized_usdt"),
                "trades_counted": int(coin.get("trades_counted") or 0), "cost_basis_note": coin.get("cost_basis_note"),
                "manual_id": None,
            }
        for base, entry in manual.items():
            if base in positions:
                # Wallet quantity stays Gate's; the user's average price overrides the trade-derived one.
                position = positions[base]
                position.update(avg_price=entry["avg_price"], avg_source="manual", manual_id=entry["id"],
                                cost_basis_note="Manual average price overrides the Gate trade-derived cost.")
            else:
                positions[base] = {
                    "base": base, "qty": entry["qty"], "qty_source": "manual", "avg_price": entry["avg_price"],
                    "avg_source": "manual", "realized_usdt": None, "trades_counted": 0, "cost_basis_note": None,
                    "manual_id": entry["id"],
                }
        cash = (snapshot or {}).get("cash") if snapshot is not None else None
        return gate_source, positions, cash, len(manual)

    def _rule_state(self, base: str) -> str | None:
        if self.rule_state_provider is None:
            return None
        try:
            state = self.rule_state_provider(base)
        except Exception:
            return None
        return state if state in RULE_STATES else None

    def payload(self) -> dict[str, Any]:
        """``GET /api/holdings`` body (see docs/holdings.md)."""

        now = self._now()
        gate_source, positions, cash, manual_count = self._positions()
        need = set(positions)
        if cash and any(c["currency"] != QUOTE for c in cash):
            need |= {c["currency"] for c in cash if c["currency"] != QUOTE}
        prices = self._prices(need)
        rows = []
        for base, position in positions.items():
            qty = position["qty"]
            avg = position["avg_price"]
            price = prices.get(base)
            value = qty * price if price is not None else None
            cost = qty * avg if avg is not None else None
            unrealized = value - cost if value is not None and cost is not None else None
            rule_state = self._rule_state(base)
            rows.append({
                "base": base,
                "qty": _round(qty),
                "qty_source": position["qty_source"],
                "avg_price": _round(avg),
                "avg_source": position["avg_source"],
                "price": _round(price),
                "value_usdt": _round(value),
                "cost_usdt": _round(cost),
                "unrealized_usdt": _round(unrealized),
                "unrealized_pct": _round(price / avg - 1, 6) if price is not None and avg else None,
                "realized_usdt": _round(position["realized_usdt"]),
                "allocation": None,
                "rule_state": rule_state,
                "alignment": alignment_for(rule_state, qty > 0),
                "trades_counted": position["trades_counted"],
                "cost_basis_note": position["cost_basis_note"],
                "manual_id": position["manual_id"],
                "_value": value, "_cost": cost, "_unrealized": unrealized,
            })
        value_total = sum(r["_value"] for r in rows if r["_value"] is not None)
        cost_total = sum(r["_cost"] for r in rows if r["_cost"] is not None)
        paired = [r for r in rows if r["_unrealized"] is not None]
        unrealized_total = sum(r["_unrealized"] for r in paired)
        paired_cost = sum(r["_cost"] for r in paired)
        realized = [r["realized_usdt"] for r in rows if r["realized_usdt"] is not None]
        for row in rows:
            if row["_value"] is not None and value_total > 0:
                row["allocation"] = _round(row["_value"] / value_total, 6)
            for key in ("_value", "_cost", "_unrealized"):
                row.pop(key)
        rows.sort(key=lambda r: (-(r["value_usdt"] if r["value_usdt"] is not None else -1.0), r["base"]))
        cash_rows = [{"currency": c["currency"], "qty": _round(c["qty"])} for c in (cash or [])]
        cash_usdt: float | None = None
        if cash is not None:
            cash_usdt = 0.0
            for c in cash:
                rate = 1.0 if c["currency"] == QUOTE else prices.get(c["currency"])
                if rate is None:
                    cash_usdt = None
                    break
                cash_usdt += c["qty"] * rate
        gate_source["last_validation"] = self.last_validation()
        return {
            "as_of": _now_iso(now),
            "sources": {
                "gate": gate_source,
                "manual_count": manual_count,
                "prices": {
                    "source": "gate_spot_public_tickers",
                    "status": "NOT_NEEDED" if not need else ("ERROR" if self._ticker_error else "OK"),
                    "error": self._ticker_error if need else None,
                },
            },
            "share_holdings_with_ai": self.settings()["share_holdings_with_ai"],
            "totals": {
                "value_usdt": _round(value_total),
                "cost_usdt": _round(cost_total),
                "unrealized_usdt": _round(unrealized_total),
                "unrealized_pct": _round(unrealized_total / paired_cost, 6) if paired_cost > 0 else None,
                "realized_usdt": _round(sum(realized)) if realized else None,
                "cash_usdt": _round(cash_usdt),
            },
            "holdings": rows,
            "cash": cash_rows,
        }

    # ---------------------------------------------------------- co-trader hooks
    def held(self, base: str) -> bool | None:
        """True/False when holdings are known (a Gate snapshot or any manual entry), else None."""

        gate_source, positions, cash, manual_count = self._positions()
        if cash is None and manual_count == 0:
            return None
        position = positions.get(base)
        return bool(position and position["qty"] > 0)

    def holding_for_ai(self, base: str) -> dict[str, Any] | None:
        """Per-coin context for the AI review, only when ``share_holdings_with_ai`` is on.

        Never includes account ids, keys, or any other coin's amounts.
        """

        if not self.settings()["share_holdings_with_ai"]:
            return None
        if not isinstance(base, str) or not BASE_RE.fullmatch(base):
            return None
        held = self.held(base)
        if held is None:
            return None
        if not held:
            return {"held": False, "qty": 0.0, "avg_price": None, "unrealized_pct": None}
        position = self._positions()[1][base]
        avg = position["avg_price"]
        price = self._prices({base}).get(base)
        return {
            "held": True,
            "qty": _round(position["qty"]),
            "avg_price": _round(avg),
            "unrealized_pct": _round(price / avg - 1, 6) if price is not None and avg else None,
        }
