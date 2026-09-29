# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Three loosely coupled parts:

1. **`skills/crypto-market-trading-analysis/`**: a Codex/Claude skill (`SKILL.md` plus progressively loaded `references/`). Machine-readable contracts it relies on live in `schemas/`, with examples in `examples/`.
2. **`crypto_eval/` evaluation harness**: point-in-time dataset building, frozen predictions, separate outcomes, scoring, baselines, and reports. It also contains a loopback Binance Spot analysis runtime (`serve`, `forward-*`).
3. **`crypto_eval/` PAPER futures lab**: `paper-server` with a SQLite-backed scheduler, Jev/GPT hybrid routing, a deterministic risk engine, and simulated isolated-margin fills. It is served together with the static `frontend/`.

Python uses only the standard library: there is no `pyproject.toml` or `requirements.txt`. CI runs Python 3.11 and Node 20. The frontend is dependency-free vanilla ES modules with no build step.

## Commands

```bash
python3 scripts/validate_repo.py                  # schema/example/enum/skill-structure checks (stdlib only)
python3 -m unittest discover -s tests -v          # all Python tests
python3 -m unittest tests.test_paper_futures -v   # one module
python3 -m unittest tests.test_paper_futures.LocalEnvironmentFileTests.test_dotenv_parser_loads_plain_values_without_evaluation_or_logging
node --test frontend/tests/*.test.mjs             # frontend tests
node --test frontend/tests/paper-api.test.mjs     # one frontend test file
find frontend -name '*.js' -exec node --check {} \;   # JS syntax check (CI step)
python3 -m crypto_eval demo --out-dir reports/crypto-eval-demo   # offline fixture pipeline; out-dir must not exist
```

The full CI gate is `.github/workflows/validate.yml`: validate_repo, unittest, node --check, node --test, and demo.

Run the apps:

```bash
python3 -m crypto_eval paper-server [--database .paper-smoke.sqlite3] [--no-live-stream]   # http://127.0.0.1:8765/
python3 -m crypto_eval serve --host 127.0.0.1 --port 8765 --interval 1h                   # Spot analysis runtime, /frontend/
python3 -m crypto_eval real-integration-check --symbol BTCUSDT   # REAL paid/external calls; local acceptance only
python3 -m crypto_eval paper-setup-real                          # moves .env creds into the OS credential store
python3 -m crypto_eval paper-backup [--database P]              # verified, secret-free snapshot (restore: paper-restore)
python3 -m crypto_eval paper-soak --database FRESH.sqlite3 --days 3   # accelerated restart/sleep soak, fixture data
python3 -m crypto_eval paper-checkpoint [--database P] [--dry-run]   # checkpoint report + promotion gate (never enables live)
```

All subcommands are defined in `crypto_eval/cli.py`.

## Architecture: PAPER futures pipeline

```
market snapshot (closed 15m bar + 1h/4h context)
  → compute_features / quant gate          (paper_runtime.py)
  → Jev atomic Choice/Score/Noul vector    (paper_ai.py, TypeSafe /v1/systemone)
  → versioned deterministic escalation policy (jev-escalation.v1)
  → optional Responses-compatible GPT + this repo's skill (paper_ai.py)
  → validated TradingIntent                (paper_contracts.py)
  → RiskEngine sizing/limits → PAPER fills, funding, liquidation (paper_runtime.py)
  → PaperStore (SQLite): wallets, arms, leverage cohorts, cycles, AI calls, costs
```

- `paper_runtime.py` is the core module (about 6.7k lines). It contains `RiskEngine`, `PaperStore` (all SQLite), `PaperRuntime` (cycle orchestration), `PaperScheduler` (15m scheduler plus the independent bar-monitor thread), and `PaperRuntimeReports`. `paper_server.py` is the stdlib HTTP API on top of it. The API contract for `/api/dashboard` is `schemas/paper-dashboard.schema.json`.
- Invariants to preserve: GPT can never set quantity or leverage, and it cannot override risk. Jev's `escalation_needed` cannot bypass the router. A risk rejection never creates an order or fill. A cycle key is unique per (experiment, symbol, closed candle), so retries return the stored cycle without calling Jev or GPT again. If one bar hits both stop and target, the stop wins. Missing data or unknown cost stays explicitly unavailable and is never zero-filled.
- Portfolio OS layer (`docs/ai-portfolio-trading-os.md`):
  - `market_catalog.py`: Gate Spot and Perp catalog, public GET only, fail-closed.
  - `spot_accounting.py`: Spot accounting and `SpotRiskEngine`.
  - `portfolio_os.py`: orders, position manager, authority modes, re-plan, review queue, attention and activity hooks, AI Spot allocations.
  - `portfolio_brain.py`: entry gate that can only shrink or block; never bypasses `RiskEngine`.
  - `activity.py`, `learning.py`, `portfolio_store.py`: schema and settings.
  - Reached as `runtime.portfolio`; the scheduler's monitor loop calls `portfolio.after_monitor()`.
