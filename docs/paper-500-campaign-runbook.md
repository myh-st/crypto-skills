# PAPER 500 USDT Campaign Runbook

**FEATURE DEVELOPMENT FROZEN: PAPER CAMPAIGN NEXT.** Development follows the stop condition in
`docs/development-train.md`: only correctness, safety, reliability, observability,
methodology, or evidence-backed strategy defects.

**LIVE EXECUTION NOT IMPLEMENTED / STILL DISABLED.** `feature/live-execution-gateway` is
`PLANNING_ONLY_LIVE_DISABLED`. Every Gate write path stays blocked by design. A gate `PASS`
never enables anything.

## Goal

Run one frozen PAPER experiment with 500 USDT total starting capital for at least 90 days, with
checkpoints at day 7, 30, 60, and 90. Target roughly 200–300 completed trades before drawing
stronger conclusions. If the sample or regime coverage is too thin, keep collecting data
rather than forcing a verdict.

## Before day 0 (one-time setup)

1. **Update and verify.**
   - Run `git switch main && git pull --ff-only`.
   - Run the full regression: `validate_repo`, `unittest`, `node --check`, `node --test`.
2. **Credentials.** Run `python3 -m crypto_eval paper-setup-real`. This moves `.env`
   credentials into the OS credential store; never commit or paste them. Test both providers
   in Settings (Jev and Azure AI Foundry GPT-6 Luna) until they show `passed`.
3. **Fresh database** for the campaign, for example
   `python3 -m crypto_eval paper-server --database ~/paper-500.sqlite3`. Keep this path for
   the whole campaign.
4. **Capital split** (both balances freeze after first activity; decide once and write it in
   the campaign notes):
   - Perpetual `starting_balance_usdt` = 300;
   - Spot `spot_starting_balance_usdt` = 200 (Settings);
   - total 500 USDT PAPER.
5. **Universe and market data.**
   - `market_data_mode = gate_usdt` (real Gate public data).
   - Keep the default liquid universe unless there is a written reason to change it.
6. **Cost policy.** These are operational fields, audited but not part of the manifest.
   - Set `cost_fx` to a manual `usdt_per_usd` with its source noted. Without it, **net
     economic PnL is unavailable and the gate cannot PASS**.
   - Set the `ai_budget` daily and experiment caps you can afford, and a `limit_action`
     (recommended: `FALLBACK_QUANT`).
7. **Promotion criteria** (Settings → promotion). They freeze into the manifest at Start;
   changing them later creates a new version and restarts the evaluation window. Keep the
   defaults unless there is a written reason:
   - 90 days, 200 trades, PF ≥ 1.15, DD ≤ 20%, positive net economic PnL;
   - top trade ≤ 50%, skipped slots ≤ 5%, 0 open critical incidents, ≥ 2 regimes.
8. **Authority.** AI-opened positions `AUTO_PAPER` (default); your own positions
   `RECOMMEND_ONLY`. Set `lifecycle.enabled` and the `ai_spot` policy deliberately; both are
   material.
9. **Disk and backups.** Keep several GB free; the DB grows by tens of MB per day. Run
   `python3 -m crypto_eval paper-backup --database ~/paper-500.sqlite3` and confirm the
   manifest says `secrets_scan: clean`.
10. **Start.** Click Start on the Overview. The manifest freezes as v1. Record the
    `material_sha256`, the commit SHA, and the date in the campaign notes.

## Daily (≈5 minutes)

- Overview:
  - System health is OK or ATTENTION;
  - no open critical incidents;
  - kill switch NORMAL (or an explained level);
  - reconciliation OK.
- Needs attention: acknowledge or act.
- Take a backup (the Overview button or `paper-backup`) and keep the last 7 plus weekly copies
  off-machine.
- Never edit material settings casually. If you must, the confirmation creates a new manifest
  version, and evaluation restarts for that version.

## Checkpoints

Run `python3 -m crypto_eval paper-checkpoint --database ~/paper-500.sqlite3 --out reports/paper-500/<checkpoint>.json`
or Evaluations → Run checkpoint review. Archive the JSON; its `report_sha256` identifies it.

