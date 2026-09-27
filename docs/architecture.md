# Trading Skill Architecture

`crypto-market-trading-analysis` is a read-only reasoning contract. It describes how to collect and challenge market evidence; it is not an order router, custody system, or portfolio accounting engine.

## Pipeline

### 1. Context and cutoff

Resolve the asset, quote, venue, instrument, horizon, risk style, portfolio context, and analysis timestamp. For historical work, freeze the point-in-time cutoff before fetching data.

### 2. Evidence collection

Collect only the layers relevant to the request:

- market structure and execution levels
- spot volume, CVD, taker flow, and absorption
- perpetual/dated futures OI, funding, basis, liquidations, and leverage
- options IV, skew, term structure, and expiry risk where liquid
- on-chain flows, holder behavior, stablecoin liquidity
- token supply, unlocks, valuation, adoption, and protocol activity
- BTC/ETH relative strength, macro liquidity, and scheduled catalysts

Each observation gets typed metric/value fields when applicable, separate observed and
retrieved timestamps, a structured source, a quality label, and an evidence family.
Missing data remains missing.

### 3. Neutral ledger

The ledger is the single source of facts for the next stages. It prevents confirmation bias, makes source discrepancies visible, and allows Bull and Bear to challenge the same evidence. See [evidence-ledger.md](../skills/crypto-market-trading-analysis/references/evidence-ledger.md) and [evidence-ledger.schema.json](../schemas/evidence-ledger.schema.json).

### 4. Adversarial synthesis

Bull and Bear each state their strongest thesis, the strongest opposing evidence, confirmation conditions, and invalidation. The research judge selects the stronger evidence set or identifies a genuine information gap. Conflicting signals do not automatically mean HOLD; direction and timing are judged separately.

### 5. Execution and risk

The trader turns the research view into entry zones, invalidation, targets, timing conditions, and sizing logic grounded in actual levels and volatility. Three risk lenses then stress-test the plan:

- **Aggressive:** opportunity cost, trend persistence, and squeeze upside.
- **Neutral:** best risk-adjusted implementation and staged execution.
- **Conservative:** leverage unwind, unlocks, thin liquidity, gaps, custody, smart-contract, and regulatory risks.

The portfolio decision synthesizes material risks rather than taking a majority vote.

### 6. Human response and memory

The default output exposes one decision state and only the decisive reasons. Detailed committee reasoning is available only when requested. If a journal exists, persist a structured decision record and evaluate it later using a point-in-time-safe outcome window. See [decision-memory.md](../skills/crypto-market-trading-analysis/references/decision-memory.md).

## State machine

```text
NEUTRAL EVIDENCE
      │
      ├── directional bias unclear ──► HOLD / NO_TRADE
      │
      ├── direction clear, timing poor ──► WAIT_FOR_PULLBACK / WAIT_FOR_BREAKOUT_CONFIRMATION
      │
      ├── direction + timing confirmed ──► ACCUMULATE / ENTER_LONG / ENTER_SHORT
      │
      └── existing thesis weakened ──► REDUCE / HEDGE_DE_RISK / EXIT
```

## Canonical decision state

The final state vocabulary is defined once in
[`schemas/decision-state.schema.json`](../schemas/decision-state.schema.json) and
must be used by analysis output, decision records, documentation, and runtime
adapters. Human-readable labels and internal execution verbs map to it as follows:

| Internal/runtime label | Canonical final state |
|---|---|
| `BUY` / `ACCUMULATE` | `ACCUMULATE` |
| `WAIT` with a support condition | `WAIT_FOR_PULLBACK` |
| `WAIT` with a breakout condition | `WAIT_FOR_BREAKOUT_CONFIRMATION` |
| `SELL` / `SHORT` after confirmation | `ENTER_SHORT` |
| `BUY` after confirmation | `ENTER_LONG` |
| `HEDGE` | `HEDGE_DE_RISK` |
| `TAKE_PROFIT` | `TAKE_PARTIAL_PROFIT` or `EXIT` |

The validator checks every schema enum against this canonical file so vocabulary
drift cannot silently reach production.

## Runtime boundaries

- Runtime adapters fetch data; the skill interprets it.
- The skill never receives or stores private keys, account secrets, or custody authority.
- Order placement requires a separate, explicitly authorized system.
- Historical evaluation must keep the original decision and later outcome in separate records.
- A data-quality failure lowers confidence or blocks a lane; it never becomes a bullish, bearish, or neutral signal by assumption.

## Evaluation harness boundary

The separate `crypto_eval` package validates normalized point-in-time cases,
freezes prediction records, attaches later outcomes in a distinct input, and
calculates trigger-aware decision-quality metrics and fixed baselines. It does
not modify the production skill workflow, invoke a model by default, or execute
trades. See [evaluation.md](evaluation.md) for the CLI, lifecycle, metric
denominators, and the distinction between validation and accuracy evidence.
