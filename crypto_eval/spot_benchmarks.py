"""Aligned Spot PAPER benchmark arms for the lifecycle manager.

Every arm sees the same closed bars, the same fee rate and the same slippage, and fills at the
close of its decision bar. The lifecycle arms call the same deterministic planner the live
manager uses, bar by bar, with point-in-time evidence only (bars up to the decision bar).

This is an offline, deterministic comparison. It is not evidence that any arm is superior:
the report says so, and prospective PAPER results are required before any such claim. The AI
recommendation layer is not simulated here (it would need paid calls); AI cost is reported as
unavailable rather than zero for arms that would use AI.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable

from .paper_contracts import PaperTradingError, iso_utc, parse_utc
from .spot_lifecycle import DEFAULT_LIFECYCLE_SETTINGS, classify_regime, plan_lifecycle, regime_evidence

BENCHMARK_SCHEMA_VERSION = "spot-benchmark-report.v1"
ARMS = ("buy_hold", "tp_ladder", "rebalance", "grid", "trailing_stop", "ai_lifecycle", "ai_lifecycle_brain")


class _Book:
    def __init__(self, cash: float, fee: float, slip: float) -> None:
        self.cash, self.qty, self.fee, self.slip = cash, 0.0, fee, slip
        self.fees = self.traded = 0.0
        self.trades: list[dict[str, Any]] = []
        self.core = 0.0

    def buy_quote(self, amount: float, price: float, index: int) -> None:
        amount = min(amount, self.cash)
        if amount <= 1e-9:
            return
        fill = price * (1 + self.slip)
        qty = amount / (fill * (1 + self.fee))
        fee = qty * fill * self.fee
        self.cash -= qty * fill + fee
        self.qty += qty
        self.fees += fee
        self.traded += qty * fill
        self.trades.append({"i": index, "side": "buy", "qty": qty, "price": fill})

    def sell_qty(self, qty: float, price: float, index: int) -> None:
        qty = min(qty, self.qty)
        if qty <= 1e-12:
            return
        fill = price * (1 - self.slip)
        fee = qty * fill * self.fee
        self.cash += qty * fill - fee
        self.qty -= qty
        self.core = min(self.core, self.qty)
        self.fees += fee
        self.traded += qty * fill
        self.trades.append({"i": index, "side": "sell", "qty": qty, "price": fill})

    def value(self, price: float) -> float:
        return self.cash + self.qty * price


def _metrics(name: str, equity: list[float], cash_frac: list[float], book: _Book, closes: list[float],
             start: int, initial: float, *, ai_cost: float | None, notes: list[str]) -> dict[str, Any]:
    rets = [b / a - 1 for a, b in zip(equity, equity[1:]) if a > 0]
    bench = [b / a - 1 for a, b in zip(closes[start:], closes[start + 1:]) if a > 0]
    peak, max_dd = equity[0], 0.0
    for value in equity:
        peak = max(peak, value)
        max_dd = max(max_dd, (peak - value) / peak if peak > 0 else 0.0)
    final = equity[-1]
    up = [(r, b) for r, b in zip(rets, bench) if b > 0]
    down = [(r, b) for r, b in zip(rets, bench) if b < 0]
    capture = lambda pairs: (sum(r for r, _ in pairs) / sum(b for _, b in pairs)) if pairs and sum(b for _, b in pairs) else None
    valid = len(rets) >= 30
    mean = sum(rets) / len(rets) if rets else 0.0
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
    dsd = math.sqrt(sum(min(0.0, r) ** 2 for r in rets) / len(rets)) if rets else 0.0
    eq_peak = max(equity)
    price_peak = max(closes[start:])
    bh_peak_gain = price_peak / closes[start] - 1
    premature = avoided = 0
    premature_value = avoided_value = 0.0
    for trade in book.trades:
        if trade["side"] != "sell":
            continue
        after = closes[trade["i"] + 1: trade["i"] + 31]
        if not after:
            continue
        if max(after) >= trade["price"] * 1.10:
            premature += 1
            premature_value += trade["qty"] * (max(after) - trade["price"])
        if min(after) <= trade["price"] * 0.90:
            avoided += 1
            avoided_value += trade["qty"] * (trade["price"] - min(after))
    total_return = final / initial - 1
    return {
        "arm": name,
        "total_return": total_return,
        "net_economic_return": None if ai_cost is None else (final - ai_cost) / initial - 1,
        "ai_cost_usdt": ai_cost,
        "max_drawdown": max_dd,
        "sharpe": (mean / sd * math.sqrt(len(rets))) if valid and sd > 0 else None,
        "sortino": (mean / dsd * math.sqrt(len(rets))) if valid and dsd > 0 else None,
        "ratio_sample_valid": valid,
        "turnover": book.traded / initial,
        "fees_usdt": book.fees,
        "trades": len(book.trades),
        "upside_capture": capture(up),
        "downside_capture": capture(down),
        "peak_capture_ratio": (total_return / bh_peak_gain) if bh_peak_gain > 0 else None,
        "profit_giveback": ((eq_peak - final) / (eq_peak - initial)) if eq_peak > initial else None,
        "time_in_cash": sum(cash_frac) / len(cash_frac) if cash_frac else None,
        "premature_exits": {"count": premature, "value_usdt": premature_value},
        "avoided_drawdowns": {"count": avoided, "value_usdt": avoided_value},
        "final_equity_usdt": final,
        "notes": notes,
    }


def _range_regime(closes: list[float]) -> bool:
    change = abs(closes[-1] / closes[0] - 1)
    hi, lo = max(closes), min(closes)
    return change < 0.1 and (hi - lo) / lo < 0.3


def run_benchmarks(
    bars: list[dict[str, Any]],
    *,
    symbol: str,
    fee_rate: float = 0.001,
    slippage_bps: float = 5.0,
    initial_usdt: float = 100.0,
    settings: dict[str, Any] | None = None,
    brain_max_allocation: float = 0.8,
    btc_bars: list[dict[str, Any]] | None = None,
    data_origin: str = "unknown",
    safety_for: Callable[[int], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    settings = settings or DEFAULT_LIFECYCLE_SETTINGS
    bars = sorted(bars, key=lambda bar: bar["close_time"])
    warmup = settings["min_bars"]
    if len(bars) < warmup + 10:
        raise PaperTradingError(f"benchmark needs at least {warmup + 10} closed bars, have {len(bars)}")
    closes = [float(bar["close"]) for bar in bars]
    slip = slippage_bps / 10_000
    start = warmup
    results = []

    def run(name: str, step: Callable[[_Book, int], None], *, ai_cost: float | None = 0.0, notes: list[str] | None = None,
            setup: Callable[[_Book], None] | None = None) -> None:
        book = _Book(initial_usdt, fee_rate, slip)
        if setup:
            setup(book)
        equity, cash = [], []
        for i in range(start, len(closes)):
            step(book, i)
            value = book.value(closes[i])
            equity.append(value)
            cash.append(book.cash / value if value > 0 else 1.0)
        results.append(_metrics(name, equity, cash, book, closes, start, initial_usdt, ai_cost=ai_cost, notes=notes or []))

    def buy_hold(book: _Book, i: int) -> None:
        if i == start:
            book.buy_quote(book.cash, closes[i], i)
    run("buy_hold", buy_hold)

    ladder = [(0.10, 0.25), (0.20, 0.25), (0.35, 0.25), (0.50, 0.25)]
    def tp_ladder(book: _Book, i: int, state: dict[str, Any] = {}) -> None:
        if i == start:
            state.clear(); state.update(entry=closes[i], hit=set(), qty0=0.0)
            book.buy_quote(book.cash, closes[i], i)
            state["qty0"] = book.qty
        for level, (gain, fraction) in enumerate(ladder):
            if level not in state["hit"] and closes[i] >= state["entry"] * (1 + gain):
                state["hit"].add(level)
                book.sell_qty(state["qty0"] * fraction, closes[i], i)
    run("tp_ladder", tp_ladder)

    def rebalance(book: _Book, i: int) -> None:
        value = book.value(closes[i])
        weight = book.qty * closes[i] / value if value > 0 else 0.0
        if i == start or abs(weight - 0.5) > 0.1:
            target = 0.5 * value
            current = book.qty * closes[i]
            if current < target:
                book.buy_quote(target - current, closes[i], i)
            else:
                book.sell_qty((current - target) / closes[i], closes[i], i)
    run("rebalance", rebalance)

    window = closes[start:]
    if _range_regime(window[: max(10, len(window) // 3)]):
        levels = [closes[start] * (1 + k * 0.02) for k in range(-5, 6)]
        def grid(book: _Book, i: int, state: dict[str, Any] = {}) -> None:
            if i == start:
                state.clear(); state["last"] = min(range(len(levels)), key=lambda k: abs(levels[k] - closes[i]))
                book.buy_quote(book.cash * 0.5, closes[i], i)
                return
            level = min(range(len(levels)), key=lambda k: abs(levels[k] - closes[i]))
            unit = initial_usdt * 0.08
            if level < state["last"]:
                book.buy_quote(unit * (state["last"] - level), closes[i], i)
            elif level > state["last"]:
                book.sell_qty(unit * (level - state["last"]) / closes[i], closes[i], i)
            state["last"] = level
        run("grid", grid, notes=["grid applies only when the opening window is range-bound"])
    else:
        results.append({"arm": "grid", "status": "NOT_APPLICABLE",
                        "notes": ["opening window is trending; a simple grid is not meaningful here"]})

    def trailing(book: _Book, i: int, state: dict[str, Any] = {}) -> None:
        if i == start:
            state.clear(); state["peak"] = closes[i]
            book.buy_quote(book.cash, closes[i], i)
        state["peak"] = max(state["peak"], closes[i])
        if book.qty > 0 and closes[i] <= state["peak"] * 0.85:
            book.sell_qty(book.qty, closes[i], i)
    run("trailing_stop", trailing, notes=["15% trailing stop from peak close; stays in cash after exit"])

    def lifecycle_arm(brain: bool) -> Callable[[_Book, int], None]:
        memory: dict[str, Any] = {}

        def step(book: _Book, i: int) -> None:
            now = parse_utc(bars[i]["close_time"], "bar.close_time")
            if i == start:
                memory.clear()
                memory.update(state="ACCUMULATE", peak=closes[i], streak=0, last=None, avg=closes[i])
                book.buy_quote(book.cash * (brain_max_allocation if brain else 1.0), closes[i], i)
                book.core = book.qty * settings["default_core_fraction"]
                memory["avg"] = closes[i] * (1 + slip)
                return
            if book.qty <= 1e-12 and memory["state"] in {"EXIT", "CASH_WAIT"}:
                memory["state"] = "CASH_WAIT"
                return
            evidence = regime_evidence(symbol=symbol, as_of=now, asset_bars=bars[: i + 1], btc_bars=btc_bars,
                                       settings=settings, safety=safety_for(i) if safety_for else None, data_origin=data_origin)
            regime = classify_regime(evidence, settings)
            value = book.value(closes[i])
            holding = {"quantity": book.qty, "core_quantity": book.core, "avg_cost": memory["avg"],
                       "mark_price": closes[i], "allocation_pct": book.qty * closes[i] / value if value > 0 else None}
            plan = plan_lifecycle(position_ref="benchmark", holding=holding,
                                  lifecycle={"state": memory["state"], "peak_mark": memory["peak"],
                                             "breakdown_streak": memory["streak"], "last_action_at": memory["last"]},
                                  evidence=evidence, regime=regime, settings=settings,
                                  safety=safety_for(i) if safety_for else None,
                                  max_allocation_pct=1.0 if not brain else brain_max_allocation, now=now)
            memory.update(state=plan["state_after"], peak=plan["peak_mark"], streak=plan["breakdown_streak"])
            if plan["action"] == "ADD":
                if brain and holding["allocation_pct"] and holding["allocation_pct"] >= brain_max_allocation:
                    return
                before_qty, before_cost = book.qty, book.qty * memory["avg"]
                book.buy_quote(book.value(closes[i]) * plan["add_fraction"], closes[i], i)
                if book.qty > before_qty:
                    memory["avg"] = (before_cost + (book.qty - before_qty) * closes[i] * (1 + slip)) / book.qty
                memory["last"] = iso_utc(now)
            elif plan["sell_quantity"] > 0:
                book.sell_qty(plan["sell_quantity"], closes[i], i)
                memory["last"] = iso_utc(now)
        return step

    run("ai_lifecycle", lifecycle_arm(False), ai_cost=None,
        notes=["deterministic lifecycle policy; the AI recommendation layer is evaluated prospectively only"])
    run("ai_lifecycle_brain", lifecycle_arm(True), ai_cost=None,
        notes=["lifecycle policy plus Portfolio Brain allocation cap; AI layer evaluated prospectively only"])
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "symbol": symbol,
        "data_origin": data_origin,
        "interval": _interval(bars),
        "bars": len(bars),
        "evaluated_bars": len(bars) - start,
        "from": bars[start]["close_time"],
        "to": bars[-1]["close_time"],
        "assumptions": {"brain_max_allocation": brain_max_allocation, "fee_rate": fee_rate, "slippage_bps": slippage_bps, "initial_usdt": initial_usdt,
                        "fill": "close of decision bar", "warmup_bars": warmup},
        "arms": results,
        "claim": "NOT_EVIDENCE_OF_SUPERIORITY: offline aligned comparison; prospective PAPER results are required",
        "generated_at": iso_utc(datetime.now().astimezone()),
    }


def _interval(bars: list[dict[str, Any]]) -> str | None:
    if len(bars) < 2:
        return None
    seconds = (parse_utc(bars[1]["close_time"], "t") - parse_utc(bars[0]["close_time"], "t")).total_seconds()
    return {60: "1m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}.get(int(seconds), f"{int(seconds)}s")
