"""Portfolio Brain: deterministic portfolio-level policy above per-symbol analysis.

It emits typed actions and reason codes only. It can allow, shrink, or block a new
entry and recommend portfolio actions; it never mutates an account and it never
bypasses the RiskEngine, which still sizes and validates every order afterwards.
"""

from __future__ import annotations

from typing import Any

from .contracts import digest


BRAIN_SCHEMA_VERSION = "portfolio-brain-decision.v1"
BRAIN_POLICY_VERSION = "portfolio-brain.v1"
ENTRY_ACTIONS = (
    "ALLOW",
    "RESIZE",
    "BLOCK_CONCENTRATION",
    "BLOCK_CORRELATED_EXPOSURE",
    "HOLD_CASH",
    "DE_RISK",
)
ADVISORY_CODES = ("PREFER_SPOT", "PREFER_PERPETUAL")
REVIEW_ACTIONS = ("PROTECT_PROFIT", "DE_RISK", "REBALANCE", "REDUCE_CONCENTRATION", "HOLD")
MAJOR_GROUPS = {"BTC": "BTC", "ETH": "ETH"}
STABLE_BASES = {"USDT", "USDC", "USD1", "DAI", "FDUSD", "TUSD"}


def correlation_group(base: str) -> str:
    """Coarse, documented correlation buckets: BTC, ETH, or ALT (alts co-move with each other)."""

    base = base.upper()
    if base in STABLE_BASES:
        return "STABLE"
    return MAJOR_GROUPS.get(base, "ALT")


def _exposures(state: dict[str, Any]) -> dict[str, Any]:
    by_asset: dict[str, float] = {}
    by_group_direction: dict[tuple[str, str], float] = {}
    by_direction: dict[str, float] = {"long": 0.0, "short": 0.0}
    gross = 0.0
    net = 0.0
    for item in state.get("exposures", []):
        base = item["base"].upper()
        side = item["side"]
        risk = max(0.0, float(item.get("risk_usdt") or 0.0))
        notional = abs(float(item.get("notional_usdt") or 0.0))
        by_asset[base] = by_asset.get(base, 0.0) + risk
        key = (correlation_group(base), side)
        by_group_direction[key] = by_group_direction.get(key, 0.0) + risk
        by_direction[side] = by_direction.get(side, 0.0) + risk
        gross += notional
        net += notional if side == "long" else -notional
    return {
        "risk_by_asset": by_asset,
        "risk_by_group_direction": by_group_direction,
        "risk_by_direction": by_direction,
        "gross_notional": gross,
        "net_notional": net,
    }


