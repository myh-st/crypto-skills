"""Continuous PAPER resilience: heartbeats, incidents, scheduler slots, provider circuits,
startup recovery, storage health, retention, backup/restore, and a single-instance lock.

Design rules:

- local-first and simple: one SQLite file, one process, stdlib only;
- recovery is deterministic and never invents market observations or repeats a consumed AI call;
- the watchdog detects and records; it never places or repeats a trading action;
- critical failures fail closed (kill switch escalation), never silently continue;
- backups never contain secrets (secrets never enter SQLite by design; backups are scanned).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .paper_contracts import PaperTradingError, iso_utc, parse_utc

DB_SCHEMA_VERSION = 5  # PRAGMA user_version after Portfolio OS, safety, lifecycle, resilience
HEALTH_SCHEMA_VERSION = "runtime-health.v1"
INCIDENT_KINDS = (
    "PROCESS_RESTART",
    "UNCLEAN_SHUTDOWN",
    "GRACEFUL_SHUTDOWN",
    "SCHEDULER_LAG",
    "SCHEDULER_GAP",
    "SCHEDULER_FAILURE",
    "MONITOR_GAP",
    "MONITOR_FAILURE",
    "FEED_GAP",
    "FEED_STALE",
    "PROVIDER_OUTAGE",
    "DATABASE_FAILURE",
    "STORAGE_LOW",
    "RECOVERY_ACTION",
    "DEGRADED_MODE",
    "RESTORED_MODE",
)
SEVERITIES = ("INFO", "WARNING", "CRITICAL")

DEFAULT_RESILIENCE_SETTINGS: dict[str, Any] = {
    "scheduler_lag_seconds": 600,
    "monitor_gap_multiplier": 5,
    "circuit_failure_threshold": 3,
    "circuit_cooldown_seconds": 600,
    "min_free_disk_mb": 512,
    "snapshot_full_resolution_days": 7,
    "snapshot_thinned_step_minutes": 15,
    "resolved_incident_retention_days": 90,
    "max_missed_slots_listed": 96,
    "cycle_snapshot_full_days": 30,
    "compaction_batch": 500,
}

RESILIENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_heartbeats(
    name TEXT PRIMARY KEY,
    last_ok_at TEXT,
    last_error_at TEXT,
    last_error TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_incidents(
    incident_id TEXT PRIMARY KEY,
    experiment_id TEXT,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    dedupe_key TEXT,
    summary TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    opened_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT
);
CREATE INDEX IF NOT EXISTS runtime_incidents_open_idx ON runtime_incidents(status, dedupe_key);
CREATE TABLE IF NOT EXISTS scheduler_slots(
    experiment_id TEXT NOT NULL,
    slot TEXT NOT NULL,
    status TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    completed_at TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(experiment_id, slot)
);
CREATE TABLE IF NOT EXISTS provider_circuits(
    provider_id TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    opened_until TEXT,
    last_error TEXT,
    last_failure_at TEXT,
    last_ok_at TEXT,
    updated_at TEXT NOT NULL
);
"""

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r'"(api[_-]?key|secret|password|token)"\s*:\s*"(?!\*|masked|<|$)[^"]{12,}"', re.IGNORECASE),
)


