"""Spot PAPER accounting and deterministic Spot risk (no leverage, margin, funding, or liquidation).

Spot is modeled as base/quote balances, not as a leverage=1 futures position:

- buys debit quote (notional + fee) and add base at a fee-inclusive average cost;
- sells credit quote (notional - fee), release base, and realize PnL against average cost;
- unrealized PnL = (mark - average cost) * quantity.

Fees are charged in the quote currency (a documented PAPER simplification).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .paper_contracts import PaperTradingError


EPSILON = 1e-12


def floor_to_step(value: float, step: float | None) -> float:
    if step is None or step <= 0:
        return math.floor(value * 1e8) / 1e8
    units = math.floor(value / step + 1e-9)
    decimals = max(0, min(18, -int(math.floor(math.log10(step))) if step < 1 else 0))
    return round(units * step, decimals)


@dataclass
class SpotHoldingState:
    quantity: float = 0.0
    avg_cost: float = 0.0
    cost_basis: float = 0.0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    total_bought: float = 0.0
    total_sold: float = 0.0

    @classmethod
    def from_row(cls, row: dict[str, Any] | None) -> "SpotHoldingState":
        if not row:
            return cls()
        return cls(
            quantity=float(row["quantity"]),
            avg_cost=float(row["avg_cost"]),
            cost_basis=float(row["cost_basis"]),
            realized_pnl=float(row["realized_pnl"]),
            fees_paid=float(row["fees_paid"]),
            total_bought=float(row["total_bought"]),
            total_sold=float(row["total_sold"]),
        )


@dataclass(frozen=True)
class SpotFill:
    side: str
    quantity: float
    price: float
    fee: float
    quote_delta: float
    realized_pnl: float
    holding: SpotHoldingState = field(compare=False)


def apply_buy(state: SpotHoldingState, quantity: float, price: float, fee_rate: float) -> SpotFill:
    if quantity <= 0 or price <= 0 or fee_rate < 0:
        raise PaperTradingError("spot buy requires positive quantity and price")
    notional = quantity * price
    fee = notional * fee_rate
    new_quantity = state.quantity + quantity
    new_basis = state.cost_basis + notional + fee
    updated = SpotHoldingState(
        quantity=new_quantity,
        avg_cost=new_basis / new_quantity,
        cost_basis=new_basis,
        realized_pnl=state.realized_pnl,
        fees_paid=state.fees_paid + fee,
        total_bought=state.total_bought + quantity,
        total_sold=state.total_sold,
    )
    return SpotFill("buy", quantity, price, fee, -(notional + fee), 0.0, updated)


def apply_sell(state: SpotHoldingState, quantity: float, price: float, fee_rate: float) -> SpotFill:
    if quantity <= 0 or price <= 0 or fee_rate < 0:
        raise PaperTradingError("spot sell requires positive quantity and price")
    if quantity > state.quantity + EPSILON:
        raise PaperTradingError("INSUFFICIENT_BASE: sell quantity exceeds the holding")
    quantity = min(quantity, state.quantity)
    notional = quantity * price
    fee = notional * fee_rate
    released_basis = state.avg_cost * quantity
    realized = notional - fee - released_basis
    remaining = state.quantity - quantity
    closed = remaining <= EPSILON
    updated = SpotHoldingState(
        quantity=0.0 if closed else remaining,
        avg_cost=0.0 if closed else state.avg_cost,
        cost_basis=0.0 if closed else state.cost_basis - released_basis,
        realized_pnl=state.realized_pnl + realized,
        fees_paid=state.fees_paid + fee,
        total_bought=state.total_bought,
        total_sold=state.total_sold + quantity,
    )
    return SpotFill("sell", quantity, price, fee, notional - fee, realized, updated)


def unrealized_pnl(state: SpotHoldingState, mark: float | None) -> float | None:
    if mark is None:
        return None
    return (float(mark) - state.avg_cost) * state.quantity


@dataclass(frozen=True)
class SpotRiskDecision:
    allowed: bool
    code: str
    reason: str
    quantity: float = 0.0
    notional: float = 0.0
    fee: float = 0.0
    slippage_cost: float = 0.0
    reference_price: float | None = None
    fill_price: float | None = None
    cash_after: float | None = None
    allocation_after: float | None = None
    deployed_after: float | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "reason": self.reason,
            "quantity": self.quantity,
            "notional": self.notional,
            "fee": self.fee,
            "slippage_cost": self.slippage_cost,
            "reference_price": self.reference_price,
            "fill_price": self.fill_price,
            "cash_after": self.cash_after,
            "allocation_after": self.allocation_after,
            "deployed_after": self.deployed_after,
            "warnings": list(self.warnings),
        }


class SpotRiskEngine:
    """Fail-closed Spot order checks. Server-side; the browser never sizes orders."""

    def evaluate(
        self,
        *,
        side: str,
        order_type: str,
        instrument: dict[str, Any],
        quote: dict[str, Any],
        settings: dict[str, Any],
        wallet: dict[str, Any],
        holdings_value: dict[str, float],
        holding_available: float,
        holding_quantity: float,
        quantity: float | None = None,
        quote_amount: float | None = None,
        limit_price: float | None = None,
        closing_whole_holding: bool = False,
    ) -> SpotRiskDecision:
        def reject(code: str, reason: str, **extra: Any) -> SpotRiskDecision:
            return SpotRiskDecision(False, code, reason, **extra)

        if side not in {"buy", "sell"}:
            return reject("INVALID_SIDE", "spot side must be buy or sell")
        if order_type not in {"market", "limit"}:
            return reject("INVALID_ORDER_TYPE", "spot order type must be market or limit")
        if instrument.get("market_type") != "spot":
            return reject("MARKET_TYPE_MISMATCH", "instrument is not a spot pair")
        if instrument.get("quote") != wallet.get("quote", "USDT"):
            return reject("QUOTE_UNSUPPORTED", "only pairs quoted in the PAPER spot wallet currency are supported")
        if not quote.get("fresh"):
            return reject("STALE_DATA", "no fresh spot quote is available; new orders fail closed")
        bid, ask = quote.get("best_bid"), quote.get("best_ask")
        if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)) or bid <= 0 or ask < bid:
            return reject("MARKET_DATA_UNAVAILABLE", "top-of-book is unavailable")
        slippage = float(settings["spot_slippage_bps"]) / 10_000
        if order_type == "market":
            reference = float(ask if side == "buy" else bid)
            fill_price = reference * (1 + slippage if side == "buy" else 1 - slippage)
        else:
            if limit_price is None or not math.isfinite(limit_price) or limit_price <= 0:
                return reject("LIMIT_PRICE_REQUIRED", "limit orders require a positive limit price")
            reference = float(limit_price)
            fill_price = reference
            mid = (bid + ask) / 2
            if abs(limit_price - mid) / mid > 0.5:
                return reject("LIMIT_PRICE_OUT_OF_BAND", "limit price is more than 50% away from the market")
        fee_rate = float(settings.get("spot_fee_rate") or instrument.get("taker_fee_rate") or 0.001)
        step = instrument.get("quantity_step")
        if quantity is None:
            if quote_amount is None or not math.isfinite(quote_amount) or quote_amount <= 0:
                return reject("SIZE_REQUIRED", "send a base quantity or a quote amount")
            if side == "buy":
                quantity = quote_amount / (fill_price * (1 + fee_rate))
            else:
                quantity = quote_amount / fill_price
        if not math.isfinite(quantity) or quantity <= 0:
            return reject("SIZE_REQUIRED", "order quantity must be positive")
        if side == "sell" and closing_whole_holding:
            quantity = min(quantity, holding_available)
        else:
            quantity = floor_to_step(quantity, step)
        if quantity <= 0:
            return reject("BELOW_QUANTITY_STEP", "order is smaller than the exchange quantity step")
        notional = quantity * fill_price
        fee = notional * fee_rate
        slippage_cost = abs(fill_price - reference) * quantity
        min_qty = instrument.get("min_quantity")
        min_notional = instrument.get("min_notional")
        max_qty = instrument.get("max_quantity")
        dust_close = side == "sell" and closing_whole_holding
        if not dust_close:
            if min_qty is not None and quantity < float(min_qty) - EPSILON:
                return reject("BELOW_MIN_QUANTITY", f"exchange minimum quantity is {min_qty}")
            if min_notional is not None and notional < float(min_notional) - EPSILON:
                return reject("BELOW_MIN_NOTIONAL", f"exchange minimum notional is {min_notional} {instrument['quote']}")
        if max_qty is not None and quantity > float(max_qty) + EPSILON:
            return reject("ABOVE_MAX_QUANTITY", f"exchange maximum quantity is {max_qty}")
        cash = float(wallet["cash_balance"]) - float(wallet.get("reserved_quote", 0.0))
        holdings_total = sum(holdings_value.values())
        equity = float(wallet["cash_balance"]) + holdings_total
        base_value = holdings_value.get(instrument["instrument_id"], 0.0)
        warnings: list[str] = []
        if side == "buy":
            cost = notional + fee
            if cost > cash + EPSILON:
                return reject("INSUFFICIENT_QUOTE", "available quote balance is insufficient",
                              quantity=quantity, notional=notional, fee=fee)
            cash_after = cash - cost
            allocation_after = (base_value + notional) / equity if equity > 0 else 1.0
            deployed_after = (holdings_total + notional) / equity if equity > 0 else 1.0
            reserve = float(settings["spot_min_cash_reserve_pct"]) * equity
            if cash_after < reserve - EPSILON:
                return reject("CASH_RESERVE", "order would breach the minimum cash reserve",
                              quantity=quantity, notional=notional, fee=fee, cash_after=cash_after)
            if allocation_after > float(settings["spot_max_allocation_pct"]) + EPSILON:
                return reject("ALLOCATION_LIMIT", "order would exceed the per-asset allocation limit",
                              quantity=quantity, notional=notional, fee=fee, allocation_after=allocation_after)
            if deployed_after > float(settings["spot_max_deployed_pct"]) + EPSILON:
                return reject("DEPLOYED_CAPITAL_LIMIT", "order would exceed the maximum deployed capital",
                              quantity=quantity, notional=notional, fee=fee, deployed_after=deployed_after)
            if allocation_after > 0.8 * float(settings["spot_max_allocation_pct"]):
                warnings.append("CONCENTRATION_RISING")
        else:
            if quantity > holding_available + EPSILON:
                return reject("INSUFFICIENT_BASE", "available base balance is insufficient", quantity=quantity)
            cash_after = cash + notional - fee
            allocation_after = max(0.0, base_value - notional) / equity if equity > 0 else 0.0
            deployed_after = max(0.0, holdings_total - notional) / equity if equity > 0 else 0.0
        if instrument.get("spread_bps") is not None and instrument["spread_bps"] > 50:
            warnings.append("WIDE_SPREAD")
        return SpotRiskDecision(
            True,
            "APPROVED",
            "spot balance, exchange constraint, allocation, and freshness checks passed",
            quantity=quantity,
            notional=notional,
            fee=fee,
            slippage_cost=slippage_cost,
            reference_price=reference,
            fill_price=fill_price,
            cash_after=cash_after,
            allocation_after=allocation_after,
            deployed_after=deployed_after,
            warnings=tuple(warnings),
        )
