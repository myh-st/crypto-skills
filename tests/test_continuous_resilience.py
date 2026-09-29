"""Continuous PAPER resilience drills: restart recovery, scheduler gaps, feed state, storage,
provider circuits, backup/restore, instance lock, and an accelerated soak. Fixture data only."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crypto_eval.market_catalog import FixtureSpotMarketDataProvider, MarketCatalog
from crypto_eval.paper_ai import AIProviderError, JevAdapter
from crypto_eval.paper_contracts import PaperTradingError, iso_utc
from crypto_eval.paper_market import FixtureFuturesMarketDataProvider
from crypto_eval.paper_runtime import PaperRuntime, PaperScheduler, PaperStore
from crypto_eval.resilience import (
    DB_SCHEMA_VERSION,
    InstanceLock,
    backup_database,
    compact_storage,
    missed_slots,
    restore_database,
)
from crypto_eval.soak import run_soak

from tests.test_portfolio_os import NOW, Clock

Usage = namedtuple("Usage", "total used free")
LOW_DISK = lambda _path: Usage(10 * 2**30, 10 * 2**30 - 2**20, 2**20)  # 1 MB free
PLENTY = lambda _path: Usage(100 * 2**30, 2**30, 99 * 2**30)


class ResilienceCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "paper.sqlite3"
        self.clock = Clock(NOW)
        self.open()

    def tearDown(self) -> None:
        try:
            self.store.close()
        except Exception:
            pass

    def open(self) -> None:
        self.store = PaperStore(self.db)
        catalog = MarketCatalog(source="fixture", spot_provider=FixtureSpotMarketDataProvider(clock=self.clock), clock=self.clock)
        self.runtime = PaperRuntime(self.store, clock=self.clock, catalog=catalog, market_provider=FixtureFuturesMarketDataProvider())
        self.scheduler = PaperScheduler(self.runtime)
        self.os = self.runtime.portfolio

    def restart(self, *, clean: bool = False) -> dict:
        if clean:
            self.scheduler.shutdown(timeout=0.1)
        self.store.close()
        self.open()
        return self.runtime.recover_on_startup(disk_usage=PLENTY)

    def open_perp(self):
        quote = self.os.quote("fixture:perpetual:BTC_USDT")
        ask = quote["best_ask"]
        self.perp_request = {"client_request_id": "perp-req-0001", "instrument_id": "fixture:perpetual:BTC_USDT", "action": "long",
                             "risk_pct": 0.01, "stop_price": ask * 0.98, "targets": [ask * 1.02], "leverage": 3}
        return self.os.create_order(dict(self.perp_request))


class RestartDrills(ResilienceCase):
    def test_schema_version_is_recorded(self):
        self.assertEqual(self.store._query("PRAGMA user_version")[0][0], DB_SCHEMA_VERSION)
        self.assertEqual(self.store._query("PRAGMA journal_mode")[0][0], "wal")

    def test_kill_with_open_perp_recovers_same_state(self):
        order = self.open_perp()
        before = self.os.list_positions()
        wallet = self.store.wallet_summary("EXP-001", "primary")
        recovery = self.restart(clean=False)
        self.assertTrue(recovery["reconciliation_ok"])
        self.assertEqual([p["position_ref"] for p in self.os.list_positions()], [p["position_ref"] for p in before])
        self.assertEqual(self.store.wallet_summary("EXP-001", "primary")["cash_balance"], wallet["cash_balance"])
        kinds = [i["kind"] for i in self.runtime.resilience.incidents()]
        self.assertIn("PROCESS_RESTART", kinds)
        replay = self.os.create_order(dict(self.perp_request))  # retry after restart replays, never duplicates
        self.assertEqual(replay["position_ref"], order["position_ref"])
        self.assertEqual(len(self.os.list_positions()), 1)

    def test_unclean_vs_clean_shutdown_is_recorded(self):
        self.runtime.recover_on_startup(disk_usage=PLENTY)
        self.restart(clean=False)
        self.assertIn("UNCLEAN_SHUTDOWN", [i["kind"] for i in self.runtime.resilience.incidents()])
        self.restart(clean=True)
        self.assertIn("GRACEFUL_SHUTDOWN", [i["kind"] for i in self.runtime.resilience.incidents()])
        self.assertEqual(sum(i["kind"] == "UNCLEAN_SHUTDOWN" for i in self.runtime.resilience.incidents()), 1)

    def test_spot_holding_and_pending_limit_survive_restart_without_duplicate_fill(self):
        self.os.create_order({"client_request_id": "spot-req-0001", "instrument_id": "fixture:spot:ETH_USDT", "action": "buy", "quote_amount": 20})
        mark = self.os.quote("fixture:spot:ETH_USDT")["best_bid"]
        self.os.create_order({"client_request_id": "limit-req-0001", "instrument_id": "fixture:spot:ETH_USDT", "action": "buy",
                              "order_type": "limit", "limit_price": round(mark * 0.8, 2), "quote_amount": 10})
        fills_before = self.store._query("SELECT COUNT(*) FROM spot_fills")[0][0]
        self.restart(clean=False)
        for _ in range(2):
            self.os.after_monitor(now=self.clock())
        self.assertEqual(self.store._query("SELECT COUNT(*) FROM spot_fills")[0][0], fills_before)
        pending = [o for o in self.os.list_orders(status="open")]
        self.assertEqual(len(pending), 1)
        replay = self.os.create_order({"client_request_id": "limit-req-0001", "instrument_id": "fixture:spot:ETH_USDT", "action": "buy",
                                       "order_type": "limit", "limit_price": round(mark * 0.8, 2), "quote_amount": 10})
        self.assertEqual(replay["order_ref"], pending[0]["order_ref"])
        self.assertEqual(len(self.os.list_orders(status="open")), 1)

    def test_restart_during_ai_request_never_repeats_the_call(self):
        self.runtime.start()
        now = NOW.replace(minute=(NOW.minute // 15) * 15, second=0, microsecond=0) + timedelta(minutes=2)
        self.clock.value = now
        slot = iso_utc(now.replace(minute=(now.minute // 15) * 15))
        cycle_id = f"EXP-001:BTCUSDT:{int(datetime.fromisoformat(slot.replace('Z', '+00:00')).timestamp())}"
        with self.store.transaction() as db:
            db.execute("INSERT INTO cycles(cycle_id, experiment_id, symbol, cycle_slot, status, payload_json, created_at, updated_at) "
                       "VALUES(?, 'EXP-001', 'BTCUSDT', ?, 'processing', '{}', ?, ?)", (cycle_id, slot, slot, slot))
        usage_before = self.store._query("SELECT COUNT(*) FROM ai_usage_events")[0][0]
        recovery = self.restart()
        self.assertEqual(recovery["interrupted_cycles"], 1)
        self.assertEqual(self.store.cycle(cycle_id)["status"], "interrupted")
        self.runtime.run_cycle("BTCUSDT", as_of=now)
        self.assertEqual(self.store.cycle(cycle_id)["status"], "interrupted")
        self.assertEqual(self.store._query("SELECT COUNT(*) FROM ai_usage_events")[0][0], usage_before)

    def test_restart_during_sliced_execution_marks_plan_interrupted(self):
        self.os.create_order({"client_request_id": "spot-req-0002", "instrument_id": "fixture:spot:ETH_USDT", "action": "buy", "quote_amount": 20})
        with self.store.transaction() as db:
            db.execute("INSERT INTO execution_plans(plan_id, experiment_id, idempotency_key, instrument_id, position_ref, action, source, "
                       "decision, status, plan_json, result_json, created_at, updated_at) VALUES('xp-test', 'EXP-001', NULL, "
                       "'fixture:spot:ETH_USDT', 'spot:x', 'reduce', 'AI', 'SLICE', 'executing', '{}', '{}', ?, ?)", (iso_utc(NOW), iso_utc(NOW)))
        recovery = self.restart()
        self.assertEqual(recovery["interrupted_plans"], 1)
        self.assertEqual(self.store._query("SELECT status FROM execution_plans WHERE plan_id='xp-test'")[0][0], "interrupted")
        codes = [e["code"] for e in self.os.safety.events("EXP-001")]
        self.assertIn("EXECUTION_DEFERRED", codes)

    def test_stale_ai_reservation_is_kept_conservative_after_restart(self):
        with self.store.transaction() as db:
            db.execute("INSERT INTO ai_budget_reservations(reservation_id, experiment_id, cycle_id, provider_id, provider_kind, call_type, "
                       "reserved_usd, status, created_at) VALUES('rsv-x', 'EXP-001', NULL, 'p', 'responses', 'gpt_decision', 0.5, 'reserved', ?)",
                       (iso_utc(NOW),))
        recovery = self.restart()
        self.assertEqual(recovery["stale_ai_reservations_kept_conservative"], 1)
        self.assertEqual(self.store._query("SELECT status FROM ai_budget_reservations WHERE reservation_id='rsv-x'")[0][0], "reconciled")

    def test_low_disk_fails_closed_to_no_new_entries(self):
        self.store.close()
        self.open()
        recovery = self.runtime.recover_on_startup(disk_usage=LOW_DISK)
        self.assertFalse(recovery["storage_ok"])
        self.assertEqual(self.os.kill_switch()["level"], "NO_NEW_ENTRIES")
        with self.assertRaisesRegex(PaperTradingError, "KILL_SWITCH"):
            self.os.create_order({"instrument_id": "fixture:spot:ETH_USDT", "action": "buy", "quote_amount": 10})
        self.assertEqual(self.runtime.health(disk_usage=LOW_DISK)["components"]["storage"]["status"], "LOW")

    def test_sqlite_lock_contention_waits_instead_of_failing(self):
        other = sqlite3.connect(self.db, timeout=5, isolation_level=None, check_same_thread=False)
        other.execute("BEGIN IMMEDIATE")
        release = threading.Timer(0.4, lambda: (other.execute("COMMIT"), other.close()))
        release.start()
        started = time.monotonic()
        self.runtime.resilience.heartbeat("contention")
        self.assertGreaterEqual(time.monotonic() - started, 0.3)
        self.assertIn("contention", self.runtime.resilience.heartbeats())
        release.join()


class SchedulerDrills(ResilienceCase):
    def setUp(self):
        super().setUp()
        config = dict(self.store.experiment()["config"], symbols=["BTCUSDT"])
        self.store.save_experiment(config)
        self.runtime.start()
        self.base = NOW.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=2)

    def test_missed_slots_math(self):
        self.assertEqual(missed_slots("2026-01-01T00:00:00.000Z", "2026-01-01T01:00:00.000Z"),
                         ["2026-01-01T00:15:00.000Z", "2026-01-01T00:30:00.000Z", "2026-01-01T00:45:00.000Z"])
        self.assertEqual(missed_slots(None, "2026-01-01T01:00:00.000Z"), [])

    def test_sleep_gap_is_explicit_and_never_backfilled(self):
        self.clock.value = self.base
        self.assertEqual(self.scheduler.cycle_tick(self.base)["status"], "ran")
        self.assertEqual(self.scheduler.cycle_tick(self.base)["status"], "already_done")
        later = self.base + timedelta(hours=3)
        self.clock.value = later
        result = self.scheduler.cycle_tick(later)
        self.assertEqual(result["missed"], 11)
        skipped = self.store._query("SELECT COUNT(*) FROM scheduler_slots WHERE status='SKIPPED_GAP'")[0][0]
        self.assertEqual(skipped, 11)
        self.assertEqual(self.store._query("SELECT COUNT(*) FROM cycles")[0][0], 2)  # no invented cycles
        gap = [i for i in self.runtime.resilience.incidents() if i["kind"] == "SCHEDULER_GAP"]
        self.assertEqual(gap[0]["detail"]["count"], 11)

    def test_late_slot_records_lag(self):
        late = self.base + timedelta(minutes=12)
        self.clock.value = late
        self.scheduler.cycle_tick(late)
        self.assertIn("SCHEDULER_LAG", [i["kind"] for i in self.runtime.resilience.incidents()])

    def test_interrupted_slot_is_resumed_after_restart_without_duplicates(self):
        config = dict(self.store.experiment()["config"], symbols=["BTCUSDT", "ETHUSDT"])
        self.runtime.stop()
        self.store.save_experiment(config)
        self.runtime.start()
        self.clock.value = self.base
        original = self.runtime.run_cycle

        def stop_after_first(symbol, **kwargs):
            result = original(symbol, **kwargs)
            self.scheduler._shutdown.set()
            return result

        self.runtime.run_cycle = stop_after_first
        self.assertEqual(self.scheduler.cycle_tick(self.base)["status"], "interrupted")
        self.restart()
        self.runtime.start() if self.store.experiment()["status"] != "running" else None
        self.assertEqual(self.scheduler.cycle_tick(self.base)["status"], "ran")
        rows = self.store._query("SELECT symbol, COUNT(*) AS n FROM cycles GROUP BY symbol")
        self.assertEqual({row["symbol"]: row["n"] for row in rows}, {"BTCUSDT": 1, "ETHUSDT": 1})

    def test_halt_and_idle_do_not_run(self):
        self.os.set_kill_switch("FULL_AUTOMATION_HALT")
        self.clock.value = self.base
        self.assertEqual(self.scheduler.cycle_tick(self.base)["status"], "halted")
        self.runtime.pause()
        self.assertEqual(self.scheduler.cycle_tick(self.base)["status"], "idle")

    def test_monitor_gap_and_feed_incidents(self):
        class FakeStream:
            def __init__(self):
                self.state, self.bars = "reconnecting", 0

            def status(self):
                return {"state": self.state, "reason": "socket closed", "counters": {"gap_fill_bars": self.bars, "reconnects": 1}}

        stream = FakeStream()
        self.runtime.live_stream = stream
        self.clock.value = self.base
        self.scheduler.monitor_tick(self.base)
        self.assertEqual(self.runtime.resilience.incidents(status="OPEN")[0]["kind"], "FEED_STALE")
        stream.state, stream.bars = "live", 7
        later = self.base + timedelta(hours=2)
        self.clock.value = later
        self.scheduler.monitor_tick(later)
        kinds = [i["kind"] for i in self.runtime.resilience.incidents()]
        self.assertIn("MONITOR_GAP", kinds)
        self.assertIn("FEED_GAP", kinds)
        self.assertFalse([i for i in self.runtime.resilience.incidents(status="OPEN") if i["kind"] == "FEED_STALE"])


class ProviderCircuitTests(ResilienceCase):
    def test_circuit_opens_after_failures_and_blocks_before_reservation(self):
        res = self.runtime.resilience
        for _ in range(3):
            res.record_provider("jev-real", ok=False, error="timeout")
        allowed, reason = res.circuit_allows("jev-real")
        self.assertFalse(allowed)
        self.assertIn("PROVIDER_CIRCUIT_OPEN", reason)
        adapter = object.__new__(JevAdapter)
        reservations = self.store._query("SELECT COUNT(*) FROM ai_budget_reservations")[0][0]
        with self.assertRaisesRegex(AIProviderError, "PROVIDER_CIRCUIT_OPEN"):
            self.runtime._paid_call(experiment_id="EXP-001", cycle_id=None, symbol="BTCUSDT", arm="shared:jev",
                                    provider_config={"provider_id": "jev-real", "kind": "typesafe_jev", "model": "jev"},
                                    adapter=adapter, call_type="jev_decision", payload_bytes=10,
                                    budget=self.store.experiment()["config"]["ai_budget"],
                                    invoke=lambda: self.fail("provider must not be called while the circuit is open"))
        self.assertEqual(self.store._query("SELECT COUNT(*) FROM ai_budget_reservations")[0][0], reservations)
        usage = self.store._query("SELECT status, error_code, real_external_call FROM ai_usage_events ORDER BY rowid DESC LIMIT 1")[0]
        self.assertEqual((usage["status"], usage["error_code"]), ("failed", "circuit_open"))
        self.assertIn("PROVIDER_OUTAGE", [i["kind"] for i in res.incidents(status="OPEN")])
        self.assertEqual(self.runtime.health(disk_usage=PLENTY)["components"]["ai_providers"]["status"], "DEGRADED")
        self.clock.value = NOW + timedelta(seconds=601)
        self.assertTrue(res.circuit_allows("jev-real")[0])  # half-open trial
        res.record_provider("jev-real", ok=True)
        self.assertFalse([i for i in res.incidents(status="OPEN") if i["kind"] == "PROVIDER_OUTAGE"])


class StorageTests(ResilienceCase):
    def test_backup_restore_round_trip_and_secret_free(self):
        self.runtime.resolver.store.set("gate_api_secret", "sk-" + "A" * 40) if hasattr(self.runtime.resolver.store, "set") else None
        self.open_perp()
        self.os.create_order({"client_request_id": "spot-req-0003", "instrument_id": "fixture:spot:ETH_USDT", "action": "buy", "quote_amount": 20})
        manifest = backup_database(self.store, self.tmp / "backups", now=NOW)
        self.assertEqual(manifest["secrets_scan"], "clean")
        self.assertFalse(manifest["contains_credentials"])
        backup = self.tmp / "backups" / manifest["file"]
        self.assertNotIn(b"sk-AAAA", backup.read_bytes())
        target = self.tmp / "restored.sqlite3"
        result = restore_database(backup, target)
        self.assertEqual(result["table_counts"], manifest["table_counts"])
        restored = PaperStore(target)
        try:
            self.assertEqual(restored.wallet_summary("EXP-001", "primary")["cash_balance"],
                             self.store.wallet_summary("EXP-001", "primary")["cash_balance"])
            self.assertEqual(restored._query("SELECT COUNT(*) FROM spot_holdings")[0][0], 1)
            self.assertEqual(restored._query("SELECT COUNT(*) FROM positions")[0][0], 1)
        finally:
            restored.close()
        with self.assertRaisesRegex(PaperTradingError, "target database exists"):
            restore_database(backup, target)
        restore_database(backup, target, force=True)
        self.assertTrue(list(self.tmp.glob("restored.sqlite3.pre-restore-*")))

    def test_backup_rejects_credential_like_values_and_tampered_files(self):
        with self.store.transaction() as db:
            db.execute("INSERT INTO runtime_heartbeats(name, detail_json, updated_at) VALUES('leak', ?, ?)",
                       (json.dumps({"note": "Bearer " + "x" * 30}), iso_utc(NOW)))
        with self.assertRaisesRegex(PaperTradingError, "BACKUP_REJECTED"):
            backup_database(self.store, self.tmp / "bk")
        with self.store.transaction() as db:
            db.execute("DELETE FROM runtime_heartbeats WHERE name='leak'")
        manifest = backup_database(self.store, self.tmp / "bk")
        backup = self.tmp / "bk" / manifest["file"]
        with backup.open("ab") as handle:
            handle.write(b"tamper")
        with self.assertRaisesRegex(PaperTradingError, "checksum"):
            restore_database(backup, self.tmp / "x.sqlite3")

    def test_retention_thins_diagnostics_and_compacts_old_snapshots_only(self):
        with self.store.transaction() as db:
            for minute in range(60):
                db.execute("INSERT INTO portfolio_snapshots(experiment_id, as_of, total_equity, perp_equity, spot_equity, open_risk, "
                           "gross_exposure, payload_json) VALUES('EXP-001', ?, 1, 1, 0, 0, 0, '{}')",
                           (iso_utc(NOW - timedelta(days=30) + timedelta(minutes=minute)),))
            payload = {"market_snapshot": {"candles_15m": [{"close_time": "a", "close": 1}], "candles_1m": [{"close_time": "b"}],
                                           "candles_1h": [], "candles_4h": []}, "snapshot_hash": "h1"}
            db.execute("INSERT INTO cycles(cycle_id, experiment_id, symbol, cycle_slot, status, payload_json, created_at, updated_at) "
                       "VALUES('old', 'EXP-001', 'BTCUSDT', ?, 'complete', ?, ?, ?)",
                       (iso_utc(NOW - timedelta(days=40)), json.dumps(payload), iso_utc(NOW), iso_utc(NOW)))
        fills_before = self.store._query("SELECT COUNT(*) FROM fills")[0][0]
        removed = compact_storage(self.store, "EXP-001", now=NOW)
        start = NOW - timedelta(days=30)
        buckets = {(start + timedelta(minutes=m)).replace(minute=((start + timedelta(minutes=m)).minute // 15) * 15, second=0, microsecond=0)
                   for m in range(60)}
        self.assertEqual(removed["portfolio_snapshots"], 60 - len(buckets))  # one per 15 minutes kept
        self.assertEqual(removed["cycle_snapshots_compacted"], 1)
        cycle = json.loads(self.store._query("SELECT payload_json FROM cycles WHERE cycle_id='old'")[0][0])
        self.assertEqual(cycle["snapshot_hash"], "h1")
        self.assertEqual(cycle["market_snapshot"]["candles_15m"], [])
        self.assertEqual(cycle["market_snapshot"]["compacted_lanes"]["candles_15m"]["count"], 1)
        self.assertEqual(self.store._query("SELECT COUNT(*) FROM fills")[0][0], fills_before)

    def test_instance_lock_prevents_second_scheduler(self):
        first = InstanceLock(self.db).acquire()
        try:
            with self.assertRaisesRegex(PaperTradingError, "INSTANCE_LOCKED"):
                InstanceLock(self.db).acquire()
        finally:
            first.release()
        InstanceLock(self.db).acquire().release()

    def test_health_summary_is_read_only_and_complete(self):
        self.runtime.recover_on_startup(disk_usage=PLENTY)
        events_before = self.store._query("SELECT COUNT(*) FROM management_events")[0][0]
        health = self.runtime.health(disk_usage=PLENTY)
        self.assertEqual(health["schema_version"], "runtime-health.v1")
        for key in ("scheduler", "monitor", "market_feed", "database", "storage", "ai_providers", "budget_guard",
                    "reconciliation", "last_success", "kill_switch"):
            self.assertIn(key, health["components"])
        self.assertEqual(health["components"]["database"]["status"], "OK")
        self.assertEqual(self.store._query("SELECT COUNT(*) FROM management_events")[0][0], events_before)


class SoakTests(unittest.TestCase):
    def test_accelerated_soak_with_restarts_and_sleep_passes(self):
        path = Path(tempfile.mkdtemp()) / "soak.sqlite3"
        report = run_soak(path, days=0.75, restart_every_hours=4, symbols=["BTCUSDT"])
        self.assertTrue(report["passed"], report)
        self.assertGreaterEqual(report["restarts"], 3)
        self.assertEqual(report["duplicate_cycle_keys"], [])
        self.assertGreater(report["scheduler_slots"].get("SKIPPED_GAP", 0), 0)
        self.assertGreaterEqual(report["incidents"].get("UNCLEAN_SHUTDOWN", 0), 1)


if __name__ == "__main__":
    unittest.main()
