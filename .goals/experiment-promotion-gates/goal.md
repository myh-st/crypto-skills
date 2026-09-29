# Goal: Experiment Promotion Gates

## Dependency gate

Do not implement until `feature/continuous-paper-resilience` is complete, reviewed, merged to main, and this branch is synchronized onto that final main.

## Mission

Prevent the project from fooling itself.
Freeze what an experiment means, compare treatments fairly, and require explicit evidence before moving from research to prolonged PAPER and eventually tiny-live validation.

This phase is methodology/governance for the trading system, not another strategy-feature phase.

## Promotion ladder

IDEA -> BACKTEST / REPLAY -> WALK-FORWARD where applicable -> SHADOW -> PAPER -> PAPER CHECKPOINTS -> LIVE-ELIGIBLE REVIEW

The system must never automatically enable real trading merely because a gate passes.

## Experiment immutability

After an experiment begins, material fields are frozen under that experiment ID:
- strategy/policy version;
- prompt/skill version or hash;
- model/provider/deployment policy;
- market universe rules;
- feature version;
- risk policy;
- Portfolio Brain policy;
- Crash / execution policy;
- Spot lifecycle policy;
- AI budget/pricing policy;
- execution assumptions;
- data-cutoff rules;
- benchmark-arm definitions.

Material changes create a new experiment version/ID.
Operational fixes may be recorded separately only when they do not change strategy treatment; otherwise start a new experiment.

## Evidence integrity

- Keep predictions/decisions frozen before outcomes are observed.
- Preserve point-in-time data boundaries.
- Compare aligned samples/windows when claiming incremental AI value.
- Report denominators and unavailable metrics.
- Do not promote from a single exceptional trade.
- Do not silently exclude losing periods/trades.
- Do not rewrite historical pricing or experiment configuration.
- Distinguish defect fixes from strategy changes.

## PAPER 500 campaign

Initial campaign assumption:
- starting PAPER capital: 500 USDT;
- checkpoints: day 7, 30, 60, 90;
- intended stronger-evidence target: at least roughly 200-300 completed trades where strategy frequency permits;
- continue longer when sample size/regime coverage is inadequate rather than force a conclusion.

Day 7 focuses on correctness/reliability, not profitability judgment.

## Required gate dimensions

### Correctness / safety
- no unresolved critical accounting divergence;
- no duplicate logical execution;
- no wrong-side/reversal defect;
- no unresolved reconciliation failure;
- Crash Safety tests pass;
- restart/recovery drills pass;
- no secret leakage;
- real-money writes remain disabled during PAPER.

### Trading economics
- net trading PnL;
- fees;
- funding;
- slippage;
- AI cost;
- net economic PnL;
- expectancy;
- profit factor;
- drawdown;
- return distribution;
- no dependence on one trade without explicit disclosure.

Positive values are not sufficient by themselves; uncertainty and sample size must be shown.

### AI incremental value
Compare aligned arms such as Quant, Jev, Luna, Hybrid and Portfolio Brain treatments.
Show incremental PnL and incremental AI cost only on comparable samples.
Do not claim causality from unmatched cases.

### Spot lifecycle evidence
Compare Buy & Hold, fixed TP, rebalance/grid/trailing baselines where applicable, AI Lifecycle and AI Lifecycle + Portfolio Brain.
Include peak capture, profit giveback, turnover and net economic result.

### Regime robustness
Report results by observable market regime/asset where sample size permits.
Do not claim robustness when the campaign observed only one narrow regime.

### Operational resilience
Report uptime/degraded periods, missed/recovered cycles, feed gaps, provider outages and recovery events.

## Gate result

Each promotion review must result in exactly one status:
- PASS;
- CONTINUE_COLLECTING_DATA;
- FAIL_STRATEGY;
- FAIL_SAFETY;
- FAIL_RELIABILITY;
- INVALID_EXPERIMENT.

Gate decisions are evidence summaries, not profitability guarantees.

## Promotion to tiny-live eligibility

Passing the PAPER gate means only that a separate live-execution implementation/review may be considered.
It does not enable live trading.

At minimum, unresolved critical safety/reconciliation/reliability findings block promotion regardless of PnL.

## UX

Evaluations should show:
- experiment identity/version;
- frozen configuration hash;
- elapsed days and completed trades;
- checkpoint status;
- economic metrics;
- safety/reliability incidents;
- arm comparisons;
- regime/sample coverage;
- promotion status and blockers.

Keep the decision concise; raw evidence remains drill-down/exportable.

## Definition of Done

- experiment material configuration is immutable/versioned;
- checkpoints can be generated reproducibly;
- promotion criteria are explicit/configurable and audited;
- aligned strategy/AI comparisons show denominators;
- PAPER 500 campaign has a canonical evaluation report;
- safety/reliability failures block promotion;
- no system path automatically enables real execution;
- development-train stop condition is enforced/documented.