class ResilienceLedger:
    """Persisted heartbeats, incidents, scheduler slots, and provider circuits."""

    def __init__(self, store: Any, clock: Callable[[], datetime], settings: dict[str, Any] | None = None) -> None:
        self.store = store
        self._clock = clock
        self.settings = dict(DEFAULT_RESILIENCE_SETTINGS, **(settings or {}))

    def _now(self) -> datetime:
        return self._clock().astimezone(timezone.utc)

    # ------------------------------------------------------------ heartbeats
    def heartbeat(self, name: str, *, ok: bool = True, error: str | None = None, detail: dict[str, Any] | None = None) -> None:
        now = iso_utc(self._now())
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO runtime_heartbeats(name, last_ok_at, last_error_at, last_error, detail_json, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "last_ok_at=COALESCE(excluded.last_ok_at, runtime_heartbeats.last_ok_at), "
                "last_error_at=COALESCE(excluded.last_error_at, runtime_heartbeats.last_error_at), "
                "last_error=CASE WHEN excluded.last_error_at IS NULL THEN runtime_heartbeats.last_error ELSE excluded.last_error END, "
                "detail_json=excluded.detail_json, updated_at=excluded.updated_at",
                (name, now if ok else None, None if ok else now, None if ok else (error or "error")[:300],
                 json.dumps(detail or {}, sort_keys=True, default=str), now),
            )

    def heartbeats(self) -> dict[str, dict[str, Any]]:
        rows = self.store._query("SELECT * FROM runtime_heartbeats ORDER BY name")
        return {row["name"]: {**{k: row[k] for k in row.keys() if k != "detail_json"}, "detail": json.loads(row["detail_json"])}
                for row in rows}

    def heartbeat_age(self, name: str) -> float | None:
        beat = self.heartbeats().get(name)
        if not beat or not beat.get("last_ok_at"):
            return None
        return (self._now() - parse_utc(beat["last_ok_at"], "heartbeat")).total_seconds()

    # ------------------------------------------------------------- incidents
    def open_incident(self, kind: str, *, severity: str, summary: str, detail: dict[str, Any] | None = None,
                      dedupe_key: str | None = None, experiment_id: str | None = None, resolved: bool = False) -> str:
        if kind not in INCIDENT_KINDS or severity not in SEVERITIES:
            raise PaperTradingError("incident kind/severity is invalid")
        now = iso_utc(self._now())
        with self.store.transaction() as db:
            if dedupe_key and not resolved:
                row = db.execute("SELECT incident_id FROM runtime_incidents WHERE status='OPEN' AND dedupe_key=?", (dedupe_key,)).fetchone()
                if row:
                    db.execute("UPDATE runtime_incidents SET occurrences=occurrences+1, last_seen_at=?, detail_json=?, summary=? "
                               "WHERE incident_id=?", (now, json.dumps(detail or {}, sort_keys=True, default=str), summary[:300], row["incident_id"]))
                    return row["incident_id"]
            incident_id = f"inc-{uuid.uuid4().hex[:16]}"
            db.execute(
                "INSERT INTO runtime_incidents(incident_id, experiment_id, kind, severity, status, dedupe_key, summary, detail_json, "
                "opened_at, last_seen_at, resolved_at, resolution) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (incident_id, experiment_id, kind, severity, "RESOLVED" if resolved else "OPEN", dedupe_key, summary[:300],
                 json.dumps(detail or {}, sort_keys=True, default=str), now, now, now if resolved else None,
                 "recorded" if resolved else None),
            )
        return incident_id

    def resolve(self, dedupe_key: str, resolution: str) -> int:
        now = iso_utc(self._now())
        with self.store.transaction() as db:
            return db.execute("UPDATE runtime_incidents SET status='RESOLVED', resolved_at=?, resolution=? "
                              "WHERE status='OPEN' AND dedupe_key=?", (now, resolution[:300], dedupe_key)).rowcount

    def incidents(self, *, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM runtime_incidents", []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        rows = self.store._query(sql + " ORDER BY opened_at DESC, incident_id LIMIT ?", (*params, limit))
        return [{**{k: row[k] for k in row.keys() if k != "detail_json"}, "detail": json.loads(row["detail_json"])} for row in rows]

    # ------------------------------------------------------------ scheduler
    def last_slot(self, experiment_id: str) -> str | None:
        rows = self.store._query("SELECT slot FROM scheduler_slots WHERE experiment_id=? AND status IN ('ATTEMPTED','DONE') "
                                 "ORDER BY slot DESC LIMIT 1", (experiment_id,))
        return rows[0]["slot"] if rows else None

    def slot_status(self, experiment_id: str, slot: str) -> str | None:
        rows = self.store._query("SELECT status FROM scheduler_slots WHERE experiment_id=? AND slot=?", (experiment_id, slot))
        return rows[0]["status"] if rows else None

    def record_slot(self, experiment_id: str, slot: str, status: str, detail: dict[str, Any] | None = None) -> None:
        now = iso_utc(self._now())
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO scheduler_slots(experiment_id, slot, status, attempted_at, completed_at, detail_json) VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(experiment_id, slot) DO UPDATE SET status=excluded.status, completed_at=excluded.completed_at, "
                "detail_json=excluded.detail_json",
                (experiment_id, slot, status, now, now if status in {"DONE", "SKIPPED_GAP"} else None,
                 json.dumps(detail or {}, sort_keys=True, default=str)),
            )

    # ------------------------------------------------------------- circuits
    def circuit(self, provider_id: str) -> dict[str, Any] | None:
        rows = self.store._query("SELECT * FROM provider_circuits WHERE provider_id=?", (provider_id,))
        return dict(rows[0]) if rows else None

    def circuit_allows(self, provider_id: str) -> tuple[bool, str | None]:
        row = self.circuit(provider_id)
        if not row or not row["opened_until"]:
            return True, None
        if parse_utc(row["opened_until"], "opened_until") > self._now():
            return False, f"PROVIDER_CIRCUIT_OPEN until {row['opened_until']} after {row['consecutive_failures']} consecutive failures"
        return True, None  # half-open: one trial call; a failure re-opens immediately

    def record_provider(self, provider_id: str, *, ok: bool, error: str | None = None, experiment_id: str | None = None) -> None:
        now = self._now()
        row = self.circuit(provider_id) or {"consecutive_failures": 0}
        failures = 0 if ok else int(row["consecutive_failures"]) + 1
        opened_until = None
        if not ok and failures >= self.settings["circuit_failure_threshold"]:
            opened_until = iso_utc(now + timedelta(seconds=self.settings["circuit_cooldown_seconds"]))
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO provider_circuits(provider_id, consecutive_failures, opened_until, last_error, last_failure_at, last_ok_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT(provider_id) DO UPDATE SET consecutive_failures=excluded.consecutive_failures, "
                "opened_until=excluded.opened_until, last_error=COALESCE(excluded.last_error, provider_circuits.last_error), "
                "last_failure_at=COALESCE(excluded.last_failure_at, provider_circuits.last_failure_at), "
                "last_ok_at=COALESCE(excluded.last_ok_at, provider_circuits.last_ok_at), updated_at=excluded.updated_at",
                (provider_id, failures, opened_until, None if ok else (error or "error")[:200], None if ok else iso_utc(now),
                 iso_utc(now) if ok else None, iso_utc(now)),
            )
        key = f"provider:{provider_id}"
        if opened_until:
            self.open_incident("PROVIDER_OUTAGE", severity="WARNING", dedupe_key=key, experiment_id=experiment_id,
                               summary=f"{provider_id}: circuit open after {failures} consecutive failures; paid calls fail closed",
                               detail={"provider_id": provider_id, "failures": failures, "opened_until": opened_until, "last_error": error})
        elif ok:
            self.resolve(key, "provider call succeeded; circuit closed")

    def circuits(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.store._query("SELECT * FROM provider_circuits ORDER BY provider_id")]


