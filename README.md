# Crypto Skills

ภาษา: English · [ไทย](README.th.md)

Crypto-native Codex skills for evidence-based market analysis, execution planning, and risk-aware investment decisions, plus a local-only PAPER futures research lab.

The repository ships one production skill, `crypto-market-trading-analysis`, and a separate local PAPER futures runtime. The runtime combines public Binance USD-M market data or deterministic fixtures, deterministic features and risk controls, typed Jev decisions, conditional Responses-compatible GPT escalation, isolated-margin paper fills, persistent evaluation arms, and secret-free exports. It has no real-money order endpoint.

## Design goals

- Separate observations from interpretation with an evidence ledger.
- Treat price structure and spot participation as primary; use derivatives to explain fragility.
- Make Bull and Bear challenge the same evidence instead of creating competing narratives.
- Separate direction from timing: a bullish asset can still be `WAIT_FOR_PULLBACK`.
- Ground entries, invalidations, and targets in observable levels and volatility.
- Preserve point-in-time integrity for historical analysis and backtests.
- Store decisions and outcomes so timing, leverage, and thesis quality can be reviewed later.
- Keep the internal analysis deep while the default human response stays concise and decision-first.
- Keep one canonical final decision-state vocabulary in `schemas/decision-state.schema.json`.

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

### Local PAPER futures research

```text
Public futures bars or offline fixture
              │
              ▼
Deterministic features + quant signal gate
              │
              ▼
Jev atomic decisions ── deterministic escalation ──► Responses-compatible GPT + skill
              └──────────────────────┬────────────────────┘
                                     ▼
                         Validated TradingIntent
                                     ▼
                 Deterministic sizing, fees, funding,
                    isolated margin and liquidation
                                     ▼
               SQLite PAPER fills / portfolio / evaluation
```

The runtime binds to loopback by default. Browser settings contain provider
metadata and environment-variable references only; raw credentials are never
returned by APIs or placed in SQLite, prompts, logs, or exports. Fixture mode
uses local mocked providers and never sends external model requests. See
[`docs/paper-futures-runtime.md`](docs/paper-futures-runtime.md) for setup,
runtime controls, API boundaries, and validation.

### AI Portfolio Trading OS (PAPER)

On top of that runtime, the local app is a portfolio-first cockpit for PAPER
Spot and Perpetual trading. Its pages are Overview, Portfolio, Trade, Activity,
Research, Evaluations, and Settings.

- **Instruments:** an exchange-backed Gate catalog.
- **Spot:** its own accounting (average cost, fees, partial limit fills), not a
  1x perpetual.
- **Manual tickets:** go through the same deterministic risk and fill path as
  AI trades.
- **Positions:** a manager with protection, reduce/close, and explicit
  authority (`AUTO_PAPER`, `RECOMMEND_ONLY`, `MANUAL_OVERRIDE`, `PAUSED`).
- **AI re-plan:** structured, with a before/after diff.
- **Portfolio Brain:** can shrink or block entries but never bypasses risk.
- **Autonomous review:** a deterministic position-review queue.
- **Journal:** a unified activity journal and attention queue.
- **Learning:** post-trade reviews and a strategy tournament that includes AI
  cost.

Real Gate money-moving writes remain blocked by design. See
[`docs/ai-portfolio-trading-os.md`](docs/ai-portfolio-trading-os.md).

### Interactive diagram

[Open the interactive architecture diagram](docs/architecture.html)

![Crypto Skills evidence-to-decision architecture](docs/architecture-preview.png)

## Repository layout

```text
crypto-skills/
├── README.md                           # English
├── README.th.md                        # ภาษาไทย
├── docs/
│   ├── architecture.md                 # workflow and implementation boundaries
│   ├── evaluation.md                   # harness, runtime, forward CLI, and data limits
│   ├── paper-futures-runtime.md        # PAPER research runtime and safe local setup
│   └── ai-portfolio-trading-os.md      # Spot + Perp PAPER cockpit, authority, re-plan, brain
├── frontend/                           # local-first research and PAPER futures UI
│   └── README.md                       # local startup, modes, and limitations
├── schemas/
│   ├── analysis-output.schema.json     # final decision contract
│   ├── decision-state.schema.json      # canonical final decision states
│   ├── decision-record.schema.json     # journal / outcome contract
│   ├── evidence-ledger.schema.json     # fact ledger contract
│   ├── eval-*.schema.json              # evaluation spec / case / prediction / outcome contracts
│   ├── paper-*.schema.json             # strict PAPER intent/provider/experiment/order contracts
│   └── (market|spot|position|portfolio|activity|attention|post-trade|learning)-*.schema.json
├── examples/
│   ├── analysis-output.yaml
│   ├── decision-record.yaml
│   └── evidence-ledger.yaml
├── scripts/
│   └── validate_repo.py                # dependency-free structural checks
├── crypto_eval/                        # point-in-time harness + PAPER futures runtime/server
├── eval/
│   └── specs/crypto-market-v1.json     # versioned multi-asset walk-forward target
├── tests/
│   ├── test_contracts.py               # contract and evaluation regression tests
│   ├── test_market_data.py             # mocked Binance Spot provider contracts
│   ├── test_openai_runner.py           # mocked Responses API runner contracts
│   ├── test_forward_runtime.py         # mocked runtime/API/lifecycle integration
│   ├── test_paper_futures.py           # mocked-provider PAPER vertical slice tests
│   ├── test_portfolio_os.py            # Spot, orders, authority, re-plan, brain, safety
│   ├── test_portfolio_schemas.py       # live Portfolio OS output vs JSON Schemas
│   └── test_frontend_smoke.py          # frontend structural smoke checks
├── .github/workflows/
│   └── validate.yml                     # PR/push contract gate
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

## One-shot setup prompt

Copy the prompt below into Codex, Claude Code, Gemini CLI, or another capable AI agent. It is designed to detect the host, install the skill at the safest supported scope, validate it, and report exactly what changed. It never asks you to paste an API key into the repository or into a chat transcript.

```text
You are installing the Crypto Skills repository as a reusable crypto-market analysis skill.