| Checkpoint | Focus | Expected status | Actions |
|---|---|---|---|
| **Day 7** | Correctness and reliability only; profitability is not judged | `CONTINUE_COLLECTING_DATA` (anything worse must be fixed now) | Verify: no duplicate cycles; reconciliation OK; skipped-slot ratio low and every gap explained; restarts recovered; AI cost within budget and priced; FX present so net economic PnL is available; secret scan clean. Test a restore to a scratch path (`paper-restore BACKUP --database /tmp/restore-test.sqlite3`). |
| **Day 30** | Early economics with denominators; data quality | Usually `CONTINUE_COLLECTING_DATA` | Review net trading vs net economic PnL (fees, funding, slippage, AI cost); tournament arms with `aligned_cycles` and incremental-vs-quant (no causal claims from unmatched cases); Spot lifecycle actions and a fresh benchmark run; regimes observed. Fix defects only. |
| **Day 60** | Robustness | `CONTINUE_COLLECTING_DATA` or an early `FAIL_*` | Trade count trajectory vs the 200-trade target; regime coverage; drawdown behavior during any crash-mode episodes; provider outages and circuit openings; storage growth. If `FAIL_SAFETY` or `FAIL_RELIABILITY`, stop and fix; the experiment is not promotable until a clean window accrues. |
| **Day 90** | Full gate | `PASS`, `FAIL_STRATEGY`, or `CONTINUE_COLLECTING_DATA` (sample too small or one regime only) | `PASS` means write a live-eligibility review memo; nothing is enabled. `FAIL_STRATEGY` means stop; any change becomes a new experiment version with a new manifest. `CONTINUE` means extend the campaign unchanged. |

## Incident handling

| Symptom | Response |
|---|---|
| `RECONCILIATION_FAILURE` / kill switch `RISK_REDUCING_ONLY` | Stop new entries. Export the bundle, investigate, fix. Lowering the kill switch requires a passing reconciliation. |
| `DATABASE_FAILURE` / `STORAGE_LOW` | Free disk or restore the latest good backup (server stopped). Record the incident in the campaign notes. |
| `PROVIDER_OUTAGE` (circuit open) | Decisions fall back automatically. If outages persist, the AI arms' samples shrink; note it at the checkpoint. |
| `SCHEDULER_GAP` / `MONITOR_GAP` | Expected after sleep or downtime. Frequent gaps inflate the skipped-slot ratio and can `FAIL_RELIABILITY`, so keep the machine awake (power settings) for the campaign. |
| Manifest drift banner | Either revert the change or record a new version with a reason. Never leave drift unresolved. |

## Day-0 decisions for EXP-001

- **Luna reasoning `medium`** (not `max`). Luna only judges escalated, bounded decisions: the
  quant gate, Jev, the escalation policy, schema validation, RiskEngine, Portfolio Brain, and
  safety do the rest. Real calls at `max` used 1k–12k reasoning tokens for the same ~19k-token
  input ($0.36–$1.02, 9–67 s) with no evidence of better decisions. Sample size matters more for
  measuring AI value.
- **Budget:** `gpt_max_output_tokens` = 8000 (the reservation is based on it) and
  `max_gpt_call_usd` = 1.0.
- **Loss-streak pause:** `loss_streak_pause_minutes` = 1440. After 5 consecutive losses, new
  entries pause for 24 h and then the streak resets. Before this fix, a streak could only
  clear on a win, so a replay stopped trading forever on day 1.
- **Drawdown stop:** `max_drawdown_stop` 15% stays a hard halt, but it now raises a CRITICAL
  `RISK_HALT` incident in Attention instead of blocking silently.

## Keeping the server up for 90 days

`docs/ops/paper-campaign.launchd.plist` is a launchd template (auto-start at login, restart
on crash, `caffeinate`). Installing it is a persistent system change, so do it deliberately.
Without it, restart the server manually after a reboot; startup recovery handles the gap.

## Scope of EXP-001 (known, not covered by the campaign)

- Spot lifecycle decisions are deterministic. No Jev/Luna lifecycle recommendation is wired, so
  the campaign does not evaluate AI lifecycle recommendations.
- The perpetual and spot capital defaults are 100/100 USDT; set 300/200 before Start (step 4).
  The Overview has no "total 500" line; confirm the split on Portfolio.
