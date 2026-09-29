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