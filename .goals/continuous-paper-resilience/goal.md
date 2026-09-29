# Goal: Continuous PAPER Resilience

## Dependency gate

Do not implement until `feature/spot-cycle-lifecycle-manager` is complete, reviewed, merged to main, and this branch is synchronized onto that final main.

## Mission

Make the local-first PAPER platform reliable enough to run unattended for 60-90+ day experiments without corrupting state, duplicating actions, silently missing market history, or requiring constant manual babysitting.

This is reliability hardening, not a distributed-systems rewrite.

## Constraints

- Keep the current simple local architecture unless evidence proves a blocker.
- No Kubernetes, Kafka, microservice split, external queue cluster or HA database.
- Prefer deterministic recovery and observability over infrastructure complexity.
- Real Gate writes remain blocked.

## Required capabilities

### Process restart / recovery
- Recover experiment state, open PAPER positions/holdings, pending orders, management modes and scheduler state after restart.
- Startup reconciliation before new autonomous actions.
- Exactly-once logical cycle behavior across retries/restarts.

### Scheduler resilience
- Persist enough scheduler state to avoid duplicate or skipped logical cycles.
- Detect clock drift / overdue cycles.
- Resume safely after machine sleep or process downtime.
- Do not invent market observations for missed periods.

### Market-data continuity
- WebSocket reconnect with bounded backoff.
- REST gap fill for recoverable gaps.
- Explicit incomplete/unrecoverable gap state.
- Stale-feed protection remains authoritative.
- Out-of-order and duplicate events are handled deterministically.

### Persistence integrity
- SQLite schema versioning / migration discipline.
- WAL and transaction behavior validated for current concurrency.
- startup integrity check appropriate to the local app.
- bounded database growth and retention behavior.
- disk-space / write-failure handling.

### Backup / restore
- Lightweight local backup or consistent snapshot procedure.
- Verify a restore can recover experiments, positions, wallets, orders, journals, AI cost and evaluation state.
- Secrets are never included in unsafe plaintext backup artifacts.

### Provider resilience
- bounded retry/backoff for transient provider failures;
- timeout behavior;
- circuit/fail-closed behavior where appropriate;
- no retry storm that burns AI budget;
- budget reservation/reconciliation remains correct on timeout/retry.

### Resource bounds
- prevent unbounded in-memory market/event growth;
- bounded logs/diagnostic history;
- graceful behavior under slow browser clients;
- avoid duplicate background workers.

### Graceful shutdown
- stop accepting new work;
- finish or safely abort bounded in-flight work;
- persist/reconcile;
- preserve open-position deterministic monitoring state for restart.

### Health / watchdog
Expose concise health for:
- scheduler;
- monitor;
- market feed;
- database;
- AI providers;
- budget guard;
- reconciliation;
- disk/storage;
- last successful cycle;
- last successful position review.

Watchdog behavior must not create duplicate trading actions.

### Incident evidence
Persist enough sanitized evidence to explain:
- process restart;
- scheduler lag;
- feed gap;
- provider outage;
- database failure;
- recovery action;
- degraded mode;
- restored mode.

## Recovery drills

Required deterministic/local drills:

- kill process with open Perpetual position, restart, recover same state;
- kill process with Spot holding + pending order, restart without duplicate fill;
- restart during AI request / timeout;
- restart during sliced PAPER execution;
- Gate WS disconnect and reconnect with REST gap fill;
- machine sleep / long scheduler gap;
- stale market feed;
- SQLite lock contention;
- disk-write failure simulation where feasible;
- corrupted/incomplete last operation fixture;
- restore from backup snapshot;
- browser closed while backend continues;
- browser reconnect after long absence.

## 90-day campaign readiness

The platform is ready for the planned 500 USDT PAPER campaign only when:
- repeated restart drills reconcile exactly;
- no duplicate logical execution occurs;
- market gaps are explicit and handled;
- state survives process/browser restarts;
- storage growth is bounded/understood;
- health status makes degradation obvious;
- critical failures fail closed rather than silently continuing.

## Definition of Done

- recovery behavior is deterministic and tested;
- scheduler continuity is tested;
- market-data recovery is tested;
- persistence/backup/restore is tested;
- provider retry does not bypass cost budgets;
- health/watchdog exists without duplicate action risk;
- long-run smoke/soak test can run locally;
- all prior Portfolio OS, Crash Safety and Spot Lifecycle tests remain green;
- no distributed-platform overengineering is introduced.