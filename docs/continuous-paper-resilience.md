# Continuous PAPER Resilience

Phase 4 of the development train (`docs/development-train.md`). This is reliability hardening for
unattended 60–90 day PAPER experiments on the existing local architecture: one process, one
SQLite file, stdlib only. There is no queue, cluster, or database migration. Code:
`crypto_eval/resilience.py`, `crypto_eval/soak.py`, the recovery/health section and scheduler
in `crypto_eval/paper_runtime.py`, and `crypto_eval/paper_server.py`.

Principles:

- Recovery is deterministic.
- Missed periods are explicit and never back-filled with invented observations.
- A consumed AI call is never repeated.
- The watchdog records and never trades.
- Critical failures fail closed.

## Startup recovery (before any worker starts)

`PaperScheduler.resume_on_startup()` calls `PaperRuntime.recover_on_startup()` first:

1. **Unclean shutdown detection.** The `process` heartbeat carries `clean_shutdown`, and a
   crash is recorded as `UNCLEAN_SHUTDOWN`.
2. **Database `quick_check`.** On failure: `DATABASE_FAILURE` (CRITICAL), the kill switch
   goes to `RISK_REDUCING_ONLY`, and the experiment is paused.
3. **Interrupted cycles** (`processing`) are marked `interrupted`. Their AI call is never
   repeated, and a retry of the same slot returns the stored cycle.
4. **Interrupted sliced executions** (`executing`) are marked `interrupted`. Filled slices
   stand (each is atomic in the ledger); the remainder is *not* resumed automatically and is
   journaled as `EXECUTION_DEFERRED`.
5. **Open AI budget reservations** from the dead process are settled conservatively: kept as
   spent (`reconciled`), never released as if unused.
6. **Free disk** below `min_free_disk_mb` (512 MB) records `STORAGE_LOW` and sets the kill
   switch to `NO_NEW_ENTRIES`.
7. **Ledger reconciliation.** A failure escalates to `RISK_REDUCING_ONLY`, as in Phase 2.
8. A `PROCESS_RESTART` incident records every recovery action.

`auto_resume` keeps its meaning. Open positions keep deterministic monitoring either way.

## Scheduler continuity

`cycle_tick` and `monitor_tick` are the whole scheduler; the threads only call them.

- The 15m slot is persisted (`scheduler_slots`) before any cycle runs. A `DONE` slot is never
  run again.
- An `ATTEMPTED` slot, interrupted by shutdown or a crash, is resumed after restart. This is
  safe because each (experiment, symbol, candle) cycle is unique.
- Slots missed while the process was down or the machine slept are recorded as
  `SKIPPED_GAP` with a `SCHEDULER_GAP` incident. They are never back-filled: a cycle only
  ever runs on the latest closed candle.
- A slot started more than 10 minutes late records `SCHEDULER_LAG`. It still only uses closed
  candles.
- After a monitor silence (sleep/downtime), `MONITOR_GAP` is recorded and the monitor catches
  up from each position's last processed bar using real closed 1m bars. Nothing is synthesized.
- Loop exceptions never kill a thread: they are recorded (`SCHEDULER_FAILURE` /
  `MONITOR_FAILURE`, deduplicated) and retried.

## Market-data continuity

The Gate WebSocket client already has:

- bounded reconnect backoff and silence reconnect;
- REST gap fill of closed candles;
- duplicate and out-of-order handling (a confirmed closed candle is never reopened);
- bounded candle lanes and bounded SSE subscriber queues (slow clients drop events and are
  counted, never block).

Phase 4 surfaces this state:

- a non-`LIVE` stream opens `FEED_STALE`, which resolves itself when the stream is live again;
- REST gap fills are recorded as `FEED_GAP` with the bar count;
- the Phase 2 stale-feed guards stay authoritative for entries.

## Provider resilience

AI calls have no automatic retries, so there is no retry storm to burn budget. A timeout after
the request was sent keeps its reservation (unknown cost stays counted).

Each provider has a circuit breaker:

- after 3 consecutive transport failures, the circuit opens for 10 minutes;
- while open, paid calls fail closed *before* any budget reservation. The usage ledger
  records `circuit_open` and the decision stack uses its existing provider-failure fallback;
- a `PROVIDER_OUTAGE` incident stays open until a trial call succeeds;
- schema-invalid answers are not counted as outages.

## Persistence

