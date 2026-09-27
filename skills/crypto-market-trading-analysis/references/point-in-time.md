# Point-in-Time Integrity

Use this reference for historical analysis, backtests, retrospective decisions, and any request containing `as_of`, a date, or a past candle.

## Cutoff rule

Let `as_of` be the analysis timestamp. Every input must have been public and observable at or before that instant:

- only closed candles, or an explicitly labelled still-open candle
- funding, OI, basis, liquidations, and options observations no later than `as_of`
- macro releases after their actual publication time, not their later revision
- token supply and unlock information publicly known by `as_of`
- news published by `as_of`
- no later outcomes, labels, reflections, or benchmark returns in the decision itself

If the timestamp or publication time cannot be established, label the conclusion approximate and do not use it as strong validation evidence.

## Runtime checklist

```text
1. Freeze the cutoff before fetching data.
2. Record the source timestamp for every material observation.
3. Reject or label observations newer than the cutoff.
4. Keep the original decision separate from the later outcome.
5. Evaluate the outcome only after the outcome window closes.
```

Point-in-time integrity prevents look-ahead bias: a model must not appear accurate because it was allowed to see the future.
