# Goal Summary: Hybrid Jev + GPT Paper Futures Trading Platform

## Outcome

The iteration-4 Inspector verdict is **PASS**. The branch implements a local,
PAPER-only hybrid research runtime and operational dashboard. Deterministic
code owns risk, execution, accounting, and scheduling; Jev provides typed
System-One decisions, with conditional GPT Responses escalation.

## Acceptance Criteria

1. **Run the local app continuously — achieved.** `python3 -m crypto_eval paper-server`
   starts a loopback-only runtime independent of the browser page.
2. **Configure providers without secret leakage — achieved.** Provider settings
   use server-side environment-variable references. A blank local `.env` is
   ignored by Git; `.env.example` contains variable names only.
3. **Test both providers — implemented; live validation deferred.** User-triggered
   TypeSafe/Jev and GPT Responses Test Connection paths are implemented and
   tested with mock transports. No live credentials or external provider calls
   were used in this session.
4. **Start a 15m PAPER experiment — achieved.** EXP-001 defaults to a virtual
   100 USDT account, 15m decisions, 1h/4h context, 60-second post-close delay,
   3x primary leverage, 1% risk, and the configured leverage cohorts.
5. **Ingest futures market data — implemented; live connectivity deferred.**
   Public USD-M market-data support and deterministic offline fixtures are
   available. Browser and CI verification used fixtures; no live market-data
   request was made.
6. **Compute deterministic signals — achieved.** Compact feature generation
   and the quant signal gate are deterministic and persist ANALYZE/SKIP results.
7. **Run Jev atomic decisions — implemented; live validation deferred.** The
   typed Jev adapter and decision-vector validation are covered by mocks.
8. **Escalate selected cases to Luna + skill — achieved in the hybrid routing
   implementation.** Escalation is deterministic, versioned, and observable;
   provider execution was validated offline only.
9. **Create validated TradingIntent — achieved.** Provider output is checked
   against the intent contract before it can reach risk controls.
10. **Apply deterministic risk controls — achieved.** Risk-based sizing,
    leverage/margin limits, loss stops, liquidation, fees, funding, and
    kill-switch behavior are deterministic.
11. **Simulate leveraged futures execution — achieved.** PAPER execution covers
    long/short positions, order simulation, fills, reduce-only, fees, funding,
    and liquidation without a live exchange order path.
12. **Persist and recover state — achieved.** SQLite-backed runtime state,
    archived market history, scheduler idempotency, and restart recovery are
    covered by tests.
13. **Inspect routing, costs, latency, and PnL — achieved.** The dashboard shows
    Quant/JeV/escalation/Luna/risk/execution routing, portfolio state, costs,
    latency, risk events, and cycle history. Unknown/interrupted states are
    shown as unknown rather than inferred.
14. **Compare experiment arms — achieved.** Quant, Jev, Luna, Luna + Skill,
    Quant + Jev, and Hybrid are supported with aligned point-in-time inputs,
    alongside leverage cohorts and asset/regime evaluation.
15. **Export for offline analysis — achieved.** Secret-free bundles include
    manifest/configuration, decisions, signals, risk, trades, positions,
    equity, AI usage/cost/latency, and grouped evaluation data; totals reconcile
    to the persistent ledgers.
16. **Keep real-money trading impossible — achieved for this branch.** Runtime
    validation restricts execution to PAPER and no live order-submission route
    is exposed.

## Verification

- `python3 scripts/validate_repo.py` — passed (16 schemas, 3 examples, 4
  evaluation examples).
- `python3 -m unittest discover -s tests -v` — passed, 111 tests.
- `node --test frontend/tests/*.test.mjs` — passed, 24 tests.
- Python compilation, relevant JavaScript syntax checks, and `git diff --check`
  passed.
- Independent browser QA used a loopback-only, fixture-backed runtime. It
  verified the paper-trading view, retention defaults/warning, provider fixture
  flows, runtime lifecycle, positions, risk/activity, evaluation, charts, and
  export. No live provider, public market-data, or exchange call was made.
- Independent Inspector iterations 1–3 identified defects; iteration 4 passed
  after the fixes were implemented and rechecked.

## Iteration History

| Iteration | Verdict | Main feedback |
|---|---|---|
| 1 | FAIL | Fix provider credential resolution and GPT reasoning-effort validation. |
| 2 | FAIL | Expose full decision routing, evaluation by asset/regime, and escalation rate. |
| 3 | FAIL | Preserve unknown interrupted states, calculate grouped drawdown, and add retention controls. |
| 4 | PASS | Prior defects and safety/accounting checks verified. |

## Deferred Validation and Recommendations

- Live TypeSafe/Jev, Foundry/OpenAI-compatible provider validation and public
  market-data connectivity were deliberately not exercised in this session.
  Test locally only after rotating the credentials that were exposed in chat.
- The local `.env` is untracked and ignored; never commit it. Use `.env.example`
  only as the variable-name reference and restart the server after configuring
  fresh credentials.
- Begin with fixture mode and PAPER execution. Review EXP-001 outputs and
  retention settings before any future changes to research scope.
- This branch does not enable real-money trading; Testnet or live execution is
  outside this goal.
