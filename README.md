# Crypto Skills

Crypto-native Codex skills for evidence-based market analysis, execution planning, and risk-aware investment decisions.

The repository currently ships one production skill: `crypto-market-trading-analysis`. It adapts the staged analyst → bull/bear research → trader → risk committee → portfolio decision workflow to crypto markets, where spot flow, leverage, funding, liquidations, tokenomics, and the BTC regime materially change trade quality.

## Design goals

- Separate observations from interpretation with an evidence ledger.
- Treat price structure and spot participation as primary; use derivatives to explain fragility.
- Make Bull and Bear challenge the same evidence instead of creating competing narratives.
- Separate direction from timing: a bullish asset can still be `WAIT FOR PULLBACK`.
- Ground entries, invalidations, and targets in observable levels and volatility.
- Preserve point-in-time integrity for historical analysis and backtests.
- Store decisions and outcomes so timing, leverage, and thesis quality can be reviewed later.
- Keep the internal analysis deep while the default human response stays concise and decision-first.

## Architecture

```text
Market / spot / derivatives / options / on-chain / tokenomics / macro
                                │
                                ▼
                    Normalize + timestamp + quality-check
                                │
                                ▼
                         Neutral evidence ledger
                                │
                         ┌──────┴──────┐
                         ▼             ▼
                    Bull thesis    Bear thesis
                         └──────┬──────┘
                                ▼
                         Research judge
                                ▼
                         Execution planner
                    entry / invalidation / targets
                                ▼
                   aggressive / neutral / conservative
                              risk lenses
                                ▼
                         Portfolio decision
                                ▼
              concise answer + monitoring conditions + journal record
```

This is a logical decomposition inside one capable model, not a requirement to run separate agents.

## Repository layout

```text
crypto-skills/
├── README.md
├── docs/
│   └── architecture.md                 # workflow and implementation boundaries
├── schemas/
│   ├── analysis-output.schema.json     # final decision contract
│   ├── decision-record.schema.json     # journal / outcome contract
│   └── evidence-ledger.schema.json    # fact ledger contract
├── examples/
│   ├── analysis-output.yaml
│   ├── decision-record.yaml
│   └── evidence-ledger.yaml
├── scripts/
│   └── validate_repo.py                # dependency-free structural checks
└── skills/
    └── crypto-market-trading-analysis/
        ├── SKILL.md                    # complete Codex skill instructions
        ├── agents/openai.yaml           # UI metadata and invocation policy
        ├── examples/                    # skill-local examples
        └── references/                   # progressively-loaded operating contracts
```

## Install for Codex

From this checkout, link the skill into the local Codex skills directory:

```bash
mkdir -p ~/.codex/skills
ln -sfn "$PWD/skills/crypto-market-trading-analysis" \
  ~/.codex/skills/crypto-market-trading-analysis
```

If symlinks are not appropriate, copy the directory instead. The repository does not contain credentials and does not persist API keys; connect CoinMarketCap, exchange, options, or on-chain data sources through the runtime environment.

## Invoke

Use the skill explicitly:

```text
$crypto-market-trading-analysis วิเคราะห์ SEI/USDT แบบ spot swing พร้อม buy zone, invalidation, targets และ risk
```

The skill accepts missing optional context and states its assumptions. For large allocations, leverage, illiquid assets, major events, or multi-x targets, it escalates the internal analysis depth automatically.

## Human-facing response contract

The default response is concise and follows this order:

1. Decision state
2. Preferred and secondary entry zones
3. Invalidation
4. Targets and horizon
5. Three to five decisive reasons
6. One material risk / what would change the view

When the user asks for a detailed report, the skill can expose the market snapshot, evidence ledger, scenario map, decision object, and the conditions that would change its mind. It never treats an indicator, funding rate, headline, or model confidence as a guarantee.

## Data and safety boundaries

- Timestamp every current-market observation with timezone, venue, and instrument.
- Prefer exchange and project primary sources; use aggregators for cross-venue context.
- Normalize mark/index/last price, USD-notional OI, contract type, and funding interval before comparison.
- Keep unavailable or stale data explicitly unavailable; never zero-fill missing evidence.
- Keep historical analysis point-in-time safe: no later candles, unlocks, news, or outcomes.
- Treat API keys, private account data, order placement, and custody actions as outside this read-only skill.

## Validate

Run the repository checks after changing the skill:

```bash
python3 scripts/validate_repo.py
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/crypto-market-trading-analysis
```

The first command checks the repository contracts and required TradingAgents-inspired sections. The second checks Codex skill frontmatter, naming, and scaffold hygiene.

## Contributing

Keep reusable domain guidance in `SKILL.md` or a focused reference. Put machine-readable contracts in `schemas/`, examples in `examples/`, and deterministic checks in `scripts/`. Do not commit credentials, exchange secrets, private portfolio data, or generated market snapshots. Any new decision rule should explain its evidence, data-quality assumptions, and failure mode.
