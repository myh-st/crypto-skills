# Crypto Skills

ภาษา: English · [ไทย](README.th.md)

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

### Interactive diagram

[Open the interactive architecture diagram](docs/architecture.html)

![Crypto Skills evidence-to-decision architecture](docs/architecture-preview.png)

## Repository layout

```text
crypto-skills/
├── README.md                           # English
├── README.th.md                        # ภาษาไทย
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
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/crypto-market-trading-analysis
```

The first command checks the repository contracts and required TradingAgents-inspired sections. The second checks Codex skill frontmatter, naming, and scaffold hygiene.

## Contributing

Keep reusable domain guidance in `SKILL.md` or a focused reference. Put machine-readable contracts in `schemas/`, examples in `examples/`, and deterministic checks in `scripts/`. Do not commit credentials, exchange secrets, private portfolio data, or generated market snapshots. Any new decision rule should explain its evidence, data-quality assumptions, and failure mode.