Source of truth:
  https://github.com/myh-st/crypto-skills.git
Skill directory inside the repository:
  skills/crypto-market-trading-analysis

Goal:
  Detect the current AI client (Codex, Claude Code, Gemini CLI, or another agent),
  install or update this skill at the safest supported scope, validate it, and give
  me a concise completion report. Prefer the current workspace for project-local
  setup. Use a user/global scope only when I explicitly ask for it or when this
  client has no workspace skill directory.

Safety rules:
1. Inspect the OS, current working directory, client/version, and repository state
   before changing files. Do not use `git reset --hard`, broad recursive deletes,
   or commands that overwrite unrelated files.
2. If the repository is not present, clone it to a clearly reported directory. If
   it already exists, fetch/update only when it is the same repository and preserve
   uncommitted work. Record the installed commit SHA.
3. The source skill must contain `SKILL.md`; use the whole skill directory so its
   `references/`, `examples/`, and optional `agents/` metadata remain available.
4. If the destination already exists, compare it with the source. Prefer a symlink
   for a local checkout or a copy for a portable install. Before replacing an
   unrelated destination, move that exact directory to a timestamped backup and
   show the backup path. Ask me first if the replacement would be destructive or
   needs elevated permissions.
5. Run the repository validator: `python3 scripts/validate_repo.py`. Also verify
   that `SKILL.md` starts with valid YAML frontmatter and that
   `agents/openai.yaml` is present when installing for Codex. If the Codex
   validator exists, run `uv run --with pyyaml python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/crypto-market-trading-analysis` without installing global packages.

Use the native integration that matches the detected client:
- Codex: install to `~/.codex/skills/crypto-market-trading-analysis` for user
  scope or `.codex/skills/crypto-market-trading-analysis` for workspace scope.
  A symlink to the checked-out directory is preferred for development. Reopen or
  reload the Codex session if the client requires it, then verify the
  `$crypto-market-trading-analysis` invocation is discoverable.
- Claude Code: install to `~/.claude/skills/crypto-market-trading-analysis` for
  personal scope or `.claude/skills/crypto-market-trading-analysis` for project
  scope. Use `/skills` to inspect discovery and `/reload-skills` after creating a
  new top-level skills directory when supported.
- Gemini CLI: if the `gemini skills` manager is available, install the
  `skills/crypto-market-trading-analysis` subdirectory (use the repository URL
  only if this client supports monorepo subdirectories), or clone locally and
  run `gemini skills link <local-skill-path>`; use `--scope workspace` only for a
  project-local install. Otherwise place the skill under `~/.gemini/skills/` or
  `.gemini/skills/` (the `.agents/skills/` aliases are also supported). Verify
  with `/skills list` and refresh with `/skills reload`.
- Another AI agent: detect its documented native skill directory and place the
  skill there. If it has no skill manager, keep the repository checkout intact,
  load `skills/crypto-market-trading-analysis/SKILL.md` as the agent instruction,
  and report that this is an explicit-reference installation rather than native
  discovery. Never claim success without showing the exact path.

CoinMarketCap / MCP security:
- Never print, commit, embed, or put API keys in this prompt, README, shell
  history, logs, screenshots, or generated files. If I have already authorized a
  key, store it only as `CMC_API_KEY` in the host-approved environment or secret
  manager and verify it with a harmless authenticated metadata request without
  revealing the value.
- If a CoinMarketCap MCP server is already available, configure it through the
  client’s supported MCP settings and report the server name and read-only tools.
  If it is not available, do not invent a package or silently install one; report
  the missing adapter and leave the analysis skill usable with other data sources.
- This skill is read-only: do not place orders, move funds, request private keys,
  or enable custody/trading permissions.

Completion report (required):
- detected client and install scope
- source checkout and commit SHA
- exact installed path and whether it is a symlink or copy
- validation commands and pass/fail results
- reload/restart command and a minimal invocation example
- CMC/MCP status without exposing any secret
- warnings, missing permissions, or unsupported native integration
```

Client-specific references: [Claude Code Skills](https://code.claude.com/docs/en/skills) · [Gemini CLI Agent Skills](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/using-agent-skills.md). The prompt is intentionally provider-aware but keeps the repository as the single source of truth.

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
python3 -m unittest discover -s tests -v
uv run --with pyyaml python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/crypto-market-trading-analysis
```

