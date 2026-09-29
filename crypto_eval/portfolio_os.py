"""AI Portfolio Trading OS service layer (PAPER only).

Binds the exchange-backed catalog, Spot PAPER accounting, unified PAPER orders, the
position manager (protection, reduce/close, authority modes), structured AI re-plans,
the Portfolio Brain, autonomous position review, activity/attention, and post-trade
learning onto the existing futures runtime.

Invariants kept here:
- every state change is PAPER-local and journaled (``management_events`` + activity);
- the browser never sizes an order: quantity, notional, margin, and risk are derived
  server-side by the deterministic ``RiskEngine`` / ``SpotRiskEngine``;
- AI may mutate a position only in ``AUTO_PAPER`` and only within deterministic policy;
  ``RECOMMEND_ONLY`` / ``MANUAL_OVERRIDE`` / ``PAUSED`` never auto-apply;
- there is no code path to a real exchange write; real account data stays in its mirror.
"""

from __future__ import annotations

import json
import math
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import activity as activity_mod
from . import learning
from .contracts import digest
from .execution_safety import (
    KILL_RANK,
    SafetyLedger,
    classify_market,
    kill_switch_allows,
    plan_execution,
    reconcile_ledgers,
    within_envelope,
)
from .market_catalog import CatalogError, MarketCatalog, neutral_symbol, parse_instrument_id
from .paper_ai import AIProviderError, REPLAN_SCHEMA_VERSION, parse_replan
from .paper_contracts import (
    INTENT_SCHEMA_VERSION,
    MARKET_DATA_ORIGINS,
    PaperTradingError,
    TradingIntent,
    iso_utc,
    parse_utc,
)
from .paper_market import MarketDataError
from .portfolio_brain import correlation_group, evaluate_entry, review_portfolio
from .portfolio_store import (
    MANAGEMENT_MODES,
    QUICK_INTENTS,
    default_portfolio_settings,
    validate_portfolio_settings,
)
from .spot_benchmarks import run_benchmarks
from .spot_lifecycle import classify_regime, merge_recommendation, plan_lifecycle, regime_evidence
from .spot_accounting import EPSILON, SpotHoldingState, SpotRiskEngine, apply_buy, apply_sell, floor_to_step


PORTFOLIO_STATE_SCHEMA = "portfolio-state.v1"
POSITION_VIEW_SCHEMA = "unified-position-view.v1"
ORDER_VIEW_SCHEMA = "paper-order-view.v1"
PREVIEW_SCHEMA = "paper-order-preview.v1"
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:-]{8,80}")
SPOT_UNPROTECTED_RISK_PROXY = 0.10
PROPOSAL_TTL_MINUTES = 30
CONFIRM_REDUCE_ABOVE = 0.5
MAX_TARGETS = 3


