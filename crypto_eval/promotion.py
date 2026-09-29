"""Experiment Promotion Gates: frozen experiment manifests, reproducible checkpoint reports, and
a deterministic promotion engine.

The ladder is IDEA -> BACKTEST/REPLAY -> WALK-FORWARD -> SHADOW -> PAPER -> PAPER CHECKPOINTS ->
LIVE-ELIGIBLE REVIEW. Nothing here can enable real trading: a PASS only records that a separate,
human-reviewed live-execution phase *may be considered*. ``live_execution_enabled`` is always
False and is part of the report contract.

A gate review returns exactly one status: PASS, CONTINUE_COLLECTING_DATA, FAIL_STRATEGY,
FAIL_SAFETY, FAIL_RELIABILITY, INVALID_EXPERIMENT. Blockers are listed; safety and reliability
failures block regardless of PnL. Decisions are evidence summaries, not profit guarantees.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .paper_contracts import OPERATIONAL_EXPERIMENT_FIELDS, PaperTradingError, iso_utc, parse_utc

MANIFEST_SCHEMA_VERSION = "experiment-manifest.v1"
CHECKPOINT_SCHEMA_VERSION = "experiment-checkpoint.v1"
GATE_POLICY_VERSION = "promotion-gate.v1"
STATUSES = ("PASS", "CONTINUE_COLLECTING_DATA", "FAIL_STRATEGY", "FAIL_SAFETY", "FAIL_RELIABILITY", "INVALID_EXPERIMENT")
LADDER = ("IDEA", "BACKTEST_REPLAY", "WALK_FORWARD", "SHADOW", "PAPER", "PAPER_CHECKPOINTS", "LIVE_ELIGIBLE_REVIEW")
CHECKPOINTS = (("DAY_7", 7), ("DAY_30", 30), ("DAY_60", 60), ("DAY_90", 90))
SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "crypto-market-trading-analysis"

# Portfolio settings that change strategy treatment (material) vs operation only.
# ``promotion`` is material: the gate criteria are part of what an experiment means, so changing
# them mid-campaign (moving the goalposts) requires a new manifest version like any treatment change.
MATERIAL_SETTING_GROUPS = ("brain", "safety", "lifecycle", "ai_spot", "promotion")
MATERIAL_SETTING_KEYS = (
    "spot_fee_rate", "spot_slippage_bps", "spot_max_allocation_pct", "spot_max_deployed_pct", "spot_min_cash_reserve_pct",
    "spot_limit_participation", "perp_manual_max_risk_pct", "default_ai_management_mode",
)
MATERIAL_REVIEW_KEYS = ("enabled", "use_jev", "use_luna", "auto_apply_max_reduce_fraction")

DEFAULT_PROMOTION_CRITERIA: dict[str, Any] = {
    "min_days": 90,
    "min_completed_trades": 200,
    "min_profit_factor": 1.15,
    "max_drawdown": 0.20,
    "require_positive_net_economic_pnl": True,
    "max_top_trade_share": 0.5,
    "max_skipped_slot_ratio": 0.05,
    "max_open_critical_incidents": 0,
    "min_regimes_observed": 2,
}
CRITERIA_BOUNDS = {
    "min_days": (7, 3650), "min_completed_trades": (10, 100_000), "min_profit_factor": (0.5, 10.0), "max_drawdown": (0.01, 1.0),
    "max_top_trade_share": (0.05, 1.0), "max_skipped_slot_ratio": (0.0, 1.0), "max_open_critical_incidents": (0, 100),
    "min_regimes_observed": (1, 10),
}


def validate_criteria(value: dict[str, Any]) -> dict[str, Any]:
    merged = dict(DEFAULT_PROMOTION_CRITERIA)
    for key, item in (value or {}).items():
        if key not in merged:
            raise PaperTradingError(f"promotion.{key} is not supported")
        merged[key] = item
    if not isinstance(merged["require_positive_net_economic_pnl"], bool):
        raise PaperTradingError("promotion.require_positive_net_economic_pnl must be boolean")
    for key, (low, high) in CRITERIA_BOUNDS.items():
        item = merged[key]
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)) or not low <= float(item) <= high:
            raise PaperTradingError(f"promotion.{key} must be between {low} and {high}")
    for key in ("min_days", "min_completed_trades", "max_open_critical_incidents", "min_regimes_observed"):
        merged[key] = int(merged[key])
    return merged


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def skill_hash(root: Path = SKILL_ROOT) -> str | None:
    if not root.exists():
        return None
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def material_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        **{group: settings.get(group) for group in MATERIAL_SETTING_GROUPS},
        **{key: settings.get(key) for key in MATERIAL_SETTING_KEYS},
        "review": {key: (settings.get("review") or {}).get(key) for key in MATERIAL_REVIEW_KEYS},
    }


def build_manifest(*, config: dict[str, Any], settings: dict[str, Any], providers: list[dict[str, Any]],
                   price_book_version: str | None, policy_versions: dict[str, str]) -> dict[str, Any]:
    """Everything that defines the treatment. Secrets are never included (providers are reduced
    to kind/model/deployment/reasoning fields)."""

    strategy = {key: value for key, value in config.items() if key not in OPERATIONAL_EXPERIMENT_FIELDS}
    safe_providers = sorted(
        ({k: p.get(k) for k in ("provider_id", "kind", "model", "deployment", "reasoning_effort", "enabled", "role")} for p in providers),
        key=lambda item: str(item.get("provider_id")),
    )
    material = {
        "experiment_config": strategy,
        "portfolio_policy": material_settings(settings),
        "providers": safe_providers,
        "skill_sha256": skill_hash(),
        "policy_versions": dict(sorted(policy_versions.items())),
        "price_book_version": price_book_version,
        "benchmark_arms": config.get("evaluation_arms"),
    }
    return {"schema_version": MANIFEST_SCHEMA_VERSION, "material": material, "material_sha256": canonical_hash(material)}


def diff_material(frozen: dict[str, Any], current: dict[str, Any], prefix: str = "") -> list[str]:
    changes: list[str] = []
    keys = set(frozen) | set(current)
    for key in sorted(keys):
        a, b = frozen.get(key), current.get(key)
        path = f"{prefix}{key}"
        if isinstance(a, dict) and isinstance(b, dict):
            changes.extend(diff_material(a, b, f"{path}."))
        elif a != b:
            changes.append(path)
    return changes


PROMOTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiment_manifests(
    manifest_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    material_sha256 TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    frozen_at TEXT NOT NULL,
    UNIQUE(experiment_id, version)
);
CREATE TABLE IF NOT EXISTS promotion_reviews(
    review_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    manifest_version INTEGER,
    checkpoint TEXT NOT NULL,
    status TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def checkpoint_label(elapsed_days: float) -> str:
    label = "PRE_DAY_7"
    for name, day in CHECKPOINTS:
        if elapsed_days >= day:
            label = name
    return label


def evaluate_gate(report: dict[str, Any], criteria: dict[str, Any]) -> dict[str, Any]:
    """Deterministic promotion decision from a checkpoint report. Order of precedence:
    INVALID_EXPERIMENT > FAIL_SAFETY > FAIL_RELIABILITY > CONTINUE_COLLECTING_DATA > FAIL_STRATEGY > PASS."""

    blockers: list[str] = []
    identity, safety, reliability = report["identity"], report["safety"], report["reliability"]
    economics, coverage = report["economics"], report["coverage"]

    def result(status: str, reasons: list[str]) -> dict[str, Any]:
        return {"status": status, "blockers": blockers, "reasons": reasons, "policy_version": GATE_POLICY_VERSION,
                "criteria": criteria, "live_execution_enabled": False,
                "next_step": {"PASS": "LIVE_ELIGIBLE_REVIEW (human review of a separate live-execution phase; nothing is enabled)",
                              "CONTINUE_COLLECTING_DATA": "keep the experiment running unchanged",
                              "FAIL_STRATEGY": "stop; start a new experiment version with a changed strategy",
                              "FAIL_SAFETY": "fix the safety defect; results under this experiment are not promotable",
                              "FAIL_RELIABILITY": "fix reliability; rerun or extend the campaign",
                              "INVALID_EXPERIMENT": "start a new experiment; this one's treatment is not well defined"}[status]}

    if not identity.get("manifest_version"):
        blockers.append("NO_FROZEN_MANIFEST")
        return result("INVALID_EXPERIMENT", ["the experiment has no frozen manifest"])
    if identity.get("manifest_drift"):
        blockers.append("UNVERSIONED_MATERIAL_CHANGE")
        return result("INVALID_EXPERIMENT", [f"material configuration changed without a new version: {', '.join(identity['manifest_drift'][:8])}"])
    if safety.get("live_execution_enabled"):
        blockers.append("LIVE_EXECUTION_ENABLED")
    if not safety.get("reconciliation_ok"):
        blockers.append("RECONCILIATION_FAILURE")
    if safety.get("duplicate_cycles"):
        blockers.append("DUPLICATE_LOGICAL_EXECUTION")
    if safety.get("secret_findings"):
        blockers.append("SECRET_LEAK")
    if safety.get("open_critical_incidents", 0) > criteria["max_open_critical_incidents"]:
        blockers.append("OPEN_CRITICAL_INCIDENT")
    if safety.get("database_ok") is False:
        blockers.append("DATABASE_INTEGRITY")
    if blockers:
        return result("FAIL_SAFETY", ["unresolved safety/accounting findings block promotion regardless of PnL"])
    ratio = reliability.get("skipped_slot_ratio")
    if isinstance(ratio, (int, float)) and ratio > criteria["max_skipped_slot_ratio"]:
        blockers.append("MISSED_CYCLES")
        return result("FAIL_RELIABILITY", [f"{ratio:.1%} of scheduled slots were missed (max {criteria['max_skipped_slot_ratio']:.1%})"])
    reasons: list[str] = []
    days, trades = identity["elapsed_days"], economics.get("completed_trades", 0)
    if days < criteria["min_days"]:
        reasons.append(f"elapsed {days:.1f} of {criteria['min_days']} days")
    if trades < criteria["min_completed_trades"]:
        reasons.append(f"{trades} of {criteria['min_completed_trades']} completed trades")
    if coverage.get("regimes_observed", 0) < criteria["min_regimes_observed"]:
        reasons.append(f"{coverage.get('regimes_observed', 0)} market regime(s) observed; robustness is not established")
    if identity.get("checkpoint") in {"PRE_DAY_7", "DAY_7"}:
        reasons.append("day-7 checkpoint judges correctness and reliability only, never profitability")
    if reasons:
        return result("CONTINUE_COLLECTING_DATA", reasons)
    failures = []
    net = economics.get("net_economic_pnl_usdt")
    if criteria["require_positive_net_economic_pnl"] and (net is None or net <= 0):
        failures.append("net economic PnL after AI cost is not positive" if net is not None else "net economic PnL is unavailable (unknown AI cost/FX)")
    pf = economics.get("profit_factor")
    if pf is None or pf < criteria["min_profit_factor"]:
        failures.append(f"profit factor {pf if pf is not None else 'unavailable'} below {criteria['min_profit_factor']}")
    dd = economics.get("max_drawdown")
    if dd is None or dd > criteria["max_drawdown"]:
        failures.append(f"max drawdown {dd if dd is not None else 'unavailable'} above {criteria['max_drawdown']}")
    share = economics.get("top_trade_share")
    if share is not None and share > criteria["max_top_trade_share"]:
        failures.append(f"one trade contributes {share:.0%} of net PnL")
    if failures:
        blockers.extend(["STRATEGY_" + str(i + 1) for i in range(len(failures))])
        return result("FAIL_STRATEGY", failures)
    return result("PASS", ["all correctness, reliability, sample, and economic criteria met on this manifest version"])


class ExperimentGovernance:
    """Manifest freezing/versioning, checkpoint reports, and gate reviews for the runtime's
    experiment. Read-mostly; it never changes trading state and never enables live execution."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.store = runtime.store

    # ------------------------------------------------------------ manifests
    def current_manifest(self) -> dict[str, Any]:
        from .ai_cost import BUDGET_POLICY_VERSION
        from .execution_safety import PLAN_SCHEMA_VERSION, SAFETY_SCHEMA_VERSION
        from .paper_contracts import ESCALATION_POLICY_VERSION
        from .spot_lifecycle import POLICY_VERSION as LIFECYCLE_POLICY_VERSION

        experiment = self.store.experiment()
        book = [{k: row.get(k) for k in ("provider_kind", "model", "version", "effective_from")}
                for row in self.store.cost_ledger.price_book()]
        return build_manifest(
            config=experiment["config"], settings=self.runtime.portfolio.settings(), providers=self.store.list_providers(),
            price_book_version=canonical_hash(book),
            policy_versions={"escalation": ESCALATION_POLICY_VERSION, "budget": BUDGET_POLICY_VERSION,
                             "execution_plan": PLAN_SCHEMA_VERSION, "market_safety": SAFETY_SCHEMA_VERSION,
                             "spot_lifecycle": LIFECYCLE_POLICY_VERSION, "promotion_gate": GATE_POLICY_VERSION},
        )

    def manifests(self) -> list[dict[str, Any]]:
        rows = self.store._query("SELECT * FROM experiment_manifests WHERE experiment_id=? ORDER BY version",
                                 (self.store.experiment()["experiment_id"],))
        return [{**{k: row[k] for k in row.keys() if k != "manifest_json"}, "manifest": json.loads(row["manifest_json"])} for row in rows]

    def frozen(self) -> dict[str, Any] | None:
        manifests = self.manifests()
        return manifests[-1] if manifests else None

    def freeze(self, *, reason: str, now: datetime | None = None) -> dict[str, Any]:
        """Record a new manifest version from the live configuration (idempotent when unchanged)."""

        experiment_id = self.store.experiment()["experiment_id"]
        manifest = self.current_manifest()
        latest = self.frozen()
        if latest and latest["material_sha256"] == manifest["material_sha256"]:
            return latest
        version = (latest["version"] + 1) if latest else 1
        now = now or self.runtime._clock()
        with self.store.transaction() as db:
            db.execute("INSERT INTO experiment_manifests(manifest_id, experiment_id, version, material_sha256, manifest_json, reason, frozen_at) "
                       "VALUES(?, ?, ?, ?, ?, ?, ?)",
                       (f"mf-{uuid.uuid4().hex[:16]}", experiment_id, version, manifest["material_sha256"],
                        json.dumps(manifest, sort_keys=True, default=str), reason[:300], iso_utc(now)))
            db.execute("INSERT INTO events(experiment_id, event_type, payload_json, created_at) VALUES(?, 'manifest_frozen', ?, ?)",
                       (experiment_id, json.dumps({"version": version, "material_sha256": manifest["material_sha256"], "reason": reason[:300],
                                                   "changed": diff_material(latest["manifest"]["material"], manifest["material"]) if latest else []}),
                        iso_utc(now)))
        return self.frozen()

    def ensure_frozen(self) -> dict[str, Any]:
        return self.frozen() or self.freeze(reason="experiment started")

    def drift(self) -> list[str]:
        latest = self.frozen()
        if not latest:
            return []
        return diff_material(latest["manifest"]["material"], self.current_manifest()["material"])

    def status(self) -> dict[str, Any]:
        latest = self.frozen()
        current = self.current_manifest()
        return {"frozen": latest is not None, "version": latest["version"] if latest else None,
                "material_sha256": latest["material_sha256"] if latest else None, "frozen_at": latest["frozen_at"] if latest else None,
                "current_sha256": current["material_sha256"], "drift": self.drift(), "versions": len(self.manifests())}

    # ------------------------------------------------------------ reports
    def checkpoint_report(self, *, as_of: datetime | None = None) -> dict[str, Any]:
        from .execution_safety import reconcile_ledgers
        from .resilience import database_health, scan_for_secrets

        as_of = (as_of or self.runtime._clock()).astimezone(timezone.utc)
        experiment = self.store.experiment()
        experiment_id, config = experiment["experiment_id"], experiment["config"]
        latest = self.frozen()
        start = latest["frozen_at"] if latest else None
        window = start or iso_utc(as_of)
        elapsed = (as_of - parse_utc(start, "frozen_at")).total_seconds() / 86_400 if start else 0.0
        q = self.store._query

        perp = [dict(r) for r in q(
            "SELECT symbol, realized_pnl, entry_fee, exit_fees, funding_paid, slippage_paid, market_regime, closed_at FROM positions "
            "WHERE experiment_id=? AND cohort='primary' AND status='closed' AND closed_pnl_recorded AND closed_at>=? AND closed_at<=?",
            (experiment_id, window, iso_utc(as_of)))]
        spot = [dict(r) for r in q(
            "SELECT symbol, realized_pnl, fees_paid, slippage_paid, closed_at FROM spot_holdings "
            "WHERE experiment_id=? AND status='closed' AND closed_at>=? AND closed_at<=?", (experiment_id, window, iso_utc(as_of)))]
        pnls = [float(p["realized_pnl"] or 0.0) for p in perp] + [float(s["realized_pnl"] or 0.0) for s in spot]
        gains = sum(v for v in pnls if v > 0)
        losses = -sum(v for v in pnls if v < 0)
        net_trading = sum(pnls)
        usage = q("SELECT estimated_cost_usd, cost_status, real_external_call FROM ai_usage_events WHERE experiment_id=? AND started_at>=? AND started_at<=?",
                  (experiment_id, window, iso_utc(as_of)))
        ai_cost = sum(float(r["estimated_cost_usd"] or 0.0) for r in usage)
        unpriced = sum(1 for r in usage if r["real_external_call"] and r["cost_status"] not in {"exact", "estimated"})
        fx = config.get("cost_fx") or {}
        rate = float(fx["usdt_per_usd"]) if isinstance(fx, dict) and fx.get("mode") == "manual" and isinstance(fx.get("usdt_per_usd"), (int, float)) else None
        net_economic = None if (rate is None and ai_cost > 0) or unpriced else net_trading - ai_cost * (rate or 0.0)
        drawdown = q("SELECT MAX(CASE WHEN peak > 0 THEN (peak - total_equity) / peak ELSE 0 END) AS dd FROM ("
                     "SELECT total_equity, MAX(total_equity) OVER (ORDER BY as_of ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak "
                     "FROM portfolio_snapshots WHERE experiment_id=? AND as_of>=? AND as_of<=?)", (experiment_id, window, iso_utc(as_of)))
        ordered = sorted(pnls)

        def pct(p: float) -> float | None:
            return ordered[min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))] if ordered else None

        economics = {
            "completed_trades": len(pnls), "perp_trades": len(perp), "spot_round_trips": len(spot),
            "net_trading_pnl_usdt": net_trading,
            "fees_usdt": sum(float(p["entry_fee"] or 0) + float(p["exit_fees"] or 0) for p in perp) + sum(float(s["fees_paid"] or 0) for s in spot),
            "funding_usdt": sum(float(p["funding_paid"] or 0) for p in perp),
            "slippage_usdt": sum(float(p["slippage_paid"] or 0) for p in perp) + sum(float(s["slippage_paid"] or 0) for s in spot),
            "ai_cost_usd": ai_cost, "ai_unpriced_calls": unpriced, "fx_usdt_per_usd": rate,
            "net_economic_pnl_usdt": net_economic,
            "expectancy_usdt": net_trading / len(pnls) if pnls else None,
            "win_rate": sum(1 for v in pnls if v > 0) / len(pnls) if pnls else None,
            "profit_factor": gains / losses if losses > 0 else None,
            "max_drawdown": float(drawdown[0]["dd"]) if drawdown and drawdown[0]["dd"] is not None else None,
            "return_distribution": {"p10": pct(0.1), "p50": pct(0.5), "p90": pct(0.9), "min": ordered[0] if ordered else None,
                                    "max": ordered[-1] if ordered else None},
            "top_trade_share": (max(pnls) / net_trading) if pnls and net_trading > 0 else None,
            "denominator": len(pnls),
        }
        reconciliation = reconcile_ledgers(self.store, experiment_id)
        duplicates = q("SELECT symbol, cycle_slot, COUNT(*) AS n FROM cycles WHERE experiment_id=? GROUP BY symbol, cycle_slot HAVING n > 1",
                       (experiment_id,))
        incidents = self.runtime.resilience.incidents(limit=100_000)
        in_window = [i for i in incidents if i["opened_at"] >= window]
        with self.store._lock:
            secrets = scan_for_secrets(self.store._db)
        safety_counts = {row["code"]: row["n"] for row in q(
            "SELECT code, COUNT(*) AS n FROM safety_events WHERE experiment_id=? AND created_at>=? GROUP BY code", (experiment_id, window))}
        safety = {
            "live_execution_enabled": False,  # no code path exists; Gate writes use DisabledLiveExecutionAdapter
            "reconciliation_ok": reconciliation["ok"], "reconciliation_problems": reconciliation["problems"][:5],
            "duplicate_cycles": [dict(r) for r in duplicates],
            "secret_findings": secrets,
            "open_critical_incidents": sum(1 for i in incidents if i["status"] == "OPEN" and i["severity"] == "CRITICAL"),
            "database_ok": database_health(self.store)["ok"],
            "kill_switch": self.runtime.portfolio.kill_switch()["level"],
            "safety_event_counts": safety_counts,
        }
        slots = {row["status"]: row["n"] for row in q(
            "SELECT status, COUNT(*) AS n FROM scheduler_slots WHERE experiment_id=? AND slot>=? AND slot<=? GROUP BY status",
            (experiment_id, window, iso_utc(as_of)))}
        scheduled = sum(slots.values())
        kinds: dict[str, int] = {}
        for incident in in_window:
            kinds[incident["kind"]] = kinds.get(incident["kind"], 0) + 1
        reliability = {"scheduler_slots": slots, "skipped_slot_ratio": (slots.get("SKIPPED_GAP", 0) / scheduled) if scheduled else None,
                       "incidents_by_kind": kinds, "restarts": kinds.get("PROCESS_RESTART", 0),
                       "provider_outages": kinds.get("PROVIDER_OUTAGE", 0), "feed_gaps": kinds.get("FEED_GAP", 0) + kinds.get("FEED_STALE", 0)}
        regimes: dict[str, int] = {}
        for p in perp:
            key = p.get("market_regime") or "unknown"
            regimes[key] = regimes.get(key, 0) + 1
        assets: dict[str, int] = {}
        for item in perp + spot:
            assets[item["symbol"]] = assets.get(item["symbol"], 0) + 1
        coverage = {"regimes": regimes, "regimes_observed": len([k for k in regimes if k != "unknown"]), "assets": assets}
        try:
            tournament = self.runtime.portfolio.tournament()
            ai_value = {"window": "WHOLE_EXPERIMENT" if (latest or {}).get("version", 1) == 1 else "WHOLE_EXPERIMENT_MIXED_VERSIONS",
                        "aligned_cycles": tournament.get("aligned_cycles"), "fx": tournament.get("fx"),
                        "arms": [{k: arm.get(k) for k in ("arm", "closed_trades", "net_trading_pnl_usdt", "ai_cost_usd", "economic_pnl_usdt",
                                                          "profit_factor", "max_drawdown", "incremental_vs_quant", "promotion_status")}
                                 for arm in tournament.get("arms", [])],
                        "causal_claims": tournament.get("causal_claims")}
        except Exception as exc:  # unavailable is reported, never zero-filled
            ai_value = {"available": False, "reason": str(exc)[:200]}
        lifecycle_counts = {f"{row['action']}:{row['status']}": row["n"] for row in q(
            "SELECT action, status, COUNT(*) AS n FROM lifecycle_events WHERE experiment_id=? AND created_at>=? GROUP BY action, status",
            (experiment_id, window))}
        benchmarks = [json.loads(row["report_json"]) for row in q(
            "SELECT report_json FROM lifecycle_benchmarks WHERE experiment_id=? ORDER BY created_at DESC LIMIT 5", (experiment_id,))]
        lifecycle = {"event_counts": lifecycle_counts,
                     "benchmarks": [{"symbol": b.get("symbol"), "from": b.get("from"), "to": b.get("to"), "claim": b.get("claim"),
                                     "arms": [{k: a.get(k) for k in ("arm", "status", "total_return", "max_drawdown", "peak_capture_ratio",
                                                                     "profit_giveback", "turnover")} for a in b.get("arms", [])]}
                                    for b in benchmarks]}
        identity = {"experiment_id": experiment_id, "manifest_version": latest["version"] if latest else None,
                    "material_sha256": latest["material_sha256"] if latest else None, "frozen_at": start,
                    "manifest_drift": self.drift(), "as_of": iso_utc(as_of), "elapsed_days": round(elapsed, 4),
                    "checkpoint": checkpoint_label(elapsed), "starting_capital_usdt": {
                        "perpetual": float(config["starting_balance_usdt"]),
                        "spot": float(self.runtime.portfolio.settings()["spot_starting_balance_usdt"])}}
        report = {"schema_version": CHECKPOINT_SCHEMA_VERSION, "identity": identity, "safety": safety, "reliability": reliability,
                  "economics": economics, "ai_incremental_value": ai_value, "spot_lifecycle": lifecycle, "coverage": coverage}
        report["report_sha256"] = canonical_hash(report)
        return report

    def review(self, *, as_of: datetime | None = None) -> dict[str, Any]:
        """Run a checkpoint review, persist it (audited), and return report + gate result."""

        criteria = validate_criteria(self.runtime.portfolio.settings().get("promotion") or {})
        report = self.checkpoint_report(as_of=as_of)
        gate = evaluate_gate(report, criteria)
        review_id = f"pr-{uuid.uuid4().hex[:16]}"
        with self.store.transaction() as db:
            db.execute("INSERT INTO promotion_reviews(review_id, experiment_id, manifest_version, checkpoint, status, report_sha256, report_json, created_at) "
                       "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                       (review_id, report["identity"]["experiment_id"], report["identity"]["manifest_version"], report["identity"]["checkpoint"],
                        gate["status"], report["report_sha256"], json.dumps({"report": report, "gate": gate}, sort_keys=True, default=str),
                        iso_utc(self.runtime._clock())))
        return {"review_id": review_id, "report": report, "gate": gate}

    def campaign_summary(self) -> dict[str, Any]:
        """Light campaign progress for the Overview (cheap enough to poll; no secret scan)."""

        now = self.runtime._clock().astimezone(timezone.utc)
        experiment = self.store.experiment()
        experiment_id, config = experiment["experiment_id"], experiment["config"]
        latest = self.frozen()
        start = latest["frozen_at"] if latest else None
        elapsed = (now - parse_utc(start, "frozen_at")).total_seconds() / 86_400 if start else 0.0
        criteria = validate_criteria(self.runtime.portfolio.settings().get("promotion") or {})
        window = start or iso_utc(now)
        perp = self.store._query("SELECT COUNT(*) AS n FROM positions WHERE experiment_id=? AND cohort='primary' AND status='closed' "
                                 "AND closed_pnl_recorded AND closed_at>=?", (experiment_id, window))[0]["n"]
        spot = self.store._query("SELECT COUNT(*) AS n FROM spot_holdings WHERE experiment_id=? AND status='closed' AND closed_at>=?",
                                 (experiment_id, window))[0]["n"]
        next_checkpoint = next(((name, day) for name, day in CHECKPOINTS if elapsed < day), None)
        fx = config.get("cost_fx") or {}
        try:
            budget = self.store.cost_ledger.budget_status(experiment_id, config["ai_budget"])
            budget_view = {k: budget.get(k) for k in ("spent_experiment_usd", "remaining_experiment_usd", "spent_today_usd",
                                                      "remaining_today_usd", "exhausted", "limit_action")}
            budget_view["experiment_cap_usd"] = config["ai_budget"].get("experiment_usd")
        except Exception:
            budget_view = None
        incidents = self.runtime.resilience.incidents(status="OPEN", limit=50)
        return {
            "experiment_id": experiment_id, "status": experiment["status"],
            "manifest_version": latest["version"] if latest else None, "frozen_at": start,
            "material_sha256": latest["material_sha256"] if latest else None, "drift": bool(self.drift()) if latest else False,
            "elapsed_days": round(elapsed, 2), "checkpoint": checkpoint_label(elapsed),
            "next_checkpoint": None if next_checkpoint is None else {
                "name": next_checkpoint[0],
                "due_at": iso_utc(parse_utc(start, "frozen_at") + timedelta(days=next_checkpoint[1])) if start else None},
            "completed_trades": perp + spot, "target_trades": criteria["min_completed_trades"], "min_days": criteria["min_days"],
            "capital_usdt": {"perpetual": float(config["starting_balance_usdt"]),
                             "spot": float(self.runtime.portfolio.settings()["spot_starting_balance_usdt"])},
            "fx": {"configured": fx.get("mode") == "manual" and fx.get("usdt_per_usd") is not None, "usdt_per_usd": fx.get("usdt_per_usd")},
            "ai_budget": budget_view,
            "risk_incidents": [{"kind": i["kind"], "severity": i["severity"], "summary": i["summary"]}
                               for i in incidents if i["kind"] in {"RISK_PAUSE", "RISK_HALT"}],
        }

    def reviews(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.store._query("SELECT review_id, checkpoint, status, report_sha256, manifest_version, created_at, report_json "
                                 "FROM promotion_reviews WHERE experiment_id=? ORDER BY created_at DESC LIMIT ?",
                                 (self.store.experiment()["experiment_id"], limit))
        result = []
        for row in rows:
            payload = json.loads(row["report_json"])
            result.append({**{k: row[k] for k in row.keys() if k != "report_json"}, "blockers": payload["gate"]["blockers"],
                           "reasons": payload["gate"]["reasons"]})
        return result