- Manifest status is on Evaluations, and the FX policy is in the experiment config; neither is
  shown on Overview.

## EXP-002: trend sleeves (runs alongside EXP-001)

EXP-002 runs the [trend sleeves engine](trend-sleeves-engine.md) in its own database and
server, so the two experiments never share a wallet, a manifest, or a scheduler.

| | EXP-001 | EXP-002 |
|---|---|---|
| Engine | 15m breakout + Jev/Luna routing | `sleeves_v1`: Donchian 4h, TSMOM, XSMOM (no AI) |
| Database | `~/paper-500.sqlite3` | `~/paper-exp002.sqlite3` |
| URL | http://127.0.0.1:8765/ | http://127.0.0.1:8768/ |
| launchd label | `com.cryptoskills.paper-campaign` | `com.cryptoskills.paper-exp002` |
| Log | `~/paper-500.log` | `~/paper-exp002.log` |
| Capital | 300 perp + 200 spot | 500 perp in three sleeves of ~166.67 (+1 USDT minimum spot, unused) |
| Risk stops | drawdown 15%, daily loss 5% | combined drawdown 25% (`RISK_HALT`), combined daily loss 8% (`RISK_PAUSE`) |
| Trade target | 200 | 80 (the replay closes ~100 sleeve trades per 90 days) |
| AI budget | $60, `FALLBACK_QUANT` | $1, `BLOCK_PAID_AI` (the engine makes no AI calls) |

**Setup on a fresh database.** The current run is already set up. To reproduce it:

1. Start the server in one of two ways:
   - **launchd (survives reboots and crashes):** fill in `REPO`, `HOME_DIR` and `PYTHON` in
     [`docs/ops/paper-exp002.launchd.plist`](ops/paper-exp002.launchd.plist), copy it to
     `~/Library/LaunchAgents/com.cryptoskills.paper-exp002.plist`, then run
     `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cryptoskills.paper-exp002.plist`.
   - **foreground:** `python3 -m crypto_eval paper-server --database ~/paper-exp002.sqlite3 --port 8768`
2. Run `python3 scripts/paper_exp002_setup.py http://127.0.0.1:8768`. The helper:
   - applies the full EXP-002 config and portfolio settings through the server's validating API
     (`experiment_config()` and `portfolio_settings()` in the script are the complete, reviewable
     definition, covered by `tests/test_sleeves.py`);
   - waits until the live feed is fresh for all seven coins;
   - starts the experiment, which freezes manifest v1.

   It refuses a non-loopback URL or an experiment that is not `stopped`.

**Daily check.** The Overview campaign panel shows each sleeve's equity, P&L net of transfers,
long/short book, the last 4h tick and the combined drawdown. Check three things:

- the last tick is at most 4h old;
- `blocked` is empty, or explained (a stale feed retries on the next tick);
- reconciliation is OK.

**What to expect.** Trend systems lose often in small amounts and make their money in a few
long runs. The replay's win rate was about 25% for Donchian and 45% for momentum, and its
worst drawdown was 18.5%. Do not judge EXP-002 on week-one P&L. Day 7 checks correctness only,
as for EXP-001.

**Operations.**

- Stop: under launchd, `launchctl bootout gui/$(id -u)/com.cryptoskills.paper-exp002` (sends a graceful
  SIGTERM). In the foreground, press Ctrl-C in its terminal. Startup recovery handles the gap either way.
- Backup: `python3 -m crypto_eval paper-backup --database ~/paper-exp002.sqlite3`
- Checkpoint: `python3 -m crypto_eval paper-checkpoint --database ~/paper-exp002.sqlite3 --out reports/paper-500/exp002-<checkpoint>.json`

## What does not happen during the campaign

- No new feature branches beyond defect fixes (stop condition).
- No real-money order, amendment, cancellation, leverage/margin change, transfer, or withdrawal.
- No tuning of the running experiment from a few outcomes.

## Evidence to keep

- Checkpoint JSONs.
- Periodic export bundles (Settings → Export).
- Backups.
- Campaign notes: capital split, FX source, commit SHA, manifest versions, and any incidents
  with their resolution.