def evaluate_entry(
    candidate: dict[str, Any],
    state: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Portfolio-aware gate for one proposed entry.

    ``candidate``: base, side (long/short), market_type, risk_usdt, notional_usdt, funding_rate.
    ``state``: equity_usdt, drawdown, max_drawdown_stop, spot_cash_usdt, spot_equity_usdt,
    exposures [{base, side, risk_usdt, notional_usdt}], ai_budget_utilization.
    """

    policy = settings
    equity = max(1e-9, float(state.get("equity_usdt") or 0.0))
    risk = max(0.0, float(candidate.get("risk_usdt") or 0.0))
    notional = abs(float(candidate.get("notional_usdt") or 0.0))
    base = str(candidate["base"]).upper()
    side = candidate["side"]
    group = correlation_group(base)
    exposure = _exposures(state)
    reasons: list[str] = []
    advisories: list[str] = []
    multiplier = 1.0
    action = "ALLOW"

    def fit(limit_pct: float, used: float) -> float:
        """Largest fraction of the candidate risk that fits under a risk limit."""

        room = limit_pct * equity - used
        if risk <= 0:
            return 1.0
        return max(0.0, min(1.0, room / risk))

    drawdown = float(state.get("drawdown") or 0.0)
    max_drawdown = float(state.get("max_drawdown_stop") or 0.15)
    if drawdown >= max_drawdown * float(policy["hold_cash_drawdown_fraction"]):
        return _decision("HOLD_CASH", 0.0, ["DRAWDOWN_HOLD_CASH"], advisories, candidate, state, exposure)
    if drawdown >= max_drawdown * float(policy["de_risk_drawdown_fraction"]):
        action = "DE_RISK"
        multiplier = min(multiplier, float(policy["resize_multiplier"]))
        reasons.append("DRAWDOWN_DE_RISK")

    asset_fit = fit(float(policy["max_asset_risk_pct"]), exposure["risk_by_asset"].get(base, 0.0))
    if asset_fit < float(policy["min_resize_fraction"]):
        return _decision("BLOCK_CONCENTRATION", 0.0, reasons + ["ASSET_CONCENTRATION"], advisories, candidate, state, exposure)
    if asset_fit < 1.0:
        multiplier = min(multiplier, asset_fit)
        reasons.append("ASSET_CONCENTRATION_RESIZE")

    if group != "STABLE":
        correlated_used = exposure["risk_by_group_direction"].get((group, side), 0.0)
        correlated_fit = fit(float(policy["max_correlated_risk_pct"]), correlated_used)
        if correlated_fit < float(policy["min_resize_fraction"]):
            return _decision(
                "BLOCK_CORRELATED_EXPOSURE", 0.0,
                reasons + [f"CORRELATED_{group}_{side.upper()}_EXPOSURE"], advisories, candidate, state, exposure,
            )
        if correlated_fit < 1.0:
            multiplier = min(multiplier, correlated_fit)
            reasons.append(f"CORRELATED_{group}_{side.upper()}_RESIZE")

    direction_fit = fit(float(policy["max_direction_risk_pct"]), exposure["risk_by_direction"].get(side, 0.0))
    if direction_fit < float(policy["min_resize_fraction"]):
        return _decision("BLOCK_CONCENTRATION", 0.0, reasons + [f"DIRECTION_{side.upper()}_CONCENTRATION"], advisories, candidate, state, exposure)
    if direction_fit < 1.0:
        multiplier = min(multiplier, direction_fit)
        reasons.append(f"DIRECTION_{side.upper()}_RESIZE")

    max_gross = float(policy["max_gross_exposure_x"]) * equity
    if notional > 0:
        gross_fit = max(0.0, min(1.0, (max_gross - exposure["gross_notional"]) / notional))
        if gross_fit < float(policy["min_resize_fraction"]):
            return _decision("BLOCK_CONCENTRATION", 0.0, reasons + ["GROSS_EXPOSURE_LIMIT"], advisories, candidate, state, exposure)
        if gross_fit < 1.0:
            multiplier = min(multiplier, gross_fit)
            reasons.append("GROSS_EXPOSURE_RESIZE")

    if candidate.get("market_type") == "spot":
        spot_equity = float(state.get("spot_equity_usdt") or 0.0)
        spot_cash = float(state.get("spot_cash_usdt") or 0.0)
        if spot_equity > 0 and spot_cash - notional < float(policy["hold_cash_min_pct"]) * spot_equity:
            return _decision("HOLD_CASH", 0.0, reasons + ["SPOT_CASH_RESERVE"], advisories, candidate, state, exposure)
    funding = candidate.get("funding_rate")
    if candidate.get("market_type") == "perpetual":
        if side == "long" and isinstance(funding, (int, float)) and funding >= float(policy["prefer_spot_funding_rate"]):
            advisories.append("PREFER_SPOT")
    elif side == "short":
        advisories.append("PREFER_PERPETUAL")

    if action == "ALLOW" and multiplier < 1.0:
        action = "RESIZE"
    if not reasons:
        reasons.append("WITHIN_PORTFOLIO_LIMITS")
    return _decision(action, round(multiplier, 6), reasons, advisories, candidate, state, exposure)


def _decision(
    action: str,
    multiplier: float,
    reasons: list[str],
    advisories: list[str],
    candidate: dict[str, Any],
    state: dict[str, Any],
    exposure: dict[str, Any],
) -> dict[str, Any]:
    equity = max(1e-9, float(state.get("equity_usdt") or 0.0))
    risk = max(0.0, float(candidate.get("risk_usdt") or 0.0)) * multiplier
    side = candidate["side"]
    group = correlation_group(candidate["base"])
    before_direction = exposure["risk_by_direction"].get(side, 0.0)
    before_group = exposure["risk_by_group_direction"].get((group, side), 0.0)
    inputs = {
        "candidate": {key: candidate.get(key) for key in ("symbol", "base", "side", "market_type", "risk_usdt", "notional_usdt", "funding_rate")},
        "equity_usdt": state.get("equity_usdt"),
        "drawdown": state.get("drawdown"),
        "exposure_count": len(state.get("exposures", [])),
    }
    return {
        "schema_version": BRAIN_SCHEMA_VERSION,
        "policy_version": BRAIN_POLICY_VERSION,
        "decision_id": f"brain-{digest(inputs)[:20]}",
        "action": action,
        "allowed": action in {"ALLOW", "RESIZE", "DE_RISK"} and multiplier > 0,
        "size_multiplier": multiplier if action not in {"BLOCK_CONCENTRATION", "BLOCK_CORRELATED_EXPOSURE", "HOLD_CASH"} else 0.0,
        "reason_codes": reasons,
        "advisories": advisories,
        "correlation_group": group,
        "exposure": {
            "direction_risk_pct_before": before_direction / equity,
            "direction_risk_pct_after": (before_direction + risk) / equity,
            "group_risk_pct_before": before_group / equity,
            "group_risk_pct_after": (before_group + risk) / equity,
            "gross_exposure_x_before": exposure["gross_notional"] / equity,
        },
        "inputs": inputs,
        "risk_engine_authoritative": True,
    }


def review_portfolio(positions: list[dict[str, Any]], state: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Portfolio-level recommendations over open positions/holdings (typed, no mutation)."""

    equity = max(1e-9, float(state.get("equity_usdt") or 0.0))
    exposure = _exposures(state)
    actions: list[dict[str, Any]] = []
    for position in positions:
        r_multiple = position.get("r_multiple")
        stop = position.get("stop_price")
        entry = position.get("entry_price")
        side = position.get("side")
        if (
            isinstance(r_multiple, (int, float))
            and r_multiple >= float(settings["protect_profit_r"])
            and isinstance(stop, (int, float))
            and isinstance(entry, (int, float))
            and ((side == "long" and stop < entry) or (side == "short" and stop > entry))
        ):
            actions.append({
                "action": "PROTECT_PROFIT",
                "position_ref": position["position_ref"],
                "symbol": position["symbol"],
                "reason_codes": ["UNREALIZED_ABOVE_THRESHOLD", "STOP_BELOW_BREAKEVEN"],
                "suggested_intent": "protect_profit",
            })
        allocation = position.get("allocation_pct")
        if position.get("market_type") == "spot" and isinstance(allocation, (int, float)) and allocation > float(settings["spot_max_allocation_pct"]):
            actions.append({
                "action": "REBALANCE",
                "position_ref": position["position_ref"],
                "symbol": position["symbol"],
                "reason_codes": ["SPOT_ALLOCATION_ABOVE_LIMIT"],
                "suggested_intent": "reduce_exposure",
            })
    for (group, side), risk in sorted(exposure["risk_by_group_direction"].items()):
        if risk / equity > float(settings["max_correlated_risk_pct"]):
            actions.append({
                "action": "REDUCE_CONCENTRATION",
                "position_ref": None,
                "symbol": group,
                "reason_codes": [f"CORRELATED_{group}_{side.upper()}_ABOVE_LIMIT"],
                "suggested_intent": "reduce_exposure",
            })
    drawdown = float(state.get("drawdown") or 0.0)
    if drawdown >= float(state.get("max_drawdown_stop") or 0.15) * float(settings["de_risk_drawdown_fraction"]):
        actions.append({
            "action": "DE_RISK",
            "position_ref": None,
            "symbol": None,
            "reason_codes": ["PORTFOLIO_DRAWDOWN"],
            "suggested_intent": "tighten_risk",
        })
    if not actions:
        actions.append({"action": "HOLD", "position_ref": None, "symbol": None, "reason_codes": ["WITHIN_PORTFOLIO_LIMITS"], "suggested_intent": None})
    return {
        "schema_version": "portfolio-review.v1",
        "policy_version": BRAIN_POLICY_VERSION,
        "actions": actions,
        "exposure": {
            "gross_exposure_x": exposure["gross_notional"] / equity,
            "net_exposure_x": exposure["net_notional"] / equity,
            "long_risk_pct": exposure["risk_by_direction"].get("long", 0.0) / equity,
            "short_risk_pct": exposure["risk_by_direction"].get("short", 0.0) / equity,
            "risk_by_asset_pct": {asset: value / equity for asset, value in sorted(exposure["risk_by_asset"].items())},
        },
        "mutates_accounts": False,
    }