# ------------------------------------------------------------------ scheduler math
def missed_slots(last_slot: str | None, current_slot: str, *, interval_minutes: int = 15) -> list[str]:
    """Slots strictly between the last attempted slot and the current one (never invented as cycles)."""

    if not last_slot:
        return []
    last = parse_utc(last_slot, "last_slot")
    current = parse_utc(current_slot, "current_slot")
    step = timedelta(minutes=interval_minutes)
    result = []
    cursor = last + step
    while cursor < current:
        result.append(iso_utc(cursor))
        cursor += step
    return result


# ------------------------------------------------------------------ storage
def database_path(store: Any) -> Path | None:
    rows = store._query("PRAGMA database_list")
    for row in rows:
        if row["name"] == "main" and row["file"]:
            return Path(row["file"])
    return None


def database_health(store: Any, *, full: bool = False) -> dict[str, Any]:
    try:
        check = store._query("PRAGMA integrity_check" if full else "PRAGMA quick_check")
        ok = [row[0] for row in check] == ["ok"]
        page_size = store._query("PRAGMA page_size")[0][0]
        pages = store._query("PRAGMA page_count")[0][0]
        freelist = store._query("PRAGMA freelist_count")[0][0]
        version = store._query("PRAGMA user_version")[0][0]
        mode = store._query("PRAGMA journal_mode")[0][0]
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)[:200]}
    path = database_path(store)
    wal = Path(f"{path}-wal") if path else None
    return {
        "ok": ok,
        "check": "integrity_check" if full else "quick_check",
        "size_bytes": page_size * pages,
        "free_pages": freelist,
        "wal_bytes": wal.stat().st_size if wal and wal.exists() else 0,
        "journal_mode": mode,
        "schema_version": version,
        "path": str(path) if path else ":memory:",
    }


