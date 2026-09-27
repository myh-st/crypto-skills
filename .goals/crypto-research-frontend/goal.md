# Goal: Build the Crypto Research frontend

## User Request

The user shared a product brief and reference screenshot for a Crypto Research frontend. The request to implement the described frontend is inferred from the attached brief.

## Refined Goal

Build a browser-runnable Crypto Research console in this repository that follows the attached research-first product brief and visual reference. The console should make the lifecycle from analysis request to research report, decision tracking, and evaluation legible, while keeping evaluation secondary and presenting evidence separately from the main report. The repository has no existing frontend framework or application runtime, so deliver a dependency-free, static frontend with clearly labeled demo data; do not imply that live analysis, market data, or trading is available.

## Acceptance Criteria

- [ ] Add a self-contained frontend that can be run locally using the existing Python installation and documented command, without adding package dependencies or requiring credentials, external APIs, or a backend.
- [ ] Provide the eight requested destinations: Overview, New Analysis, Runs, Decisions, Watchlist, Evaluations, Data Sources, and Settings. Navigation must work and visibly identify the active destination.
- [ ] Implement the Overview with an analysis prompt, quick-start assets, recent decisions, an agent-aware watchlist, and recent run activity; do not lead with KPI/telemetry cards.
- [ ] Implement a Research Composer with asset, analysis type, horizon, question, optional capital and risk controls, and collapsible advanced settings.
- [ ] Make the primary workflow interactive: submitting an analysis creates a clearly labeled demo run/report; users can inspect the report, open and filter its evidence drawer, save a decision, and review the resulting run/decision state.
- [ ] Present the report in the requested hierarchy: decision and confidence, entry zones, invalidation, targets and horizon; a price-level chart; up to five rationale points; market structure, leverage, scenario map, and evidence. Show a research summary, not chain-of-thought.
- [ ] Use demo-only market values and evidence with an always-visible explanation that no live research or investment recommendation is being produced. Do not claim real providers are connected, create orders, or simulate portfolio PnL.
- [ ] Follow the reference visual direction: persistent dark sidebar, light neutral workspace, compact blue primary accent, restrained decision colors, no gradients, and usable desktop/mobile layouts.
- [ ] Keep presentation data structured and aligned with the repository's `analysis-output`, `evidence-ledger`, and `decision-record` contracts where applicable; do not make prose the source of truth.
- [ ] Add concise usage and demo limitations documentation alongside the frontend. Preserve existing repository files and all pre-existing worktree changes outside this goal.
- [ ] Run `python3 scripts/validate_repo.py`, `python3 -m unittest discover -s tests -v`, and verify the frontend in a browser at desktop and mobile widths, including the analysis-to-report/evidence/decision interaction.

## Scope Boundaries

**In scope:**
- A dependency-free static frontend prototype for the specified research-first workflow.
- Local demo data and browser-local interactions needed to demonstrate navigation, report/evidence inspection, and decision tracking.
- Frontend-specific usage documentation and tests or checks that fit the repository's existing tooling.

**Out of scope:**
- Backend/API, agent runtime, persistence service, authentication, live market data or provider integrations.
- Real trading, order placement, financial recommendations, portfolio PnL/backtesting, or claims of actual model analysis.
- Reworking the existing evaluation harness or modifying unrelated changes already present in the worktree.

## Applicable Project Conventions

**Quality gate command:**
- `python3 scripts/validate_repo.py`
- `python3 -m unittest discover -s tests -v`
- Browser-check the frontend at desktop and mobile widths and exercise the primary demo workflow.

**Commit convention:**
- No repository-specific convention was found. Use `type(scope): [B/I] description` (conventional commits, title at most 72 characters) for goal-role commits.
- Include the role-specific `Assisted-by:` trailer and `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` on commits.

**Guidelines:**
- No `AGENTS.md`, `CONSTITUTION.md`, `.agents/guidelines/`, or `.github/guidelines/` files were found during discovery.

**Rules:**
- Existing CI checks are `python scripts/validate_repo.py` and `python -m unittest discover -s tests -v` in `.github/workflows/validate.yml`.
- The repository has no frontend package manifest, Makefile, or justfile. Avoid adding dependencies unless the implementation requires them.
- Existing unrelated changes are present in `README.md`, `scripts/validate_repo.py`, `.goals/crypto-eval-harness/`, `crypto_eval/`, `eval/`, and several `schemas/eval-*.schema.json` files. Do not modify, stage, or commit those changes as part of this goal.
