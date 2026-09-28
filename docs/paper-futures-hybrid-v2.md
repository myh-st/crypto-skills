# Hybrid Jev + GPT-6 Luna Architecture (v2)

## Objective

Reduce decision latency and model cost while increasing auditability by separating **fast typed judgment** from **deep reasoning**.

Jev handles high-frequency atomic judgments. GPT-6 Luna + the crypto skill handles only the cases that benefit from extended reasoning. Deterministic code owns money, risk, execution, and accounting.

## Runtime

```text
                           +----------------------+
Real futures data -------->| Point-in-time state  |
                           +----------+-----------+
                                      |
                                      v
                           +----------------------+
                           | Quant feature engine |
                           +----------+-----------+
                                      |
                                      v
                           +----------------------+
                           | Jev System-One       |
                           | Choice/Score/Noul    |
                           +----------+-----------+
                                      |
                              decision vector
                                      |
                    +-----------------+----------------+
                    |                                  |
             simple/confident                   uncertain/conflict
                    |                                  |
                    |                                  v
                    |                         +-------------------+
                    |                         | GPT-6 Luna +      |
                    |                         | crypto skill      |
                    |                         +---------+---------+
                    |                                   |
                    +-----------------+-----------------+
                                      v
                              TradingIntent
                                      |
                                      v
                              Risk Engine
                                      |
                                      v
                              PAPER Engine
```

## Atomic Decision Design

Prefer several narrow questions in one Jev call instead of a single broad trading question.

Example state:

```json
{
  "symbol": "SOLUSDT",
  "decision_tf": "15m",
  "market": {
    "regime_4h": "...",
    "trend_1h": "...",
    "atr_percentile": 0.71,
    "volume_zscore": 1.8,
    "distance_to_breakout": 0.003,
    "btc_relative_strength": 0.64,
    "funding": 0.00012,
    "oi_change_1h": 0.047
  },
  "quant": {
    "direction": "long",
    "signal_strength": 0.72,
    "trigger": "breakout"
  },
  "portfolio": {
    "has_position": false,
    "exposure_pct": 0.12
  }
}
```

Question set:

```json
{
  "market_regime": {
    "type": "choice",
    "instructions": "Classify the current tradeable market regime",
    "criteria": {
      "bull": "Directional bullish regime",
      "bear": "Directional bearish regime",
      "sideways": "Range-bound or mean-reverting regime",
      "unstable": "Conflicting/high-instability regime"
    }
  },
  "breakout_valid": {
    "type": "noul",
    "instructions": "The breakout evidence is strong enough to treat as a valid breakout setup"
  },
  "setup_quality": {
    "type": "score",
    "instructions": "Rate the setup quality",
    "criteria": [
      "poor",
      "weak",
      "acceptable",
      "good",
      "excellent"
    ]
  },
  "signal_conflict": {
    "type": "noul",
    "instructions": "Important evidence materially conflicts with the proposed direction"
  },
  "escalation_needed": {
    "type": "noul",
    "instructions": "This case requires deeper multi-factor reasoning before creating a trading intent"
  }
}
```

The actual implementation may expand this set but should keep questions atomic.

## Decision Vector

Normalize Jev output into a versioned internal object:

```text
JevDecisionVector
  snapshot_hash
  model
  model_version
  question_schema_version
  answers[]
    key
    type
    value
    confidence?
    probabilities?
  request_usage?
  latency_ms
  observed_at
```

Never treat probabilities as calibrated for our crypto workload until measured prospectively.

## Escalation Router

Escalation is deterministic code.

Example logic:

```text
if force_luna:
    escalate
elif critical_confidence < threshold:
    escalate
elif signal_conflict >= threshold:
    escalate
elif regime == unstable:
    escalate
elif quant_direction conflicts with jev:
    escalate
elif existing_position and risk context changed materially:
    escalate
else:
    remain fast path
```

Version all thresholds.

Do not let Jev self-authorize a bypass around this router.

## Fast Path

A fast-path case can create a TradingIntent only when:

- required market lanes are fresh;
- quant signal is eligible;
- Jev outputs pass schema checks;
- escalation policy does not require Luna;
- a deterministic intent builder can map approved decision states to entry/stop/target rules;
- risk engine approves.

If trade levels cannot be derived safely without deeper reasoning, escalate rather than invent them.

## Deep Path

GPT-6 Luna receives:

- bounded point-in-time snapshot;
- quant features/signals;
- Jev decision vector;
- conflicts/uncertainties;
- current portfolio context;
- crypto skill instructions.

Luna should not receive raw secrets or future outcome data.

Its output remains schema-validated.

## Settings Data Model

ProviderConfig:

```text
id
kind = typesafe | openai | foundry | compatible
display_name
base_url
model
enabled
timeout_seconds
credential_ref
capabilities
last_validated_at
last_validation_status
```

Credential values are outside normal application storage.

TypeSafe defaults:

```text
base_url = https://api.typesafe.ai
endpoint = /v1/systemone
model = jev-latest
auth = Bearer
```

Do not assume these remain stable forever; centralize them in the adapter.

## PoC Evaluation

The goal is not merely "Jev is cheaper".

Measure:

```text
quality:
  decision quality
  trigger quality
  strategy expectancy
  PnL after costs
  max drawdown

routing:
  Jev calls
  Luna escalation rate
  Luna avoided calls
  fallback/error rate

latency:
  Jev p50/p95
  Luna p50/p95
  full-cycle p50/p95

cost:
  Jev estimated cost
  Luna estimated cost
  total cost / eligible case
  total cost / executed trade
```

Compare all metrics on aligned cases.

## Failure Policy

If Jev is unavailable:

- do not silently assume approval;
- obey experiment-configured fallback:
  - DEFER,
  - GPT_FALLBACK,
  - or SKIP;
- persist the event.

If GPT escalation fails:

- no new trade unless the configured experiment explicitly permits a validated fast-path fallback;
- persist the failure.

Risk engine remains fail-closed.

## Security

- TypeSafe API key and GPT API key are independent credential references.
- No raw credential is exposed by GET APIs.
- Masking must not leak length/prefix beyond what is necessary.
- Logs redact Authorization headers.
- Exports contain provider IDs/config but no raw secret.
- Browser must not retain credentials in localStorage/session persistence.
- Local server binds loopback by default.

## Build Order

1. provider abstraction + secure credential handling
2. TypeSafe adapter + mocks/tests
3. JevDecisionVector contract
4. atomic question schema
5. deterministic escalation router
6. Jev metrics/usage persistence
7. Hybrid experiment arms
8. Settings UI
9. Runtime pipeline integration
10. evaluation/export/dashboard
11. end-to-end local smoke with real market data + PAPER execution
