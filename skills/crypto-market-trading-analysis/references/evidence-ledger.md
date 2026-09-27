# Evidence Ledger

Use this reference when a request needs an auditable analysis, a historical `as_of` cutoff, or a detailed Bull/Bear challenge. The ledger is the boundary between observed data and model interpretation.

## Entry contract

```yaml
evidence_ledger:
  - claim: "Daily structure remains HH/HL"
    observation_time: "2026-09-27T00:00:00Z"
    source: "Binance spot OHLCV"
    evidence_type: price_structure
    quality: high
    supports: bull
    notes: "Last confirmed daily higher low at ..."
```

Required fields are `claim`, `observation_time`, `source`, `evidence_type`, `quality`, and `supports`. `supports` is `bull`, `bear`, or `neutral`; use `neutral` for descriptive evidence that does not yet establish direction.

## Ledger rules

- Record the observation before writing the implication.
- Keep the same evidence set for Bull and Bear; neither side may silently rewrite a fact.
- Correlated indicators from one evidence family count as one family, not independent votes.
- A missing or stale source lowers confidence; it is not neutral evidence.
- Any exact entry, stop, or target must trace to a current level, volatility measure, liquidity zone, or explicit user constraint.
- News can explain a move, but price and flow acceptance decide whether it matters to the trade.
- Record source freshness and known discrepancies instead of silently selecting the more convenient value.

## Review questions

1. Which two to four ledger entries actually decided the judgment?
2. What is the strongest opposing entry, and did the thesis address it directly?
3. Which condition would invalidate the interpretation rather than merely weaken it?
4. Is the decision directional, or is direction valid but timing unattractive?

The machine-readable contract lives at `schemas/evidence-ledger.schema.json` in the repository root.