- **Schema:** SQLite WAL, `busy_timeout` 15 s (lock contention waits instead of failing),
  and `PRAGMA user_version` = 5.
- **Single instance:** `paper-server` takes an OS advisory lock beside the database
  (`<db>.lock`), so a second server (or `paper-restore`) on the same file is refused with
  `INSTANCE_LOCKED`. The lock is released automatically if the process dies.
- **Graceful shutdown (SIGTERM / Ctrl-C):**
  1. stop accepting requests;
  2. join the workers (a bounded wait for the in-flight tick);
  3. record `GRACEFUL_SHUTDOWN`;
  4. mark the clean stop;
  5. `wal_checkpoint(TRUNCATE)`;
  6. close.

## Bounded growth and retention

Retention runs from the monitor at most once a day and only touches diagnostics:

- `portfolio_snapshots` older than 7 days are thinned to one per 15 minutes;
- resolved incidents and completed scheduler slots older than 90 days are deleted;
- cycle snapshots older than 30 days have their duplicated candle arrays replaced by a
  per-lane summary (count, first/last close time, sha256). `snapshot_hash` is kept, and the
  15m/1h/4h candles remain in `market_history`; 1m candles are not retained after compaction.

Trades, fills, wallets, journals, AI cost, decisions, and safety/lifecycle evidence are never
deleted.

Measured growth (accelerated soak, fixture data, 2 symbols, 5-minute monitor): about 21 MB per
simulated day before compaction; compaction reduces the cycles table about 3x. Real growth
scales with the number of symbols, the monitor interval, and open positions (the `processed_bars`
and `equity` series are accounting evidence and are kept). The Overview shows the database
size and free disk, and `STORAGE_LOW` fails closed.

## Long-run performance

The soak found that a decision cycle's risk statistics exported *every* record, once per arm,
on every cycle. Cycle cost therefore grew with history: about 3 s per cycle after one simulated
day, which would have been minutes per cycle deep into a 90-day campaign. `_risk_statistics`
now uses indexed queries:

- day-start equity: one row;
- drawdown: a window-function peak;
- loss streak: paged in batches.

Results are identical on every cohort. A cycle now costs about 60 ms at day 3, and a
regression test forbids a full-history export inside a cycle.

## Backup and restore

- **`python3 -m crypto_eval paper-backup [--database P] [--out-dir D]`:** a consistent online
  snapshot via the SQLite backup API. It is `integrity_check`ed and scanned for credential-like
  values (it is rejected if any are found; secrets never enter SQLite by design), and gets a
  manifest (sha256, table counts). The Overview has a "Create backup" button (`POST /api/backup`).
- **`python3 -m crypto_eval paper-restore BACKUP [--database P] [--force]`:** refused while a
  server holds the database. It verifies the checksum, integrity, and table counts. An existing
  database is kept as `.pre-restore-<stamp>`.

## Health

`GET /api/runtime-health` (`runtime-health.v1`) is read-only and never triggers an action. It
reports scheduler, monitor, market feed, database, storage, AI providers (open circuits),
budget guard, reconciliation, kill switch, the last successful cycle and position review, and
open incidents (`runtime-incident`). The Overview shows it as the "System health" panel.
`GET /api/incidents` lists incident evidence. The existing `GET /api/health` liveness probe
is unchanged.

## Soak and drills

- **`python3 -m crypto_eval paper-soak --database FRESH.sqlite3 --days N`:** drives the real
  scheduler ticks on fixture data with a simulated clock, restarting the runtime every
  12 simulated hours (every second restart without a clean shutdown) and injecting a 3-hour
  machine-sleep gap. It checks:
  - no duplicate cycles or slots;
  - explicit skipped slots;
  - restart evidence;
  - reconciliation after each restart and at the end;
  - database integrity;
  - storage growth.
- **`tests/test_continuous_resilience.py`** covers:
  - kill with an open perpetual;
  - Spot holding plus pending limit order (no duplicate fill, idempotent replay);
  - restart during an AI request;
  - restart during sliced execution;
  - stale AI reservations;
  - low disk;
  - SQLite lock contention;
  - sleep gap;
  - scheduler lag;
  - interrupted-slot resume;
  - halt/idle;
  - monitor gap and feed stale/gap;
  - provider circuit;
  - backup/restore (including tampering and credential rejection);
  - retention;
  - instance lock;
  - health;
  - a short soak.
