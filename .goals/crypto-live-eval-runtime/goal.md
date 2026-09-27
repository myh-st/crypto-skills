# Goal: Connect real market data, GPT-6 Luna, evaluation, and frontend

## User Request

The user reviewed `main` at `c821bd8` and set the next order of work: (1) real market-data provider, (2) GPT-6 Luna API runner, (3) forward/real benchmark evaluation, and (4) frontend/runtime integration. They noted the evaluation harness is implemented and passing, while real historical data, live provider, model API integration, and a frontend/runtime in `main` are still missing. Their desired user flow is: click Analyze in the web app, call Luna with the skill, return a structured report, freeze the decision, and score it after the outcome horizon.

## Refined Goal

Deliver a safe, local-first vertical slice that connects a public, read-only real market-data source to the existing point-in-time evaluation contracts, a server-side GPT-6 Luna API runner, forward evaluation, and the existing local frontend work. The same frozen snapshot must be used for skill and no-skill control runs; secrets remain server-side; no future outcomes can enter a prompt; and an end-to-end UI/API flow must return and persist a structured, auditable decision. The system must fail explicitly if credentials or an API endpoint are unavailable and must not claim market-performance improvement from the small synthetic pilot.

Assumptions chosen to avoid blocking: use Binance public Spot klines for initial market data and OpenAI's documented Responses API with model ID `gpt-6-luna` and `reasoning.effort=max`; obtain `OPENAI_API_KEY` only from the runtime environment. Official model reference: https://developers.openai.com/api/docs/models/gpt-6-luna. Do not make a paid API call unless the user separately authorizes one; use mocked transport for validation.

## Acceptance Criteria

- [ ] Preserve the existing evaluation contracts and current behavior; `python3 scripts/validate_repo.py` and `python3 -m unittest discover -s tests -v` pass.
- [ ] Add a read-only public Spot OHLCV provider (initially Binance) that supports requested symbol/interval/as-of ranges, normalizes source timestamps, includes only candles closed no later than `as_of`/`data_cutoff`, and fails closed on malformed, future, stale, missing, rate-limited, or unavailable data. No API credentials are needed for market candles.
- [ ] Add an explicit historical archive/cache path that records source/provider metadata, retrieval time, cutoff, and content hash without persisting secrets. CI and unit tests must use deterministic mocked responses and never contact a live market API.
- [ ] Add a server-side GPT-6 Luna runner using the documented Responses API/model and `reasoning.effort=max`. Read the API key from an environment variable only; allow endpoint override only through explicit configuration; redact secrets/errors; never store credentials in requests, predictions, reports, or logs.
- [ ] Normalize and validate model output against the existing frozen-prediction contract. Invalid JSON, missing fields, unsupported states, tool-use/refusal responses, timeout, HTTP errors, and missing credentials fail explicitly; no success-shaped defaults.
- [ ] Add paired skill/control execution over exactly the same frozen case and model configuration; only the skill instructions differ. Record model ID, reasoning/config hash, skill commit, prompt version, dataset version/hash, run ID, and freeze time. Outcomes/future candles must not appear in either prompt.
- [ ] Add forward-evaluation commands/API that create pending cases and immutable predictions, then accept/score separate outcomes only after the configured horizon has closed. Enforce the existing lifecycle and point-in-time checks.
- [ ] Preserve and integrate the existing local untracked `frontend/` work rather than replacing it. Add a local-first backend serving a same-origin Analyze API; keep OpenAI credentials exclusively server-side and use existing UI/service boundaries. Document local startup/configuration and the distinction between demo and live mode.
- [ ] The frontend can submit a symbol/instrument/horizon, receive a schema-valid structured analysis, display it, and show pending/evaluation status. No trade/order execution or public unauthenticated deployment is added.
- [ ] Extend CI with offline provider/runner/API/frontend tests. No CI secret, paid API call, or live API dependency is permitted. Add a mocked end-to-end test from request through provider snapshot, skill/control runner contract, and frozen decision.
- [ ] If no usable API credential is present in the environment, do not attempt a paid/live model request. State that live invocation remains unverified, while the authenticated runner contract is validated with mocks.
- [ ] Preserve the real-model paired pilot record and explicitly distinguish the 3-case synthetic pilot from real historical/forward evidence. Do not claim skill accuracy or performance improvement without a sufficiently powered prospective evaluation.
- [ ] Run repository validation, all Python tests, all frontend checks, and a deterministic mocked end-to-end flow. If a local UI is runnable, verify the Analyze flow in a browser. Inspect staged changes and leave unrelated user files untouched.

## Scope Boundaries

**In scope:**
- The user's ordered next-phase path: read-only real market data → GPT-6 Luna runner → forward paired evaluation → frontend/runtime integration.
- Track and integrate the existing untracked `frontend/` application after inspecting it. Preserve it and its UX where possible.
- Minimal local-first runtime/API, tests, docs, configuration examples, and CI updates.

**Out of scope:**
- Trading, exchange order placement, custody, position execution, or portfolio-PnL claims without full portfolio mechanics.
- Committing or displaying API credentials, making paid model calls without separate authorization, or adding live API dependencies to CI.
- Public cloud deployment, public unauthenticated hosting, or inventing unsupported API endpoints/provider capabilities.
- Treating synthetic fixtures or the 3-case GPT pilot as market-performance evidence.
- Deleting/overwriting unrelated untracked files, including `.goals/crypto-research-frontend/`; that folder contains separate process artifacts and is not required to ship the UI.

## Applicable Project Conventions

**Quality gate command:**
- `python3 scripts/validate_repo.py`
- `python3 -m unittest discover -s tests -v`
- `node --check` on frontend JavaScript modules and `node --test frontend/tests/*.test.mjs` where applicable.
- CI's deterministic offline eval demo.

**Commit convention:**
- No repository-wide authoritative commit convention found. Use goal workflow titles: `type(scope): [B/I] description`, ≤72 characters; Builder and Inspector each commit once per iteration.
- Include `Assisted-by: OpenAI:GPT-5.6 Luna` for Builder and `Assisted-by: OpenAI:GPT-5.6 Sol` for Inspector, plus `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.

**Guidelines:**
- No `AGENTS.md`, `CONSTITUTION.md`, `.agents/guidelines/`, or `.github/guidelines/` found.
- Existing evaluation docs: `docs/evaluation.md`; existing frontend overview: `frontend/README.md`.
- GPT-6 Luna API reference: https://developers.openai.com/api/docs/models/gpt-6-luna.

**Rules:**
- Keep the Python evaluation harness and frontend boundaries separate; use read-only market data and point-in-time timestamps.
- Never expose secrets to the browser, logs, fixture data, or reports. Fail closed on cutoff/schema violations and API errors.
- Preserve existing user-created frontend work; do not modify the separate `.goals/crypto-research-frontend/` process artifacts.
- No external API in deterministic tests/CI, no fabricated results, and no unverified performance claims.