The first command checks JSON/YAML examples against their schemas, canonical enum
consistency, required references, and required TradingAgents-inspired sections.
The second runs the schema/fixture regression tests. The third checks Codex skill
frontmatter, naming, and scaffold hygiene.
GitHub Actions also runs the deterministic synthetic evaluation pipeline on
every PR and push to `main`; it is a harness check, not an accuracy claim.

## Evaluation harness

### Validation != Accuracy Evaluation

`scripts/validate_repo.py` and the unit tests check repository structure, schemas,
and deterministic behavior. They do **not** establish that the analysis skill is
accurate, profitable, or better than a control.

Run the complete, offline fixture pipeline:

```bash
python3 -m crypto_eval demo --out-dir reports/crypto-eval-demo
```

It builds a dataset, freezes fixture predictions, scores separate outcomes,
compares fixed baselines, and writes JSON/Markdown reports. The generated report
must be read as **DEMO / HARNESS VALIDATION — NOT MARKET PERFORMANCE EVIDENCE**.
The fixture runner does not invoke the analysis skill or any model. Use
[`docs/evaluation.md`](docs/evaluation.md) for individual CLI commands, dataset
contracts, metric denominators, and extension examples.

The versioned `eval/specs/crypto-market-v1.json` defines a chronological,
no-shuffle dataset target for BTC, ETH, SOL, SUI, SEI, AVAX, and PYTH across bull,
bear, range, and high-volatility regimes. Each case requires explicit
`as_of`, `data_cutoff`, `asset`, `instrument`, `venue`, and `horizon`.
Historical snapshots reject future observations; news requires a point-in-time
archive timestamp. A sampling manifest reconciles scheduled, included, and
excluded cases with declared exclusion rules. Predictions are frozen in an
append-only log, outcomes are stored separately, and a wait whose trigger never
occurs is not scored as a failed entry.

Reports include directional and trigger-aware decision metrics, BTC benchmark
return/alpha when available, MFE/MAE, time-to-trigger/target, sample counts and
intervals, plus fixed Buy & Hold, BTC, EMA20/EMA50, RSI14, naive, and seeded
random comparators. Missing data stays unavailable. The drawdown result is an
equal-weight decision-sequence proxy—not portfolio PnL; sizing, cash, fills,
fees, slippage, and funding are not modeled.

The optional local runtime includes a read-only public Binance Spot klines
provider and a server-side GPT-6 Luna Responses runner. It binds to loopback by
default, reads `OPENAI_API_KEY` only from the server process environment, and
keeps the static frontend in clearly labeled fixture mode when no runtime API
is available. CI and unit tests use mocked transports only. See
[`docs/evaluation.md`](docs/evaluation.md) for archive, startup, configuration,
and forward-score commands.

Historical model predictions are meaningful only when they were frozen before
the outcome window was known; otherwise use forward paper evaluation. The
earlier three-case paired pilot used synthetic snapshots and is not real market
evidence. A skill-vs-control claim requires archived, same-model,
same-configuration predictions on the same prospective cases and sufficient
samples. The harness can compare such paired runs, but it cannot manufacture
accuracy or performance evidence. The separate PAPER futures lab uses its own
loopback server, provider references, deterministic risk controls, and simulated
fills only; it does not submit real-money orders.

### PAPER futures lab

Start the local research application with:

```bash
python3 -m crypto_eval paper-server
```

Open `http://127.0.0.1:8765/`. **Overview** shows portfolio health and what
needs attention; **Trade** runs manual PAPER Spot/Perpetual tickets; the
experiment itself is configured in **Research › Paper Trading Lab**. EXP-001 starts with
$100 USDT, 15m decisions, 1h/4h context, 3x primary leverage, 1% risk per
trade, 3 primary positions, and 1x/2x/3x/5x/10x shadow cohorts. The first-run
mode is deterministic fixtures; switching to Binance USD-M uses public
unauthenticated market-data endpoints only. Provider inference is opt-in and
requires a server-side environment-variable reference. Neither fixtures nor
public market-data mode enable real-money execution.

The server runs the scheduler and model-free position monitor independently
of the browser tab. SQLite state, cycles, provider metadata, simulated fills,
funding, fees, and risk events persist across local restarts. Provider secret
values are not accepted by the browser API. The smoke suite uses only fixtures
and mocks:

```bash
python3 -m unittest discover -s tests -v
node --test frontend/tests/*.test.mjs
```

## Contributing

Keep reusable domain guidance in `SKILL.md` or a focused reference. Put machine-readable contracts in `schemas/`, examples in `examples/`, and deterministic checks in `scripts/`. Do not commit credentials, exchange secrets, private portfolio data, or generated market snapshots. Any new decision rule should explain its evidence, data-quality assumptions, and failure mode.