class ConfirmationRequired(PaperTradingError):
    """The action changes risk or authority materially; resend with an explicit confirmation."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.details = details or {}


def _loads(value: Any, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _num(value: Any, name: str, *, positive: bool = False, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PaperTradingError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise PaperTradingError(f"{name} must be a finite{' positive' if positive else ''} number")
    return result


def parse_position_ref(ref: str) -> tuple[str, str]:
    if not isinstance(ref, str) or ":" not in ref:
        raise PaperTradingError("position reference is invalid")
    kind, _, identifier = ref.partition(":")
    if kind not in {"perp", "spot"} or not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", identifier):
        raise PaperTradingError("position reference is invalid")
    return kind, identifier


def parse_order_ref(ref: str) -> tuple[str, str]:
    if not isinstance(ref, str) or ":" not in ref:
        raise PaperTradingError("order reference is invalid")
    kind, _, identifier = ref.partition(":")
    if kind not in {"perp", "spot"} or not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", identifier):
        raise PaperTradingError("order reference is invalid")
    return kind, identifier


def normalize_targets(prices: Any, fractions: Any, *, side: str, quantity: float) -> list[dict[str, Any]]:
    if prices is None:
        return []
    if not isinstance(prices, list) or not 1 <= len(prices) <= MAX_TARGETS:
        raise PaperTradingError(f"targets must list 1 to {MAX_TARGETS} prices")
    values = [_num(price, "target price", positive=True) for price in prices]
    ordered = sorted(values) if side == "long" else sorted(values, reverse=True)
    if len(set(ordered)) != len(ordered):
        raise PaperTradingError("target prices must be distinct")
    if fractions is None:
        count = len(ordered)
        fractions = [round(1 / count, 6)] * (count - 1) + [round(1 - round(1 / count, 6) * (count - 1), 6)]
    if not isinstance(fractions, list) or len(fractions) != len(ordered):
        raise PaperTradingError("target fractions must match the target prices")
    parsed = [_num(value, "target fraction", positive=True) for value in fractions]
    if abs(sum(parsed) - 1.0) > 1e-6:
        raise PaperTradingError("target fractions must sum to 1")
    result = []
    for index, (price, fraction) in enumerate(zip(ordered, parsed)):
        final = index == len(ordered) - 1
        result.append({
            "price": price,
            "fraction": fraction,
            "quantity": None if final else quantity * fraction,
            "final": final,
            "hit": False,
        })
    return result


class PortfolioOS:
    """Service facade over the PAPER runtime. Thread-safe; mutations are serialized."""

    def __init__(self, runtime: Any, *, catalog: MarketCatalog | None = None) -> None:
        self.runtime = runtime
        self.store = runtime.store
        self._catalog_override = catalog
        self._catalogs: dict[str, MarketCatalog] = {}
        self.spot_risk = SpotRiskEngine()
        self._lock = threading.RLock()
        self._last_snapshot_at: datetime | None = None
        self.safety = SafetyLedger(self.store, runtime._clock)
        self._assess_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}

    # ------------------------------------------------------------------ basics
    def _now(self) -> datetime:
        return self.runtime._clock().astimezone(timezone.utc)

    def _iso(self, value: datetime | None = None) -> str:
        return iso_utc(value or self._now())

    def experiment(self) -> dict[str, Any]:
        return self.store.experiment()

    @property
    def experiment_id(self) -> str:
        return self.experiment()["experiment_id"]

    def catalog_source(self) -> str:
        if self._catalog_override is not None:
            return self._catalog_override.source
        mode = self.experiment()["config"]["market_data_mode"]
        return "fixture" if mode == "fixture" else "gate"

    @property
    def catalog(self) -> MarketCatalog:
        if self._catalog_override is not None:
            return self._catalog_override
        source = self.catalog_source()
        if source not in self._catalogs:
            self._catalogs[source] = MarketCatalog(
                source=source,
                live_stream=self.runtime.live_stream,
                clock=self.runtime._clock,
            )
        return self._catalogs[source]

    def perp_instrument_id(self, symbol: str) -> str:
        return f"{self.catalog.exchange}:perpetual:{symbol[:-4]}_USDT"

    # --------------------------------------------------------------- settings
    def settings(self) -> dict[str, Any]:
        rows = self.store._query(
            "SELECT settings_json FROM portfolio_settings WHERE experiment_id=?", (self.experiment_id,)
        )
        if not rows:
            return default_portfolio_settings()
        return validate_portfolio_settings(_loads(rows[0]["settings_json"], {}))

    def update_settings(self, patch: dict[str, Any], *, source: str = "USER", confirm: bool = False) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise PaperTradingError("portfolio settings must be an object")
        with self._lock:
            current = self.settings()
            updated = validate_portfolio_settings(patch, base=current)
            from .promotion import diff_material, material_settings

            material = diff_material(material_settings(current), material_settings(updated))
            frozen = self.runtime.governance.frozen() if material else None
            if frozen and not confirm:
                raise ConfirmationRequired(
                    "CONFIRM_MATERIAL_CHANGE",
                    "this changes the experiment's treatment; results after it are evaluated under a new manifest version",
                    {"fields": material[:12], "manifest_version": frozen["version"]},
                )
            if updated["spot_starting_balance_usdt"] != current["spot_starting_balance_usdt"]:
                activity = self.store._query(
                    "SELECT COUNT(*) AS n FROM spot_orders WHERE experiment_id=?", (self.experiment_id,)
                )[0]["n"]
                if activity:
                    raise PaperTradingError("the spot starting balance is frozen after the first spot order")
            now = self._iso()
            with self.store.transaction() as db:
                db.execute(
                    "INSERT INTO portfolio_settings(experiment_id, settings_json, updated_at) VALUES(?, ?, ?) "
                    "ON CONFLICT(experiment_id) DO UPDATE SET settings_json=excluded.settings_json, "
                    "updated_at=excluded.updated_at",
                    (self.experiment_id, _dumps(updated), now),
                )
                if updated["spot_starting_balance_usdt"] != current["spot_starting_balance_usdt"]:
                    db.execute(
                        "UPDATE spot_wallets SET starting_balance=?, cash_balance=? WHERE experiment_id=? AND quote='USDT'",
                        (updated["spot_starting_balance_usdt"], updated["spot_starting_balance_usdt"], self.experiment_id),
                    )
                changed = sorted(
                    key for key in updated if updated.get(key) != current.get(key) and key != "schema_version"
                )
                self._journal_locked(
                    db, action="SETTINGS_CHANGED", source=source, before={k: current.get(k) for k in changed},
                    after={k: updated.get(k) for k in changed},
                )
                if "automation" in changed:
                    self._activity_locked(
                        db, source=source, category="ALERT", severity="ACTION" if updated["automation"]["emergency_stop"] else "INFO",
                        title="Automation controls changed",
                        summary=", ".join(f"{k}={v}" for k, v in updated["automation"].items()),
                    )
                elif changed:
                    self._activity_locked(
                        db, source=source, category="RISK", severity="INFO",
                        title="Portfolio policy updated", summary=", ".join(changed)[:300],
                    )
            if frozen:
                self.runtime.governance.freeze(reason=f"material settings change: {', '.join(material[:8])}")
            return updated

    def set_automation(self, patch: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
        if not isinstance(patch, dict) or set(patch) - {"new_entries_paused", "ai_management_paused", "emergency_stop"}:
            raise PaperTradingError("automation accepts new_entries_paused, ai_management_paused, emergency_stop")
        current = self.settings()["automation"]
        resuming_ai = patch.get("ai_management_paused") is False and current["ai_management_paused"]
        if resuming_ai and self._open_refs() and not confirm:
            raise ConfirmationRequired(
                "CONFIRM_AUTOMATION_CHANGE",
                "resuming AI management lets AUTO_PAPER positions be managed autonomously",
                {"open_positions": len(self._open_refs())},
            )
        return self.update_settings({"automation": patch})

    # ---------------------------------------------------------------- journal
    def _journal_locked(
        self,
        db: Any,
        *,
        action: str,
        source: str,
        position_ref: str | None = None,
        order_ref: str | None = None,
        market_type: str | None = None,
        before: Any = None,
        after: Any = None,
        risk_before: float | None = None,
        risk_after: float | None = None,
        snapshot: Any = None,
        request_id: str | None = None,
    ) -> int:
        cursor = db.execute(
            "INSERT INTO management_events(experiment_id, position_ref, order_ref, market_type, action, source, "
            "request_id, before_json, after_json, risk_before, risk_after, snapshot_json, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.experiment_id, position_ref, order_ref, market_type, action, source, request_id,
                _dumps(before), _dumps(after), risk_before, risk_after, _dumps(snapshot), self._iso(),
            ),
        )
        return int(cursor.lastrowid)

    def _activity_locked(self, db: Any, **fields: Any) -> dict[str, Any]:
        event = activity_mod.activity_event(
            experiment_id=self.experiment_id,
            timestamp=fields.pop("timestamp", None) or self._iso(),
            event_id=fields.pop("event_id", None) or f"act-{uuid.uuid4().hex[:20]}",
            **fields,
        )
        db.execute(
            "INSERT OR IGNORE INTO activity_events(event_id, experiment_id, timestamp, instrument_id, symbol, "
            "market_type, position_ref, source, category, severity, title, summary, payload_ref) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event["event_id"], event["experiment_id"], event["timestamp"], event["instrument_id"], event["symbol"],
                event["market_type"], event["position_ref"], event["source"], event["category"], event["severity"],
                event["title"], event["summary"], event["payload_ref"],
            ),
        )
        return event

    def management_events(self, position_ref: str | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
        if position_ref:
            rows = self.store._query(
                "SELECT * FROM management_events WHERE experiment_id=? AND position_ref=? ORDER BY event_id DESC LIMIT ?",
                (self.experiment_id, position_ref, limit),
            )
        else:
            rows = self.store._query(
                "SELECT * FROM management_events WHERE experiment_id=? ORDER BY event_id DESC LIMIT ?",
                (self.experiment_id, limit),
            )
        return [
            {
                **{k: row[k] for k in row.keys() if not k.endswith("_json")},
                "before": _loads(row["before_json"]),
                "after": _loads(row["after_json"]),
                "snapshot": _loads(row["snapshot_json"]),
            }
            for row in rows
        ]

    # ------------------------------------------------------------ idempotency
    def _idempotent(self, request_id: str | None, kind: str, request: dict[str, Any]) -> dict[str, Any] | None:
        if request_id is None:
            return None
        if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
            raise PaperTradingError("client_request_id must be 8-80 characters of letters, digits, . _ : -")
        rows = self.store._query(
            "SELECT kind, request_hash, response_json FROM order_requests WHERE client_request_id=?", (request_id,)
        )
        if not rows:
            return None
        row = rows[0]
        if row["kind"] != kind or row["request_hash"] != digest(request):
            raise PaperTradingError("client_request_id was already used for a different request")
        return {**_loads(row["response_json"], {}), "idempotent_replay": True}

    def _remember_locked(self, db: Any, request_id: str | None, kind: str, request: dict[str, Any], response: dict[str, Any]) -> None:
        if request_id is None:
            return
        db.execute(
            "INSERT OR IGNORE INTO order_requests(client_request_id, experiment_id, kind, request_hash, response_json, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (request_id, self.experiment_id, kind, digest(request), _dumps(response), self._iso()),
        )

    # ------------------------------------------------------------ spot wallet
    def _spot_wallet_locked(self, db: Any) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM spot_wallets WHERE experiment_id=? AND quote='USDT'", (self.experiment_id,)
        ).fetchone()
        if row is None:
            starting = float(self.settings()["spot_starting_balance_usdt"])
            db.execute(
                "INSERT INTO spot_wallets(experiment_id, quote, starting_balance, cash_balance, reserved_quote) "
                "VALUES(?, 'USDT', ?, ?, 0)",
                (self.experiment_id, starting, starting),
            )
            row = db.execute(
                "SELECT * FROM spot_wallets WHERE experiment_id=? AND quote='USDT'", (self.experiment_id,)
            ).fetchone()
        return dict(row)

    def spot_holdings(self, *, status: str | None = "open") -> list[dict[str, Any]]:
        if status is None:
            rows = self.store._query(
                "SELECT * FROM spot_holdings WHERE experiment_id=? ORDER BY opened_at DESC", (self.experiment_id,)
            )
        else:
            rows = self.store._query(
                "SELECT * FROM spot_holdings WHERE experiment_id=? AND status=? ORDER BY opened_at DESC",
                (self.experiment_id, status),
            )
        return [dict(row) for row in rows]

    def spot_wallet(self) -> dict[str, Any]:
        with self.store.transaction() as db:
            wallet = self._spot_wallet_locked(db)
        holdings = self.spot_holdings()
        holdings_value = sum(float(h["quantity"]) * float(h["mark_price"] or h["avg_cost"]) for h in holdings)
        unrealized = sum((float(h["mark_price"] or h["avg_cost"]) - float(h["avg_cost"])) * float(h["quantity"]) for h in holdings)
        realized = self.store._query(
            "SELECT COALESCE(SUM(realized_pnl),0) AS v, COALESCE(SUM(fees_paid),0) AS f, "
            "COALESCE(SUM(slippage_paid),0) AS s FROM spot_holdings WHERE experiment_id=?",
            (self.experiment_id,),
        )[0]
        equity = float(wallet["cash_balance"]) + holdings_value
        return {
            "schema_version": "spot-wallet.v1",
            "quote": wallet["quote"],
            "starting_balance_usdt": float(wallet["starting_balance"]),
            "cash_balance_usdt": float(wallet["cash_balance"]),
            "reserved_quote_usdt": float(wallet["reserved_quote"]),
            "available_quote_usdt": float(wallet["cash_balance"]) - float(wallet["reserved_quote"]),
            "holdings_value_usdt": holdings_value,
            "equity_usdt": equity,
            "realized_pnl_usdt": float(realized["v"]),
            "unrealized_pnl_usdt": unrealized,
            "fees_usdt": float(realized["f"]),
            "slippage_usdt": float(realized["s"]),
            "net_pnl_usdt": equity - float(wallet["starting_balance"]),
            "leverage": None,
            "liquidation": None,
            "funding": None,
        }

    # ------------------------------------------------------------------ safety
    def safety_settings(self) -> dict[str, Any]:
        return self.settings()["safety"]

    def kill_switch(self) -> dict[str, Any]:
        return self.safety.kill_switch(self.experiment_id)

    def set_kill_switch(self, level: str, *, reason: str = "", source: str = "USER", confirm: bool = False) -> dict[str, Any]:
        current = self.kill_switch()
        if level not in KILL_RANK:
            raise PaperTradingError(f"kill switch level must be one of {', '.join(KILL_RANK)}")
        if level == current["level"]:
            return current
        lowering = KILL_RANK[level] < KILL_RANK[current["level"]]
        if lowering:
            if source != "USER":
                raise PaperTradingError("AUTHORITY_DENIED: only the user can lower the kill switch")
            reconciliation = self.reconcile(escalate=False)
            if not reconciliation["ok"]:
                raise PaperTradingError("RECONCILIATION_FAILURE: resolve the ledger mismatch before lowering the kill switch")
            if not confirm:
                raise ConfirmationRequired(
                    "CONFIRM_KILL_SWITCH_LOWER",
                    f"lower the kill switch from {current['level']} to {level}?",
                    {"from": current["level"], "to": level},
                )
        result = self.safety.set_kill_switch(self.experiment_id, level, reason=reason or f"set by {source}", source=source)
        with self.store.transaction() as db:
            self._journal_locked(db, action="KILL_SWITCH", source=source, before={"level": current["level"]},
                                 after={"level": level, "reason": reason})
            self._activity_locked(
                db, source=source, category="ALERT",
                severity="CRITICAL" if KILL_RANK[level] >= KILL_RANK["RISK_REDUCING_ONLY"] else "ACTION" if level != "NORMAL" else "INFO",
                title=f"Kill switch {current['level']} → {level}", summary=(reason or "")[:300],
            )
        return result

    def reconcile(self, *, escalate: bool = True) -> dict[str, Any]:
        result = reconcile_ledgers(self.store, self.experiment_id, tolerance=self.safety_settings()["reconciliation_tolerance_usdt"])
        if not result["ok"] and escalate:
            self.safety.event(self.experiment_id, kind="reconciliation", code="RECONCILIATION_FAILURE", source="SYSTEM",
                              detail={"problems": result["problems"][:10]})
            if KILL_RANK[self.kill_switch()["level"]] < KILL_RANK["RISK_REDUCING_ONLY"]:
                self.set_kill_switch("RISK_REDUCING_ONLY", reason="RECONCILIATION_FAILURE: ledger invariant mismatch", source="SYSTEM")
        return result

    def _bars_for(self, market_type: str, exchange_symbol: str, now: datetime, minutes: int = 31) -> list[dict[str, Any]]:
        start = now - timedelta(minutes=minutes)
        try:
            if market_type == "spot":
                return self.catalog.spot.fetch_monitor_bars(exchange_symbol, start, now)
            provider = self.catalog.perpetual if self.catalog.source == "gate" else self.runtime._market(self.experiment()["config"])
            return provider.fetch_monitor_bars(neutral_symbol(exchange_symbol), start, now)
        except (PaperTradingError, MarketDataError, AttributeError):
            return []

    def assess(self, instrument_id: str, *, force: bool = False) -> dict[str, Any]:
        """Deterministic market safety state for an instrument (cached ~10 s). AI has no input."""

        now = self._now()
        cached = self._assess_cache.get(instrument_id)
        if cached and not force and (now - cached[0]).total_seconds() < 10:
            return cached[1]
        exchange, market_type, exchange_symbol = parse_instrument_id(instrument_id)
        try:
            quote = self.catalog.quote(instrument_id)
        except (PaperTradingError, MarketDataError):
            quote = None
        other = f"{exchange}:{'perpetual' if market_type == 'spot' else 'spot'}:{exchange_symbol}"
        reference = None
        try:
            other_quote = self.catalog.quote(other)
            if other_quote.get("fresh"):
                reference = other_quote.get("mark_price") or other_quote.get("mid_price")
        except (PaperTradingError, MarketDataError):
            reference = None
        assessment = classify_market(
            instrument_id=instrument_id, market_type=market_type, quote=quote,
            bars_1m=self._bars_for(market_type, exchange_symbol, now), settings=self.safety_settings(), now=now,
            reference_price=reference, prior=self.safety.prior_state(self.experiment_id, instrument_id),
        )
        assessment["quote"] = None if quote is None else {k: quote.get(k) for k in ("best_bid", "best_ask", "mid_price", "last_price", "observed_at", "source", "fresh")}
        self.safety.record_state(self.experiment_id, assessment)
        self._assess_cache[instrument_id] = (now, assessment)
        return assessment

    def safety_overview(self) -> dict[str, Any]:
        return {
            "schema_version": "safety-overview.v1",
            "kill_switch": self.kill_switch(),
            "market_states": self.safety.states(self.experiment_id),
            "counts": self.safety.counts(self.experiment_id),
            "recent_events": self.safety.events(self.experiment_id, limit=50),
            "reconciliation": reconcile_ledgers(self.store, self.experiment_id, tolerance=self.safety_settings()["reconciliation_tolerance_usdt"]),
            "hierarchy": ["LIQUIDATION / ACCOUNTING SAFETY", "RISK ENGINE", "CRASH / PRICE / LIQUIDITY GUARDS", "PORTFOLIO BRAIN", "AI / HUMAN INTENT"],
            "settings": self.safety_settings(),
        }

    def _safety_reject(self, code: str, detail: dict[str, Any], *, instrument_id: str | None = None, position_ref: str | None = None,
                       source: str = "SYSTEM") -> None:
        self.safety.event(self.experiment_id, kind="guard", code=code, source=source, detail=detail,
                          instrument_id=instrument_id, position_ref=position_ref)

    def _entry_plan(self, preview: dict[str, Any], req: dict[str, Any], source: str) -> dict[str, Any]:
        """Execution planning for an entry/buy preview; may reject, defer, or resize (never enlarge)."""

        if not preview.get("allowed"):
            return preview
        instrument = self.catalog.get(req["instrument_id"])
        assessment = self.assess(req["instrument_id"])
        buying = req["action"] in {"long", "buy"}
        action = "entry" if buying or req["market_type"] == "perpetual" else "reduce"
        holding_qty = None
        core = 0.0
        if action == "reduce":
            holding = next((h for h in self.spot_holdings() if h["instrument_id"] == req["instrument_id"]), None)
            holding_qty = 0.0 if holding is None else float(holding["quantity"]) - float(holding["reserved_quantity"])
            meta = self._meta(f"spot:{holding['holding_id']}") if holding else None
            core = float((meta or {}).get("core_quantity") or 0.0)
        plan = plan_execution(
            action=action, source=source, market_type=req["market_type"], side=req["action"], quantity=float(preview["quantity"]),
            assessment=assessment, quote=preview.get("quote"), settings=self.safety_settings(),
            quantity_step=instrument.get("quantity_step"), position_quantity=holding_qty, core_quantity=core,
            kill_switch_level=self.kill_switch()["level"],
        )
        preview = dict(preview)
        preview["market_safety"] = {k: assessment[k] for k in ("state", "price_confidence", "reasons", "restrictions")}
        preview["execution_plan"] = {k: plan.get(k) for k in ("plan_id", "decision", "style", "slices", "max_slippage_bps", "price_envelope", "reasons", "expires_at", "blocked_reason")}
        if plan["decision"] in {"REJECT", "DEFER"}:
            code = (plan.get("blocked_reason") or "").split(":")[0] or (plan["reasons"][0] if plan["reasons"] else "EXECUTION_DEFERRED")
            preview.update({"allowed": False, "code": code, "reason": plan.get("blocked_reason") or f"execution {plan['decision'].lower()}: {', '.join(plan['reasons']) or assessment['state']}"})
            return preview
        factor = plan["quantity"] / float(preview["quantity"]) if preview["quantity"] else 1.0
        if factor < 1 - 1e-9:
            preview["quantity"] = plan["quantity"]
            for key in ("notional_usdt", "margin_usdt", "max_loss_usdt", "estimated_entry_fee_usdt", "estimated_exit_fee_at_stop_usdt",
                        "estimated_slippage_usdt", "notional", "fee", "slippage_cost", "quote_amount_usdt"):
                if isinstance(preview.get(key), (int, float)):
                    preview[key] = preview[key] * factor
            preview.setdefault("warnings", []).append("SAFETY_RESIZE")
        return preview

    # ------------------------------------------------------------ quotes/marks
    def quote(self, instrument_id: str) -> dict[str, Any]:
        try:
            return self.catalog.quote(instrument_id)
        except (CatalogError, MarketDataError) as exc:
            raise PaperTradingError(f"MARKET_DATA_UNAVAILABLE: {exc}") from None

    def _live_prices(self) -> dict[str, float]:
        prices: dict[str, float] = {}
        for market_type in ("spot", "perpetual"):
            try:
                entry = self.catalog.refresh(market_type)
            except PaperTradingError:
                continue
            for item in entry["instruments"].values():
                price = item.get("mark_price") or item.get("last_price")
                if isinstance(price, (int, float)):
                    prices[item["instrument_id"]] = float(price)
        stream = self.runtime.live_stream
        if stream is not None and self.catalog.source == "gate":
            for symbol in getattr(stream, "symbols", []):
                try:
                    state = stream.symbol_state(symbol)
                except Exception:
                    continue
                ticker = state.get("ticker") or {}
                price = ticker.get("mark_price") or ticker.get("last_price")
                if state.get("fresh") and isinstance(price, (int, float)):
                    prices[self.perp_instrument_id(symbol)] = float(price)
        return prices

    # ---------------------------------------------------------- position meta
    def _meta_rows(self) -> dict[str, dict[str, Any]]:
        rows = self.store._query("SELECT * FROM position_meta WHERE experiment_id=?", (self.experiment_id,))
        return {row["position_ref"]: {**dict(row), "targets": _loads(row["targets_json"], [])} for row in rows}

    def _meta(self, ref: str) -> dict[str, Any] | None:
        rows = self.store._query("SELECT * FROM position_meta WHERE position_ref=?", (ref,))
        return None if not rows else {**dict(rows[0]), "targets": _loads(rows[0]["targets_json"], [])}

    def _insert_meta_locked(
        self,
        db: Any,
        *,
        ref: str,
        market_type: str,
        instrument_id: str | None,
        owner: str,
        mode: str,
        stop: float | None,
        targets: list[dict[str, Any]],
        initial_risk: float | None,
    ) -> None:
        now = self._iso()
        interval = int(self.settings()["review"]["management_interval_minutes"])
        db.execute(
            "INSERT OR IGNORE INTO position_meta(position_ref, experiment_id, market_type, instrument_id, "
            "management_mode, owner_source, plan_version, stop_price, targets_json, thesis_status, "
            "next_ai_review_at, initial_risk, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', ?, ?, ?, ?)",
            (
                ref, self.experiment_id, market_type, instrument_id, mode, owner, stop, _dumps(targets),
                iso_utc(self._now() + timedelta(minutes=interval)), initial_risk, now, now,
            ),
        )

    def sync_meta(self) -> int:
        """Attach management metadata to AI-opened primary positions (idempotent)."""

        existing = self._meta_rows()
        created = 0
        settings = self.settings()
        positions = [p for p in self.store.open_positions(self.experiment_id) if p["cohort"] == "primary"]
        missing = [p for p in positions if f"perp:{p['position_id']}" not in existing]
        if not missing:
            return 0
        with self.store.transaction() as db:
            for position in missing:
                order = db.execute(
                    "SELECT risk_json FROM orders WHERE order_id=?", (f"{position['position_id']}:entry",)
                ).fetchone()
                risk = (_loads(order["risk_json"], {}) if order else {}).get("risk", {})
                manual = str(position["cycle_id"]).startswith("manual:")
                owner = "USER" if manual else "AI"
                mode = settings["default_user_management_mode" if manual else "default_ai_management_mode"]
                self._insert_meta_locked(
                    db,
                    ref=f"perp:{position['position_id']}",
                    market_type="perpetual",
                    instrument_id=self.perp_instrument_id(position["symbol"]),
                    owner=owner,
                    mode=mode,
                    stop=float(position["stop_price"]),
                    targets=[{"price": float(position["target_price"]), "fraction": 1.0, "quantity": None, "final": True, "hit": False}],
                    initial_risk=risk.get("risk_amount"),
                )
                created += 1
                if not manual:
                    self._journal_locked(
                        db, action="MODE_ASSIGNED", source="SYSTEM", position_ref=f"perp:{position['position_id']}",
                        market_type="perpetual", before=None, after={"management_mode": mode, "owner": owner},
                    )
        return created

    def _open_refs(self) -> list[str]:
        refs = [f"perp:{p['position_id']}" for p in self.store.open_positions(self.experiment_id) if p["cohort"] == "primary"]
        refs += [f"spot:{h['holding_id']}" for h in self.spot_holdings()]
        return refs

    # ------------------------------------------------------------ view models
    def _perp_view(
        self,
        row: dict[str, Any],
        meta: dict[str, Any] | None,
        live: float | None,
        equity: float,
        pending: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        side = row["side"]
        sign = 1 if side == "long" else -1
        quantity = float(row["quantity"])
        entry = float(row["entry_price"])
        mark = float(row["mark_price"])
        stop = float(row["stop_price"])
        liq = float(row["liquidation_price"])
        unrealized = sign * (mark - entry) * quantity if row["status"] == "open" else 0.0
        realized_partial = (
            float(row["realized_gross"])
            - (float(row["entry_fee"]) - float(row["entry_fee_remaining"]))
            - float(row["exit_fees"])
            - float(row["funding_paid"])
        )
        open_risk = max(0.0, sign * (mark - stop) * quantity) if row["status"] == "open" else 0.0
        initial_risk = (meta or {}).get("initial_risk")
        total_pnl = unrealized + realized_partial if row["status"] == "open" else float(row["realized_pnl"] or 0)
        r_multiple = total_pnl / float(initial_risk) if initial_risk else None
        targets = (meta or {}).get("targets") or [
            {"price": float(row["target_price"]), "fraction": 1.0, "quantity": None, "final": True, "hit": False}
        ]
        price_for_buffer = live or mark
        buffer = sign * (price_for_buffer - liq) / price_for_buffer if price_for_buffer else None
        instrument = self.perp_instrument_id(row["symbol"])
        manual = str(row["cycle_id"]).startswith("manual:")
        return {
            "schema_version": POSITION_VIEW_SCHEMA,
            "position_ref": f"perp:{row['position_id']}",
            "position_id": row["position_id"],
            "market_type": "perpetual",
            "instrument_id": instrument,
            "symbol": row["symbol"],
            "display_symbol": f"{row['symbol'][:-4]}/USDT Perp",
            "base": row["symbol"][:-4],
            "side": side,
            "status": row["status"],
            "source": "USER" if manual else "AI",
            "source_arm": row["source_arm"],
            "quantity": quantity,
            "opened_quantity": float(row["opened_quantity"]),
            "notional_usdt": quantity * mark,
            "entry_price": entry,
            "mark_price": mark,
            "live_price": live,
            "unrealized_pnl_usdt": unrealized,
            "realized_pnl_usdt": realized_partial if row["status"] == "open" else float(row["realized_pnl"] or 0),
            "leverage": int(row["leverage"]),
            "margin_usdt": float(row["margin"]),
            "liquidation_price": liq,
            "liquidation_buffer_pct": buffer,
            "stop_price": stop,
            "targets": targets,
            "target_price": float(row["target_price"]),
            "funding_usdt": float(row["funding_paid"]),
            "fees_usdt": float(row["entry_fee"]) + float(row["exit_fees"]),
            "slippage_usdt": float(row["slippage_paid"]),
            "open_risk_usdt": open_risk,
            "initial_risk_usdt": initial_risk,
            "r_multiple": r_multiple,
            "allocation_pct": (float(row["margin"]) / equity) if equity > 0 else None,
            "exposure_pct": (quantity * mark / equity) if equity > 0 else None,
            "management_mode": (meta or {}).get("management_mode", "UNASSIGNED"),
            "owner_source": (meta or {}).get("owner_source", "USER" if manual else "AI"),
            "thesis_status": (meta or {}).get("thesis_status", "unknown"),
            "plan_version": (meta or {}).get("plan_version", 1),
            "last_ai_review_at": (meta or {}).get("last_ai_review_at"),
            "next_ai_review_at": (meta or {}).get("next_ai_review_at"),
            "last_user_override_at": (meta or {}).get("last_user_override_at"),
            "pending_proposal": pending,
            "market_regime": row.get("market_regime"),
            "cycle_id": row["cycle_id"],
            "opened_at": row["opened_at"],
            "closed_at": row.get("closed_at"),
            "exit_reason": row.get("exit_reason"),
            "exit_price": row.get("exit_price"),
            "cohort": row["cohort"],
        }

    def _spot_view(
        self,
        row: dict[str, Any],
        meta: dict[str, Any] | None,
        live: float | None,
        spot_equity: float,
        equity: float,
        pending: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        quantity = float(row["quantity"])
        avg = float(row["avg_cost"])
        mark = float(row["mark_price"] or avg)
        value = quantity * mark
        unrealized = (mark - avg) * quantity
        stop = (meta or {}).get("stop_price")
        open_risk = max(0.0, (mark - float(stop)) * quantity) if stop is not None else None
        initial_risk = (meta or {}).get("initial_risk")
        total = unrealized + float(row["realized_pnl"])
        return {
            "schema_version": POSITION_VIEW_SCHEMA,
            "position_ref": f"spot:{row['holding_id']}",
            "holding_id": row["holding_id"],
            "market_type": "spot",
            "instrument_id": row["instrument_id"],
            "symbol": row["symbol"],
            "display_symbol": f"{row['base']}/{row['quote']}",
            "base": row["base"],
            "quote": row["quote"],
            "side": "long",
            "status": row["status"],
            "source": row["source"],
            "quantity": quantity,
            "reserved_quantity": float(row["reserved_quantity"]),
            "available_quantity": quantity - float(row["reserved_quantity"]),
            "avg_cost": avg,
            "entry_price": avg,
            "mark_price": mark,
            "live_price": live,
            "current_value_usdt": value,
            "notional_usdt": value,
            "cost_basis_usdt": float(row["cost_basis"]),
            "unrealized_pnl_usdt": unrealized,
            "realized_pnl_usdt": float(row["realized_pnl"]),
            "fees_usdt": float(row["fees_paid"]),
            "slippage_usdt": float(row["slippage_paid"]),
            "allocation_pct": value / spot_equity if spot_equity > 0 else None,
            "portfolio_weight_pct": value / equity if equity > 0 else None,
            "stop_price": stop,
            "targets": (meta or {}).get("targets") or [],
            "open_risk_usdt": open_risk,
            "initial_risk_usdt": initial_risk,
            "r_multiple": total / float(initial_risk) if initial_risk else None,
            "leverage": None,
            "liquidation_price": None,
            "funding_usdt": None,
            "management_mode": (meta or {}).get("management_mode", "UNASSIGNED"),
            "owner_source": (meta or {}).get("owner_source", row["source"]),
            "thesis_status": (meta or {}).get("thesis_status", "unknown"),
            "plan_version": (meta or {}).get("plan_version", 1),
            "last_ai_review_at": (meta or {}).get("last_ai_review_at"),
            "next_ai_review_at": (meta or {}).get("next_ai_review_at"),
            "last_user_override_at": (meta or {}).get("last_user_override_at"),
            "pending_proposal": pending,
            "opened_at": row["opened_at"],
            "closed_at": row.get("closed_at"),
            "exit_reason": row.get("exit_reason"),
            "total_bought": float(row["total_bought"]),
            "total_sold": float(row["total_sold"]),
            "core_quantity": min(quantity, float((meta or {}).get("core_quantity") or 0.0)),
            "tactical_quantity": max(0.0, quantity - min(quantity, float((meta or {}).get("core_quantity") or 0.0))),
            "lifecycle_state": (self._lifecycle_row(f"spot:{row['holding_id']}") or {}).get("state"),
        }

    def _pending_by_ref(self) -> dict[str, dict[str, Any]]:
        rows = self.store._query(
            "SELECT proposal_id, position_ref, payload_json, created_at FROM replan_proposals "
            "WHERE experiment_id=? AND status='proposed' ORDER BY created_at",
            (self.experiment_id,),
        )
        result = {}
        for row in rows:
            payload = _loads(row["payload_json"], {})
            result[row["position_ref"]] = {
                "proposal_id": row["proposal_id"],
                "headline": payload.get("headline"),
                "created_at": row["created_at"],
            }
        return result

    def list_positions(self, *, status: str = "open", with_live: bool = True) -> list[dict[str, Any]]:
        if status not in {"open", "closed", "all"}:
            raise PaperTradingError("status must be open, closed, or all")
        self.sync_meta()
        metas = self._meta_rows()
        live = self._live_prices() if with_live else {}
        pending = self._pending_by_ref()
        perp_wallet = self.store.wallet_summary(self.experiment_id, "primary")
        spot = self.spot_wallet()
        equity = float(perp_wallet["equity"]) + spot["equity_usdt"]
        rows = self.store.list_positions(self.experiment_id, cohort="primary")
        views = []
        for row in rows:
            if status != "all" and row["status"] != status:
                continue
            ref = f"perp:{row['position_id']}"
            views.append(self._perp_view(row, metas.get(ref), live.get(self.perp_instrument_id(row["symbol"])), equity, pending.get(ref)))
        for row in self.spot_holdings(status=None if status == "all" else status):
            ref = f"spot:{row['holding_id']}"
            views.append(self._spot_view(row, metas.get(ref), live.get(row["instrument_id"]), spot["equity_usdt"], equity, pending.get(ref)))
        views.sort(key=lambda item: item["opened_at"], reverse=True)
        return views

    def position(self, ref: str) -> dict[str, Any]:
        kind, identifier = parse_position_ref(ref)
        self.sync_meta()
        meta = self._meta(ref)
        live_prices = self._live_prices()
        pending = self._pending_by_ref().get(ref)
        perp_wallet = self.store.wallet_summary(self.experiment_id, "primary")
        spot = self.spot_wallet()
        equity = float(perp_wallet["equity"]) + spot["equity_usdt"]
        if kind == "perp":
            rows = self.store._query(
                "SELECT * FROM positions WHERE position_id=? AND experiment_id=? AND cohort='primary'",
                (identifier, self.experiment_id),
            )
            if not rows:
                raise PaperTradingError("PAPER position was not found")
            row = dict(rows[0])
            view = self._perp_view(row, meta, live_prices.get(self.perp_instrument_id(row["symbol"])), equity, pending)
            cycle = None if row["cycle_id"].startswith("manual:") else self.store.cycle(row["cycle_id"])
            view["evidence"] = self._cycle_evidence(cycle)
            fills = self.store._query(
                "SELECT fill_id, side, quantity, price, fee, slippage_cost, as_of FROM fills WHERE position_id=? ORDER BY as_of",
                (identifier,),
            )
        else:
            rows = self.store._query(
                "SELECT * FROM spot_holdings WHERE holding_id=? AND experiment_id=?", (identifier, self.experiment_id)
            )
            if not rows:
                raise PaperTradingError("PAPER spot holding was not found")
            row = dict(rows[0])
            view = self._spot_view(row, meta, live_prices.get(row["instrument_id"]), spot["equity_usdt"], equity, pending)
            view["evidence"] = None
            fills = self.store._query(
                "SELECT fill_id, side, quantity, price, fee, slippage_cost, realized_pnl, liquidity, as_of "
                "FROM spot_fills WHERE holding_id=? ORDER BY as_of",
                (identifier,),
            )
        view["fills"] = [dict(fill) for fill in fills]
        view["journal"] = self.management_events(ref, limit=100)
        view["proposals"] = self.list_replans(position_ref=ref, limit=10)
        view["review"] = self._review_for(ref)
        if kind == "spot":
            view["lifecycle"] = self.lifecycle_view(ref)
        return view

    @staticmethod
    def _cycle_evidence(cycle: dict[str, Any] | None) -> dict[str, Any] | None:
        if not cycle:
            return None
        primary = cycle.get("primary_decision") or {}
        features = cycle.get("features") or {}
        return {
            "cycle_id": cycle.get("cycle_id"),
            "data_cutoff": cycle.get("data_cutoff"),
            "snapshot_hash": cycle.get("snapshot_hash"),
            "decision": primary.get("decision"),
            "reason": primary.get("reason"),
            "ai_path": primary.get("ai_path"),
            "escalation": primary.get("escalation"),
            "quant_gate": cycle.get("quant_gate"),
            "jev_regime": cycle.get("market_regime"),
            "portfolio_brain": cycle.get("portfolio_brain"),
            "risk": cycle.get("risk"),
            "key_signals": {
                key: features.get(key)
                for key in ("quant_direction", "signal_strength", "atr_pct", "rsi14", "volume_zscore", "funding_rate", "quant_regime")
            },
        }

    # ------------------------------------------------------------- portfolio
    def exposure_state(self, positions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        positions = positions if positions is not None else self.list_positions(with_live=False)
        exposures = []
        for position in positions:
            if position["status"] != "open":
                continue
            if position["market_type"] == "perpetual":
                risk = position["open_risk_usdt"]
            else:
                risk = position["open_risk_usdt"]
                if risk is None:
                    risk = position["current_value_usdt"] * SPOT_UNPROTECTED_RISK_PROXY
            exposures.append({
                "base": position["base"],
                "side": position["side"],
                "risk_usdt": risk,
                "notional_usdt": position["notional_usdt"],
                "market_type": position["market_type"],
            })
        perp_wallet = self.store.wallet_summary(self.experiment_id, "primary")
        spot = self.spot_wallet()
        equity = float(perp_wallet["equity"]) + spot["equity_usdt"]
        drawdown = self.drawdown()
        config = self.experiment()["config"]
        return {
            "equity_usdt": equity,
            "drawdown": drawdown["current"],
            "max_drawdown_stop": config["max_drawdown_stop"],
            "spot_cash_usdt": spot["available_quote_usdt"],
            "spot_equity_usdt": spot["equity_usdt"],
            "exposures": exposures,
        }

    def drawdown(self) -> dict[str, Any]:
        rows = self.store._query(
            "SELECT as_of, total_equity FROM portfolio_snapshots WHERE experiment_id=? ORDER BY as_of",
            (self.experiment_id,),
        )
        values = [float(row["total_equity"]) for row in rows]
        if not values:
            return {"current": 0.0, "max": 0.0, "peak_usdt": None, "observations": 0}
        peak = -math.inf
        worst = 0.0
        for value in values:
            peak = max(peak, value)
            if peak > 0:
                worst = max(worst, (peak - value) / peak)
        current = (peak - values[-1]) / peak if peak > 0 else 0.0
        return {"current": max(0.0, current), "max": worst, "peak_usdt": peak, "observations": len(values)}

    def portfolio(self) -> dict[str, Any]:
        experiment = self.experiment()
        config = experiment["config"]
        positions = self.list_positions()
        perp_wallet = self.store.wallet_summary(self.experiment_id, "primary")
        spot = self.spot_wallet()
        economics = self.runtime.economics()
        perp_equity = float(perp_wallet["equity"])
        total = perp_equity + spot["equity_usdt"]
        starting = float(config["starting_balance_usdt"]) + spot["starting_balance_usdt"]
        trading_pnl = total - starting
        ai_cost = economics["ai_cost"]
        fx = economics["fx"]
        ai_cost_usdt = None
        if fx.get("mode") == "manual" and isinstance(fx.get("usdt_per_usd"), (int, float)) and ai_cost.get("complete"):
            ai_cost_usdt = float(ai_cost["total_ai_cost_usd"]) * float(fx["usdt_per_usd"])
        economic = None if ai_cost_usdt is None else trading_pnl - ai_cost_usdt
        open_positions = [p for p in positions if p["status"] == "open"]
        perp_open = [p for p in open_positions if p["market_type"] == "perpetual"]
        spot_open = [p for p in open_positions if p["market_type"] == "spot"]
        long_notional = sum(p["notional_usdt"] for p in open_positions if p["side"] == "long")
        short_notional = sum(p["notional_usdt"] for p in open_positions if p["side"] == "short")
        by_asset: dict[str, float] = {}
        for position in open_positions:
            by_asset[position["base"]] = by_asset.get(position["base"], 0.0) + position["notional_usdt"]
        top_asset, top_value = (max(by_asset.items(), key=lambda item: item[1]) if by_asset else (None, 0.0))
        protected_risk = sum(p["open_risk_usdt"] or 0.0 for p in open_positions)
        unprotected = sum(p["current_value_usdt"] for p in spot_open if p["stop_price"] is None)
        budget = self.store.cost_ledger.budget_status(self.experiment_id, config["ai_budget"])
        drawdown = self.drawdown()
        pending_orders = [o for o in self.list_orders() if o["status"] in {"pending", "partially_filled"}]
        metrics_fees = sum(p["fees_usdt"] for p in positions)
        allocation = [
            {"bucket": "Spot cash", "value_usdt": spot["cash_balance_usdt"], "market_type": "spot"},
            {"bucket": "Perp free margin", "value_usdt": max(0.0, float(perp_wallet["available_margin"])), "market_type": "perpetual"},
            {"bucket": "Perp margin in use", "value_usdt": float(perp_wallet["margin_used"]), "market_type": "perpetual"},
        ] + [
            {"bucket": f"{p['base']} spot", "value_usdt": p["current_value_usdt"], "market_type": "spot"}
            for p in spot_open
        ]
        accounts = self.store.list_exchange_accounts()
        return {
            "schema_version": PORTFOLIO_STATE_SCHEMA,
            "experiment_id": self.experiment_id,
            "as_of": self._iso(),
            "execution_mode": "PAPER",
            "paper": {
                "total_equity_usdt": total,
                "starting_capital_usdt": starting,
                "trading_pnl_usdt": trading_pnl,
                "trading_pnl_pct": trading_pnl / starting if starting > 0 else None,
                "realized_pnl_usdt": float(economics["trading"]["realized_net_pnl_usdt"]) + spot["realized_pnl_usdt"],
                "unrealized_pnl_usdt": float(perp_wallet["unrealized_pnl"]) + spot["unrealized_pnl_usdt"],
                "fees_usdt": metrics_fees,
                "ai_cost_usd": ai_cost.get("total_ai_cost_usd"),
                "ai_cost_complete": bool(ai_cost.get("complete")),
                "ai_cost_usdt": ai_cost_usdt,
                "economic_pnl_usdt": economic,
                "economic_unavailable_reason": None if economic is not None else (
                    "set a USD→USDT cost FX policy in Settings" if ai_cost_usdt is None else None
                ),
                "drawdown": drawdown,
                "open_risk_usdt": protected_risk,
                "open_risk_pct": protected_risk / total if total > 0 else None,
                "unprotected_spot_value_usdt": unprotected,
                "exposure": {
                    "gross_usdt": long_notional + short_notional,
                    "net_usdt": long_notional - short_notional,
                    "long_usdt": long_notional,
                    "short_usdt": short_notional,
                    "gross_x": (long_notional + short_notional) / total if total > 0 else None,
                    "by_asset": [
                        {"base": base, "notional_usdt": value, "pct": value / total if total > 0 else None, "group": correlation_group(base)}
                        for base, value in sorted(by_asset.items(), key=lambda item: -item[1])
                    ],
                    "top_asset": top_asset,
                    "top_asset_pct": top_value / total if total > 0 and top_asset else None,
                },
                "allocation": allocation,
                "spot": {"wallet": spot, "holdings": spot_open},
                "perpetual": {
                    "wallet": {
                        "starting_balance_usdt": float(perp_wallet["starting_balance"]),
                        "cash_balance_usdt": float(perp_wallet["cash_balance"]),
                        "equity_usdt": perp_equity,
                        "unrealized_pnl_usdt": float(perp_wallet["unrealized_pnl"]),
                        "margin_used_usdt": float(perp_wallet["margin_used"]),
                        "available_margin_usdt": float(perp_wallet["available_margin"]),
                        "realized_pnl_usdt": float(perp_wallet["realized_pnl"]),
                        "net_pnl_usdt": perp_equity - float(perp_wallet["starting_balance"]),
                        "funding_usdt": float(economics["trading"]["funding_usdt"]),
                    },
                    "positions": perp_open,
                },
                "counts": {
                    "open_positions": len(open_positions),
                    "open_perpetual": len(perp_open),
                    "open_spot": len(spot_open),
                    "pending_orders": len(pending_orders),
                },
                "ai_budget": {
                    "spent_today_usd": budget.get("spent_today_usd"),
                    "spent_experiment_usd": budget.get("spent_experiment_usd"),
                    "remaining_today_usd": budget.get("remaining_today_usd"),
                    "remaining_experiment_usd": budget.get("remaining_experiment_usd"),
                    "utilization_today": budget.get("utilization_today"),
                    "exhausted": budget.get("exhausted"),
                    "limit_action": (config.get("ai_budget") or {}).get("limit_action"),
                },
                "equity_curve": self.equity_curve(),
                "reconciliation": {
                    "perp_equity_equals_cash_plus_unrealized": abs(
                        perp_equity - float(perp_wallet["cash_balance"]) - float(perp_wallet["unrealized_pnl"])
                    ) < 1e-6,
                    "spot_equity_equals_cash_plus_holdings": abs(
                        spot["equity_usdt"] - spot["cash_balance_usdt"] - spot["holdings_value_usdt"]
                    ) < 1e-6,
                },
            },
            "real_mirror": {
                "read_only": True,
                "merged_with_paper": False,
                "write_execution": "BLOCKED_BY_DESIGN",
                "accounts": [
                    {
                        "account_id": account["account_id"],
                        "display_name": account["display_name"],
                        "environment": account["environment"],
                        "last_sync": account.get("last_sync"),
                    }
                    for account in accounts
                ],
            },
        }

    def equity_curve(self, *, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.store._query(
            "SELECT as_of, total_equity, perp_equity, spot_equity, open_risk FROM portfolio_snapshots "
            "WHERE experiment_id=? ORDER BY as_of DESC LIMIT ?",
            (self.experiment_id, limit),
        )
        return [dict(row) for row in reversed(rows)]

    def record_snapshot(self, *, force: bool = False) -> dict[str, Any] | None:
        now = self._now()
        if not force and self._last_snapshot_at is not None and now - self._last_snapshot_at < timedelta(seconds=55):
            return None
        perp_wallet = self.store.wallet_summary(self.experiment_id, "primary")
        spot = self.spot_wallet()
        positions = self.list_positions(with_live=False)
        open_risk = sum(p["open_risk_usdt"] or 0.0 for p in positions if p["status"] == "open")
        gross = sum(p["notional_usdt"] for p in positions if p["status"] == "open")
        total = float(perp_wallet["equity"]) + spot["equity_usdt"]
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO portfolio_snapshots(experiment_id, as_of, total_equity, perp_equity, spot_equity, "
                "open_risk, gross_exposure, payload_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.experiment_id, iso_utc(now), total, float(perp_wallet["equity"]), spot["equity_usdt"],
                    open_risk, gross, _dumps({"open_positions": sum(1 for p in positions if p["status"] == "open")}),
                ),
            )
        self._last_snapshot_at = now
        return {"as_of": iso_utc(now), "total_equity": total}

    # ------------------------------------------------------------------ orders
    ORDER_FIELDS = {
        "client_request_id", "instrument_id", "action", "order_type", "limit_price", "risk_pct",
        "stop_price", "targets", "target_fractions", "leverage", "quantity", "quote_amount",
        "allocation_target_pct", "note", "preview_as_of",
    }

    def _validate_order_request(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise PaperTradingError("order request must be an object")
        unknown = set(request) - self.ORDER_FIELDS
        if unknown:
            raise PaperTradingError(f"order request contains unsupported fields: {', '.join(sorted(unknown))}")
        if "instrument_id" not in request:
            raise PaperTradingError("instrument_id is required")
        _exchange, market_type, _symbol = parse_instrument_id(request["instrument_id"])
        action = request.get("action")
        order_type = request.get("order_type", "market")
        if order_type not in {"market", "limit"}:
            raise PaperTradingError("order_type must be market or limit")
        if market_type == "perpetual" and action not in {"long", "short"}:
            raise PaperTradingError("perpetual action must be long or short")
        if market_type == "spot" and action not in {"buy", "sell"}:
            raise PaperTradingError("spot action must be buy or sell")
        if market_type == "perpetual" and ({"quantity", "quote_amount", "allocation_target_pct"} & set(request)):
            raise PaperTradingError(
                "perpetual size is derived server-side from risk_pct, stop, and leverage; do not send a quantity"
            )
        if order_type == "limit":
            _num(request.get("limit_price"), "limit_price", positive=True)
        elif request.get("limit_price") is not None:
            raise PaperTradingError("market orders cannot set a limit price")
        return {**request, "order_type": order_type, "market_type": market_type}

    def _guard_new_entries(self, source: str) -> None:
        level = self.kill_switch()["level"]
        allowed, why = kill_switch_allows(level, action="entry", source=source, risk_reducing=False)
        if not allowed:
            raise PaperTradingError(why)
        automation = self.settings()["automation"]
        if automation["emergency_stop"]:
            raise PaperTradingError("EMERGENCY_STOP: new PAPER orders are blocked until the emergency stop is cleared")
        if source == "AI" and automation["new_entries_paused"]:
            raise PaperTradingError("NEW_ENTRIES_PAUSED: AI new entries are paused")

    def preview_order(self, request: Any, *, source: str = "USER") -> dict[str, Any]:
        req = self._validate_order_request(request)
        preview = self._perp_preview(req, source=source) if req["market_type"] == "perpetual" else self._spot_preview(req, source=source)
        preview = self._entry_plan(preview, req, source)
        preview["expires_at"] = iso_utc(self._now() + timedelta(seconds=self.safety_settings()["preview_ttl_seconds"]))
        return preview

    def _perp_preview(self, req: dict[str, Any], *, source: str) -> dict[str, Any]:
        experiment = self.experiment()
        config = experiment["config"]
        if config["market_data_mode"] == "binance_usdm":
            raise PaperTradingError(
                "MANUAL_PERP_REQUIRES_GATE_OR_FIXTURE: manual perpetual PAPER orders use the Gate catalog; "
                "switch the market data mode to Gate or fixture"
            )
        instrument = self.catalog.require_tradable(req["instrument_id"], side=req["action"])
        symbol = instrument["symbol"]
        side = req["action"]
        settings = self.settings()
        risk_pct = _num(req.get("risk_pct", config["risk_per_trade"]), "risk_pct", positive=True)
        if risk_pct > settings["perp_manual_max_risk_pct"] + 1e-12:
            raise PaperTradingError(
                f"risk_pct exceeds the manual limit of {settings['perp_manual_max_risk_pct'] * 100:.2f}%"
            )
        leverage = req.get("leverage", config["primary_leverage"])
        if isinstance(leverage, bool) or not isinstance(leverage, int):
            raise PaperTradingError("leverage must be an integer")
        venue_max = instrument.get("max_leverage")
        if isinstance(venue_max, (int, float)) and leverage > venue_max:
            raise PaperTradingError("leverage exceeds the exchange contract maximum")
        stop = _num(req.get("stop_price"), "stop_price", positive=True)
        quote = self.quote(req["instrument_id"])
        warnings: list[str] = []
        now = self._now()
        if not quote.get("fresh"):
            decision_payload = {
                "allowed": False, "code": "STALE_DATA", "reason": "no fresh quote; new entries fail closed",
                "quantity": 0.0, "leverage": leverage, "notional": 0.0, "margin": 0.0, "risk_amount": 0.0,
                "liquidation_price": None, "reduce_only": False,
            }
            return self._perp_preview_payload(req, instrument, quote, decision_payload, None, [], None, warnings, now, risk_pct)
        reference = float(quote["best_ask"] if side == "long" else quote["best_bid"])
        entry = reference if req["order_type"] == "market" else float(req["limit_price"])
        targets = normalize_targets(req.get("targets"), req.get("target_fractions"), side=side, quantity=0.0)
        if not targets:
            raise PaperTradingError("at least one target price is required for a perpetual PAPER entry")
        cutoff = min(parse_utc(quote["observed_at"], "quote.observed_at"), now)
        try:
            intent = TradingIntent.from_dict({
                "schema_version": INTENT_SCHEMA_VERSION,
                "action": "open",
                "symbol": symbol,
                "side": side,
                "order_type": req["order_type"],
                "entry_price": entry,
                "stop_price": stop,
                "target_price": targets[-1]["price"],
                "reduce_only": False,
                "reduce_fraction": None,
                "position_id": None,
                "reason": (req.get("note") or "manual PAPER ticket")[:200],
                "source_arm": "manual",
                "as_of": iso_utc(cutoff),
            })
        except PaperTradingError as exc:
            raise PaperTradingError(f"INVALID_LEVELS: {exc}") from None
        for target in targets:
            if (side == "long" and target["price"] <= entry) or (side == "short" and target["price"] >= entry):
                raise PaperTradingError("INVALID_LEVELS: every target must be on the profit side of the entry")
        risk_config = dict(config)
        risk_config["symbols"] = [symbol]
        risk_config["risk_per_trade"] = risk_pct
        if isinstance(instrument.get("maintenance_rate"), (int, float)):
            risk_config["maintenance_margin_rate"] = max(float(config["maintenance_margin_rate"]), float(instrument["maintenance_rate"]))
        wallet = self.store.wallet_summary(self.experiment_id, "primary")
        daily_loss, drawdown, streak = self.runtime._risk_statistics(self.experiment_id)
        decision = self.runtime.risk_engine.evaluate(
            intent,
            risk_config,
            equity=float(wallet["equity"]),
            margin_used=float(wallet["margin_used"]),
            open_positions=self.store.open_positions(self.experiment_id),
            data_cutoff=iso_utc(cutoff),
            decision_as_of=now,
            signal_approved=True,
            leverage=leverage,
            current_price=float(quote["mid_price"]),
            current_atr=None,
            last_bar_close_time=None,
            daily_loss=daily_loss,
            drawdown=drawdown,
            consecutive_losses=streak,
        )
        brain = None
        payload = decision.to_dict()
        if decision.allowed:
            state = self.exposure_state()
            brain = evaluate_entry(
                {
                    "symbol": symbol, "base": instrument["base"], "side": side, "market_type": "perpetual",
                    "risk_usdt": decision.risk_amount, "notional_usdt": decision.notional,
                    "funding_rate": instrument.get("funding_rate"),
                },
                state,
                settings["brain"],
            )
            if brain["action"] != "ALLOW":
                warnings.append(f"PORTFOLIO_BRAIN_{brain['action']}")
            warnings.extend(f"ADVISORY_{code}" for code in brain["advisories"])
            if source == "AI" and settings["brain"]["enabled"] and not brain["allowed"]:
                payload.update({"allowed": False, "code": "PORTFOLIO_BRAIN_BLOCK", "reason": ", ".join(brain["reason_codes"])})
        if quote.get("spread_bps") is not None and quote["spread_bps"] > 20:
            warnings.append("WIDE_SPREAD")
        return self._perp_preview_payload(req, instrument, quote, payload, intent.to_dict(), targets, brain, warnings, now, risk_pct, reference)

    def _perp_preview_payload(
        self,
        req: dict[str, Any],
        instrument: dict[str, Any],
        quote: dict[str, Any],
        decision: dict[str, Any],
        intent: dict[str, Any] | None,
        targets: list[dict[str, Any]],
        brain: dict[str, Any] | None,
        warnings: list[str],
        now: datetime,
        risk_pct: float,
        reference: float | None = None,
    ) -> dict[str, Any]:
        config = self.experiment()["config"]
        quantity = float(decision.get("quantity") or 0.0)
        side = req["action"]
        slip = config["slippage_bps"] / 10_000
        est_fill = None
        if reference is not None:
            est_fill = reference * (1 + slip if side == "long" else 1 - slip) if req["order_type"] == "market" else float(req["limit_price"])
        fee_rate = config["taker_fee_rate"] if req["order_type"] == "market" else config["maker_fee_rate"]
        liq = decision.get("liquidation_price")
        entry = est_fill or (intent or {}).get("entry_price")
        stop = (intent or {}).get("stop_price")
        first_target = targets[0]["price"] if targets else None
        return {
            "schema_version": PREVIEW_SCHEMA,
            "market_type": "perpetual",
            "instrument_id": instrument["instrument_id"],
            "display_symbol": instrument["display_symbol"],
            "symbol": instrument["symbol"],
            "action": side,
            "order_type": req["order_type"],
            "allowed": bool(decision["allowed"]),
            "code": decision["code"],
            "reason": decision["reason"],
            "risk_pct": risk_pct,
            "quantity": quantity,
            "notional_usdt": decision.get("notional"),
            "margin_usdt": decision.get("margin"),
            "leverage": decision.get("leverage"),
            "max_loss_usdt": decision.get("risk_amount"),
            "estimated_entry_price": est_fill,
            "estimated_entry_fee_usdt": None if entry is None else quantity * entry * fee_rate,
            "estimated_exit_fee_at_stop_usdt": None if stop is None else quantity * stop * config["taker_fee_rate"],
            "estimated_slippage_usdt": None if reference is None or req["order_type"] != "market" else quantity * reference * slip,
            "liquidation_price": liq,
            "liquidation_buffer_pct": None if liq is None or not entry else abs(entry - liq) / entry,
            "stop_price": stop,
            "targets": targets,
            "reward_risk": None if not (entry and stop and first_target) else abs(first_target - entry) / abs(entry - stop),
            "quote": {k: quote.get(k) for k in ("best_bid", "best_ask", "mid_price", "spread_bps", "observed_at", "source", "fresh", "age_seconds")},
            "portfolio_brain": brain,
            "warnings": warnings,
            "intent": intent,
            "server_authoritative": True,
            "execution_mode": "PAPER",
            "as_of": iso_utc(now),
        }

    def _spot_preview(self, req: dict[str, Any], *, source: str) -> dict[str, Any]:
        instrument = self.catalog.require_tradable(req["instrument_id"], side=req["action"])
        quote = self.quote(req["instrument_id"])
        settings = self.settings()
        side = req["action"]
        with self.store.transaction() as db:
            wallet = self._spot_wallet_locked(db)
        holdings = self.spot_holdings()
        holdings_value = {
            h["instrument_id"]: float(h["quantity"]) * float(h["mark_price"] or h["avg_cost"]) for h in holdings
        }
        holding = next((h for h in holdings if h["instrument_id"] == req["instrument_id"]), None)
        available = 0.0 if holding is None else float(holding["quantity"]) - float(holding["reserved_quantity"])
        quantity = _num(req.get("quantity"), "quantity", positive=True, allow_none=True)
        quote_amount = _num(req.get("quote_amount"), "quote_amount", positive=True, allow_none=True)
        target_pct = _num(req.get("allocation_target_pct"), "allocation_target_pct", allow_none=True)
        if sum(value is not None for value in (quantity, quote_amount, target_pct)) != 1:
            raise PaperTradingError("send exactly one of quantity, quote_amount, or allocation_target_pct")
        equity = float(wallet["cash_balance"]) + sum(holdings_value.values())
        if target_pct is not None:
            if not 0 <= target_pct <= 1:
                raise PaperTradingError("allocation_target_pct must be between 0 and 1")
            current = holdings_value.get(req["instrument_id"], 0.0)
            delta = target_pct * equity - current
            if side == "buy" and delta <= 0:
                raise PaperTradingError("allocation is already at or above the target; nothing to buy")
            if side == "sell" and delta >= 0:
                raise PaperTradingError("allocation is already at or below the target; nothing to sell")
            quote_amount = abs(delta)
        closing = side == "sell" and quantity is not None and holding is not None and quantity >= available - EPSILON
        decision = self.spot_risk.evaluate(
            side=side,
            order_type=req["order_type"],
            instrument=instrument,
            quote=quote,
            settings=settings,
            wallet=wallet,
            holdings_value=holdings_value,
            holding_available=available,
            holding_quantity=0.0 if holding is None else float(holding["quantity"]),
            quantity=quantity,
            quote_amount=quote_amount,
            limit_price=_num(req.get("limit_price"), "limit_price", positive=True, allow_none=True),
            closing_whole_holding=closing,
        )
        payload = decision.to_dict()
        brain = None
        warnings = list(payload.pop("warnings"))
        if decision.allowed and side == "buy":
            stop = _num(req.get("stop_price"), "stop_price", positive=True, allow_none=True)
            risk = (decision.fill_price - stop) * decision.quantity if stop is not None else decision.notional * SPOT_UNPROTECTED_RISK_PROXY
            brain = evaluate_entry(
                {"symbol": instrument["symbol"], "base": instrument["base"], "side": "long", "market_type": "spot",
                 "risk_usdt": max(0.0, risk), "notional_usdt": decision.notional},
                self.exposure_state(),
                settings["brain"],
            )
            if brain["action"] != "ALLOW":
                warnings.append(f"PORTFOLIO_BRAIN_{brain['action']}")
            if source == "AI" and settings["brain"]["enabled"] and not brain["allowed"]:
                payload.update({"allowed": False, "code": "PORTFOLIO_BRAIN_BLOCK", "reason": ", ".join(brain["reason_codes"])})
        if req.get("stop_price") is not None and side == "buy":
            stop = _num(req["stop_price"], "stop_price", positive=True)
            if decision.fill_price and stop >= decision.fill_price:
                payload.update({"allowed": False, "code": "INVALID_LEVELS", "reason": "protection stop must be below the buy price"})
        plan_targets = normalize_targets(req.get("targets"), req.get("target_fractions"), side="long", quantity=decision.quantity) if req.get("targets") else []
        if plan_targets and decision.fill_price and any(t["price"] <= decision.fill_price for t in plan_targets):
            payload.update({"allowed": False, "code": "INVALID_LEVELS", "reason": "targets must be above the buy price"})
        fee_rate = float(settings.get("spot_fee_rate") or instrument.get("taker_fee_rate") or 0.001)
        return {
            "schema_version": PREVIEW_SCHEMA,
            "market_type": "spot",
            "instrument_id": instrument["instrument_id"],
            "display_symbol": instrument["display_symbol"],
            "symbol": instrument["symbol"],
            "action": side,
            "order_type": req["order_type"],
            **payload,
            "fee_rate": fee_rate,
            "estimated_fill_price": payload.get("fill_price"),
            "quote_amount_usdt": payload.get("notional"),
            "resulting_allocation_pct": payload.get("allocation_after"),
            "cash_remaining_usdt": payload.get("cash_after"),
            "stop_price": _num(req.get("stop_price"), "stop_price", positive=True, allow_none=True),
            "targets": plan_targets,
            "leverage": None,
            "liquidation_price": None,
            "funding": None,
            "quote": {k: quote.get(k) for k in ("best_bid", "best_ask", "mid_price", "spread_bps", "observed_at", "source", "fresh", "age_seconds")},
            "portfolio_brain": brain,
            "warnings": warnings,
            "server_authoritative": True,
            "execution_mode": "PAPER",
            "as_of": self._iso(),
        }

    def create_order(self, request: Any, *, source: str = "USER") -> dict[str, Any]:
        req = self._validate_order_request(request)
        request_id = req.get("client_request_id")
        replay = self._idempotent(request_id, "order", request)
        if replay is not None:
            self._safety_reject("DUPLICATE_PREVENTED", {"client_request_id": request_id}, instrument_id=req["instrument_id"], source=source)
            return replay
        if req.get("preview_as_of") is not None:
            age = (self._now() - parse_utc(req["preview_as_of"], "preview_as_of")).total_seconds()
            if age > self.safety_settings()["preview_ttl_seconds"]:
                self._safety_reject("STALE_DECISION", {"preview_age_seconds": age}, instrument_id=req["instrument_id"], source=source)
                raise PaperTradingError("STALE_DECISION: the preview expired; review the updated preview before submitting")
        if req["market_type"] == "perpetual" or req["action"] == "buy":
            self._guard_new_entries(source)
        with self._lock:
            replay = self._idempotent(request_id, "order", request)
            if replay is not None:
                return replay
            preview = self.preview_order({k: v for k, v in request.items() if k != "preview_as_of"}, source=source)
            if not preview["allowed"] and preview.get("execution_plan"):
                self._safety_reject(preview["code"], {"reason": preview["reason"], "plan": preview["execution_plan"]},
                                    instrument_id=req["instrument_id"], source=source)
            if preview["allowed"] and req["order_type"] == "market":
                plan = preview.get("execution_plan") or {}
                buying = req["action"] in {"long", "buy"}
                fill = preview.get("estimated_entry_price") or preview.get("fill_price")
                if plan and fill and not within_envelope(float(fill), plan, buying):
                    self._safety_reject("SLIPPAGE_LIMIT", {"fill": fill, "envelope": plan.get("price_envelope")}, instrument_id=req["instrument_id"], source=source)
                    preview = {**preview, "allowed": False, "code": "SLIPPAGE_LIMIT", "reason": "estimated fill is outside the acceptable-price envelope"}
            if req["market_type"] == "perpetual":
                response = self._create_perp_order(req, preview, source=source)
            else:
                response = self._create_spot_order(req, preview, source=source)
            with self.store.transaction() as db:
                self._remember_locked(db, request_id, "order", request, response)
            self.record_snapshot(force=True)
            return response

    def _create_perp_order(self, req: dict[str, Any], preview: dict[str, Any], *, source: str) -> dict[str, Any]:
        now = self._now()
        instrument_id = req["instrument_id"]
        if not preview["allowed"]:
            with self.store.transaction() as db:
                self.store.record_risk_event(db, self.experiment_id, None, preview["symbol"], "primary", {
                    "allowed": False, "code": preview["code"], "reason": preview["reason"], "quantity": 0.0,
                    "leverage": preview.get("leverage") or 0, "notional": 0.0, "margin": 0.0, "risk_amount": 0.0,
                    "liquidation_price": None, "reduce_only": False, "source": source,
                })
                self._activity_locked(
                    db, source="SYSTEM", category="RISK", severity="WATCH",
                    title=f"Risk rejected {preview['action'].upper()} · {preview['code']}", summary=preview["reason"][:300],
                    symbol=preview["symbol"], instrument_id=instrument_id, market_type="perpetual",
                )
            return {"accepted": False, "status": "rejected", "code": preview["code"], "reason": preview["reason"], "preview": preview}
        cycle_id = f"manual:{uuid.uuid4().hex[:16]}"
        position_id = f"{cycle_id}:primary"
        config = self.experiment()["config"]
        risk = {k: preview[k] for k in ("allowed", "code", "reason")} | {
            "quantity": preview["quantity"], "leverage": preview["leverage"], "notional": preview["notional_usdt"],
            "margin": preview["margin_usdt"], "risk_amount": preview["max_loss_usdt"],
            "liquidation_price": preview["liquidation_price"], "reduce_only": False,
        }
        quote = preview["quote"]
        reference = float(quote["best_ask"] if req["action"] == "long" else quote["best_bid"])
        origin = MARKET_DATA_ORIGINS["fixture" if self.catalog.source == "fixture" else "gate_usdt"]
        execution = {
            "cohort": "primary",
            "risk": risk,
            "intent": preview["intent"],
            "reference_price": reference,
            "reference_price_source": f"{quote['source']}_best_{'ask' if req['action'] == 'long' else 'bid'}",
            "slippage_bps": config["slippage_bps"],
            "fee_rate": config["taker_fee_rate"],
            "as_of": iso_utc(now),
            "data_origin": origin,
            "market_regime": "unknown",
            "regime_source": "unknown",
            "manual_request": {k: v for k, v in req.items() if k not in {"client_request_id", "market_type"}},
            "source": source,
        }
        targets = normalize_targets(
            [t["price"] for t in preview["targets"]], [t["fraction"] for t in preview["targets"]],
            side=req["action"], quantity=preview["quantity"],
        )
        settings = self.settings()
        with self.store.transaction() as db:
            receipt = self.store._insert_order_locked(db, self.experiment_id, cycle_id, execution, require_running=False)
            ref = f"perp:{position_id}"
            self._insert_meta_locked(
                db, ref=ref, market_type="perpetual", instrument_id=instrument_id, owner=source,
                mode=settings["default_user_management_mode" if source == "USER" else "default_ai_management_mode"],
                stop=preview["stop_price"], targets=targets, initial_risk=preview["max_loss_usdt"],
            )
            order_ref = f"perp:{position_id}:entry"
            self._journal_locked(
                db, action="ORDER_CREATE", source=source, position_ref=ref, order_ref=order_ref, market_type="perpetual",
                before=None, after={"receipt": receipt, "preview": {k: preview[k] for k in ("quantity", "leverage", "max_loss_usdt", "stop_price", "targets")}},
                risk_after=preview["max_loss_usdt"], snapshot={"quote": quote},
            )
            self._activity_locked(
                db, source=source, category="ORDER", severity="INFO",
                title=f"PAPER {req['action'].upper()} {req['order_type']} {'filled' if receipt['status'] == 'filled' else receipt['status']}",
                summary=f"qty {preview['quantity']:.8g} · {preview['leverage']}x · max loss {preview['max_loss_usdt']:.2f} USDT",
                symbol=preview["symbol"], instrument_id=instrument_id, market_type="perpetual",
                position_ref=ref if receipt["status"] == "filled" else None, payload_ref=order_ref,
            )
        return {
            "accepted": receipt["status"] in {"filled", "pending"},
            "status": receipt["status"],
            "order_ref": f"perp:{receipt.get('order_id', order_ref.split(':', 1)[1])}",
            "position_ref": f"perp:{position_id}" if receipt["status"] == "filled" else None,
            "reason": receipt.get("reason"),
            "preview": preview,
        }

    def ai_spot_entry(self, *, cycle_id: str, intent: dict[str, Any], brain: dict[str, Any] | None) -> dict[str, Any] | None:
        """Optional AI Spot allocation for a long primary decision (AUTO_PAPER, policy-gated).

        Same frozen decision as the perpetual entry; Spot sizing is an allocation of the Spot
        wallet, the Portfolio Brain is binding (source AI), and the SpotRiskEngine decides.
        Idempotent per cycle. Never raises into the decision cycle.
        """

        settings = self.settings()
        policy = settings["ai_spot"]
        if not policy["enabled"] or intent.get("side") != "long":
            return None
        if policy["trigger"] == "prefer_spot" and "PREFER_SPOT" not in ((brain or {}).get("advisories") or []):
            return None
        base = intent["symbol"][:-4]
        instrument_id = f"{self.catalog.exchange}:spot:{base}_USDT"
        wallet = self.spot_wallet()
        amount = round(policy["allocation_pct"] * wallet["equity_usdt"], 8)
        request = {
            "client_request_id": f"ai-spot:{cycle_id}"[:80],
            "instrument_id": instrument_id,
            "action": "buy",
            "quote_amount": amount,
            "stop_price": intent["stop_price"],
            "targets": [intent["target_price"]],
            "note": "AI spot allocation from the frozen primary decision",
        }
        try:
            response = self.create_order(request, source="AI")
        except PaperTradingError as exc:
            response = {"accepted": False, "status": "rejected", "code": "AI_SPOT_ENTRY_BLOCKED", "reason": str(exc)[:200]}
            with self.store.transaction() as db:
                self._activity_locked(
                    db, source="SYSTEM", category="RISK", severity="INFO", title="AI Spot entry not taken",
                    summary=response["reason"], symbol=f"{base}USDT", instrument_id=instrument_id, market_type="spot",
                    payload_ref=f"cycle:{cycle_id}",
                )
        return {k: response.get(k) for k in ("accepted", "status", "code", "reason", "order_ref", "position_ref")}

    def _holding_for_locked(self, db: Any, instrument_id: str) -> dict[str, Any] | None:
        row = db.execute(
            "SELECT * FROM spot_holdings WHERE experiment_id=? AND instrument_id=? AND status='open'",
            (self.experiment_id, instrument_id),
        ).fetchone()
        return None if row is None else dict(row)

    def _spot_fill_locked(
        self,
        db: Any,
        *,
        order_id: str,
        instrument: dict[str, Any],
        side: str,
        quantity: float,
        price: float,
        reference: float,
        fee_rate: float,
        liquidity: str,
        source: str,
        exit_reason: str | None = None,
        release_reserved_quantity: float = 0.0,
    ) -> dict[str, Any]:
        now = self._iso()
        holding = self._holding_for_locked(db, instrument["instrument_id"])
        wallet = self._spot_wallet_locked(db)
        state = SpotHoldingState.from_row(holding)
        fill = apply_buy(state, quantity, price, fee_rate) if side == "buy" else apply_sell(state, quantity, price, fee_rate)
        slippage = abs(price - reference) * fill.quantity
        if side == "buy" and fill.quote_delta + float(wallet["cash_balance"]) < -EPSILON:
            raise PaperTradingError("INSUFFICIENT_QUOTE: spot cash became insufficient before the fill")
        db.execute(
            "UPDATE spot_wallets SET cash_balance=cash_balance+? WHERE experiment_id=? AND quote='USDT'",
            (fill.quote_delta, self.experiment_id),
        )
        origin = instrument.get("data_origin") or "GATE_SPOT_PUBLIC"
        if holding is None:
            holding_id = f"sh-{uuid.uuid4().hex[:16]}"
            db.execute(
                "INSERT INTO spot_holdings(holding_id, experiment_id, instrument_id, exchange_symbol, symbol, base, quote, "
                "quantity, reserved_quantity, avg_cost, cost_basis, realized_pnl, fees_paid, slippage_paid, total_bought, "
                "total_sold, mark_price, mark_at, status, source, opened_at, data_origin) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
                (
                    holding_id, self.experiment_id, instrument["instrument_id"], instrument["exchange_symbol"],
                    instrument["symbol"], instrument["base"], instrument["quote"], fill.holding.quantity,
                    fill.holding.avg_cost, fill.holding.cost_basis, fill.holding.realized_pnl, fill.holding.fees_paid,
                    slippage, fill.holding.total_bought, fill.holding.total_sold, price, now, source, now, origin,
                ),
            )
        else:
            holding_id = holding["holding_id"]
            closed = fill.holding.quantity <= EPSILON
            db.execute(
                "UPDATE spot_holdings SET quantity=?, reserved_quantity=MAX(0, reserved_quantity-?), avg_cost=?, cost_basis=?, "
                "realized_pnl=?, fees_paid=?, slippage_paid=slippage_paid+?, total_bought=?, total_sold=?, "
                "mark_price=?, mark_at=?, status=?, closed_at=?, exit_reason=? WHERE holding_id=?",
                (
                    fill.holding.quantity, release_reserved_quantity, fill.holding.avg_cost, fill.holding.cost_basis,
                    fill.holding.realized_pnl, fill.holding.fees_paid, slippage, fill.holding.total_bought,
                    fill.holding.total_sold, price, now, "closed" if closed else "open", now if closed else None,
                    (exit_reason or "user_sell") if closed else None, holding_id,
                ),
            )
        fill_id = f"{order_id}:fill:{uuid.uuid4().hex[:8]}"
        db.execute(
            "INSERT INTO spot_fills(fill_id, experiment_id, order_id, holding_id, instrument_id, side, quantity, price, fee, "
            "slippage_cost, realized_pnl, liquidity, as_of) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fill_id, self.experiment_id, order_id, holding_id, instrument["instrument_id"], side, fill.quantity,
                price, fill.fee, slippage, fill.realized_pnl, liquidity, now,
            ),
        )
        closed = side == "sell" and fill.holding.quantity <= EPSILON
        self._activity_locked(
            db, source="SYSTEM", category="OUTCOME" if closed else "FILL", severity="INFO",
            title=f"PAPER spot {side} filled" + (" · holding closed" if closed else ""),
            summary=f"{fill.quantity:.8g} {instrument['base']} @ {price:.8g} · fee {fill.fee:.4f} USDT"
            + (f" · realized {fill.realized_pnl:+.4f} USDT" if side == "sell" else ""),
            symbol=instrument["symbol"], instrument_id=instrument["instrument_id"], market_type="spot",
            position_ref=f"spot:{holding_id}", payload_ref=f"spot-fill:{fill_id}",
        )
        return {
            "fill_id": fill_id,
            "holding_id": holding_id,
            "quantity": fill.quantity,
            "price": price,
            "fee": fill.fee,
            "realized_pnl": fill.realized_pnl,
            "holding_closed": closed,
            "holding_quantity": fill.holding.quantity,
        }

    def _create_spot_order(self, req: dict[str, Any], preview: dict[str, Any], *, source: str) -> dict[str, Any]:
        instrument = self.catalog.get(req["instrument_id"])
        order_id = f"so-{uuid.uuid4().hex[:16]}"
        now = self._iso()
        side = req["action"]
        if not preview["allowed"]:
            with self.store.transaction() as db:
                self._activity_locked(
                    db, source="SYSTEM", category="RISK", severity="WATCH",
                    title=f"Spot {side} rejected · {preview['code']}", summary=preview["reason"][:300],
                    symbol=instrument["symbol"], instrument_id=instrument["instrument_id"], market_type="spot",
                )
            return {"accepted": False, "status": "rejected", "code": preview["code"], "reason": preview["reason"], "preview": preview}
        quantity = float(preview["quantity"])
        risk_json = _dumps({"preview": preview, "request": {k: v for k, v in req.items() if k != "client_request_id"}})
        settings = self.settings()
        fee_rate = float(preview["fee_rate"])
        with self.store.transaction() as db:
            holding_before = self._holding_for_locked(db, instrument["instrument_id"])
            if req["order_type"] == "market":
                fill = self._spot_fill_locked(
                    db, order_id=order_id, instrument=instrument, side=side, quantity=quantity,
                    price=float(preview["fill_price"]), reference=float(preview["reference_price"]),
                    fee_rate=fee_rate, liquidity="taker", source=source,
                    exit_reason="user_sell" if source == "USER" else "ai_sell",
                )
                db.execute(
                    "INSERT INTO spot_orders(order_id, experiment_id, instrument_id, exchange_symbol, symbol, side, order_type, "
                    "source, requested_quantity, requested_quote, limit_price, filled_quantity, filled_quote, fees, avg_fill_price, "
                    "status, holding_id, risk_json, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, 'market', ?, ?, ?, NULL, ?, ?, ?, ?, 'filled', ?, ?, ?, ?)",
                    (
                        order_id, self.experiment_id, instrument["instrument_id"], instrument["exchange_symbol"],
                        instrument["symbol"], side, source, quantity, req.get("quote_amount"), fill["quantity"],
                        fill["quantity"] * fill["price"], fill["fee"], fill["price"], fill["holding_id"], risk_json, now, now,
                    ),
                )
                status = "filled"
                holding_id = fill["holding_id"]
            else:
                limit = float(req["limit_price"])
                reserved_quote = quantity * limit * (1 + fee_rate) if side == "buy" else 0.0
                reserved_quantity = quantity if side == "sell" else 0.0
                if side == "buy":
                    db.execute(
                        "UPDATE spot_wallets SET reserved_quote=reserved_quote+? WHERE experiment_id=? AND quote='USDT'",
                        (reserved_quote, self.experiment_id),
                    )
                else:
                    db.execute(
                        "UPDATE spot_holdings SET reserved_quantity=reserved_quantity+? WHERE holding_id=?",
                        (reserved_quantity, holding_before["holding_id"]),
                    )
                db.execute(
                    "INSERT INTO spot_orders(order_id, experiment_id, instrument_id, exchange_symbol, symbol, side, order_type, "
                    "source, requested_quantity, requested_quote, limit_price, status, holding_id, reserved_quote, "
                    "reserved_quantity, risk_json, created_at, updated_at, last_checked_bar) "
                    "VALUES(?, ?, ?, ?, ?, ?, 'limit', ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        order_id, self.experiment_id, instrument["instrument_id"], instrument["exchange_symbol"],
                        instrument["symbol"], side, source, quantity, req.get("quote_amount"), limit,
                        None if holding_before is None else holding_before["holding_id"], reserved_quote,
                        reserved_quantity, risk_json, now, now, now,
                    ),
                )
                status = "pending"
                fill = None
                holding_id = None if holding_before is None else holding_before["holding_id"]
            if holding_id and side == "buy" and fill is not None:
                ref = f"spot:{holding_id}"
                stop = preview.get("stop_price")
                targets = normalize_targets(
                    [t["price"] for t in preview["targets"]] or None,
                    [t["fraction"] for t in preview["targets"]] or None,
                    side="long", quantity=fill["holding_quantity"],
                ) if preview["targets"] else []
                if holding_before is None:
                    self._insert_meta_locked(
                        db, ref=ref, market_type="spot", instrument_id=instrument["instrument_id"], owner=source,
                        mode=settings["default_user_management_mode" if source == "USER" else "default_ai_management_mode"],
                        stop=stop, targets=targets,
                        initial_risk=None if stop is None else max(0.0, (fill["price"] - stop) * fill["quantity"]),
                    )
                elif stop is not None or targets:
                    db.execute(
                        "UPDATE position_meta SET stop_price=COALESCE(?, stop_price), targets_json=?, plan_version=plan_version+1, "
                        "updated_at=? WHERE position_ref=?",
                        (stop, _dumps(targets), now, ref),
                    )
            order_ref = f"spot:{order_id}"
            self._journal_locked(
                db, action="ORDER_CREATE", source=source, position_ref=None if holding_id is None else f"spot:{holding_id}",
                order_ref=order_ref, market_type="spot", before=None,
                after={"status": status, "quantity": quantity, "limit_price": req.get("limit_price"), "fill": fill},
                snapshot={"quote": preview["quote"]},
            )
            self._activity_locked(
                db, source=source, category="ORDER", severity="INFO",
                title=f"PAPER spot {side} {req['order_type']} {status}",
                summary=f"{quantity:.8g} {instrument['base']}"
                + (f" @ limit {float(req['limit_price']):.8g}" if req["order_type"] == "limit" else f" ≈ {float(preview['notional']):.2f} USDT"),
                symbol=instrument["symbol"], instrument_id=instrument["instrument_id"], market_type="spot",
                position_ref=None if holding_id is None else f"spot:{holding_id}", payload_ref=order_ref,
            )
        return {
            "accepted": True,
            "status": status,
            "order_ref": f"spot:{order_id}",
            "position_ref": None if holding_id is None else f"spot:{holding_id}",
            "fill": fill,
            "preview": preview,
        }

    def list_orders(self, *, status: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        views: list[dict[str, Any]] = []
        perp_rows = self.store._query(
            "SELECT * FROM orders WHERE experiment_id=? AND cohort='primary' ORDER BY created_at DESC LIMIT ?",
            (self.experiment_id, limit),
        )
        for row in perp_rows:
            saved = _loads(row["risk_json"], {})
            intent = saved.get("intent") or {}
            manual = str(row["cycle_id"]).startswith("manual:")
            normalized = {"open": "working", "closed": "filled"}.get(row["status"], row["status"])
            quantity = float(row["quantity"])
            filled = float(row["filled_quantity"])
            views.append({
                "schema_version": ORDER_VIEW_SCHEMA,
                "order_ref": f"perp:{row['order_id']}",
                "order_id": row["order_id"],
                "market_type": "perpetual",
                "instrument_id": self.perp_instrument_id(row["symbol"]),
                "symbol": row["symbol"],
                "display_symbol": f"{row['symbol'][:-4]}/USDT Perp",
                "side": row["side"],
                "action": "reduce" if row["reduce_only"] else f"open_{row['side']}",
                "order_type": row["order_type"],
                "reduce_only": bool(row["reduce_only"]),
                "requested_quantity": quantity,
                "requested_notional_usdt": None if not intent.get("entry_price") else quantity * float(intent["entry_price"]),
                "filled_quantity": filled,
                "remaining_quantity": max(0.0, quantity - filled) if normalized in {"pending", "working"} else 0.0,
                "filled_pct": filled / quantity if quantity > 0 else 0.0,
                "limit_price": row["limit_price"],
                "stop_price": intent.get("stop_price"),
                "target_price": intent.get("target_price"),
                "status": normalized,
                "source": (saved.get("execution") or {}).get("source") or ("USER" if manual else "AI"),
                "position_ref": f"perp:{row['position_id']}",
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "cancellable": row["status"] == "pending",
                "amendable": row["status"] == "pending" and manual,
            })
        spot_rows = self.store._query(
            "SELECT * FROM spot_orders WHERE experiment_id=? ORDER BY created_at DESC LIMIT ?", (self.experiment_id, limit)
        )
        for row in spot_rows:
            quantity = float(row["requested_quantity"])
            filled = float(row["filled_quantity"])
            base = row["symbol"][: -len("USDT")] if row["symbol"].endswith("USDT") else row["symbol"]
            views.append({
                "schema_version": ORDER_VIEW_SCHEMA,
                "order_ref": f"spot:{row['order_id']}",
                "order_id": row["order_id"],
                "market_type": "spot",
                "instrument_id": row["instrument_id"],
                "symbol": row["symbol"],
                "display_symbol": row["exchange_symbol"].replace("_", "/"),
                "side": row["side"],
                "action": row["side"],
                "order_type": row["order_type"],
                "reduce_only": False,
                "requested_quantity": quantity,
                "requested_notional_usdt": row["requested_quote"] or (None if row["limit_price"] is None else quantity * float(row["limit_price"])),
                "filled_quantity": filled,
                "remaining_quantity": max(0.0, quantity - filled) if row["status"] in {"pending", "partially_filled"} else 0.0,
                "filled_pct": filled / quantity if quantity > 0 else 0.0,
                "avg_fill_price": row["avg_fill_price"],
                "limit_price": row["limit_price"],
                "fees_usdt": float(row["fees"]),
                "status": row["status"],
                "source": row["source"],
                "position_ref": None if not row["holding_id"] else f"spot:{row['holding_id']}",
                "base": base,
                "replaces_order_ref": None if not row["replaces_order_id"] else f"spot:{row['replaces_order_id']}",
                "replaced_by_order_ref": None if not row["replaced_by_order_id"] else f"spot:{row['replaced_by_order_id']}",
                "reject_code": row["reject_code"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "cancellable": row["status"] in {"pending", "partially_filled"},
                "amendable": row["status"] == "pending",
            })
        if status:
            wanted = {"open": {"pending", "partially_filled", "working"}}.get(status, {status})
            views = [view for view in views if view["status"] in wanted]
        views.sort(key=lambda item: item["created_at"], reverse=True)
        return views[:limit]

    def cancel_order(self, order_ref: str, *, source: str = "USER", reason: str = "user_cancel") -> dict[str, Any]:
        kind, order_id = parse_order_ref(order_ref)
        with self._lock:
            with self.store.transaction() as db:
                if kind == "perp":
                    row = db.execute(
                        "SELECT * FROM orders WHERE order_id=? AND experiment_id=?", (order_id, self.experiment_id)
                    ).fetchone()
                    if row is None:
                        raise PaperTradingError("order was not found")
                    if row["status"] != "pending":
                        raise PaperTradingError(
                            "CANCEL_APPLIES_TO_PENDING_ORDERS: this order is not pending; use Close for an open position"
                        )
                    db.execute(
                        "UPDATE orders SET status='cancelled', updated_at=? WHERE order_id=? AND status='pending'",
                        (self._iso(), order_id),
                    )
                    symbol = row["symbol"]
                    instrument = self.perp_instrument_id(symbol)
                    market_type = "perpetual"
                else:
                    row = db.execute(
                        "SELECT * FROM spot_orders WHERE order_id=? AND experiment_id=?", (order_id, self.experiment_id)
                    ).fetchone()
                    if row is None:
                        raise PaperTradingError("order was not found")
                    if row["status"] not in {"pending", "partially_filled"}:
                        raise PaperTradingError(
                            "CANCEL_APPLIES_TO_PENDING_ORDERS: this order is not pending; use Close for a holding"
                        )
                    self._release_spot_reservation_locked(db, dict(row))
                    db.execute(
                        "UPDATE spot_orders SET status='cancelled', reserved_quote=0, reserved_quantity=0, updated_at=? WHERE order_id=?",
                        (self._iso(), order_id),
                    )
                    symbol = row["symbol"]
                    instrument = row["instrument_id"]
                    market_type = "spot"
                self._journal_locked(
                    db, action="ORDER_CANCEL", source=source, order_ref=order_ref, market_type=market_type,
                    before={"status": row["status"]}, after={"status": "cancelled", "reason": reason},
                )
                self._activity_locked(
                    db, source=source, category="ORDER", severity="INFO", title="PAPER order cancelled",
                    summary=f"{symbol} {row['side']} {row['order_type']} · {reason}",
                    symbol=symbol, instrument_id=instrument, market_type=market_type, payload_ref=order_ref,
                )
        return {"order_ref": order_ref, "status": "cancelled"}

    def _release_spot_reservation_locked(self, db: Any, row: dict[str, Any]) -> None:
        if float(row["reserved_quote"] or 0) > 0:
            db.execute(
                "UPDATE spot_wallets SET reserved_quote=MAX(0, reserved_quote-?) WHERE experiment_id=? AND quote='USDT'",
                (float(row["reserved_quote"]), self.experiment_id),
            )
        if float(row["reserved_quantity"] or 0) > 0 and row.get("holding_id"):
            db.execute(
                "UPDATE spot_holdings SET reserved_quantity=MAX(0, reserved_quantity-?) WHERE holding_id=?",
                (float(row["reserved_quantity"]), row["holding_id"]),
            )

    def amend_order(self, order_ref: str, patch: Any, *, source: str = "USER") -> dict[str, Any]:
        """Cancel/replace a pending limit order; the replacement is re-validated from scratch."""

        if not isinstance(patch, dict) or not patch:
            raise PaperTradingError("amend requires changed fields")
        kind, order_id = parse_order_ref(order_ref)
        allowed = {"limit_price", "quantity", "quote_amount", "stop_price", "targets", "target_fractions", "risk_pct", "leverage", "client_request_id"}
        if set(patch) - allowed:
            raise PaperTradingError("amend accepts limit_price, size, stop, targets, risk_pct, leverage only")
        with self._lock:
            if kind == "perp":
                rows = self.store._query("SELECT * FROM orders WHERE order_id=? AND experiment_id=?", (order_id, self.experiment_id))
                if not rows or rows[0]["status"] != "pending":
                    raise PaperTradingError("AMEND_APPLIES_TO_PENDING_ORDERS: only pending limit orders can be amended")
                saved = _loads(rows[0]["risk_json"], {})
                original = (saved.get("execution") or {}).get("manual_request")
                if not original:
                    raise PaperTradingError("AI-generated pending orders can be cancelled but not amended")
            else:
                rows = self.store._query("SELECT * FROM spot_orders WHERE order_id=? AND experiment_id=?", (order_id, self.experiment_id))
                if not rows or rows[0]["status"] != "pending":
                    raise PaperTradingError("AMEND_APPLIES_TO_PENDING_ORDERS: only unfilled pending limit orders can be amended")
                original = _loads(rows[0]["risk_json"], {}).get("request") or {}
            replacement = {k: v for k, v in original.items() if k not in {"market_type", "client_request_id"}}
            if "quantity" in patch or "quote_amount" in patch:
                replacement.pop("quantity", None)
                replacement.pop("quote_amount", None)
                replacement.pop("allocation_target_pct", None)
            replacement.update({k: v for k, v in patch.items() if k != "client_request_id"})
            replacement["order_type"] = "limit"
            preview = self.preview_order(replacement, source=source)
            if not preview["allowed"]:
                return {"accepted": False, "status": "rejected", "code": preview["code"], "reason": preview["reason"], "preview": preview, "original_order_ref": order_ref}
            self.cancel_order(order_ref, source=source, reason="amend_cancel_replace")
            created = self.create_order({**replacement, **({"client_request_id": patch["client_request_id"]} if patch.get("client_request_id") else {})}, source=source)
            with self.store.transaction() as db:
                if kind == "spot" and created.get("order_ref"):
                    new_id = created["order_ref"].split(":", 1)[1]
                    db.execute("UPDATE spot_orders SET replaced_by_order_id=? WHERE order_id=?", (new_id, order_id))
                    db.execute("UPDATE spot_orders SET replaces_order_id=? WHERE order_id=?", (order_id, new_id))
                self._journal_locked(
                    db, action="ORDER_AMEND", source=source, order_ref=order_ref,
                    market_type="perpetual" if kind == "perp" else "spot",
                    before={k: original.get(k) for k in patch if k != "client_request_id"},
                    after={"replacement_order_ref": created.get("order_ref"), **{k: v for k, v in patch.items() if k != "client_request_id"}},
                )
            return {**created, "original_order_ref": order_ref, "amend_mode": "cancel_replace"}

    # ------------------------------------------------------ position manager
    def _authorize(self, ref: str, meta: dict[str, Any] | None, source: str, *, reason: str | None = None) -> None:
        if source == "USER":
            return
        if source == "SYSTEM":
            # Deterministic liquidation safety sits above every authority mode.
            if reason == "liquidation_emergency":
                return
            raise PaperTradingError("AUTHORITY_DENIED: SYSTEM mutations are limited to liquidation emergencies")
        if source != "AI":
            raise PaperTradingError("mutation source must be USER, AI, or SYSTEM")
        if KILL_RANK[self.kill_switch()["level"]] >= KILL_RANK["AI_MANAGEMENT_PAUSED"]:
            raise PaperTradingError(f"AUTHORITY_DENIED: kill switch {self.kill_switch()['level']} pauses AI management")
        automation = self.settings()["automation"]
        mode = (meta or {}).get("management_mode")
        if mode != "AUTO_PAPER":
            raise PaperTradingError(f"AUTHORITY_DENIED: AI cannot mutate a {mode or 'UNASSIGNED'} position")
        if automation["ai_management_paused"] or automation["emergency_stop"]:
            raise PaperTradingError("AUTHORITY_DENIED: AI position management is paused")

    def _position_row(self, ref: str) -> tuple[str, str, dict[str, Any]]:
        kind, identifier = parse_position_ref(ref)
        if kind == "perp":
            rows = self.store._query(
                "SELECT * FROM positions WHERE position_id=? AND experiment_id=? AND cohort='primary'",
                (identifier, self.experiment_id),
            )
        else:
            rows = self.store._query(
                "SELECT * FROM spot_holdings WHERE holding_id=? AND experiment_id=?", (identifier, self.experiment_id)
            )
        if not rows:
            raise PaperTradingError("PAPER position was not found")
        row = dict(rows[0])
        if row["status"] != "open":
            raise PaperTradingError("POSITION_CLOSED: the position is no longer open")
        return kind, identifier, row

    def _current_price(self, kind: str, row: dict[str, Any]) -> tuple[float, dict[str, Any] | None]:
        instrument = self.perp_instrument_id(row["symbol"]) if kind == "perp" else row["instrument_id"]
        try:
            quote = self.catalog.quote(instrument)
        except (PaperTradingError, MarketDataError):
            quote = None
        if quote and quote.get("fresh"):
            if kind == "perp":
                price = quote.get("mark_price") or quote.get("mid_price")
            else:
                price = quote.get("mid_price")
            return float(price), quote
        return float(row["mark_price"] or row.get("avg_cost") or 0.0), quote

    def update_protection(
        self,
        ref: str,
        *,
        stop_price: Any = "__unchanged__",
        targets: Any = None,
        target_fractions: Any = None,
        source: str = "USER",
        confirm_risk_increase: bool = False,
        request_id: str | None = None,
        proposal_id: str | None = None,
    ) -> dict[str, Any]:
        request = {"ref": ref, "stop_price": stop_price, "targets": targets, "target_fractions": target_fractions, "confirm": confirm_risk_increase}
        replay = self._idempotent(request_id, "protection", request)
        if replay is not None:
            return replay
        with self._lock:
            self.sync_meta()
            kind, identifier, row = self._position_row(ref)
            meta = self._meta(ref)
            self._authorize(ref, meta, source)
            side = row.get("side", "long")
            quantity = float(row["quantity"])
            price, quote = self._current_price(kind, row)
            old_stop = float(row["stop_price"]) if kind == "perp" else (meta or {}).get("stop_price")
            new_stop = old_stop
            if stop_price != "__unchanged__":
                if stop_price is None:
                    if kind == "perp":
                        raise PaperTradingError("a perpetual PAPER position always keeps a protective stop")
                    new_stop = None
                else:
                    new_stop = _num(stop_price, "stop_price", positive=True)
            if new_stop is not None:
                if (side == "long" and new_stop >= price) or (side == "short" and new_stop <= price):
                    raise PaperTradingError("INVALID_LEVELS: the stop must stay on the loss side of the current price")
                if abs(price - new_stop) / price > 0.25:
                    raise PaperTradingError("STOP_DISTANCE: the stop is more than 25% from the current price")
                if kind == "perp":
                    liq = float(row["liquidation_price"])
                    if (side == "long" and new_stop <= liq) or (side == "short" and new_stop >= liq):
                        raise PaperTradingError("LIQUIDATION_BEFORE_STOP: the stop would sit beyond the liquidation estimate")
            old_targets = (meta or {}).get("targets") or (
                [{"price": float(row["target_price"]), "fraction": 1.0, "quantity": None, "final": True, "hit": False}]
                if kind == "perp" else []
            )
            new_targets = old_targets
            if targets is not None:
                if targets == [] and kind == "spot":
                    new_targets = []
                else:
                    new_targets = [t for t in old_targets if t.get("hit")] + normalize_targets(
                        targets, target_fractions, side=side, quantity=quantity
                    )
                    for target in new_targets:
                        if target.get("hit"):
                            continue
                        if (side == "long" and target["price"] <= price) or (side == "short" and target["price"] >= price):
                            raise PaperTradingError("INVALID_LEVELS: targets must be on the profit side of the current price")
            if kind == "perp" and not [t for t in new_targets if not t.get("hit")]:
                raise PaperTradingError("a perpetual PAPER position needs at least one open target")
            sign = 1 if side == "long" else -1

            def risk_at(stop: float | None) -> float:
                if stop is None:
                    return quantity * price
                return max(0.0, sign * (price - stop) * quantity)

            risk_before = risk_at(old_stop)
            risk_after = risk_at(new_stop)
            increased = risk_after > risk_before + 1e-9
            if increased and source == "AI":
                raise PaperTradingError("AI_RISK_INCREASE_FORBIDDEN: autonomous changes may not increase risk")
            level = self.kill_switch()["level"]
            if increased and KILL_RANK[level] >= KILL_RANK["RISK_REDUCING_ONLY"]:
                raise PaperTradingError(f"KILL_SWITCH_{level}: only risk-reducing protection changes are allowed")
            if increased and not confirm_risk_increase:
                raise ConfirmationRequired(
                    "CONFIRM_RISK_INCREASE",
                    "this change increases risk-at-stop; confirm to apply",
                    {"risk_before_usdt": risk_before, "risk_after_usdt": risk_after, "instrument": row["symbol"]},
                )
            now = self._iso()
            with self.store.transaction() as db:
                if kind == "perp":
                    final_target = [t for t in new_targets if not t.get("hit")][-1]["price"]
                    db.execute(
                        "UPDATE positions SET stop_price=?, target_price=? WHERE position_id=? AND status='open'",
                        (new_stop, final_target, identifier),
                    )
                db.execute(
                    "UPDATE position_meta SET stop_price=?, targets_json=?, plan_version=plan_version+1, updated_at=?, "
                    "last_user_override_at=CASE WHEN ?='USER' THEN ? ELSE last_user_override_at END WHERE position_ref=?",
                    (new_stop, _dumps(new_targets), now, source, now, ref),
                )
                event_id = self._journal_locked(
                    db, action="PROTECTION_UPDATE", source=source, position_ref=ref,
                    market_type="perpetual" if kind == "perp" else "spot",
                    before={"stop_price": old_stop, "targets": old_targets},
                    after={"stop_price": new_stop, "targets": new_targets, "proposal_id": proposal_id},
                    risk_before=risk_before, risk_after=risk_after,
                    snapshot={"price": price, "quote": None if quote is None else {k: quote.get(k) for k in ("best_bid", "best_ask", "observed_at", "source")}},
                    request_id=request_id,
                )
                self._activity_locked(
                    db, source=source, category="MANAGEMENT", severity="INFO",
                    title=f"Protection updated · stop {self._fmt(old_stop)} → {self._fmt(new_stop)}",
                    summary=f"targets {', '.join(self._fmt(t['price']) for t in new_targets if not t.get('hit')) or '—'} · risk {risk_before:.2f} → {risk_after:.2f} USDT",
                    symbol=row["symbol"], instrument_id=(meta or {}).get("instrument_id"),
                    market_type="perpetual" if kind == "perp" else "spot", position_ref=ref,
                    payload_ref=f"management:{event_id}",
                )
                response = {
                    "position_ref": ref,
                    "previous": {"stop_price": old_stop, "targets": old_targets},
                    "new": {"stop_price": new_stop, "targets": new_targets},
                    "risk_before_usdt": risk_before,
                    "risk_after_usdt": risk_after,
                    "risk_increased": increased,
                    "snapshot": {"price": price, "observed_at": None if quote is None else quote.get("observed_at")},
                    "audit_event_id": event_id,
                    "source": source,
                }
                self._remember_locked(db, request_id, "protection", request, response)
            return response

    @staticmethod
    def _fmt(value: Any) -> str:
        if not isinstance(value, (int, float)):
            return "none"
        return f"{value:,.2f}" if abs(value) >= 1000 else f"{value:.6g}"

    def reduce_position(
        self,
        ref: str,
        fraction: Any,
        *,
        source: str = "USER",
        confirm: bool = False,
        request_id: str | None = None,
        reason: str | None = None,
        proposal_id: str | None = None,
    ) -> dict[str, Any]:
        fraction = _num(fraction, "fraction", positive=True)
        if fraction > 1:
            raise PaperTradingError("fraction must be greater than 0 and at most 1")
        request = {"ref": ref, "fraction": fraction, "confirm": confirm}
        replay = self._idempotent(request_id, "reduce", request)
        if replay is not None:
            self._safety_reject("DUPLICATE_PREVENTED", {"request_id": request_id}, position_ref=ref, source=source)
            return replay
        with self._lock:
            self.sync_meta()
            try:
                kind, identifier, row = self._position_row(ref)
            except PaperTradingError as exc:
                if "POSITION_CLOSED" in str(exc):
                    self._safety_reject("WRONG_SIDE_BLOCK", {"reason": "reduce/close after flat cannot open or reverse"}, position_ref=ref, source=source)
                raise
            meta = self._meta(ref)
            self._authorize(ref, meta, source, reason=reason)
            closing = fraction >= 1 - 1e-9
            plan = self._reduce_plan(kind, row, meta, fraction=1.0 if closing else fraction, closing=closing, source=source, reason=reason)
            if plan["decision"] in {"REJECT", "DEFER"}:
                code = (plan.get("blocked_reason") or "").split(":")[0] or (plan["reasons"][0] if plan["reasons"] else "EXECUTION_DEFERRED")
                self._safety_reject(code, {"plan": plan}, instrument_id=(meta or {}).get("instrument_id"), position_ref=ref, source=source)
                raise PaperTradingError(f"{code}: execution {plan['decision'].lower()} ({', '.join(plan['reasons']) or plan['market_state']})")
            clamped = bool({"SELL_VELOCITY_LIMIT", "CORE_PROTECTED"} & set(plan["reasons"]))
            if clamped and source == "USER" and not confirm:
                raise ConfirmationRequired(
                    "CONFIRM_SAFETY_OVERRIDE",
                    f"safety guards limit this {row['symbol']} reduction ({', '.join(plan['reasons'])}); override?",
                    {"instrument": row["symbol"], "requested_quantity": plan["requested_quantity"], "guarded_quantity": plan["quantity"],
                     "market_state": plan["market_state"]},
                )
            if clamped and source == "USER":
                self._safety_reject("SAFETY_OVERRIDE", {"plan_reasons": plan["reasons"], "requested": plan["requested_quantity"]},
                                    position_ref=ref, source="USER")
                plan["quantity"] = plan["requested_quantity"]
                plan["slices"] = [plan["requested_quantity"]]
            if source == "USER" and not confirm:
                if closing:
                    raise ConfirmationRequired(
                        "CONFIRM_CLOSE", f"close the full PAPER {row['symbol']} position?",
                        {"instrument": row["symbol"], "quantity": float(row["quantity"])},
                    )
                if fraction > CONFIRM_REDUCE_ABOVE:
                    raise ConfirmationRequired(
                        "CONFIRM_LARGE_REDUCE", f"reduce {fraction * 100:.0f}% of the PAPER {row['symbol']} position?",
                        {"instrument": row["symbol"], "fraction": fraction, "quantity": float(row["quantity"]) * fraction},
                    )
            price, quote = self._current_price(kind, row)
            before = {"quantity": float(row["quantity"])}
            after, closed = self._execute_plan(kind, identifier, row, meta, plan, closing=closing, source=source, reason=reason,
                                               request_id=request_id)
            with self.store.transaction() as db:
                event_id = self._journal_locked(
                    db, action="CLOSE" if closed else "REDUCE", source=source, position_ref=ref,
                    market_type="perpetual" if kind == "perp" else "spot",
                    before=before, after={**after, "fraction": fraction, "reason": reason, "proposal_id": proposal_id,
                                          "plan_id": plan["plan_id"], "plan_decision": plan["decision"], "plan_reasons": plan["reasons"]},
                    snapshot={"price": price}, request_id=request_id,
                )
                if kind == "perp":
                    self._activity_locked(
                        db, source=source, category="OUTCOME" if closed else "MANAGEMENT", severity="INFO",
                        title=("Position closed" if closed else f"Reduced {fraction * 100:.0f}%") + f" · {row['symbol']}",
                        summary=f"filled {after['filled_quantity']:.8g}"
                        + (f" · realized {after['realized_pnl']:+.4f} USDT" if isinstance(after.get("realized_pnl"), (int, float)) else ""),
                        symbol=row["symbol"], instrument_id=(meta or {}).get("instrument_id"), market_type="perpetual",
                        position_ref=ref, payload_ref=f"management:{event_id}",
                    )
                if source == "USER":
                    db.execute("UPDATE position_meta SET last_user_override_at=? WHERE position_ref=?", (self._iso(), ref))
                response = {"position_ref": ref, "closed": closed, "fraction": fraction, "result": after, "audit_event_id": event_id,
                            "source": source, "execution_plan": {k: plan.get(k) for k in ("plan_id", "decision", "style", "slices", "reasons", "market_state")}}
                self._remember_locked(db, request_id, "reduce", request, response)
            self.record_snapshot(force=True)
            self.reconcile()
            return response

    def _recent_reduced_fraction(self, ref: str, quantity_now: float) -> float:
        window = timedelta(minutes=self.safety_settings()["sell_velocity_window_minutes"])
        since = iso_utc(self._now() - window)
        reduced = 0.0
        for event in self.management_events(ref, limit=200):
            if event["created_at"] < since or event["action"] not in {"REDUCE", "CLOSE", "PLAN_STOP", "PLAN_TARGET", "TARGET_PARTIAL"}:
                continue
            after = event.get("after") or {}
            reduced += float(after.get("filled_quantity") or after.get("quantity") or 0.0) if event["action"] in {"REDUCE", "CLOSE"} else float(after.get("quantity") or after.get("filled_quantity") or 0.0)
        total = reduced + quantity_now
        return reduced / total if total > 0 else 0.0

    def _reduce_plan(self, kind: str, row: dict[str, Any], meta: dict[str, Any] | None, *, fraction: float, closing: bool,
                     source: str, reason: str | None) -> dict[str, Any]:
        instrument_id = (meta or {}).get("instrument_id") or (self.perp_instrument_id(row["symbol"]) if kind == "perp" else row["instrument_id"])
        assessment = self.assess(instrument_id)
        quote = assessment.get("quote")
        if kind == "perp":
            position_quantity = float(row["quantity"])
            step = None
        else:
            position_quantity = float(row["quantity"]) - (0.0 if closing else float(row["reserved_quantity"]))
            step = self.catalog.get(instrument_id).get("quantity_step")
        return plan_execution(
            action="close" if closing else "reduce", source=source, market_type="perpetual" if kind == "perp" else "spot",
            side=row.get("side", "long"), quantity=position_quantity * fraction, assessment=assessment, quote=quote,
            settings=self.safety_settings(), urgency="emergency" if reason == "liquidation_emergency" else "normal",
            quantity_step=None if closing else step, recent_reduced_fraction=self._recent_reduced_fraction(f"{'perp' if kind == 'perp' else 'spot'}:{row.get('position_id') or row.get('holding_id')}", float(row["quantity"])),
            position_quantity=position_quantity, core_quantity=float((meta or {}).get("core_quantity") or 0.0),
            kill_switch_level=self.kill_switch()["level"],
        )

    def _execute_plan(self, kind: str, identifier: str, row: dict[str, Any], meta: dict[str, Any] | None, plan: dict[str, Any], *,
                      closing: bool, source: str, reason: str | None, request_id: str | None) -> tuple[dict[str, Any], bool]:
        """Execute slices with revalidation between them; the remainder defers if conditions degrade."""

        instrument_id = (meta or {}).get("instrument_id") or (self.perp_instrument_id(row["symbol"]) if kind == "perp" else row["instrument_id"])
        filled = 0.0
        realized = 0.0
        remaining_qty = float(row["quantity"])
        closed = False
        deferred: list[float] = []
        buying_to_close = row.get("side") == "short"
        slices = plan["slices"]
        # A close only liquidates "everything" when no guard clamped it; otherwise it is a sized reduce.
        full_close = closing and plan["quantity"] >= plan["requested_quantity"] - 1e-12
        self.safety.save_plan(self.experiment_id, plan, instrument_id=instrument_id, position_ref=f"{kind}:{identifier}",
                              idempotency_key=None if request_id is None else f"plan:{request_id}", status="executing")
        for index, quantity in enumerate(slices):
            if index > 0:
                check = self.assess(instrument_id, force=True)
                bid = (check.get("quote") or {}).get("best_bid")
                ask = (check.get("quote") or {}).get("best_ask")
                reference = ask if buying_to_close else bid
                if (check["state"] == "MARKET_DATA_UNTRUSTED" and plan["urgency"] != "emergency") or reference is None or not within_envelope(float(reference), plan, buying_to_close):
                    deferred = slices[index:]
                    self._safety_reject("EXECUTION_DEFERRED", {"plan_id": plan["plan_id"], "remaining_slices": len(deferred),
                                                                 "state": check["state"]}, instrument_id=instrument_id,
                                        position_ref=f"{kind}:{identifier}", source=source)
                    break
            final_slice = full_close and index == len(slices) - 1
            if kind == "perp":
                current = self.store._query("SELECT quantity FROM positions WHERE position_id=?", (identifier,))[0]["quantity"]
                slice_fraction = 1.0 if final_slice else min(1.0, quantity / float(current))
                quote = self.assess(instrument_id).get("quote") or {}
                ref_price = quote.get("best_ask" if buying_to_close else "best_bid")
                result = self.runtime.close_or_reduce(
                    identifier, fraction=slice_fraction, reference_price=ref_price, data_origin=None,
                    order_key=f"{plan['plan_id']}:{index}",
                )
                if not result.get("accepted"):
                    raise PaperTradingError(f"REDUCE_REJECTED: {result.get('reason') or 'risk engine rejected the reduce'}")
                filled += float(result.get("filled_quantity") or 0.0)
                realized += float(result.get("realized_pnl") or 0.0)
                remaining_qty = float(result.get("remaining_quantity") or 0.0)
                closed = remaining_qty <= 1e-10
            else:
                fill = self._spot_market_sell(
                    f"spot:{identifier}", identifier, fraction=1.0 if final_slice else 0.0, quantity=None if final_slice else quantity,
                    source=source, exit_reason=reason or ("user_close" if source == "USER" else "ai_close"),
                )
                filled += fill["quantity"]
                realized += fill["realized_pnl"]
                remaining_qty = fill["holding_quantity"]
                closed = fill["holding_closed"]
            if closed:
                break
        status = "complete" if not deferred else "partially_deferred"
        result = {"quantity": remaining_qty, "filled_quantity": filled, "realized_pnl": realized, "deferred_slices": len(deferred)}
        self.safety.save_plan(self.experiment_id, plan, instrument_id=instrument_id, position_ref=f"{kind}:{identifier}",
                              idempotency_key=None if request_id is None else f"plan:{request_id}", status=status, result=result)
        return result, closed

    def close_position(self, ref: str, *, source: str = "USER", confirm: bool = False, request_id: str | None = None, reason: str | None = None, proposal_id: str | None = None) -> dict[str, Any]:
        return self.reduce_position(ref, 1.0, source=source, confirm=confirm, request_id=request_id, reason=reason, proposal_id=proposal_id)

    def _spot_market_sell(self, ref: str, holding_id: str, *, fraction: float, source: str, exit_reason: str,
                          quantity: float | None = None) -> dict[str, Any]:
        rows = self.store._query("SELECT * FROM spot_holdings WHERE holding_id=?", (holding_id,))
        holding = dict(rows[0])
        instrument = self.catalog.get(holding["instrument_id"])
        quote = self.quote(holding["instrument_id"])
        if not quote.get("fresh"):
            raise PaperTradingError("STALE_DATA: no fresh spot quote; retry when market data recovers")
        settings = self.settings()
        closing = quantity is None and fraction >= 1 - 1e-9
        if closing:
            pending_sells = self.store._query(
                "SELECT order_id FROM spot_orders WHERE holding_id=? AND side='sell' AND status IN ('pending','partially_filled')",
                (holding_id,),
            )
            for order in pending_sells:
                self.cancel_order(f"spot:{order['order_id']}", source=source, reason="holding_close")
            holding = dict(self.store._query("SELECT * FROM spot_holdings WHERE holding_id=?", (holding_id,))[0])
        available = float(holding["quantity"]) - float(holding["reserved_quantity"])
        if quantity is not None:
            if quantity > available + 1e-12:
                self._safety_reject("WRONG_SIDE_BLOCK", {"requested": quantity, "available": available}, position_ref=ref, source=source)
                raise PaperTradingError("INSUFFICIENT_BASE: a Spot sell cannot exceed the available holding")
            quantity = min(quantity, available)
        else:
            quantity = available if closing else floor_to_step(available * fraction, instrument.get("quantity_step"))
        if quantity <= 0:
            raise PaperTradingError("INSUFFICIENT_BASE: no available quantity to sell")
        reference = float(quote["best_bid"])
        price = reference * (1 - float(settings["spot_slippage_bps"]) / 10_000)
        fee_rate = float(settings.get("spot_fee_rate") or instrument.get("taker_fee_rate") or 0.001)
        order_id = f"so-{uuid.uuid4().hex[:16]}"
        now = self._iso()
        with self.store.transaction() as db:
            fill = self._spot_fill_locked(
                db, order_id=order_id, instrument=instrument, side="sell", quantity=quantity, price=price,
                reference=reference, fee_rate=fee_rate, liquidity="taker", source=source, exit_reason=exit_reason,
            )
            db.execute(
                "INSERT INTO spot_orders(order_id, experiment_id, instrument_id, exchange_symbol, symbol, side, order_type, "
                "source, requested_quantity, filled_quantity, filled_quote, fees, avg_fill_price, status, holding_id, "
                "risk_json, created_at, updated_at) VALUES(?, ?, ?, ?, ?, 'sell', 'market', ?, ?, ?, ?, ?, ?, 'filled', ?, ?, ?, ?)",
                (
                    order_id, self.experiment_id, instrument["instrument_id"], instrument["exchange_symbol"],
                    instrument["symbol"], source, quantity, fill["quantity"], fill["quantity"] * price, fill["fee"],
                    price, holding_id, _dumps({"reason": exit_reason, "position_ref": ref}), now, now,
                ),
            )
        return fill

    def set_management_mode(self, ref: str, mode: str, *, source: str = "USER", confirm: bool = False, reason: str | None = None) -> dict[str, Any]:
        if source != "USER":
            raise PaperTradingError("AUTHORITY_DENIED: only the user can change management authority")
        if mode not in MANAGEMENT_MODES:
            raise PaperTradingError(f"mode must be one of {', '.join(MANAGEMENT_MODES)}")
        with self._lock:
            self.sync_meta()
            kind, _identifier, row = self._position_row(ref)
            meta = self._meta(ref)
            if meta is None:
                raise PaperTradingError("position has no management metadata")
            current = meta["management_mode"]
            if current == mode:
                return {"position_ref": ref, "management_mode": mode, "changed": False}
            if mode == "AUTO_PAPER" and current == "MANUAL_OVERRIDE" and not confirm:
                raise ConfirmationRequired(
                    "CONFIRM_RETURN_TO_AI",
                    f"return {row['symbol']} to autonomous AI PAPER management?",
                    {"instrument": row["symbol"], "from": current, "to": mode},
                )
            now = self._iso()
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE position_meta SET management_mode=?, updated_at=?, "
                    "last_user_override_at=CASE WHEN ?='MANUAL_OVERRIDE' THEN ? ELSE last_user_override_at END "
                    "WHERE position_ref=?",
                    (mode, now, mode, now, ref),
                )
                event_id = self._journal_locked(
                    db, action="MODE_CHANGE", source=source, position_ref=ref,
                    market_type="perpetual" if kind == "perp" else "spot",
                    before={"management_mode": current}, after={"management_mode": mode, "reason": reason},
                )
                labels = {"AUTO_PAPER": "AI managed", "RECOMMEND_ONLY": "Recommend only", "MANUAL_OVERRIDE": "Manual override", "PAUSED": "AI paused"}
                self._activity_locked(
                    db, source=source, category="MANAGEMENT", severity="INFO",
                    title=f"Control: {labels[current]} → {labels[mode]}", summary=reason or "",
                    symbol=row["symbol"], instrument_id=meta.get("instrument_id"),
                    market_type="perpetual" if kind == "perp" else "spot", position_ref=ref,
                    payload_ref=f"management:{event_id}",
                )
            return {"position_ref": ref, "management_mode": mode, "previous_mode": current, "changed": True, "audit_event_id": event_id}

    # ---------------------------------------------------------- spot monitor
    def monitor_spot(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Fill pending spot limits (partial fills bounded by bar volume) and run holding plans."""

        now = (now or self._now()).astimezone(timezone.utc)
        orders = [dict(r) for r in self.store._query(
            "SELECT * FROM spot_orders WHERE experiment_id=? AND status IN ('pending','partially_filled') ORDER BY created_at",
            (self.experiment_id,),
        )]
        holdings = self.spot_holdings()
        instruments = sorted({o["instrument_id"] for o in orders} | {h["instrument_id"] for h in holdings})
        events: list[dict[str, Any]] = []
        settings = self.settings()
        for instrument_id in instruments:
            symbol_orders = [o for o in orders if o["instrument_id"] == instrument_id]
            symbol_holdings = [h for h in holdings if h["instrument_id"] == instrument_id]
            cursors = [parse_utc(o["last_checked_bar"] or o["created_at"], "spot.cursor") for o in symbol_orders]
            cursors += [parse_utc(h["mark_at"] or h["opened_at"], "spot.mark_at") for h in symbol_holdings]
            if not cursors:
                continue
            start = min(cursors)
            try:
                instrument = self.catalog.get(instrument_id)
                bars = self.catalog.spot.fetch_monitor_bars(instrument["exchange_symbol"], start, now)
            except (PaperTradingError, MarketDataError):
                continue
            with self._lock:
                for bar in bars:
                    close_time = bar["close_time"]
                    for order in symbol_orders:
                        current = dict(self.store._query("SELECT * FROM spot_orders WHERE order_id=?", (order["order_id"],))[0])
                        if current["status"] not in {"pending", "partially_filled"}:
                            continue
                        if current["last_checked_bar"] and current["last_checked_bar"] >= close_time:
                            continue
                        event = self._fill_spot_limit(current, instrument, bar, settings)
                        if event:
                            events.append(event)
                    for holding in symbol_holdings:
                        event = self._apply_spot_plan(holding["holding_id"], instrument, bar, settings)
                        if event:
                            events.append(event)
        return events

    def _fill_spot_limit(self, order: dict[str, Any], instrument: dict[str, Any], bar: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any] | None:
        close_time = bar["close_time"]
        limit = float(order["limit_price"])
        side = order["side"]
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        touched = l <= limit if side == "buy" else h >= limit
        with self.store.transaction() as db:
            if not touched:
                db.execute("UPDATE spot_orders SET last_checked_bar=? WHERE order_id=?", (close_time, order["order_id"]))
                return None
            price = (min(o, limit) if o <= limit else limit) if side == "buy" else (max(o, limit) if o >= limit else limit)
            remaining = float(order["requested_quantity"]) - float(order["filled_quantity"])
            capacity = float(bar["volume"]) * float(settings["spot_limit_participation"])
            quantity = floor_to_step(min(remaining, capacity), instrument.get("quantity_step"))
            min_qty = float(instrument.get("min_quantity") or 0.0)
            if remaining - quantity < max(min_qty, EPSILON) or quantity <= 0 and remaining <= max(min_qty, EPSILON):
                quantity = remaining
            if quantity <= 0:
                db.execute("UPDATE spot_orders SET last_checked_bar=? WHERE order_id=?", (close_time, order["order_id"]))
                return None
            fee_rate = float(settings.get("spot_fee_rate") or instrument.get("maker_fee_rate") or 0.001)
            release_quote = 0.0
            if side == "buy":
                share = quantity / float(order["requested_quantity"])
                release_quote = min(float(order["reserved_quote"]), float(order["requested_quantity"]) * limit * (1 + fee_rate) * share)
                db.execute(
                    "UPDATE spot_wallets SET reserved_quote=MAX(0, reserved_quote-?) WHERE experiment_id=? AND quote='USDT'",
                    (release_quote, self.experiment_id),
                )
            holding_before = self._holding_for_locked(db, order["instrument_id"])
            fill = self._spot_fill_locked(
                db, order_id=order["order_id"], instrument=instrument, side=side, quantity=quantity, price=price,
                reference=limit, fee_rate=fee_rate, liquidity="maker", source=order["source"],
                exit_reason="limit_sell", release_reserved_quantity=quantity if side == "sell" else 0.0,
            )
            filled = float(order["filled_quantity"]) + fill["quantity"]
            filled_quote = float(order["filled_quote"]) + fill["quantity"] * price
            status = "filled" if filled >= float(order["requested_quantity"]) - EPSILON else "partially_filled"
            db.execute(
                "UPDATE spot_orders SET filled_quantity=?, filled_quote=?, fees=fees+?, avg_fill_price=?, status=?, "
                "holding_id=?, reserved_quote=MAX(0, reserved_quote-?), reserved_quantity=MAX(0, reserved_quantity-?), "
                "last_checked_bar=?, updated_at=? WHERE order_id=?",
                (
                    filled, filled_quote, fill["fee"], filled_quote / filled, status, fill["holding_id"], release_quote,
                    quantity if side == "sell" else 0.0, close_time, close_time, order["order_id"],
                ),
            )
            if side == "buy" and holding_before is None:
                request = (_loads(order["risk_json"], {}) or {}).get("request") or {}
                stop = request.get("stop_price")
                targets = normalize_targets(request.get("targets"), request.get("target_fractions"), side="long", quantity=fill["holding_quantity"]) if request.get("targets") else []
                self._insert_meta_locked(
                    db, ref=f"spot:{fill['holding_id']}", market_type="spot", instrument_id=order["instrument_id"],
                    owner=order["source"],
                    mode=settings["default_user_management_mode" if order["source"] == "USER" else "default_ai_management_mode"],
                    stop=stop, targets=targets,
                    initial_risk=None if stop is None else max(0.0, (price - float(stop)) * fill["quantity"]),
                )
            if status == "partially_filled":
                self._activity_locked(
                    db, source="SYSTEM", category="FILL", severity="INFO", title="Spot limit partially filled",
                    summary=f"{filled:.8g} / {float(order['requested_quantity']):.8g} {instrument['base']} @ {price:.8g}",
                    symbol=instrument["symbol"], instrument_id=instrument["instrument_id"], market_type="spot",
                    position_ref=f"spot:{fill['holding_id']}", payload_ref=f"spot:{order['order_id']}",
                )
        return {"event": "spot_limit_fill", "order_ref": f"spot:{order['order_id']}", "status": status, "quantity": fill["quantity"]}

    def _apply_spot_plan(self, holding_id: str, instrument: dict[str, Any], bar: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any] | None:
        rows = self.store._query("SELECT * FROM spot_holdings WHERE holding_id=?", (holding_id,))
        if not rows or rows[0]["status"] != "open":
            return None
        holding = dict(rows[0])
        close_time = bar["close_time"]
        if holding["mark_at"] and holding["mark_at"] >= close_time:
            return None
        ref = f"spot:{holding_id}"
        meta = self._meta(ref) or {}
        o, h, l, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
        stop = meta.get("stop_price")
        targets = meta.get("targets") or []
        slip = float(settings["spot_slippage_bps"]) / 10_000
        fee_rate = float(settings.get("spot_fee_rate") or instrument.get("taker_fee_rate") or 0.001)
        available = float(holding["quantity"]) - float(holding["reserved_quantity"])
        core = float(meta.get("core_quantity") or 0.0)
        confirm_needed = int(settings["safety"]["spot_stop_confirm_closes"])
        breaches = int(meta.get("stop_breaches") or 0)
        result = None
        with self.store.transaction() as db:
            # Spot has no liquidation risk, so a stop needs N consecutive closes below it: a wick
            # or bad print alone never sells. Core quantity is never sold by a plan stop.
            if stop is not None:
                breaches = breaches + 1 if c <= float(stop) else 0
                db.execute("UPDATE position_meta SET stop_breaches=? WHERE position_ref=?", (breaches, ref))
            sellable = max(0.0, available - core)
            if stop is not None and breaches >= confirm_needed and sellable > 0:
                reference = c
                fill = self._spot_fill_locked(
                    db, order_id=f"so-plan-{uuid.uuid4().hex[:12]}", instrument=instrument, side="sell",
                    quantity=sellable, price=reference * (1 - slip), reference=reference, fee_rate=fee_rate,
                    liquidity="taker", source="SYSTEM", exit_reason="plan_stop",
                )
                db.execute("UPDATE position_meta SET stop_breaches=0 WHERE position_ref=?", (ref,))
                self._journal_locked(db, action="PLAN_STOP", source="SYSTEM", position_ref=ref, market_type="spot",
                                     before={"quantity": float(holding["quantity"]), "core_quantity": core},
                                     after={**fill, "confirmed_closes": breaches}, snapshot={"bar": bar})
                result = {"event": "plan_stop", "position_ref": ref, "core_protected": core > 0}
            elif stop is not None and breaches >= confirm_needed and core > 0:
                result = {"event": "core_protected", "position_ref": ref}
            else:
                changed = False
                for target in targets:
                    if target.get("hit") or available <= 0:
                        continue
                    if h >= float(target["price"]):
                        quantity = available if target.get("final") else min(available, float(target.get("quantity") or available))
                        quantity = available if available - quantity < float(instrument.get("min_quantity") or 0) else quantity
                        fill = self._spot_fill_locked(
                            db, order_id=f"so-plan-{uuid.uuid4().hex[:12]}", instrument=instrument, side="sell",
                            quantity=quantity, price=float(target["price"]), reference=float(target["price"]),
                            fee_rate=fee_rate, liquidity="maker", source="SYSTEM", exit_reason="plan_target",
                        )
                        target["hit"] = True
                        changed = True
                        available -= fill["quantity"]
                        self._journal_locked(db, action="PLAN_TARGET", source="SYSTEM", position_ref=ref, market_type="spot",
                                             before={"target": target["price"]}, after=fill, snapshot={"bar": bar})
                        result = {"event": "plan_target", "position_ref": ref}
                if changed:
                    db.execute("UPDATE position_meta SET targets_json=? WHERE position_ref=?", (_dumps(targets), ref))
            db.execute(
                "UPDATE spot_holdings SET mark_price=CASE WHEN status='open' THEN ? ELSE mark_price END, mark_at=? WHERE holding_id=?",
                (c, close_time, holding_id),
            )
        return result

    # ------------------------------------------------------------- re-plan
    def _features_for(self, kind: str, row: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        from .paper_runtime import compute_features

        config = self.experiment()["config"]
        now = self._now()
        if kind == "perp":
            snapshot = self.runtime._market(config).fetch_snapshot(row["symbol"], now).validate()
        else:
            snapshot = self.catalog.spot.fetch_snapshot(row["exchange_symbol"], now)
        features = compute_features(
            snapshot,
            minimum_signal_strength=config["minimum_signal_strength"],
            signal_gate_enabled=config["signal_gate_enabled"],
        )
        return snapshot, features

    @staticmethod
    def _thesis(side: str, features: dict[str, Any], jev_vector: dict[str, Any] | None = None) -> tuple[str, list[str]]:
        regime = features.get("quant_regime")
        codes: list[str] = []
        status = "intact"
        aligned = (regime == "bull" and side == "long") or (regime == "bear" and side == "short")
        opposite = (regime == "bear" and side == "long") or (regime == "bull" and side == "short")
        if opposite:
            status = "broken"
            codes.append("REGIME_SHIFT")
        elif not aligned:
            status = "weakening"
            codes.append("MOMENTUM_WEAKENED")
        fast_against = (features.get("ema12", 0) < features.get("ema26", 0)) if side == "long" else (features.get("ema12", 0) > features.get("ema26", 0))
        if fast_against and status == "intact":
            status = "weakening"
            codes.append("MOMENTUM_WEAKENED")
        if jev_vector:
            answers = jev_vector.get("answers", {})
            jev_regime = (answers.get("market_regime") or {}).get("value")
            conflict = (answers.get("signal_conflict") or {}).get("value")
            if (jev_regime == "bear" and side == "long") or (jev_regime == "bull" and side == "short"):
                status = "broken"
                codes.append("REGIME_SHIFT")
            elif isinstance(conflict, (int, float)) and conflict >= 0.65 and status == "intact":
                status = "weakening"
                codes.append("THESIS_WEAKENED")
        if status == "intact":
            codes.append("THESIS_INTACT")
        return status, list(dict.fromkeys(codes))

    def _baseline(self, view: dict[str, Any], features: dict[str, Any], thesis: str, thesis_codes: list[str], intent: str | None, price: float) -> dict[str, Any]:
        side = view["side"]
        sign = 1 if side == "long" else -1
        atr = max(float(features.get("atr") or 0.0), price * 0.002)
        stop = view.get("stop_price")
        entry = float(view["entry_price"])
        open_targets = [t["price"] for t in view.get("targets") or [] if not t.get("hit")]
        settings = self.settings()
        in_profit = sign * (price - entry) > 0
        codes = list(thesis_codes)

        def tighter(candidate: float) -> float | None:
            if (side == "long" and candidate >= price) or (side == "short" and candidate <= price):
                return None
            if stop is None:
                return candidate
            return candidate if sign * (candidate - stop) > 0 else None

        action, new_stop, fraction = "hold", None, None
        if intent == "tighten_risk":
            new_stop = tighter(price - sign * max(1.0 * atr, price * 0.004))
            codes.append("TIGHTEN_RISK")
            action = "adjust" if new_stop is not None else "hold"
        elif intent == "protect_profit":
            if in_profit:
                breakeven = entry * (1 + sign * 0.001)
                new_stop = tighter(breakeven) or tighter(price - sign * max(0.75 * atr, price * 0.003))
                fraction = 0.25
                action = "reduce"
                codes.append("PROTECT_PROFIT")
            else:
                new_stop = tighter(price - sign * max(1.0 * atr, price * 0.004))
                action = "adjust" if new_stop is not None else "hold"
                codes.append("TIGHTEN_RISK")
        elif intent == "give_more_room":
            base_stop = stop if stop is not None else price - sign * 1.5 * atr
            candidate = base_stop - sign * 0.5 * atr
            liq = view.get("liquidation_price")
            if abs(price - candidate) / price <= 0.25 and not (
                isinstance(liq, (int, float)) and ((side == "long" and candidate <= liq) or (side == "short" and candidate >= liq))
            ):
                new_stop = candidate
                action = "adjust"
            codes.append("GIVE_ROOM")
        elif intent == "reduce_exposure":
            action, fraction = "reduce", 0.5
            codes.append("REDUCE_EXPOSURE")
        elif intent == "exit_if_thesis_weakened":
            if thesis in {"weakening", "broken"}:
                action = "close"
                codes.append("THESIS_BROKEN" if thesis == "broken" else "THESIS_WEAKENED")
            else:
                codes.append("NO_CHANGE_NEEDED")
        else:
            r_multiple = view.get("r_multiple")
            near_stop = stop is not None and abs(price - stop) / price <= settings["review"]["stop_proximity_pct"]
            if thesis == "broken":
                action = "close"
                codes.append("THESIS_BROKEN")
            elif isinstance(r_multiple, (int, float)) and r_multiple >= settings["brain"]["protect_profit_r"] and stop is not None and sign * (stop - entry) < 0:
                new_stop = tighter(entry * (1 + sign * 0.001))
                action = "adjust" if new_stop is not None else "hold"
                codes.append("PROTECT_PROFIT")
            elif thesis == "weakening" and near_stop:
                action, fraction = "reduce", 0.5
                codes.extend(["NEAR_STOP", "REDUCE_EXPOSURE"])
            elif thesis == "weakening":
                new_stop = tighter(price - sign * max(1.0 * atr, price * 0.004))
                action = "adjust" if new_stop is not None else "hold"
                codes.append("TIGHTEN_RISK")
            else:
                codes.append("NO_CHANGE_NEEDED")
        if action == "reduce" and new_stop is None:
            pass
        if intent:
            codes.append("USER_REQUEST")
        return {
            "action": action,
            "stop_price": new_stop,
            "target_prices": open_targets if action in {"adjust", "reduce"} and open_targets else [],
            "reduce_fraction": fraction if action == "reduce" else None,
            "thesis_status": thesis,
            "reason_codes": [code for code in dict.fromkeys(codes)][:5],
        }

    def _state_hash(self, kind: str, row: dict[str, Any], meta: dict[str, Any] | None) -> str:
        return digest({
            "quantity": round(float(row["quantity"]), 12),
            "stop": (float(row["stop_price"]) if kind == "perp" else (meta or {}).get("stop_price")),
            "targets": (meta or {}).get("targets"),
            "mode": (meta or {}).get("management_mode"),
        })

    def request_replan(
        self,
        ref: str,
        *,
        intent: str | None = None,
        use_ai: bool = True,
        origin: str = "user",
        note: str | None = None,
    ) -> dict[str, Any]:
        if intent is not None and intent not in QUICK_INTENTS:
            raise PaperTradingError(f"intent must be one of {', '.join(QUICK_INTENTS)}")
        if origin not in {"user", "autonomous"}:
            raise PaperTradingError("origin is invalid")
        self.sync_meta()
        kind, identifier, row = self._position_row(ref)
        meta = self._meta(ref) or {}
        config = self.experiment()["config"]
        settings = self.settings()
        perp_wallet = self.store.wallet_summary(self.experiment_id, "primary")
        spot_wallet = self.spot_wallet()
        equity = float(perp_wallet["equity"]) + spot_wallet["equity_usdt"]
        price, quote = self._current_price(kind, row)
        view = (
            self._perp_view(row, meta, price, equity) if kind == "perp"
            else self._spot_view(row, meta, price, spot_wallet["equity_usdt"], equity)
        )
        proposal_id = f"rp-{uuid.uuid4().hex[:16]}"
        now = self._iso()
        base = {
            "schema_version": REPLAN_SCHEMA_VERSION,
            "proposal_id": proposal_id,
            "position_ref": ref,
            "market_type": view["market_type"],
            "symbol": view["symbol"],
            "display_symbol": view["display_symbol"],
            "origin": origin,
            "intent": intent,
            "note": (note or "")[:200] or None,
            "created_at": now,
            "management_mode": meta.get("management_mode"),
            "position_state_hash": self._state_hash(kind, row, meta),
        }
        if quote is None or not quote.get("fresh"):
            return self._store_proposal({**base, "status": "blocked", "code": "STALE_DATA",
                                         "reason": "no fresh market observation; re-plan fails closed"})
        try:
            snapshot, features = self._features_for(kind, row)
        except (PaperTradingError, MarketDataError) as exc:
            return self._store_proposal({**base, "status": "blocked", "code": "MARKET_DATA_UNAVAILABLE", "reason": str(exc)[:200]})
        ai_trace: dict[str, Any] = {"jev": "not_requested", "luna": "not_requested", "budget_block": None, "provider_error": None}
        jev_vector = None
        if use_ai and settings["review"]["use_jev"] and config["jev_enabled"]:
            try:
                provider, provider_config = self.runtime._jev(config)
                portfolio_ctx = self.runtime._portfolio_context(self.experiment_id, snapshot, features)
                jev_vector, blocked = self.runtime._paid_call(
                    experiment_id=self.experiment_id, cycle_id=None, symbol=row["symbol"], arm="replan:jev",
                    provider_config=provider_config, adapter=provider, call_type="jev_replan",
                    payload_bytes=2048, budget=config["ai_budget"],
                    invoke=lambda: provider.evaluate(snapshot, features, portfolio_ctx),
                )
                if blocked is not None:
                    ai_trace["jev"] = "budget_blocked"
                    ai_trace["budget_block"] = blocked.get("code")
                    jev_vector = None
                else:
                    ai_trace["jev"] = "completed"
            except (AIProviderError, PaperTradingError) as exc:
                ai_trace["jev"] = "failed"
                ai_trace["provider_error"] = str(exc)[:160]
        thesis, thesis_codes = self._thesis(view["side"], features, jev_vector)
        baseline = self._baseline(view, features, thesis, thesis_codes, intent, price)
        proposal = dict(baseline)
        proposal_source = "deterministic"
        conflict = ((jev_vector or {}).get("answers", {}).get("signal_conflict") or {}).get("value")
        escalate = use_ai and settings["review"]["use_luna"] and (config["gpt_escalation_enabled"] or config["force_escalation"]) and (
            intent == "reassess" or thesis != "intact" or (isinstance(conflict, (int, float)) and conflict >= 0.65)
            or (origin == "user" and intent is None)
        ) and ai_trace["budget_block"] is None
        context = {
            "position": {**view, "mark_price": price, "targets": [t["price"] for t in view["targets"] if not t.get("hit")]},
            "features": features,
            "jev_vector": jev_vector,
            "portfolio": {"equity_usdt": equity, "open_positions": len(self._open_refs())},
            "intent": intent,
            "baseline": baseline,
        }
        if escalate:
            try:
                provider, provider_config = self.runtime._gpt(config)
                if not hasattr(provider, "generate_replan"):
                    raise AIProviderError("configured GPT adapter does not support structured re-plans")
                result, blocked = self.runtime._paid_call(
                    experiment_id=self.experiment_id, cycle_id=None, symbol=row["symbol"], arm="replan:luna",
                    provider_config=provider_config, adapter=provider, call_type="gpt_replan",
                    payload_bytes=4096, budget=config["ai_budget"], invoke=lambda: provider.generate_replan(context),
                )
                if blocked is not None:
                    ai_trace["luna"] = "budget_blocked"
                    ai_trace["budget_block"] = blocked.get("code")
                else:
                    proposal = dict(result["proposal"])
                    proposal_source = "fixture_gpt" if provider_config["kind"].startswith("fixture_") else "luna"
                    ai_trace["luna"] = "completed"
            except (AIProviderError, PaperTradingError) as exc:
                ai_trace["luna"] = "failed"
                ai_trace["provider_error"] = str(exc)[:160]
        elif ai_trace["jev"] == "completed":
            proposal_source = "jev+deterministic"
        ai_trace["jev_provider_kind"] = (self.store.provider(config["jev_provider_id"]) or {}).get("kind")
        if ai_trace["budget_block"]:
            proposal["reason_codes"] = list(dict.fromkeys([*proposal["reason_codes"], "AI_BUDGET_BLOCK"]))[:5]
        diff = self._proposal_diff(kind, view, proposal, price, equity)
        payload = {
            **base,
            "status": "proposed" if diff["changes"] else "no_change",
            "source": proposal_source,
            "proposal": proposal,
            "current": diff["current"],
            "proposed": diff["proposed"],
            "risk_before_usdt": diff["current"]["risk_usdt"],
            "risk_after_usdt": diff["proposed"]["risk_usdt"],
            "risk_increased": diff["proposed"]["risk_usdt"] > diff["current"]["risk_usdt"] + 1e-9,
            "portfolio_exposure_before": diff["current"]["exposure_pct"],
            "portfolio_exposure_after": diff["proposed"]["exposure_pct"],
            "economic_impact": diff["economic"],
            "changes": diff["changes"],
            "headline": diff["headline"],
            "reason_codes": proposal["reason_codes"],
            "thesis_status": proposal["thesis_status"],
            "evidence": {
                "data_cutoff": features.get("data_cutoff"),
                "snapshot_hash": snapshot.snapshot_hash,
                "price": price,
                "quote_observed_at": quote.get("observed_at"),
                "atr": features.get("atr"),
                "rsi14": features.get("rsi14"),
                "quant_regime": features.get("quant_regime"),
                "jev_regime": ((jev_vector or {}).get("answers", {}).get("market_regime") or {}).get("value"),
                "jev_conflict": conflict,
                "summary": proposal.get("summary") or "",
            },
            "ai": ai_trace,
            "baseline": baseline,
            "auto_applied": False,
        }
        stored = self._store_proposal(payload)
        with self.store.transaction() as db:
            db.execute(
                "UPDATE position_meta SET thesis_status=?, updated_at=? WHERE position_ref=?",
                (proposal["thesis_status"], now, ref),
            )
        if origin == "autonomous" and payload["status"] == "proposed" and self._auto_apply_allowed(payload, meta):
            try:
                return self.apply_replan(proposal_id, source="AI")
            except PaperTradingError as exc:
                with self.store.transaction() as db:
                    self._journal_locked(db, action="REPLAN_AUTO_APPLY_REFUSED", source="SYSTEM", position_ref=ref,
                                         before=None, after={"proposal_id": proposal_id, "reason": str(exc)[:200]})
        return stored

    def _auto_apply_allowed(self, payload: dict[str, Any], meta: dict[str, Any]) -> bool:
        settings = self.settings()
        if meta.get("management_mode") != "AUTO_PAPER":
            return False
        if settings["automation"]["ai_management_paused"] or settings["automation"]["emergency_stop"]:
            return False
        if payload["risk_increased"]:
            return False
        proposal = payload["proposal"]
        if proposal["action"] == "reduce" and (proposal["reduce_fraction"] or 0) > settings["review"]["auto_apply_max_reduce_fraction"]:
            return False
        if proposal["action"] == "close" and proposal["thesis_status"] != "broken":
            return False
        return True

    def _proposal_diff(self, kind: str, view: dict[str, Any], proposal: dict[str, Any], price: float, equity: float) -> dict[str, Any]:
        side = view["side"]
        sign = 1 if side == "long" else -1
        quantity = float(view["quantity"])
        entry = float(view["entry_price"])
        stop = view.get("stop_price")
        open_targets = [t["price"] for t in view.get("targets") or [] if not t.get("hit")]
        action = proposal["action"]
        remaining = 0.0 if action == "close" else (1 - (proposal["reduce_fraction"] or 0.0)) if action == "reduce" else 1.0
        new_stop = proposal["stop_price"] if proposal["stop_price"] is not None and action != "close" else (None if action == "close" else stop)
        new_targets = proposal["target_prices"] or (open_targets if action != "close" else [])

        def risk(stop_value: float | None, qty: float) -> float:
            if qty <= 0:
                return 0.0
            if stop_value is None:
                return qty * price
            return max(0.0, sign * (price - stop_value) * qty)

        def locked(stop_value: float | None, qty: float) -> float | None:
            if stop_value is None or qty <= 0:
                return None
            return sign * (stop_value - entry) * qty

        gross = sum(p["notional_usdt"] for p in self.list_positions(with_live=False) if p["status"] == "open")
        notional = quantity * price
        exposure_before = gross / equity if equity > 0 else None
        exposure_after = (gross - notional * (1 - remaining)) / equity if equity > 0 else None
        changes = []
        if action == "close":
            changes.append("close")
        if action == "reduce":
            changes.append(f"reduce {proposal['reduce_fraction'] * 100:.0f}%")
        if new_stop is not None and stop is not None and abs(new_stop - stop) > 1e-12 and action != "close":
            changes.append("stop")
        if new_stop is not None and stop is None and action != "close":
            changes.append("stop")
        if action != "close" and proposal["target_prices"] and [round(t, 10) for t in proposal["target_prices"]] != [round(t, 10) for t in open_targets]:
            changes.append("targets")
        headline = {
            "close": "Close position",
            "reduce": f"Reduce {(proposal['reduce_fraction'] or 0) * 100:.0f}%",
            "adjust": "Tighten stop" if (new_stop is not None and stop is not None and sign * (new_stop - stop) > 0) or (stop is None and new_stop is not None)
            else "Adjust protection",
            "hold": "Hold · no change",
        }[action]
        if "PROTECT_PROFIT" in proposal["reason_codes"] and action in {"adjust", "reduce"}:
            headline = "Protect profit" + (f" · reduce {(proposal['reduce_fraction'] or 0) * 100:.0f}%" if action == "reduce" else "")
        return {
            "current": {
                "stop_price": stop,
                "targets": open_targets,
                "remaining_pct": 1.0,
                "quantity": quantity,
                "risk_usdt": risk(stop, quantity),
                "exposure_pct": exposure_before,
                "management_mode": view.get("management_mode"),
                "locked_pnl_at_stop_usdt": locked(stop, quantity),
            },
            "proposed": {
                "stop_price": new_stop,
                "targets": new_targets,
                "remaining_pct": remaining,
                "quantity": quantity * remaining,
                "risk_usdt": risk(new_stop, quantity * remaining),
                "exposure_pct": exposure_after,
                "management_mode": view.get("management_mode"),
                "locked_pnl_at_stop_usdt": locked(new_stop, quantity * remaining),
            },
            "economic": {
                "realized_now_usdt_estimate": sign * (price - entry) * quantity * (1 - remaining) if remaining < 1 else 0.0,
                "estimable": True,
            },
            "changes": changes,
            "headline": headline,
        }

    def _store_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.store.transaction() as db:
            if payload["status"] == "proposed":
                db.execute(
                    "UPDATE replan_proposals SET status='superseded', decided_at=?, decided_by='SYSTEM' "
                    "WHERE experiment_id=? AND position_ref=? AND status='proposed'",
                    (payload["created_at"], self.experiment_id, payload["position_ref"]),
                )
            db.execute(
                "INSERT INTO replan_proposals(proposal_id, experiment_id, position_ref, market_type, status, origin, intent, "
                "payload_json, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payload["proposal_id"], self.experiment_id, payload["position_ref"], payload["market_type"],
                    payload["status"], payload["origin"], payload.get("intent"), _dumps(payload), payload["created_at"],
                ),
            )
            self._journal_locked(
                db, action="REPLAN_PROPOSED" if payload["status"] != "blocked" else "REPLAN_BLOCKED",
                source="AI" if payload.get("source", "deterministic") != "deterministic" else "SYSTEM",
                position_ref=payload["position_ref"], market_type=payload["market_type"], before=None,
                after={k: payload.get(k) for k in ("proposal_id", "status", "headline", "reason_codes", "code")},
                risk_before=payload.get("risk_before_usdt"), risk_after=payload.get("risk_after_usdt"),
            )
            if payload["status"] == "proposed" or payload["origin"] == "user":
                self._activity_locked(
                    db, source="AI" if payload.get("source") in {"luna", "fixture_gpt", "jev+deterministic"} else "SYSTEM",
                    category="DECISION", severity="ACTION" if payload["status"] == "proposed" else "INFO",
                    title=f"Re-plan {payload['status'].replace('_', ' ')}: {payload.get('headline') or payload.get('code')}",
                    summary=", ".join(payload.get("reason_codes") or [payload.get("reason") or ""])[:300],
                    symbol=payload["symbol"], market_type=payload["market_type"], position_ref=payload["position_ref"],
                    payload_ref=f"replan:{payload['proposal_id']}",
                )
        return payload

    def list_replans(self, *, status: str | None = None, position_ref: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses = ["experiment_id=?"]
        params: list[Any] = [self.experiment_id]
        if status:
            clauses.append("status=?")
            params.append(status)
        if position_ref:
            clauses.append("position_ref=?")
            params.append(position_ref)
        rows = self.store._query(
            f"SELECT * FROM replan_proposals WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        return [
            {**_loads(row["payload_json"], {}), "status": row["status"], "decided_at": row["decided_at"], "decided_by": row["decided_by"]}
            for row in rows
        ]

    def _proposal(self, proposal_id: str) -> dict[str, Any]:
        rows = self.store._query(
            "SELECT * FROM replan_proposals WHERE proposal_id=? AND experiment_id=?", (proposal_id, self.experiment_id)
        )
        if not rows:
            raise PaperTradingError("re-plan proposal was not found")
        return {**_loads(rows[0]["payload_json"], {}), "status": rows[0]["status"]}

    def apply_replan(
        self,
        proposal_id: str,
        *,
        edits: dict[str, Any] | None = None,
        confirm: bool = False,
        source: str = "USER",
    ) -> dict[str, Any]:
        with self._lock:
            payload = self._proposal(proposal_id)
            if payload["status"] != "proposed":
                raise PaperTradingError(f"PROPOSAL_NOT_PENDING: proposal is {payload['status']}")
            created = parse_utc(payload["created_at"], "proposal.created_at")
            if self._now() - created > timedelta(minutes=PROPOSAL_TTL_MINUTES):
                self._decide(proposal_id, "expired", "SYSTEM", {"reason": "ttl"})
                raise PaperTradingError("STALE_PROPOSAL: the proposal expired; request a new re-plan")
            ref = payload["position_ref"]
            kind, _identifier, row = self._position_row(ref)
            meta = self._meta(ref)
            if self._state_hash(kind, row, meta) != payload["position_state_hash"]:
                self._decide(proposal_id, "superseded", "SYSTEM", {"reason": "position changed"})
                raise PaperTradingError("STALE_PROPOSAL: the position changed after this proposal; request a new re-plan")
            self._authorize(ref, meta, source)
            proposal = dict(payload["proposal"])
            edited = False
            if edits:
                if not isinstance(edits, dict) or set(edits) - {"action", "stop_price", "target_prices", "reduce_fraction"}:
                    raise PaperTradingError("edits accept action, stop_price, target_prices, reduce_fraction")
                price, _quote = self._current_price(kind, row)
                merged = {**proposal, **edits}
                content = {k: merged.get(k) for k in ("action", "stop_price", "target_prices", "reduce_fraction", "thesis_status", "reason_codes")}
                content["target_prices"] = content["target_prices"] or []
                content["summary"] = "user-edited proposal"
                proposal = parse_replan(content, {"position": {"side": row.get("side", "long"), "mark_price": price}})
                proposal["reason_codes"] = list(dict.fromkeys([*proposal["reason_codes"], "USER_REQUEST"]))[:5]
                edited = True
            action = proposal["action"]
            applied: list[dict[str, Any]] = []
            if source == "USER" and not confirm and (
                action == "close" or (action == "reduce" and (proposal["reduce_fraction"] or 0) > CONFIRM_REDUCE_ABOVE)
            ):
                raise ConfirmationRequired(
                    "CONFIRM_REPLAN", f"apply '{payload['headline']}' to PAPER {payload['symbol']}?",
                    {"instrument": payload["symbol"], "action": action, "reduce_fraction": proposal["reduce_fraction"]},
                )
            if action == "close":
                applied.append({"close": self.close_position(ref, source=source, confirm=True, reason="replan_close", proposal_id=proposal_id)})
            else:
                if proposal["stop_price"] is not None or proposal["target_prices"]:
                    applied.append({"protection": self.update_protection(
                        ref,
                        stop_price=proposal["stop_price"] if proposal["stop_price"] is not None else "__unchanged__",
                        targets=proposal["target_prices"] or None,
                        source=source,
                        confirm_risk_increase=confirm,
                        proposal_id=proposal_id,
                    )})
                if action == "reduce" and proposal["reduce_fraction"]:
                    applied.append({"reduce": self.reduce_position(
                        ref, proposal["reduce_fraction"], source=source, confirm=True, reason="replan_reduce", proposal_id=proposal_id,
                    )})
            status = "applied"
            self._decide(proposal_id, status, source, {"edited": edited, "applied": applied, "final": proposal})
            with self.store.transaction() as db:
                self._activity_locked(
                    db, source=source, category="MANAGEMENT", severity="INFO",
                    title=f"Re-plan {'auto-' if source == 'AI' else ''}applied: {payload['headline']}" + (" (edited)" if edited else ""),
                    summary=", ".join(proposal["reason_codes"])[:300], symbol=payload["symbol"],
                    market_type=payload["market_type"], position_ref=ref, payload_ref=f"replan:{proposal_id}",
                )
                db.execute(
                    "UPDATE position_meta SET thesis_status=? WHERE position_ref=?", (proposal["thesis_status"], ref)
                )
            result = self._proposal(proposal_id)
            result["auto_applied"] = source == "AI"
            result["applied_changes"] = applied
            return result

    def reject_replan(self, proposal_id: str, *, source: str = "USER", reason: str | None = None) -> dict[str, Any]:
        with self._lock:
            payload = self._proposal(proposal_id)
            if payload["status"] != "proposed":
                raise PaperTradingError(f"PROPOSAL_NOT_PENDING: proposal is {payload['status']}")
            self._decide(proposal_id, "rejected", source, {"reason": reason})
            with self.store.transaction() as db:
                self._activity_locked(
                    db, source=source, category="MANAGEMENT", severity="INFO",
                    title=f"Re-plan rejected: {payload['headline']}", summary=(reason or "")[:200],
                    symbol=payload["symbol"], market_type=payload["market_type"], position_ref=payload["position_ref"],
                    payload_ref=f"replan:{proposal_id}",
                )
            return self._proposal(proposal_id)

    def _decide(self, proposal_id: str, status: str, source: str, details: dict[str, Any]) -> None:
        with self.store.transaction() as db:
            row = db.execute("SELECT payload_json, position_ref, market_type FROM replan_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            payload = _loads(row["payload_json"], {})
            payload["decision"] = {"status": status, "by": source, **details}
            db.execute(
                "UPDATE replan_proposals SET status=?, decided_at=?, decided_by=?, payload_json=? WHERE proposal_id=?",
                (status, self._iso(), source, _dumps(payload), proposal_id),
            )
            self._journal_locked(
                db, action=f"REPLAN_{status.upper()}", source=source, position_ref=row["position_ref"],
                market_type=row["market_type"], before={"status": "proposed"}, after={"status": status, **{k: v for k, v in details.items() if k != "applied"}},
            )

    # ------------------------------------------------- autonomous review queue
    def review_positions(self, *, now: datetime | None = None, force: bool = False) -> list[dict[str, Any]]:
        """Deterministic trigger → Jev → optional Luna → proposal/auto-apply. Never per tick."""

        settings = self.settings()
        experiment = self.experiment()
        if not settings["review"]["enabled"] or settings["automation"]["ai_management_paused"]:
            return []
        if KILL_RANK[self.kill_switch()["level"]] >= KILL_RANK["AI_MANAGEMENT_PAUSED"]:
            return []
        if experiment["status"] != "running" and not force:
            return []
        now = (now or self._now()).astimezone(timezone.utc)
        self.sync_meta()
        positions = [p for p in self.list_positions() if p["status"] == "open"]
        gross = sum(p["notional_usdt"] for p in positions)
        results = []
        review = settings["review"]
        for position in positions:
            mode = position["management_mode"]
            if mode == "PAUSED":
                continue
            ref = position["position_ref"]
            meta = self._meta(ref) or {}
            last = meta.get("last_ai_review_at")
            if last and now - parse_utc(last, "last_ai_review_at") < timedelta(minutes=review["min_minutes_between_reviews"]):
                continue
            triggers = []
            due = meta.get("next_ai_review_at")
            if due and parse_utc(due, "next_ai_review_at") <= now:
                triggers.append("management_candle")
            price = position.get("live_price") or position.get("mark_price")
            stop = position.get("stop_price")
            if isinstance(price, (int, float)) and isinstance(stop, (int, float)) and price > 0:
                if abs(price - stop) / price <= review["stop_proximity_pct"]:
                    triggers.append("near_stop")
            for target in position.get("targets") or []:
                if not target.get("hit") and isinstance(price, (int, float)) and price > 0:
                    if abs(target["price"] - price) / price <= review["target_proximity_pct"]:
                        triggers.append("near_target")
                        break
            previous = meta.get("review_exposure")
            if previous and abs(gross - previous) / max(previous, 1e-9) >= review["exposure_change_pct"]:
                triggers.append("exposure_change")
            if not triggers:
                continue
            try:
                proposal = self.request_replan(ref, intent=None, use_ai=True, origin="autonomous")
            except PaperTradingError as exc:
                proposal = {"status": "error", "reason": str(exc)[:200]}
            interval = review["management_interval_minutes"]
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE position_meta SET last_ai_review_at=?, next_ai_review_at=?, review_exposure=?, "
                    "review_count=review_count+1 WHERE position_ref=?",
                    (iso_utc(now), iso_utc(now + timedelta(minutes=interval)), gross, ref),
                )
            results.append({"position_ref": ref, "triggers": triggers, "status": proposal.get("status"),
                            "proposal_id": proposal.get("proposal_id"), "auto_applied": proposal.get("auto_applied", False)})
        return results

    def portfolio_review(self, *, source: str = "USER") -> dict[str, Any]:
        positions = [p for p in self.list_positions() if p["status"] == "open"]
        state = self.exposure_state(positions)
        settings = self.settings()
        review = review_portfolio(positions, state, settings["brain"])
        review["as_of"] = self._iso()
        review["equity_usdt"] = state["equity_usdt"]
        review["drawdown"] = state["drawdown"]
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO brain_decisions(decision_id, experiment_id, cycle_id, cohort, symbol, action, payload_json, created_at) "
                "VALUES(?, ?, NULL, 'portfolio', '*', ?, ?, ?)",
                (f"review-{uuid.uuid4().hex[:12]}", self.experiment_id, review["actions"][0]["action"], _dumps(review), review["as_of"]),
            )
            self._activity_locked(
                db, source="SYSTEM", category="DECISION", severity="INFO",
                title="Portfolio review: " + ", ".join(sorted({a["action"] for a in review["actions"]})),
                summary=f"gross {review['exposure']['gross_exposure_x']:.2f}x · long risk {review['exposure']['long_risk_pct'] * 100:.2f}%",
            )
        return review

    # --------------------------------------------------- attention / activity
    def _attention_context(self) -> dict[str, Any]:
        experiment = self.experiment()
        positions = [p for p in self.list_positions() if p["status"] == "open"]
        stream = self.runtime.live_stream
        stream_status = None
        if stream is not None:
            try:
                stream.refresh_state()
                stream_status = stream.status()
            except Exception:
                stream_status = {"state": "OFFLINE"}
        proposals = []
        for proposal in self.list_replans(status="proposed", limit=50):
            proposals.append({k: proposal.get(k) for k in ("proposal_id", "position_ref", "symbol", "headline", "reason_codes")})
        pending_orders = [o for o in self.list_orders(status="open")]
        cycles = self.store.list_cycles(self.experiment_id, limit=1)
        settings = self.settings()
        state = self.exposure_state(positions)
        equity = max(1e-9, state["equity_usdt"])
        concentration = []
        groups: dict[tuple[str, str], float] = {}
        for exposure in state["exposures"]:
            key = (correlation_group(exposure["base"]), exposure["side"])
            groups[key] = groups.get(key, 0.0) + float(exposure["risk_usdt"] or 0.0)
        for (group, side), risk in groups.items():
            if risk / equity > settings["brain"]["max_correlated_risk_pct"]:
                concentration.append({
                    "key": f"{group}:{side}",
                    "title": f"{group} {side} risk {risk / equity * 100:.1f}% of equity",
                    "summary": f"above the {settings['brain']['max_correlated_risk_pct'] * 100:.1f}% correlated-risk limit",
                })
        ttl = timedelta(minutes=settings["attention"]["info_ttl_minutes"])
        since = iso_utc(self._now() - ttl)
        recent = self.store._query(
            "SELECT event_id, title, summary, symbol, position_ref, category FROM activity_events "
            "WHERE experiment_id=? AND timestamp>=? AND category IN ('FILL','OUTCOME') ORDER BY timestamp DESC LIMIT 5",
            (self.experiment_id, since),
        )
        wallet = self.store.wallet_summary(self.experiment_id, "primary")
        spot = self.spot_wallet()
        return {
            "incidents": self.runtime.resilience.incidents(status="OPEN", limit=50),
            "kill_switch": self.kill_switch(),
            "market_states": self.safety.states(self.experiment_id),
            "experiment": experiment,
            "positions": positions,
            "market_stream": stream_status,
            "pending_proposals": proposals,
            "pending_orders": pending_orders,
            "budget_status": self.store.cost_ledger.budget_status(self.experiment_id, experiment["config"]["ai_budget"]),
            "last_cycle_at": cycles[0]["created_at"] if cycles else None,
            "reconciliation": {
                "perp": abs(float(wallet["equity"]) - float(wallet["cash_balance"]) - float(wallet["unrealized_pnl"])) < 1e-6,
                "spot": abs(spot["equity_usdt"] - spot["cash_balance_usdt"] - spot["holdings_value_usdt"]) < 1e-6,
            },
            "concentration": concentration,
            "recent_info": [
                {
                    "key": row["event_id"], "title": row["title"], "summary": row["summary"], "symbol": row["symbol"],
                    "position_ref": row["position_ref"], "category": row["category"],
                    "action": {"route": "position", "position_ref": row["position_ref"]} if row["position_ref"] else {"route": "activity"},
                }
                for row in recent
            ],
        }

    def evaluate_attention(self) -> dict[str, int]:
        now = self._now()
        settings = self.settings()
        candidates = activity_mod.derive_attention(self._attention_context(), settings, now)
        by_key = {item["dedupe_key"]: item for item in candidates}
        stamp = iso_utc(now)
        created = resolved = 0
        with self._lock, self.store.transaction() as db:
            active = {
                row["dedupe_key"]: dict(row)
                for row in db.execute(
                    "SELECT * FROM attention_items WHERE experiment_id=? AND status IN ('open','acknowledged')",
                    (self.experiment_id,),
                ).fetchall()
            }
            for key, item in by_key.items():
                if key in active:
                    # "seen N×" counts real changes of a condition, not every re-evaluation.
                    row = active[key]
                    changed = item["kind"] != "info" and (item["severity"] != row["severity"] or item["summary"] != row["summary"])
                    db.execute(
                        "UPDATE attention_items SET severity=?, title=?, summary=?, action_json=?, last_seen_at=?, "
                        "occurrences=occurrences+? WHERE attention_id=?",
                        (item["severity"], item["title"], item["summary"], _dumps(item["action"]), stamp, int(changed),
                         row["attention_id"]),
                    )
                    continue
                if item["kind"] == "info" and db.execute(
                    "SELECT 1 FROM attention_items WHERE experiment_id=? AND dedupe_key=?", (self.experiment_id, key)
                ).fetchone():
                    continue
                db.execute(
                    "INSERT INTO attention_items(attention_id, experiment_id, dedupe_key, kind, severity, category, title, "
                    "summary, instrument_id, symbol, position_ref, action_json, status, first_seen_at, last_seen_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                    (
                        f"att-{uuid.uuid4().hex[:16]}", self.experiment_id, key, item["kind"], item["severity"],
                        item["category"], item["title"], item["summary"], item["instrument_id"], item["symbol"],
                        item["position_ref"], _dumps(item["action"]), stamp, stamp,
                    ),
                )
                created += 1
                if item["severity"] in {"CRITICAL", "ACTION"} and item["kind"] not in {"replan"}:
                    self._activity_locked(
                        db, source="SYSTEM", category="ALERT", severity=item["severity"], title=item["title"],
                        summary=item["summary"], symbol=item["symbol"], instrument_id=item["instrument_id"],
                        position_ref=item["position_ref"], payload_ref=f"attention:{key}",
                    )
            ttl = timedelta(minutes=settings["attention"]["info_ttl_minutes"])
            for key, row in active.items():
                if row["kind"] == "info":
                    if now - parse_utc(row["first_seen_at"], "first_seen") > ttl:
                        db.execute(
                            "UPDATE attention_items SET status='resolved', resolved_at=?, resolution='expired' WHERE attention_id=?",
                            (stamp, row["attention_id"]),
                        )
                        resolved += 1
                    continue
                if key not in by_key:
                    db.execute(
                        "UPDATE attention_items SET status='resolved', resolved_at=?, resolution='condition_cleared' WHERE attention_id=?",
                        (stamp, row["attention_id"]),
                    )
                    resolved += 1
        return {"created": created, "resolved": resolved, "active": len(by_key)}

    def attention(self, *, evaluate: bool = True, include_resolved: bool = False, limit: int = 100) -> dict[str, Any]:
        if evaluate:
            self.evaluate_attention()
        statuses = ("open", "acknowledged", "resolved") if include_resolved else ("open", "acknowledged")
        rows = self.store._query(
            f"SELECT * FROM attention_items WHERE experiment_id=? AND status IN ({','.join('?' for _ in statuses)}) "
            "ORDER BY last_seen_at DESC LIMIT ?",
            (self.experiment_id, *statuses, limit),
        )
        rank = activity_mod.SEVERITY_RANK
        items = sorted(
            (
                {
                    "schema_version": activity_mod.ATTENTION_SCHEMA_VERSION,
                    **{k: row[k] for k in row.keys() if k != "action_json"},
                    "action": _loads(row["action_json"], {}),
                }
                for row in rows
            ),
            key=lambda item: (item["status"] != "open", rank.get(item["severity"], 9), item["last_seen_at"]),
        )
        counts = {severity: 0 for severity in ("CRITICAL", "ACTION", "WATCH", "INFO")}
        for item in items:
            if item["status"] == "open":
                counts[item["severity"]] += 1
        return {"items": items, "counts": counts, "as_of": self._iso()}

    def acknowledge_attention(self, attention_id: str) -> dict[str, Any]:
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM attention_items WHERE attention_id=? AND experiment_id=?", (attention_id, self.experiment_id)
            ).fetchone()
            if row is None:
                raise PaperTradingError("attention item was not found")
            status = "resolved" if row["kind"] == "info" else "acknowledged"
            db.execute(
                "UPDATE attention_items SET status=?, resolved_at=CASE WHEN ?='resolved' THEN ? ELSE resolved_at END, "
                "resolution=CASE WHEN ?='resolved' THEN 'acknowledged' ELSE resolution END WHERE attention_id=?",
                (status, status, self._iso(), status, attention_id),
            )
        return {"attention_id": attention_id, "status": status}

    def activity(self, **filters: Any) -> dict[str, Any]:
        stored = [
            {"schema_version": activity_mod.ACTIVITY_SCHEMA_VERSION, **dict(row)}
            for row in self.store._query(
                "SELECT * FROM activity_events WHERE experiment_id=? ORDER BY timestamp DESC LIMIT 2000", (self.experiment_id,)
            )
        ]
        cycles = self.store.list_cycles(self.experiment_id, limit=500)
        legacy = self.store.activity(self.experiment_id, limit=2000)
        positions = {p["position_id"]: p for p in self.store.list_positions(self.experiment_id)}
        projected = activity_mod.project_runtime_activity(
            experiment_id=self.experiment_id,
            exchange=self.catalog.exchange,
            legacy_events=legacy,
            cycles=cycles,
            positions_by_id=positions,
        )
        projected = [
            event for event in projected
            if not (event["category"] in {"OUTCOME", "MANAGEMENT"} and "reduce_only" in event["title"])
        ]
        titles = {
            "SUSPECT_PRINT": "Suspect print ignored for stops/liquidation",
            "EXECUTION_DEFERRED": "Execution deferred by safety",
            "SELL_VELOCITY_LIMIT": "Sell velocity limit",
            "CORE_PROTECTED": "Spot Core protected",
            "SLIPPAGE_LIMIT": "Slippage limit",
            "STALE_DECISION": "Stale decision rejected",
            "DUPLICATE_PREVENTED": "Duplicate request prevented",
            "WRONG_SIDE_BLOCK": "Wrong-side request blocked",
            "LIQUIDATION_BUFFER_CRITICAL": "Liquidation emergency",
            "RECONCILIATION_FAILURE": "Reconciliation failure",
            "SAFETY_OVERRIDE": "User overrode an execution guard",
            "CRASH_MODE_ENTERED": "Crash mode entered",
            "CRASH_MODE_RECOVERED": "Crash mode recovered",
        }
        for event in self.safety.events(self.experiment_id, limit=500):
            if event["kind"] == "kill_switch":
                continue
            code = event["code"]
            critical = code in {"LIQUIDATION_BUFFER_CRITICAL", "RECONCILIATION_FAILURE", "CRASH_MODE_ENTERED"}
            instrument = event.get("instrument_id")
            projected.append(activity_mod.activity_event(
                experiment_id=self.experiment_id, timestamp=event["created_at"], source=event["source"] if event["source"] in {"AI", "USER", "SYSTEM"} else "SYSTEM",
                category="RISK" if event["kind"] != "market_state" else "ALERT",
                severity="CRITICAL" if critical else "WATCH" if code not in {"DUPLICATE_PREVENTED", "CRASH_MODE_RECOVERED"} else "INFO",
                title=titles.get(code, code.replace("_", " ").title()),
                summary=", ".join(f"{k}={v}" for k, v in (event.get("detail") or {}).items() if isinstance(v, (str, int, float)))[:300],
                instrument_id=instrument, symbol=None if not instrument else instrument.split(":")[-1].replace("_", ""),
                market_type=None if not instrument else instrument.split(":")[1], position_ref=event.get("position_ref"),
                payload_ref=f"safety:{event['event_id']}", event_id=f"safety-{event['event_id']}",
            ))
        events = activity_mod.filter_activity(stored + projected, **filters)
        return {"events": events, "count": len(events), "as_of": self._iso()}

    # ------------------------------------------------------------- learning
    def _review_for(self, ref: str) -> dict[str, Any] | None:
        rows = self.store._query("SELECT payload_json FROM post_trade_reviews WHERE position_ref=?", (ref,))
        return _loads(rows[0]["payload_json"]) if rows else None

    def sync_reviews(self, *, limit: int = 5) -> int:
        existing = {row["position_ref"] for row in self.store._query(
            "SELECT position_ref FROM post_trade_reviews WHERE experiment_id=?", (self.experiment_id,)
        )}
        config = self.experiment()["config"]
        economics_fx = config.get("cost_fx") or {}
        usdt_per_usd = economics_fx.get("usdt_per_usd") if economics_fx.get("mode") == "manual" else None
        created = 0
        closed_perps = [
            p for p in self.store.list_positions(self.experiment_id, cohort="primary")
            if p["status"] == "closed" and f"perp:{p['position_id']}" not in existing
        ]
        closed_spot = [h for h in self.spot_holdings(status="closed") if f"spot:{h['holding_id']}" not in existing]
        usage = self.store.cost_ledger.usage_events(self.experiment_id)
        for position in closed_perps[:limit]:
            ref = f"perp:{position['position_id']}"
            meta = self._meta(ref) or {}
            cycle = None if position["cycle_id"].startswith("manual:") else self.store.cycle(position["cycle_id"])
            order = self.store._query("SELECT risk_json FROM orders WHERE order_id=?", (f"{position['position_id']}:entry",))
            original_intent = (_loads(order[0]["risk_json"], {}) if order else {}).get("intent") or {}
            events = self.management_events(ref)
            overrides = [e for e in events if e["action"] in {"PROTECTION_UPDATE", "REDUCE", "CLOSE"}]
            override_source = "USER" if any(e["source"] == "USER" for e in overrides) else "AI" if any(e["source"] == "AI" for e in overrides) else None
            bars: list[dict[str, Any]] = []
            future: list[dict[str, Any]] = []
            try:
                provider = self.runtime._market(config)
                opened = parse_utc(position["opened_at"], "opened_at")
                closed = parse_utc(position["closed_at"], "closed_at")
                horizon = min(self._now(), closed + timedelta(hours=6))
                path = provider.fetch_monitor_bars(position["symbol"], opened, horizon)
                bars = [bar for bar in path if parse_utc(bar["close_time"], "bar") <= closed + timedelta(minutes=1)]
                future = path
            except (PaperTradingError, MarketDataError, ValueError):
                bars, future = [], []
            counterfactual = None
            if override_source and future and original_intent.get("stop_price") and original_intent.get("target_price"):
                counterfactual = learning.counterfactual_plan_pnl(
                    position["side"], float(position["entry_price"]), float(original_intent["stop_price"]),
                    float(original_intent["target_price"]), float(position["opened_quantity"]), future,
                )
            cost_usd = sum(float(e.get("estimated_cost_usd") or 0) for e in usage if e.get("cycle_id") == position["cycle_id"])
            exit_reason = position["exit_reason"]
            if exit_reason == "reduce_only":
                closer = next((e for e in events if e["action"] == "CLOSE"), None)
                if closer is not None:
                    exit_reason = (closer.get("after") or {}).get("reason") or f"{closer['source'].lower()}_close"
            context = {
                "position_ref": ref,
                "market_type": "perpetual",
                "symbol": position["symbol"],
                "side": position["side"],
                "entry_price": position["entry_price"],
                "opened_quantity": position["opened_quantity"],
                "realized_pnl": position["realized_pnl"],
                "realized_gross": position["realized_gross"],
                "initial_risk_usdt": meta.get("initial_risk"),
                "exit_reason": exit_reason,
                "path_bars": bars,
                "target_distance": abs(float(original_intent.get("target_price") or position["target_price"]) - float(position["entry_price"])),
                "entry_features": (cycle or {}).get("features") or {},
                "entry_regime": (cycle or {}).get("market_regime"),
                "funding_paid": position["funding_paid"],
                "slippage_paid": position["slippage_paid"],
                "fees_paid": float(position["entry_fee"]) + float(position["exit_fees"]),
                "brain": (cycle or {}).get("portfolio_brain"),
                "correlated_limit_pct": self.settings()["brain"]["max_correlated_risk_pct"],
                "ai_cost_usd": cost_usd,
                "ai_cost_usdt": None if usdt_per_usd is None else cost_usd * float(usdt_per_usd),
                "counterfactual_gross_pnl": counterfactual,
                "override_source": override_source,
                "holding_minutes": learning.holding_minutes(position["opened_at"], position["closed_at"]),
                "closed_at": position["closed_at"],
                "decision_stack": {
                    "source": "USER" if position["cycle_id"].startswith("manual:") else "AI",
                    "quant_gate": ((cycle or {}).get("quant_gate") or {}).get("eligible"),
                    "jev_status": (cycle or {}).get("jev_status"),
                    "ai_path": ((cycle or {}).get("primary_decision") or {}).get("ai_path"),
                    "portfolio_brain": ((cycle or {}).get("portfolio_brain") or {}).get("action"),
                    "risk_code": ((cycle or {}).get("risk") or {}).get("code"),
                },
                "management_actions": [{"action": e["action"], "source": e["source"], "at": e["created_at"]} for e in reversed(events)][:20],
                "user_overrides": sum(1 for e in overrides if e["source"] == "USER"),
            }
            self._insert_review(learning.build_review(context))
            created += 1
        for holding in closed_spot[:limit]:
            ref = f"spot:{holding['holding_id']}"
            meta = self._meta(ref) or {}
            events = self.management_events(ref)
            fees = float(holding["fees_paid"])
            context = {
                "position_ref": ref,
                "market_type": "spot",
                "symbol": holding["symbol"],
                "side": "long",
                "entry_price": float(holding["cost_basis"]) / float(holding["total_bought"]) if float(holding["total_bought"]) else 0.0,
                "opened_quantity": holding["total_bought"],
                "realized_pnl": holding["realized_pnl"],
                "realized_gross": float(holding["realized_pnl"]) + fees,
                "initial_risk_usdt": meta.get("initial_risk"),
                "exit_reason": holding["exit_reason"],
                "path_bars": [],
                "slippage_paid": holding["slippage_paid"],
                "fees_paid": fees,
                "holding_minutes": learning.holding_minutes(holding["opened_at"], holding["closed_at"]),
                "closed_at": holding["closed_at"],
                "decision_stack": {"source": holding["source"]},
                "management_actions": [{"action": e["action"], "source": e["source"], "at": e["created_at"]} for e in reversed(events)][:20],
                "user_overrides": sum(1 for e in events if e["source"] == "USER" and e["action"] in {"PROTECTION_UPDATE", "REDUCE", "CLOSE"}),
            }
            self._insert_review(learning.build_review(context))
            created += 1
        if created:
            self._refresh_hypotheses()
        return created

    def _insert_review(self, review: dict[str, Any]) -> None:
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO post_trade_reviews(review_id, experiment_id, position_ref, market_type, symbol, outcome, "
                "tags_json, payload_json, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    review["review_id"], self.experiment_id, review["position_ref"], review["market_type"], review["symbol"],
                    review["outcome"], _dumps(review["tags"]), _dumps(review), self._iso(),
                ),
            )
            self._activity_locked(
                db, source="SYSTEM", category="OUTCOME", severity="INFO",
                title=f"Post-trade review: {review['outcome']}" + (f" · {', '.join(review['tags'][:2])}" if review["tags"] else ""),
                summary=review["lesson"], symbol=review["symbol"], market_type=review["market_type"],
                position_ref=review["position_ref"], payload_ref=f"review:{review['review_id']}",
            )

    def reviews(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.store._query(
            "SELECT payload_json FROM post_trade_reviews WHERE experiment_id=? ORDER BY created_at DESC LIMIT ?",
            (self.experiment_id, limit),
        )
        return [_loads(row["payload_json"], {}) for row in rows]

    def _refresh_hypotheses(self) -> None:
        hypotheses = learning.update_hypotheses(self.reviews(limit=1000))
        now = self._iso()
        with self.store.transaction() as db:
            for item in hypotheses:
                db.execute(
                    "INSERT INTO improvement_hypotheses(hypothesis_id, experiment_id, tag, statement, evidence_count, status, "
                    "created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(experiment_id, tag) DO UPDATE SET "
                    "evidence_count=excluded.evidence_count, status=excluded.status, updated_at=excluded.updated_at",
                    (f"hyp-{digest({'e': self.experiment_id, 't': item['tag']})[:16]}", self.experiment_id, item["tag"],
                     item["statement"], item["evidence_count"], item["status"], now, now),
                )

    def hypotheses(self) -> list[dict[str, Any]]:
        rows = self.store._query(
            "SELECT * FROM improvement_hypotheses WHERE experiment_id=? ORDER BY evidence_count DESC, tag", (self.experiment_id,)
        )
        return [{**dict(row), "auto_applied": False} for row in rows]

    def tournament(self) -> dict[str, Any]:
        experiment = self.experiment()
        config = experiment["config"]
        records = self.store.export_records(self.experiment_id)
        result = learning.strategy_tournament(
            positions=records["positions"],
            usage_events=self.store.cost_ledger.usage_events(self.experiment_id),
            cycles=records["cycles"],
            arms=config["evaluation_arms"],
            starting_balance=float(config["starting_balance_usdt"]),
            fx=config.get("cost_fx"),
        )
        result["ai_filter"] = learning.ai_filter_counterfactuals(records["cycles"], records["positions"])
        reviews = self.reviews(limit=1000)
        tags: dict[str, int] = {}
        for review in reviews:
            for tag in review.get("tags", []):
                tags[tag] = tags.get(tag, 0) + 1
        result["learning_tags"] = tags
        result["reviews"] = len(reviews)
        return result

    def set_core_quantity(self, ref: str, *, core_fraction: float, source: str = "USER") -> dict[str, Any]:
        """Logical Spot Core/Tactical split. Core is protected from plan stops and crash-state
        discretionary sells unless a structural breakdown is confirmed."""

        kind, _identifier, row = self._position_row(ref)
        if kind != "spot":
            raise PaperTradingError("Core/Tactical allocation applies to Spot holdings")
        if source not in {"USER", "SYSTEM"}:
            raise PaperTradingError("AUTHORITY_DENIED: Core allocation is set by the user or the lifecycle policy")
        fraction = _num(core_fraction, "core_fraction")
        if not 0 <= fraction <= 1:
            raise PaperTradingError("core_fraction must be between 0 and 1")
        core = float(row["quantity"]) * fraction
        with self.store.transaction() as db:
            before = db.execute("SELECT core_quantity FROM position_meta WHERE position_ref=?", (ref,)).fetchone()
            db.execute("UPDATE position_meta SET core_quantity=?, updated_at=? WHERE position_ref=?", (core, self._iso(), ref))
            self._journal_locked(db, action="CORE_ALLOCATION", source=source, position_ref=ref, market_type="spot",
                                 before={"core_quantity": None if before is None else before["core_quantity"]},
                                 after={"core_quantity": core, "core_fraction": fraction})
        return {"position_ref": ref, "core_quantity": core, "tactical_quantity": float(row["quantity"]) - core}

    def liquidation_guard(self) -> list[dict[str, Any]]:
        """Futures liquidation safety overrides slow confirmation and every authority mode."""

        settings = self.safety_settings()
        actions = []
        for position in [p for p in self.list_positions() if p["status"] == "open" and p["market_type"] == "perpetual"]:
            buffer = position.get("liquidation_buffer_pct")
            if not isinstance(buffer, (int, float)) or buffer > settings["liquidation_emergency_buffer_pct"]:
                continue
            ref = position["position_ref"]
            recent = [e for e in self.management_events(ref, limit=50)
                      if e["source"] == "SYSTEM" and (e.get("after") or {}).get("reason") == "liquidation_emergency"
                      and e["created_at"] >= iso_utc(self._now() - timedelta(minutes=settings["emergency_cooldown_minutes"]))]
            if recent:
                continue
            self._safety_reject("LIQUIDATION_BUFFER_CRITICAL", {"buffer_pct": buffer, "mode": position["management_mode"]},
                                instrument_id=position["instrument_id"], position_ref=ref)
            try:
                result = self.reduce_position(ref, settings["emergency_reduce_fraction"], source="SYSTEM", confirm=True,
                                              reason="liquidation_emergency")
                actions.append({"position_ref": ref, "buffer_pct": buffer, "closed": result["closed"]})
            except PaperTradingError as exc:
                actions.append({"position_ref": ref, "buffer_pct": buffer, "error": str(exc)[:160]})
        return actions

    # ------------------------------------------------------------ Spot lifecycle
    LIFECYCLE_MAJORS = ("BTC", "ETH", "SOL", "BNB", "XRP")

    def lifecycle_settings(self) -> dict[str, Any]:
        return self.settings()["lifecycle"]

    def _regime_bars(self, base: str, now: datetime, bars: int = 120) -> list[dict[str, Any]]:
        """Closed 4h Spot candles ending at ``now`` (point-in-time). Empty when unavailable."""

        pair = f"{base}_USDT"
        key = f"{pair}:{bars}:{int(now.timestamp()) // 14400}"
        cache = self.__dict__.setdefault("_regime_cache", {})
        if key in cache:
            return cache[key]
        try:
            spot = self.catalog.spot
            if self.catalog.source == "gate":
                result = spot.fetch_candles(pair, "4h", bars=bars, as_of=now)
            else:
                result = spot._futures._lane(neutral_symbol(pair), "4h", bars, now)
        except (PaperTradingError, MarketDataError, AttributeError, KeyError, ValueError):
            result = []
        if len(cache) > 256:
            cache.clear()
        cache[key] = result
        return result

    def _lifecycle_row(self, ref: str) -> dict[str, Any] | None:
        rows = self.store._query("SELECT * FROM spot_lifecycle WHERE position_ref=?", (ref,))
        if not rows:
            return None
        row = dict(rows[0])
        row["pending_plan"] = _loads(row.pop("pending_plan_json"), None)
        return row

    def lifecycle_events(self, ref: str | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM lifecycle_events WHERE experiment_id=?"
        params: list[Any] = [self.experiment_id]
        if ref:
            sql += " AND position_ref=?"
            params.append(ref)
        rows = self.store._query(sql + " ORDER BY event_id DESC LIMIT ?", (*params, limit))
        return [{**{k: v for k, v in dict(row).items() if not k.endswith("_json")},
                 "reasons": _loads(row["reasons_json"], []), "plan": _loads(row["plan_json"], {}),
                 "result": _loads(row["result_json"], {})} for row in rows]

    def _lifecycle_evidence(self, view: dict[str, Any], now: datetime) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        settings = self.lifecycle_settings()
        base = view["base"]
        bars = self._regime_bars(base, now)
        btc = self._regime_bars("BTC", now) if base != "BTC" else bars
        eth = self._regime_bars("ETH", now) if base != "ETH" else bars
        tracked = {p["base"] for p in self.list_positions() if p["market_type"] == "spot" and p["status"] == "open"}
        basket_bases = sorted((tracked | set(self.LIFECYCLE_MAJORS)) - {base})[:6]
        basket = {other: self._regime_bars(other, now) for other in basket_bases}
        try:
            safety = self.assess(view["instrument_id"])
        except PaperTradingError:
            safety = None
        evidence = regime_evidence(symbol=view["symbol"], as_of=now, asset_bars=bars, btc_bars=btc, eth_bars=eth,
                                   basket=basket, settings=settings, safety=safety, data_origin=self.catalog.data_origin)
        return evidence, classify_regime(evidence, settings), safety

    def lifecycle_review(
        self,
        ref: str,
        *,
        now: datetime | None = None,
        recommendation: dict[str, Any] | None = None,
        source: str = "SYSTEM",
        execute: bool = True,
    ) -> dict[str, Any]:
        """One lifecycle review: evidence -> regime -> deterministic plan (-> bounded recommendation).
        AUTO_PAPER holdings execute through the normal risk/safety path as AI; other modes get a
        pending proposal the user can apply. HOLD is recorded and changes nothing."""

        settings = self.lifecycle_settings()
        now = (now or self._now()).astimezone(timezone.utc)
        view = self.position(ref)
        if view["market_type"] != "spot":
            raise PaperTradingError("the lifecycle manager applies to Spot holdings")
        if view["status"] != "open":
            raise PaperTradingError("POSITION_CLOSED: the Spot holding is closed")
        meta = self._meta(ref) or {}
        row = self._lifecycle_row(ref)
        auto = meta.get("management_mode") == "AUTO_PAPER"
        with self.store.transaction() as db:
            if row is None:
                db.execute("INSERT OR IGNORE INTO spot_lifecycle(position_ref, experiment_id, state, peak_mark, updated_at) "
                           "VALUES(?, ?, 'ACCUMULATE', ?, ?)", (ref, self.experiment_id, view["mark_price"], iso_utc(now)))
        row = self._lifecycle_row(ref) or {}
        if auto and meta.get("owner_source") == "AI" and not view.get("core_quantity") and settings["default_core_fraction"] > 0:
            # AI-owned holdings get the policy Core split once; user holdings keep the user's choice.
            self.set_core_quantity(ref, core_fraction=settings["default_core_fraction"], source="SYSTEM")
            view = self.position(ref)
        evidence, regime, safety = self._lifecycle_evidence(view, now)
        spot_settings = self.settings()
        plan = plan_lifecycle(
            position_ref=ref, holding=view, lifecycle=row, evidence=evidence, regime=regime, settings=settings,
            safety=safety, max_allocation_pct=spot_settings["spot_max_allocation_pct"], now=now,
        )
        if recommendation is not None:
            if not isinstance(recommendation, dict):
                raise PaperTradingError("recommendation must be an object")
            plan = merge_recommendation(plan, {**recommendation, "source": "AI" if source == "AI" else "USER"})
        status, result = "HOLD", {}
        if plan["action"] != "HOLD":
            if auto and execute and settings["enabled"]:
                status, result = self._execute_lifecycle(ref, plan, source="AI", now=now)
            else:
                status = "PROPOSED"
        next_review = iso_utc(now + timedelta(minutes=settings["review_interval_minutes"]))
        state_after = plan["state_after"] if status in {"HOLD", "APPLIED"} else row.get("state", "ACCUMULATE")
        if status == "APPLIED" and result.get("closed"):
            state_after = "CASH_WAIT"
        with self.store.transaction() as db:
            db.execute(
                "UPDATE spot_lifecycle SET state=?, state_version=state_version+?, regime=?, peak_mark=?, breakdown_streak=?, "
                "last_review_at=?, next_review_at=?, last_action_at=COALESCE(?, last_action_at), pending_plan_json=?, updated_at=? "
                "WHERE position_ref=?",
                (state_after, int(state_after != row.get("state")), plan["regime"], plan["peak_mark"], plan["breakdown_streak"],
                 iso_utc(now), next_review, iso_utc(now) if status == "APPLIED" else None,
                 _dumps(plan) if status == "PROPOSED" else None, iso_utc(now), ref),
            )
            event_id = db.execute(
                "INSERT INTO lifecycle_events(experiment_id, position_ref, instrument_id, created_at, source, state_before, state_after, "
                "action, regime, status, sell_quantity, add_quote_usdt, reasons_json, evidence_json, plan_json, result_json) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.experiment_id, ref, view["instrument_id"], iso_utc(now), "AI" if status == "APPLIED" else source,
                 plan["state_before"], state_after, plan["action"], plan["regime"], status, plan["sell_quantity"],
                 float(result.get("add_quote_usdt") or 0.0), _dumps(plan["reasons"]), _dumps(evidence), _dumps(plan), _dumps(result)),
            ).lastrowid
            if status != "HOLD" or state_after != row.get("state"):
                self._activity_locked(
                    db, source="AI" if status == "APPLIED" else "SYSTEM", category="MANAGEMENT",
                    severity="ACTION" if status in {"PROPOSED", "BLOCKED"} else "INFO",
                    title=f"Lifecycle {plan['action'].replace('_', ' ').lower()} ({status.lower()})",
                    summary=f"{plan['state_before']} -> {state_after} · regime {plan['regime']} · {', '.join(plan['reasons'])}"[:300],
                    instrument_id=view["instrument_id"], symbol=view["symbol"], market_type="spot", position_ref=ref,
                    payload_ref=f"lifecycle:{event_id}",
                )
        return {"position_ref": ref, "status": status, "plan": plan, "result": result, "regime": regime,
                "evidence": evidence, "event_id": event_id}

    def _execute_lifecycle(self, ref: str, plan: dict[str, Any], *, source: str, now: datetime,
                           confirm: bool = True) -> tuple[str, dict[str, Any]]:
        try:
            if plan["action"] == "ADD":
                view = self.position(ref)
                amount = round(plan["add_fraction"] * self.spot_wallet()["equity_usdt"], 8)
                response = self.create_order({
                    "client_request_id": f"lifecycle:{ref}:{plan['as_of']}"[:80], "instrument_id": view["instrument_id"],
                    "action": "buy", "quote_amount": amount, "note": "lifecycle add on qualified pullback",
                }, source=source)
                if not response.get("accepted"):
                    return "BLOCKED", {"code": response.get("code"), "reason": response.get("reason")}
                return "APPLIED", {"add_quote_usdt": amount, "order_ref": response.get("order_ref")}
            quantity = float(self.position(ref)["quantity"])
            fraction = min(1.0, plan["sell_quantity"] / quantity) if quantity > 0 else 0.0
            if fraction <= 0:
                return "HOLD", {}
            result = self.reduce_position(ref, 1.0 if plan["action"] == "EXIT" else fraction, source=source, confirm=confirm,
                                          request_id=f"lifecycle:{ref}:{plan['as_of']}"[:80], reason="lifecycle")
            return "APPLIED", {"closed": result["closed"], "fraction": fraction,
                               "filled_quantity": (result.get("result") or {}).get("filled_quantity")}
        except ConfirmationRequired:
            raise
        except PaperTradingError as exc:
            return "BLOCKED", {"reason": str(exc)[:200]}

    def apply_lifecycle(self, ref: str, *, confirm: bool = False, source: str = "USER") -> dict[str, Any]:
        """Apply a pending lifecycle proposal. Stale proposals (older than one review interval) are refused."""

        row = self._lifecycle_row(ref)
        plan = (row or {}).get("pending_plan")
        if not plan:
            raise PaperTradingError("no pending lifecycle proposal for this holding")
        now = self._now()
        age = (now - parse_utc(plan["as_of"], "plan.as_of")).total_seconds() / 60
        if age > self.lifecycle_settings()["review_interval_minutes"]:
            self._safety_reject("STALE_DECISION", {"lifecycle_plan_age_minutes": age}, position_ref=ref, source=source)
            raise PaperTradingError("STALE_DECISION: the lifecycle proposal expired; run a new review")
        status, result = self._execute_lifecycle(ref, plan, source=source, now=now, confirm=confirm)
        state_after = "CASH_WAIT" if result.get("closed") else plan["state_after"] if status == "APPLIED" else row["state"]
        with self.store.transaction() as db:
            db.execute("UPDATE spot_lifecycle SET state=?, state_version=state_version+?, last_action_at=?, pending_plan_json=?, "
                       "updated_at=? WHERE position_ref=?",
                       (state_after, int(state_after != row["state"]), iso_utc(now) if status == "APPLIED" else row["last_action_at"],
                        None if status == "APPLIED" else _dumps(plan), iso_utc(now), ref))
            db.execute(
                "INSERT INTO lifecycle_events(experiment_id, position_ref, instrument_id, created_at, source, state_before, state_after, "
                "action, regime, status, sell_quantity, add_quote_usdt, reasons_json, evidence_json, plan_json, result_json) "
                "VALUES(?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)",
                (self.experiment_id, ref, iso_utc(now), source, row["state"], state_after, plan["action"], plan["regime"], status,
                 plan["sell_quantity"], float(result.get("add_quote_usdt") or 0.0), _dumps(plan["reasons"]), _dumps(plan), _dumps(result)),
            )
        return {"position_ref": ref, "status": status, "result": result, "state": state_after}

    def dismiss_lifecycle(self, ref: str) -> dict[str, Any]:
        with self.store.transaction() as db:
            db.execute("UPDATE spot_lifecycle SET pending_plan_json=NULL, updated_at=? WHERE position_ref=?", (self._iso(), ref))
        return {"position_ref": ref, "pending_plan": None}

    def lifecycle_due(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Scheduler hook: review open Spot holdings whose review is due. Never per tick."""

        settings = self.lifecycle_settings()
        if not settings["enabled"]:
            return []
        now = (now or self._now()).astimezone(timezone.utc)
        results = []
        for view in [p for p in self.list_positions() if p["market_type"] == "spot" and p["status"] == "open"]:
            if view["management_mode"] == "PAUSED":
                continue
            row = self._lifecycle_row(view["position_ref"])
            due = (row or {}).get("next_review_at")
            if due and parse_utc(due, "next_review_at") > now:
                continue
            try:
                review = self.lifecycle_review(view["position_ref"], now=now)
                results.append({"position_ref": view["position_ref"], "status": review["status"], "action": review["plan"]["action"]})
            except PaperTradingError as exc:
                results.append({"position_ref": view["position_ref"], "status": "ERROR", "reason": str(exc)[:160]})
        return results

    def lifecycle_view(self, ref: str) -> dict[str, Any]:
        row = self._lifecycle_row(ref)
        return {"position_ref": ref, "lifecycle": row, "events": self.lifecycle_events(ref, limit=20)}

    def lifecycle_overview(self) -> dict[str, Any]:
        rows = [dict(r) for r in self.store._query("SELECT * FROM spot_lifecycle WHERE experiment_id=?", (self.experiment_id,))]
        for row in rows:
            row["pending_plan"] = _loads(row.pop("pending_plan_json"), None)
        counts: dict[str, int] = {}
        for event in self.lifecycle_events(limit=10_000):
            key = f"{event['action']}:{event['status']}"
            counts[key] = counts.get(key, 0) + 1
        return {"holdings": rows, "counts": counts, "settings": self.lifecycle_settings()}

    def lifecycle_benchmark(self, instrument_id: str, *, bars: int = 360) -> dict[str, Any]:
        """Aligned offline benchmark arms on closed 4h Spot bars (point-in-time)."""

        instrument = self.catalog.get(instrument_id)
        if instrument.get("market_type") != "spot":
            raise PaperTradingError("benchmarks run on Spot instruments")
        bars = max(120, min(1000, int(_num(bars, "bars", positive=True))))
        now = self._now()
        series = self._regime_bars(instrument["base"], now, bars)
        btc = self._regime_bars("BTC", now, bars) if instrument["base"] != "BTC" else None
        settings = self.settings()
        report = run_benchmarks(
            series, symbol=instrument["symbol"], fee_rate=float(settings["spot_fee_rate"] or instrument.get("taker_fee_rate") or 0.001),
            slippage_bps=float(settings["spot_slippage_bps"]), initial_usdt=float(settings["spot_starting_balance_usdt"]),
            settings=self.lifecycle_settings(), btc_bars=btc, data_origin=self.catalog.data_origin,
        )
        report["report_id"] = f"lcb-{uuid.uuid4().hex[:16]}"
        report["instrument_id"] = instrument_id
        with self.store.transaction() as db:
            db.execute("INSERT INTO lifecycle_benchmarks(report_id, experiment_id, instrument_id, created_at, report_json) VALUES(?, ?, ?, ?, ?)",
                       (report["report_id"], self.experiment_id, instrument_id, iso_utc(now), _dumps(report)))
        return report

    def lifecycle_benchmarks(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.store._query("SELECT report_json FROM lifecycle_benchmarks WHERE experiment_id=? ORDER BY created_at DESC LIMIT ?",
                                 (self.experiment_id, limit))
        return [_loads(row["report_json"], {}) for row in rows]

    # --------------------------------------------------------------- hooks
    def after_monitor(self, *, now: datetime | None = None) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for name, step in (
            ("meta", self.sync_meta),
            ("liquidation_guard", lambda: len(self.liquidation_guard())),
            ("spot", lambda: len(self.monitor_spot(now=now))),
            ("reconciliation", lambda: self.reconcile()["ok"]),
            ("reviews", self.sync_reviews),
            ("position_reviews", lambda: len(self.review_positions(now=now))),
            ("lifecycle", lambda: len(self.lifecycle_due(now=now))),
            ("snapshot", lambda: bool(self.record_snapshot())),
            ("attention", self.evaluate_attention),
        ):
            try:
                summary[name] = step()
            except PaperTradingError as exc:
                summary[name] = f"error: {str(exc)[:120]}"
        return summary

    def stream_state(self, *, since: str | None = None) -> dict[str, Any]:
        perp_wallet = self.store.wallet_summary(self.experiment_id, "primary")
        spot = self.spot_wallet()
        experiment = self.experiment()
        events = self.activity(since=since, limit=25)["events"] if since else []
        attention = self.attention(evaluate=False)
        total = float(perp_wallet["equity"]) + spot["equity_usdt"]
        starting = float(experiment["config"]["starting_balance_usdt"]) + spot["starting_balance_usdt"]
        return {
            "type": "runtime",
            "as_of": self._iso(),
            "experiment_status": experiment["status"],
            "portfolio": {"total_equity_usdt": total, "trading_pnl_usdt": total - starting},
            "attention_counts": attention["counts"],
            "activity": events,
        }

    def export_files(self) -> dict[str, list[dict[str, Any]] | dict[str, Any]]:
        experiment_id = self.experiment_id

        def rows(sql: str) -> list[dict[str, Any]]:
            return [dict(row) for row in self.store._query(sql, (experiment_id,))]

        return {
            "portfolio_snapshots": rows("SELECT * FROM portfolio_snapshots WHERE experiment_id=? ORDER BY as_of"),
            "spot_wallets": rows("SELECT * FROM spot_wallets WHERE experiment_id=?"),
            "spot_holdings": rows("SELECT * FROM spot_holdings WHERE experiment_id=? ORDER BY opened_at"),
            "spot_orders": rows("SELECT * FROM spot_orders WHERE experiment_id=? ORDER BY created_at"),
            "spot_fills": rows("SELECT * FROM spot_fills WHERE experiment_id=? ORDER BY as_of"),
            "position_plans": rows("SELECT * FROM position_meta WHERE experiment_id=? ORDER BY created_at"),
            "position_replans": [
                {**{k: v for k, v in row.items() if k != "payload_json"}, "payload": _loads(row["payload_json"], {})}
                for row in rows("SELECT * FROM replan_proposals WHERE experiment_id=? ORDER BY created_at")
            ],
            "management_events": rows("SELECT * FROM management_events WHERE experiment_id=? ORDER BY event_id"),
            "activity": self.activity(limit=1000)["events"],
            "attention": rows("SELECT * FROM attention_items WHERE experiment_id=? ORDER BY first_seen_at"),
            "post_trade_reviews": self.reviews(limit=10_000),
            "hypotheses": self.hypotheses(),
            "brain_decisions": rows("SELECT * FROM brain_decisions WHERE experiment_id=? ORDER BY created_at"),
            "tournament": self.tournament(),
            "settings": self.settings(),
            "safety_events": self.safety.events(experiment_id, limit=100_000),
            "execution_plans": self.safety.plans(experiment_id, limit=100_000),
            "market_safety_states": self.safety.states(experiment_id),
            "kill_switch": self.kill_switch(),
            "lifecycle_states": rows("SELECT * FROM spot_lifecycle WHERE experiment_id=? ORDER BY position_ref"),
            "lifecycle_events": self.lifecycle_events(limit=100_000),
            "lifecycle_benchmarks": self.lifecycle_benchmarks(limit=1000),
        }
