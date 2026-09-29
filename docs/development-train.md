# Crypto-Skills Development Train — Stop Building, Start Proving

## Purpose

This is the fixed development sequence for the current product program. Do not add new major feature phases unless testing uncovers a concrete blocking gap.

After the phases below, development shifts to evidence gathering, PAPER operation, defect fixing, and experiment iteration rather than continuous feature expansion.

## Merge / Development Order

1. `feature/ai-portfolio-trading-os`
   - Finish the human + AI portfolio/trading product loop.
   - Spot + Perpetual PAPER, portfolio, positions, orders, authority modes, re-plan, Portfolio Brain, activity/attention, strategy tournament.

2. `feature/crash-execution-safety`
   - Add deterministic crash, price, liquidity, execution, reconciliation, duplicate, wrong-side, TTL and kill-switch protection.
   - AI intent must not bypass execution safety.

3. `feature/spot-cycle-lifecycle-manager`
   - Add long-cycle Spot behavior: Core/Tactical, accumulate/hold/trend/protect/distribute/exit/cash-wait states.
   - Benchmark against Buy & Hold, fixed take-profit, rebalance, grid and trailing approaches.

4. `feature/continuous-paper-resilience`
   - Make the local runtime reliable enough for 60-90+ day unattended PAPER experiments.
   - Restart recovery, scheduler continuity, market-data gap fill, persistence integrity, watchdog/health, backups and incident evidence.

5. `feature/experiment-promotion-gates`
   - Freeze experiment definitions and define evidence gates between backtest/shadow/PAPER/tiny-live.
   - Prevent self-deception, strategy mutation inside one experiment, and promotion from weak samples.

6. `feature/live-execution-gateway`
   - Future conditional phase only.
   - DO NOT IMPLEMENT until PAPER promotion gates explicitly pass.
   - Adds the smallest possible real-execution adapter on top of already-proven risk, crash safety, execution planning and reconciliation.

## Status

| Phase | Branch | State | Evidence |
|-------|--------|-------|----------|
| 1 | `feature/ai-portfolio-trading-os` | MERGED (PR #3, `ff4c633`) | `docs/ai-portfolio-trading-os.md`; 211 Python / 66 frontend tests; browser QA on real Gate data; real Jev + GPT-6 Luna re-plan acceptance PASS; live AI-opened entry NOT_VERIFIED (market had no qualifying setup; `portfolio-real-check --full-loop`) |
| 2 | `feature/crash-execution-safety` | MERGED (PR #4, `ca34171`) | `docs/crash-execution-safety.md`; 238 Python / 71 frontend tests; 10 torture scenarios on fixtures; browser QA; real Gate public-data safety acceptance PASS; live crash event NOT_VERIFIED (none occurred) |
| 3 | `feature/spot-cycle-lifecycle-manager` | MERGED (PR #5, `e21f91f`) | `docs/spot-cycle-lifecycle-manager.md`; 264 Python / 74 frontend tests; 8 lifecycle scenarios; browser QA; real Gate 4h regime + benchmark acceptance PASS; AI lifecycle recommendation via Jev/Luna NOT_VERIFIED (deterministic policy only) |
| 4 | `feature/continuous-paper-resilience` | MERGED (PR #6, `504604a`) | `docs/continuous-paper-resilience.md`; 288 Python / 75 frontend tests; recovery drills; 7-day accelerated soak PASS (13 restarts, 0 duplicates); real process kill/TERM/lock/backup acceptance PASS; cycle-cost growth fixed; real-time multi-day soak NOT_VERIFIED |
| 5 | `feature/experiment-promotion-gates` | COMPLETE, PR pending | `docs/experiment-promotion-gates.md`, `docs/paper-500-campaign-runbook.md`; 303 Python / 76 frontend tests; gate rehearsal of every status; browser QA; real-provider manifest + checkpoint acceptance PASS |
| 6 | `feature/live-execution-gateway` | PLANNING_ONLY_LIVE_DISABLED | — |

## Dependency Rule

Each phase is developed only after the previous phase is complete, reviewed, merged to main, and the next branch is synchronized onto that new main.

Do not stack large unreviewed implementations across multiple branches.

## Testing Phase After Development

After phases 1-5 are merged:

- freeze a PAPER experiment with 500 USDT starting capital;
- run checkpoints at 7 / 30 / 60 / 90 days;
- target at least 200-300 completed trades before drawing stronger strategy conclusions;
- compare aligned strategy arms and Spot lifecycle baselines;
- track net trading PnL, fees, funding, slippage, AI cost and net economic PnL;
- investigate reliability/safety defects immediately;
- create a new experiment version for material strategy changes instead of mutating a running experiment.

The operational procedure, including setup, daily checks, checkpoint actions, and incident
handling, is `docs/paper-500-campaign-runbook.md`. Checkpoints are produced by
`paper-checkpoint` and gated by `docs/experiment-promotion-gates.md`.

## Stop Condition

Do not create more major feature branches merely because additional ideas are possible.

During the PAPER campaign, new development should be limited to:

- correctness defects;
- safety defects;
- reliability defects;
- observability required to explain results;
- experiment methodology defects;
- a clearly demonstrated strategy gap supported by experiment evidence.

Everything else goes to a future backlog.

## Live Capital Gate

`feature/live-execution-gateway` remains planning-only until the PAPER program shows adequate evidence and the system has no unresolved critical safety, accounting, reconciliation, or resilience defects.

Passing PAPER does not guarantee future profit; it only permits a controlled next validation stage.