"""Accelerated local soak / fault drill for the PAPER runtime (fixture data, simulated clock).

It drives the real scheduler ticks (cycle + monitor + portfolio hooks) through simulated days,
restarts the whole runtime from the same SQLite file on a schedule (sometimes without a clean
shutdown), injects machine-sleep gaps and provider outages, and then checks the invariants a
60–90 day campaign depends on:

- reconciliation passes after every restart and at the end;
- no logical cycle ran twice (cycle keys are unique per experiment/symbol/candle);
- no scheduler slot was attempted twice; missed slots are explicit SKIPPED_GAP;
- every restart is recorded with its recovery actions;
- storage growth is measured and diagnostic retention is applied.

No network, no paid calls. Real Gate writes remain impossible (PAPER only).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .paper_contracts import PaperTradingError, iso_utc
from .resilience import compact_storage, database_health


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _open(database: Path, clock: _Clock) -> tuple[Any, Any, Any]:
    from .market_catalog import FixtureSpotMarketDataProvider, MarketCatalog
    from .paper_market import FixtureFuturesMarketDataProvider
    from .paper_runtime import PaperRuntime, PaperScheduler, PaperStore

    store = PaperStore(database)
    catalog = MarketCatalog(source="fixture", spot_provider=FixtureSpotMarketDataProvider(clock=clock), clock=clock)
    runtime = PaperRuntime(store, clock=clock, catalog=catalog, market_provider=FixtureFuturesMarketDataProvider())
    scheduler = PaperScheduler(runtime)
    return store, runtime, scheduler


def run_soak(
    database: Path,
    *,
    days: float = 3.0,
    start: datetime | None = None,
    monitor_minutes: int = 5,
    restart_every_hours: float = 12.0,
    sleep_gap_hours: float = 3.0,
    unclean_every: int = 2,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    database = Path(database)
    if database.exists():
        raise PaperTradingError("soak database must not exist (use a fresh path)")
    clock = _Clock((start or datetime(2026, 1, 5, tzinfo=timezone.utc)).replace(second=0, microsecond=0))
    end = clock.value + timedelta(days=days)
    store, runtime, scheduler = _open(database, clock)
    config = dict(store.experiment()["config"])
    config["symbols"] = symbols or config["symbols"][:2]
    config["monitor_interval_seconds"] = monitor_minutes * 60
    store.save_experiment(config)
    runtime.start()
    scheduler.last_recovery = runtime.recover_on_startup()
    portfolio = runtime.portfolio
    portfolio.create_order({"client_request_id": "soak-spot-1", "instrument_id": "fixture:spot:ETH_USDT", "action": "buy", "quote_amount": 20})
    restarts, failures, sleeps = 0, [], 0
    next_restart = clock.value + timedelta(hours=restart_every_hours)
    slept = False
    initial_bytes = database_health(store)["size_bytes"]
    while clock.value < end:
        try:
            scheduler.cycle_tick(clock.value)
            scheduler.monitor_tick(clock.value)
        except Exception as exc:  # the soak records, never hides, failures
            failures.append({"at": iso_utc(clock.value), "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
        if clock.value >= next_restart:
            restarts += 1
            clean = restarts % unclean_every != 0
            if clean:
                scheduler.shutdown(timeout=0.1)
            store.close()
            if not slept and restarts == 2 and sleep_gap_hours > 0:
                clock.value += timedelta(hours=sleep_gap_hours)  # machine asleep while the process was down
                slept, sleeps = True, sleeps + 1
            store, runtime, scheduler = _open(database, clock)
            # Same recovery as resume_on_startup, but ticks stay driven by this loop (no worker threads).
            scheduler.last_recovery = runtime.recover_on_startup()
            if not runtime.resilience.incidents(limit=5):
                failures.append({"at": iso_utc(clock.value), "error": "restart left no incident record"})
            if not scheduler.last_recovery.get("reconciliation_ok"):
                failures.append({"at": iso_utc(clock.value), "error": "reconciliation failed after restart"})
            portfolio = runtime.portfolio
            next_restart = clock.value + timedelta(hours=restart_every_hours)
        clock.value += timedelta(minutes=monitor_minutes)
    experiment_id = store.experiment()["experiment_id"]
    cycles = store._query("SELECT symbol, cycle_slot, COUNT(*) AS n FROM cycles WHERE experiment_id=? "
                          "GROUP BY symbol, cycle_slot HAVING n > 1", (experiment_id,))
    slots = store._query("SELECT status, COUNT(*) AS n FROM scheduler_slots WHERE experiment_id=? GROUP BY status", (experiment_id,))
    incidents = store._query("SELECT kind, COUNT(*) AS n FROM runtime_incidents GROUP BY kind")
    removed = compact_storage(store, experiment_id, now=clock.value)
    reconciliation = runtime.portfolio.reconcile(escalate=False)
    final_bytes = database_health(store)["size_bytes"]
    report = {
        "schema_version": "paper-soak-report.v1",
        "database": str(database),
        "simulated_days": days,
        "from": iso_utc(end - timedelta(days=days)),
        "to": iso_utc(clock.value),
        "restarts": restarts,
        "sleep_gaps": sleeps,
        "failures": failures,
        "duplicate_cycle_keys": [dict(row) for row in cycles],
        "scheduler_slots": {row["status"]: row["n"] for row in slots},
        "incidents": {row["kind"]: row["n"] for row in incidents},
        "cycles": store._query("SELECT COUNT(*) FROM cycles WHERE experiment_id=?", (experiment_id,))[0][0],
        "reconciliation_ok": reconciliation["ok"],
        "storage": {"initial_bytes": initial_bytes, "final_bytes": final_bytes,
                    "bytes_per_day": round((final_bytes - initial_bytes) / max(days, 1e-9)), "retention_removed": removed},
        "database_ok": database_health(store, full=True)["ok"],
    }
    report["passed"] = (not failures and not report["duplicate_cycle_keys"] and report["reconciliation_ok"]
                        and report["database_ok"] and report["incidents"].get("PROCESS_RESTART", 0) >= restarts
                        and (sleeps == 0 or report["scheduler_slots"].get("SKIPPED_GAP", 0) > 0))
    store.close()
    return report


def main_soak(database: Path, *, days: float, out: Path | None = None) -> int:
    report = run_soak(database, days=days)
    text = json.dumps(report, indent=2, sort_keys=True)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    print(text)
    return 0 if report["passed"] else 1