- Portfolio OS invariants to keep:
  - AI may mutate a position only in `AUTO_PAPER`, and never to increase risk.
  - Only the user changes authority.
  - Perp order requests never carry a quantity.
  - Confirmation-required actions return HTTP 409.
- `sleeves.py` is the second strategy engine (`strategy_engine: "sleeves_v1"`, EXP-002): Donchian, TSMOM and XSMOM sleeves, each in its own cohort/wallet (`sleeve-don`, `sleeve-ts`, `sleeve-xs`), with a monthly `wallet_transfers` capital rebalance. Use `capital_cohorts(config)` wherever the code sums wallets or positions; never hard-code `"primary"`. See `docs/trend-sleeves-engine.md`.
- `ai_cost.py` holds the AI usage/cost ledger, the versioned price book, and budget guards (`BLOCK_PAID_AI`, `FALLBACK_QUANT`, `JEV_ONLY`, `PAUSE_NEW_ENTRIES`). Paid calls fail closed when their price is unknown.

## Current baseline and canonical active goal

The implementation baseline is `main` after Phase 5 (Experiment Promotion Gates, PR #7). Phases 1–5 are merged:
- Portfolio OS (`docs/ai-portfolio-trading-os.md`);
- crash, price, liquidity and execution safety (`docs/crash-execution-safety.md`);
- the Spot lifecycle manager (`docs/spot-cycle-lifecycle-manager.md`);
- continuous resilience (`docs/continuous-paper-resilience.md`);
- experiment manifests, checkpoints and the promotion gate (`docs/experiment-promotion-gates.md`).

**FIRST PRIORITY (set 2026-09-29): DAY-TRADE FUTURES.** The canonical active goal is:

- `.goals/day-trade-futures/goal.md`
- `.goals/day-trade-futures/status.json`

The goal is a PAPER futures day-trading engine that makes a net profit per day after fees,
slippage and funding. Every position closes within 24h, and it trades long and short on BTC, ETH,
NEAR, SEI, SUI, AVAX and ENA. It advances through gated stages: research → engine → runtime replay
→ a short forward PAPER test (EXP-003, ≥14 days and ≥150 trades) → review. Day trading is the way
the forward test shrinks from 90 days to 2–3 weeks.

EXP-001 (15m breakout + AI, `:8765`) and EXP-002 (trend sleeves, `:8768`) keep running untouched
as background evidence. Their operations are in `docs/paper-500-campaign-runbook.md`. Scheduled
tasks check them twice a day with `scripts/paper_campaign_status.py`.

`feature/live-execution-gateway` stays `PLANNING_ONLY_LIVE_DISABLED`: do not implement it. A gate
PASS never enables real execution.

CI and unit tests remain offline and fake-only. Real Gate money-moving writes remain blocked by design.

## Safety boundaries (enforced by design, keep them)

- No real-money execution path. Any Gate order, leverage change, transfer, or withdrawal must stay blocked or mocked.
- Servers bind to loopback only. `paper-server` rejects non-loopback hosts. Remote use goes only through a private, tailnet-only `tailscale serve` proxy, with its exact HTTPS origin listed in `PAPER_TRUSTED_ORIGINS` (see `docs/ops/remote-access.md`). Never use a public tunnel (funnel, ngrok) and never use a `0.0.0.0` bind.
- Credentials are read only from the server process env, the repo `.env`, or the OS credential store. `envfile.py` parses `.env` as plain key=value and never evaluates it; existing env vars win. Settings accepts a key once and the server stores it in the OS credential store, returning only masked metadata; the browser never persists or receives secret values. Secrets must never reach SQLite, prompts, logs, API responses, or export bundles.
- Keep historical analysis point-in-time safe: no observation later than `data_cutoff`. A wait whose trigger never fires is not scored as a failed entry.

## Conventions

- The canonical decision-state vocabulary lives only in `schemas/decision-state.schema.json`. `validate_repo.py` checks enum consistency against it.
- `validate_repo.py` has its own minimal YAML reader, so keep `examples/*.yaml` within simple maps, lists, scalars, and inline arrays.
- Put domain guidance in `SKILL.md` or a focused `references/*.md`, contracts in `schemas/`, and deterministic checks in `scripts/`.
- `README.th.md` is a Thai mirror of `README.md`. Update both when user-facing docs change.
- `reports/`, `*.jsonl`, `.env*` (except `.env.example`), and `.crypto-eval/` are git-ignored outputs.