def storage_health(path: Path | None, *, min_free_mb: float, disk_usage: Callable[[str], Any] = shutil.disk_usage) -> dict[str, Any]:
    target = str(path.parent if path else Path.cwd())
    try:
        usage = disk_usage(target)
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:200]}
    free_mb = usage.free / 1_048_576
    return {"ok": free_mb >= min_free_mb, "free_mb": round(free_mb, 1), "total_mb": round(usage.total / 1_048_576, 1),
            "min_free_mb": min_free_mb}


def compact_storage(store: Any, experiment_id: str, *, now: datetime, settings: dict[str, Any] | None = None) -> dict[str, int]:
    """Bounded growth for diagnostic tables only. Trading records, fills, journals, AI cost,
    decisions, and safety/lifecycle evidence are never deleted."""

    cfg = dict(DEFAULT_RESILIENCE_SETTINGS, **(settings or {}))
    cutoff = iso_utc(now - timedelta(days=cfg["snapshot_full_resolution_days"]))
    step = int(cfg["snapshot_thinned_step_minutes"])
    removed = {"portfolio_snapshots": 0, "runtime_incidents": 0, "scheduler_slots": 0, "cycle_snapshots_compacted": 0}
    with store.transaction() as db:
        rows = db.execute("SELECT as_of FROM portfolio_snapshots WHERE experiment_id=? AND as_of < ? ORDER BY as_of",
                          (experiment_id, cutoff)).fetchall()
        keep_bucket: set[str] = set()
        doomed = []
        for row in rows:
            stamp = parse_utc(row["as_of"], "snapshot.as_of")
            bucket = iso_utc(stamp.replace(minute=stamp.minute - stamp.minute % step, second=0, microsecond=0))
            if bucket in keep_bucket:
                doomed.append(row["as_of"])
            else:
                keep_bucket.add(bucket)
        for stamp in doomed:
            db.execute("DELETE FROM portfolio_snapshots WHERE experiment_id=? AND as_of=?", (experiment_id, stamp))
        removed["portfolio_snapshots"] = len(doomed)
        incident_cutoff = iso_utc(now - timedelta(days=cfg["resolved_incident_retention_days"]))
        removed["runtime_incidents"] = db.execute(
            "DELETE FROM runtime_incidents WHERE status='RESOLVED' AND resolved_at < ?", (incident_cutoff,)).rowcount
        removed["scheduler_slots"] = db.execute(
            "DELETE FROM scheduler_slots WHERE experiment_id=? AND slot < ? AND status IN ('DONE','SKIPPED_GAP')",
            (experiment_id, incident_cutoff)).rowcount
        # Old cycle snapshots: the decision inputs stay auditable by snapshot_hash, and 15m/1h/4h
        # candles remain in market_history; the duplicated candle arrays are summarized.
        cycle_cutoff = iso_utc(now - timedelta(days=cfg["cycle_snapshot_full_days"]))
        rows = db.execute(
            "SELECT cycle_id, payload_json FROM cycles WHERE experiment_id=? AND cycle_slot < ? "
            "AND json_array_length(payload_json, '$.market_snapshot.candles_15m') > 0 LIMIT ?",
            (experiment_id, cycle_cutoff, int(cfg["compaction_batch"])),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            snapshot = payload.get("market_snapshot")
            if not isinstance(snapshot, dict):
                continue
            for lane in ("candles_1m", "candles_15m", "candles_1h", "candles_4h"):
                candles = snapshot.get(lane)
                if isinstance(candles, list) and candles:
                    snapshot[lane] = []
                    snapshot.setdefault("compacted_lanes", {})[lane] = {
                        "count": len(candles), "first_close_time": candles[0].get("close_time"),
                        "last_close_time": candles[-1].get("close_time"),
                        "sha256": hashlib.sha256(json.dumps(candles, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                        "archived_in": "market_history" if lane != "candles_1m" else None,
                    }
            snapshot["compacted_at"] = iso_utc(now)
            db.execute("UPDATE cycles SET payload_json=? WHERE cycle_id=?", (json.dumps(payload, sort_keys=True), row["cycle_id"]))
            removed["cycle_snapshots_compacted"] += 1
    return removed


# ------------------------------------------------------------------ backup / restore
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_for_secrets(connection: sqlite3.Connection) -> list[str]:
    """Return table.column locations whose text looks like a credential. Expected: none."""

    findings = []
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")') if (row[2] or "").upper() in {"TEXT", ""}]
        for column in columns:
            for (value,) in connection.execute(f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'):
                if isinstance(value, str) and any(pattern.search(value) for pattern in _SECRET_PATTERNS):
                    findings.append(f"{table}.{column}")
                    break
    return findings


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}


def backup_database(store: Any, destination_dir: Path, *, now: datetime | None = None, label: str = "manual") -> dict[str, Any]:
    """Consistent online snapshot via the SQLite backup API, verified and scanned for secrets."""

    now = now or datetime.now(timezone.utc)
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    name = f"paper-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    target = destination_dir / f"{name}.sqlite3"
    with store._lock:
        destination = sqlite3.connect(target)
        try:
            store._db.backup(destination)
        finally:
            destination.close()
    connection = sqlite3.connect(target)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        secrets = scan_for_secrets(connection)
        counts = table_counts(connection)
    finally:
        connection.close()
    if integrity != "ok" or secrets:
        target.unlink(missing_ok=True)
        raise PaperTradingError(
            "BACKUP_REJECTED: " + ("integrity check failed" if integrity != "ok" else f"credential-like values found in {', '.join(secrets)}")
        )
    manifest = {"schema_version": "paper-backup.v1", "file": target.name, "created_at": iso_utc(now), "label": label,
                "sha256": _sha256(target), "bytes": target.stat().st_size, "integrity": integrity, "table_counts": counts,
                "secrets_scan": "clean", "contains_credentials": False}
    (destination_dir / f"{name}.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def restore_database(backup_file: Path, target: Path, *, force: bool = False) -> dict[str, Any]:
    """Restore a verified backup to ``target`` (the server must be stopped). An existing target
    is kept as ``<target>.pre-restore-<stamp>`` and only replaced with ``force``."""

    backup_file, target = Path(backup_file), Path(target)
    manifest_path = backup_file.with_suffix(".json")
    if not backup_file.exists() or not manifest_path.exists():
        raise PaperTradingError("RESTORE_REJECTED: backup file or manifest is missing")
    manifest = json.loads(manifest_path.read_text())
    if _sha256(backup_file) != manifest.get("sha256"):
        raise PaperTradingError("RESTORE_REJECTED: backup checksum does not match its manifest")
    connection = sqlite3.connect(f"file:{backup_file}?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise PaperTradingError("RESTORE_REJECTED: backup integrity check failed")
        counts = table_counts(connection)
    finally:
        connection.close()
    preserved = None
    if target.exists():
        if not force:
            raise PaperTradingError("RESTORE_REJECTED: target database exists; pass force to keep it aside and restore")
        preserved = target.with_name(f"{target.name}.pre-restore-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
        os.replace(target, preserved)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{target}{suffix}")
            if sidecar.exists():
                os.replace(sidecar, Path(f"{preserved}{suffix}"))
    shutil.copy2(backup_file, target)
    if counts != manifest.get("table_counts"):
        raise PaperTradingError("RESTORE_REJECTED: restored table counts differ from the manifest")
    return {"restored": str(target), "from": str(backup_file), "preserved_previous": str(preserved) if preserved else None,
            "table_counts": counts}


# ------------------------------------------------------------------ single instance
class InstanceLock:
    """Advisory OS lock beside the database so two servers never run schedulers on one file.
    Released automatically by the OS if the process dies."""

    def __init__(self, database: Path) -> None:
        self.path = Path(f"{database}.lock")
        self._handle = None

    def acquire(self) -> "InstanceLock":
        try:
            import fcntl
        except ImportError:  # pragma: no cover - non-POSIX; fall back to no lock with a clear note
            return self
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise PaperTradingError("INSTANCE_LOCKED: another paper-server is already running on this database") from None
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "acquired_at": iso_utc(datetime.now(timezone.utc))}))
        handle.flush()
        self._handle = handle
        return self

    def release(self) -> None:
        if self._handle is not None:
            try:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._handle.close()
                self._handle = None
