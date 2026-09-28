// Settings › AI Providers / Exchange Accounts / Cost & Budgets for the local PAPER runtime.
// Credential inputs are password fields that are cleared immediately after submit. Values go
// once to the loopback server, which stores them in the OS credential store; the browser
// never persists or receives them back.
import { escapeHtml, formatTimestamp } from "../format.js";
import { paperApi } from "../paperApi.js";

const LIMIT_ACTIONS = [
  ["PAUSE_NEW_ENTRIES", "Pause new entries (monitor open positions)"],
  ["FALLBACK_QUANT", "Fall back to deterministic Quant (labeled)"],
  ["JEV_ONLY", "Jev only — block GPT escalation"],
  ["BLOCK_PAID_AI", "Block all paid AI"],
];

function usd(value, digits = 4) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return `$${Number(value).toFixed(digits)}`;
}

function pct(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function rate(value) {
  return value === null || value === undefined ? "unknown" : `$${Number(value)} / 1M`;
}

export function renderProviderCredentialCards(providers, secretStore) {
  const real = providers.filter((provider) => !provider.kind.startsWith("fixture_"));
  if (!real.length) {
    return '<p class="muted">No external provider is configured. Add TypeSafe Jev or Azure AI Foundry on the Paper Trading page, or run <code>python3 -m crypto_eval paper-setup-real</code>.</p>';
  }
  return real.map((provider) => {
    const validation = provider.last_validation || {};
    const connected = provider.last_validation_status === "passed";
    return `
      <article class="paper-provider-card" data-provider-card="${escapeHtml(provider.provider_id)}">
        <div class="section-heading">
          <strong>${escapeHtml(provider.display_name)}</strong>
          <span class="status-pill status-pill--${connected ? "ok" : provider.last_validation_status === "failed" ? "bad" : "idle"}">
            ${connected ? "Connected" : provider.last_validation_status === "failed" ? "Failed" : "Not tested"}
          </span>
        </div>
        <dl class="paper-definition-list paper-definition-list--compact">
          <div><dt>Kind</dt><dd>${escapeHtml(provider.kind)}</dd></div>
          <div><dt>Model / deployment</dt><dd>${escapeHtml(provider.model)}</dd></div>
          <div><dt>Returned model</dt><dd>${escapeHtml(validation.returned_model || validation.model || "—")}</dd></div>
          <div><dt>Reasoning effort</dt><dd>${escapeHtml(provider.kind === "typesafe_jev" ? "n/a" : provider.reasoning_effort)}${validation.reasoning_effort_echoed ? ` · echoed ${escapeHtml(validation.reasoning_effort_echoed)}` : ""}</dd></div>
          <div><dt>Credential</dt><dd>${escapeHtml(String(provider.credential_status).replaceAll("_", " "))}</dd></div>
          <div><dt>Last validation</dt><dd>${escapeHtml(formatTimestamp(provider.last_validated_at))}</dd></div>
          <div><dt>Last real-call latency</dt><dd>${provider.last_validation_latency_ms == null ? "—" : `${Number(provider.last_validation_latency_ms).toFixed(0)} ms`}</dd></div>
          <div><dt>Request ID</dt><dd>${escapeHtml(validation.provider_request_id || "—")}</dd></div>
          <div><dt>Last error</dt><dd>${escapeHtml(validation.error || provider.last_validation_error || "—")}</dd></div>
        </dl>
        <form class="paper-form paper-secret-form" data-form="provider-secret" data-provider-id="${escapeHtml(provider.provider_id)}" autocomplete="off">
          <label>API key (stored in ${escapeHtml(secretStore?.backend || "OS credential store")})
            <input name="secret_value" type="password" autocomplete="new-password" spellcheck="false" placeholder="Paste a new key to store or rotate" />
          </label>
          <div class="composer-actions">
            <button class="btn btn--primary btn--small" type="submit">Store key</button>
            <button class="btn btn--ghost btn--small" type="button" data-settings-action="test-provider" data-provider-id="${escapeHtml(provider.provider_id)}">Test connection (real call)</button>
            ${provider.credential_secret_configured ? `<button class="btn btn--ghost btn--small" type="button" data-settings-action="delete-provider-secret" data-provider-id="${escapeHtml(provider.provider_id)}">Remove stored key</button>` : ""}
          </div>
        </form>
      </article>`;
  }).join("");
}

export function renderAccountMirror(account) {
  const sync = account?.last_sync;
  if (!sync) return '<p class="muted">Not synced yet. Test connection performs signed GET requests only.</p>';
  const capability = sync.capability || {};
  const balance = sync.balance || {};
  return `
    <div class="real-account-mirror">
      <div class="section-heading"><strong>REAL ACCOUNT · read-only mirror</strong>
        <span class="demo-tag demo-tag--real">${escapeHtml(sync.source || "GATE_READONLY")}</span></div>
      <p class="field-help">Context only. Never merged with the PAPER wallet; the PAPER engine cannot reach this account.</p>
      <dl class="paper-definition-list paper-definition-list--compact">
        <div><dt>Synced</dt><dd>${escapeHtml(formatTimestamp(sync.synced_at))}</dd></div>
        <div><dt>Equity</dt><dd>${balance.equity == null ? "—" : `${Number(balance.equity).toFixed(2)} ${escapeHtml(balance.currency || "USDT")}`}</dd></div>
        <div><dt>Available</dt><dd>${balance.available == null ? "—" : Number(balance.available).toFixed(2)}</dd></div>
        <div><dt>Unrealised PnL</dt><dd>${balance.unrealised_pnl == null ? "—" : Number(balance.unrealised_pnl).toFixed(2)}</dd></div>
        <div><dt>Positions</dt><dd>${(sync.positions || []).length}</dd></div>
        <div><dt>Open orders</dt><dd>${(sync.open_orders || []).length}</dd></div>
      </dl>
      <div class="capability-grid">
        ${["authenticated", "futures_read", "balance_sync", "positions_sync", "orders_read", "trades_read", "write_execution"]
          .map((key) => `<span class="status-pill status-pill--${key === "write_execution" ? "blocked" : capability[key] ? "ok" : "idle"}">${escapeHtml(key.replaceAll("_", " "))}: ${key === "write_execution" ? "BLOCKED BY DESIGN" : capability[key] ? "yes" : "no"}</span>`)
          .join("")}
      </div>
      ${Object.keys(sync.errors || {}).length ? `<p class="field-help">Unavailable: ${escapeHtml(Object.entries(sync.errors).map(([k, v]) => `${k} (${v})`).join("; "))}</p>` : ""}
    </div>`;
}

export function renderExchangeAccounts(accounts) {
  const account = accounts[0] || null;
  return `
    <form class="paper-form" data-form="exchange-account" autocomplete="off">
      <div class="paper-form-grid">
        <label>Account display name<input name="display_name" type="text" value="${escapeHtml(account?.display_name || "Gate.io")}" /></label>
        <label>Environment
          <select name="environment">
            <option value="live" ${account?.environment !== "testnet" ? "selected" : ""}>Live (read-only)</option>
            <option value="testnet" ${account?.environment === "testnet" ? "selected" : ""}>Futures TestNet (read-only)</option>
          </select>
        </label>
        <label>Settle currency<select name="settle_currency"><option value="USDT">USDT</option></select></label>
        <label>Expected IP allowlist (optional)<input name="expected_ip" type="text" value="${escapeHtml(account?.expected_ip || "")}" /></label>
        <label>API key${account?.credentials?.api_key ? " (stored — enter to rotate)" : ""}
          <input name="api_key" type="password" autocomplete="new-password" spellcheck="false" /></label>
        <label>API secret${account?.credentials?.api_secret ? " (stored — enter to rotate)" : ""}
          <input name="api_secret" type="password" autocomplete="new-password" spellcheck="false" /></label>
      </div>
      <label class="checkbox-row"><input type="checkbox" name="enabled" ${account?.enabled === false ? "" : "checked"} />Enabled</label>
      <label class="checkbox-row"><input type="checkbox" name="sync_enabled" ${account?.sync_enabled === false ? "" : "checked"} />Account sync (read-only)</label>
      <p class="field-help">Recommended key permissions: perpetual futures <strong>read-only</strong>, no withdrawal, no wallet writes, IP allowlist. Even a write-enabled key stays <strong>write_execution = false</strong>: live orders, leverage changes, transfers and withdrawals are blocked by design before any network request.</p>
      <div class="composer-actions">
        <button class="btn btn--primary" type="submit">Save Gate account</button>
        ${account ? `<button class="btn btn--ghost" type="button" data-settings-action="sync-account" data-account-id="${escapeHtml(account.account_id)}">Test connection / sync (signed GET)</button>
        <button class="btn btn--ghost" type="button" data-settings-action="copy-equity" data-account-id="${escapeHtml(account.account_id)}">Copy Gate equity as starting PAPER balance (new experiment only)</button>` : ""}
      </div>
    </form>
    ${account ? renderAccountMirror(account) : ""}`;
}

export function renderPriceBook(cost) {
  const rows = cost.price_book || [];
  const coverage = cost.price_coverage || [];
  return `
    <div class="table-scroll"><table class="data-table">
      <thead><tr><th>Provider</th><th>Model</th><th>Version</th><th>Effective</th><th>Input</th><th>Cached input</th><th>Output</th><th>Reasoning rule</th><th>Source</th></tr></thead>
      <tbody>${rows.length ? rows.map((row) => `<tr>
        <td>${escapeHtml(row.provider_kind)}</td><td>${escapeHtml(row.model)}</td><td>${escapeHtml(row.version)}</td>
        <td>${escapeHtml(formatTimestamp(row.effective_from))}</td><td>${escapeHtml(rate(row.input_per_million))}</td>
        <td>${escapeHtml(rate(row.cached_input_per_million))}</td><td>${escapeHtml(rate(row.output_per_million))}</td>
        <td>${escapeHtml(row.reasoning_billing_rule)}</td><td>${escapeHtml(row.source)}</td></tr>`).join("")
        : '<tr><td colspan="9" class="muted">No prices configured. Unknown price is not zero: scheduled paid calls fail closed.</td></tr>'}</tbody>
    </table></div>
    <div class="capability-grid">${coverage.map((item) => `<span class="status-pill status-pill--${item.price_known ? "ok" : "bad"}">${escapeHtml(item.provider_id)} · ${escapeHtml(item.model)}: ${escapeHtml(item.status.replaceAll("_", " "))}</span>`).join("")}</div>
    <form class="paper-form" data-form="price-entry">
      <h3>Add a price-book version (append-only)</h3>
      <div class="paper-form-grid">
        <label>Provider kind<select name="provider_kind">
          <option value="foundry_responses">Azure AI Foundry (Responses)</option>
          <option value="typesafe_jev">TypeSafe Jev</option>
          <option value="openai_responses">OpenAI Responses</option>
          <option value="compatible_responses">Compatible Responses</option></select></label>
        <label>Model / deployment<input name="model" type="text" value="gpt-6-luna" required /></label>
        <label>Version label<input name="version" type="text" placeholder="azure-contract-2026-09" required /></label>
        <label>Effective from<input name="effective_from" type="datetime-local" required /></label>
        <label>Input $ / 1M tokens<input name="input_per_million" type="number" min="0" step="any" /></label>
        <label>Cached input $ / 1M<input name="cached_input_per_million" type="number" min="0" step="any" /></label>
        <label>Output $ / 1M tokens<input name="output_per_million" type="number" min="0" step="any" /></label>
        <label>Reasoning billing<select name="reasoning_billing_rule">
          <option value="included_in_output">Included in output tokens</option>
          <option value="separate_rate">Separate reasoning rate</option></select></label>
        <label>Reasoning $ / 1M (if separate)<input name="reasoning_per_million" type="number" min="0" step="any" /></label>
        <label>Source / reference<input name="source" type="text" placeholder="Azure contract / invoice / pricing page" required /></label>
      </div>
      <p class="field-help">Do not assume public OpenAI pricing equals your Azure contract. Historical versions are never rewritten.</p>
      <button class="btn btn--primary" type="submit">Add price version</button>
    </form>`;
}

export function renderBudgetStatus(status) {
  const budget = status.budget;
  return `
    <div class="paper-metric-grid">
      <div class="paper-metric"><span class="paper-metric-value">${escapeHtml(usd(status.spent_today_usd))}</span><span class="paper-metric-label">AI spend today</span><small>${escapeHtml(pct(status.utilization_today))} of ${escapeHtml(usd(budget.daily_usd, 2))}</small></div>
      <div class="paper-metric"><span class="paper-metric-value">${escapeHtml(usd(status.remaining_today_usd))}</span><span class="paper-metric-label">Remaining today</span></div>
      <div class="paper-metric"><span class="paper-metric-value">${escapeHtml(usd(status.spent_experiment_usd))}</span><span class="paper-metric-label">AI spend experiment</span><small>${escapeHtml(pct(status.utilization_experiment))} of ${escapeHtml(usd(budget.experiment_usd, 2))}</small></div>
      <div class="paper-metric"><span class="paper-metric-value">${escapeHtml(usd(status.remaining_experiment_usd))}</span><span class="paper-metric-label">Remaining experiment</span></div>
      <div class="paper-metric"><span class="paper-metric-value">${escapeHtml(usd(status.projection.projected_today_usd))}</span><span class="paper-metric-label">Projected today</span><small>ESTIMATE</small></div>
      <div class="paper-metric"><span class="paper-metric-value">${status.projection.days_until_experiment_budget_exhausted == null ? "—" : Number(status.projection.days_until_experiment_budget_exhausted).toFixed(1)}</span><span class="paper-metric-label">Days to exhaustion</span><small>ESTIMATE</small></div>
    </div>
    ${status.exhausted ? `<p class="paper-feedback paper-feedback--error">AI budget reached — no new paid call starts. Action at limit: ${escapeHtml(status.limit_action)}.</p>` : ""}`;
}

export function renderBudgetForm(budget, fx) {
  const field = (name, label, value, step = "any") => `<label>${label}<input name="${name}" type="number" min="0" step="${step}" value="${value ?? ""}" placeholder="no limit" /></label>`;
  return `
    <form class="paper-form" data-form="cost-controls">
      <div class="paper-form-grid">
        ${field("daily_usd", "Daily AI budget (USD)", budget.daily_usd)}
        ${field("experiment_usd", "Experiment AI budget (USD)", budget.experiment_usd)}
        ${field("max_gpt_call_usd", "Max estimated cost / GPT call (USD)", budget.max_gpt_call_usd)}
        ${field("max_cycle_usd", "Max AI spend / 15m cycle (USD)", budget.max_cycle_usd)}
        ${field("max_gpt_calls_per_hour", "Max GPT calls / hour", budget.max_gpt_calls_per_hour, "1")}
        ${field("max_gpt_calls_per_day", "Max GPT calls / day", budget.max_gpt_calls_per_day, "1")}
        ${field("max_gpt_calls_per_cycle", "Max GPT calls / cycle", budget.max_gpt_calls_per_cycle, "1")}
        ${field("max_jev_calls_per_day", "Max Jev calls / day", budget.max_jev_calls_per_day, "1")}
        ${field("max_paid_calls", "Max paid calls / experiment", budget.max_paid_calls, "1")}
        ${field("gpt_max_input_tokens", "GPT max input tokens", budget.gpt_max_input_tokens, "1")}
        ${field("gpt_max_output_tokens", "GPT max output + reasoning tokens", budget.gpt_max_output_tokens, "1")}
        ${field("jev_max_input_tokens", "Jev max input tokens", budget.jev_max_input_tokens, "1")}
        <label>Warning thresholds (%)<input name="warning_thresholds" type="text" value="${escapeHtml(budget.warning_thresholds.map((v) => Math.round(v * 100)).join(", "))}" /></label>
        <label>Action at limit<select name="limit_action">${LIMIT_ACTIONS.map(([id, label]) => `<option value="${id}" ${id === budget.limit_action ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      </div>
      <label class="checkbox-row"><input type="checkbox" name="enabled" ${budget.enabled ? "checked" : ""} />Enforce monetary and call limits</label>
      <fieldset class="paper-fieldset"><legend>USD → USDT cost FX policy (required for net economic PnL)</legend>
        <div class="paper-form-grid">
          <label>Mode<select name="fx_mode"><option value="none" ${fx.mode === "none" ? "selected" : ""}>None — keep currencies separate</option><option value="manual" ${fx.mode === "manual" ? "selected" : ""}>Manual fixed rate</option></select></label>
          <label>USDT per 1 USD<input name="usdt_per_usd" type="number" min="0.5" max="2" step="any" value="${fx.usdt_per_usd ?? ""}" /></label>
          <label>Rate source<input name="fx_source" type="text" value="${escapeHtml(fx.source || "")}" placeholder="e.g. operator assumption USD = USDT" /></label>
        </div>
        <p class="field-help">Current price-book policy: unknown price = fail closed. Projections are labeled ESTIMATE.</p>
      </fieldset>
      <button class="btn btn--primary" type="submit">Save cost controls</button>
    </form>`;
}

function numberOrNull(value) {
  const text = String(value ?? "").trim();
  if (text === "") return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

export function budgetFromForm(form, original) {
  const data = new FormData(form);
  const integers = ["max_gpt_calls_per_hour", "max_gpt_calls_per_day", "max_gpt_calls_per_cycle", "max_jev_calls_per_day", "max_paid_calls"];
  const required = ["gpt_max_input_tokens", "gpt_max_output_tokens", "jev_max_input_tokens"];
  const budget = { ...original, enabled: data.get("enabled") === "on", limit_action: data.get("limit_action") };
  for (const name of ["daily_usd", "experiment_usd", "max_gpt_call_usd", "max_cycle_usd"]) budget[name] = numberOrNull(data.get(name));
  for (const name of integers) {
    const value = numberOrNull(data.get(name));
    budget[name] = value === null ? null : Math.trunc(value);
  }
  for (const name of required) budget[name] = Math.trunc(numberOrNull(data.get(name)) ?? original[name]);
  budget.warning_thresholds = String(data.get("warning_thresholds") || "")
    .split(",").map((item) => Number(item.trim()) / 100).filter((value) => value > 0 && value <= 1);
  const mode = data.get("fx_mode");
  const fx = mode === "manual"
    ? { version: "ai-cost-fx.v1", mode: "manual", usdt_per_usd: numberOrNull(data.get("usdt_per_usd")), source: String(data.get("fx_source") || "").trim(), recorded_at: new Date().toISOString() }
    : { version: "ai-cost-fx.v1", mode: "none", usdt_per_usd: null, source: null, recorded_at: null };
  return { ai_budget: budget, cost_fx: fx };
}

export function priceFromForm(form) {
  const data = new FormData(form);
  const entry = {
    provider_kind: data.get("provider_kind"),
    model: String(data.get("model") || "").trim(),
    version: String(data.get("version") || "").trim(),
    effective_from: new Date(String(data.get("effective_from"))).toISOString(),
    currency: "USD",
    reasoning_billing_rule: data.get("reasoning_billing_rule"),
    source: String(data.get("source") || "").trim(),
  };
  for (const name of ["input_per_million", "cached_input_per_million", "output_per_million", "reasoning_per_million"]) {
    const value = numberOrNull(data.get(name));
    if (value !== null) entry[name] = value;
  }
  return entry;
}

export function renderRuntimeSettingsShell() {
  return `
    <section class="panel" id="settings-ai-providers">
      <div class="section-heading"><h2>AI Providers</h2><span class="demo-tag">Keys stay in the OS credential store</span></div>
      <p class="panel-subtitle">Test Connection makes a <strong>real</strong> provider request (may incur usage) and records model, reasoning effort, latency and request ID. Keys are never returned to this page.</p>
      <div data-settings-providers><p class="muted">Loading…</p></div>
    </section>
    <section class="panel" id="settings-exchange-accounts">
      <div class="section-heading"><h2>Exchange Accounts · Gate.io</h2><span class="demo-tag demo-tag--real">Read-only · write execution BLOCKED BY DESIGN</span></div>
      <div data-settings-accounts><p class="muted">Loading…</p></div>
    </section>
    <section class="panel" id="settings-cost-budgets">
      <div class="section-heading"><h2>Cost &amp; Budgets</h2><span class="demo-tag">Hard limits · checked before every paid call</span></div>
      <div data-settings-budget-status></div>
      <div data-settings-budget-form></div>
      <h3>Provider / model price book</h3>
      <div data-settings-price-book></div>
    </section>
    <div class="paper-feedback" data-settings-feedback role="status" aria-live="polite"></div>`;
}

export function mountRuntimeSettings(root, api = paperApi) {
  const feedback = (message, tone = "neutral") => {
    const el = root.querySelector("[data-settings-feedback]");
    if (!el) return;
    el.className = `paper-feedback paper-feedback--${tone}`;
    el.textContent = message;
  };
  let config = null;

  async function refresh() {
    try {
      const [providers, accounts, cost, store, experiment] = await Promise.all([
        api.providers(), api.exchangeAccounts(), api.cost(), api.secretStore(), api.experiment(),
      ]);
      config = experiment.experiment.config;
      root.querySelector("[data-settings-providers]").innerHTML = renderProviderCredentialCards(providers.providers || [], store);
      root.querySelector("[data-settings-accounts]").innerHTML = renderExchangeAccounts(accounts.accounts || []);
      root.querySelector("[data-settings-budget-status]").innerHTML = renderBudgetStatus(cost.budget_status);
      root.querySelector("[data-settings-budget-form]").innerHTML = renderBudgetForm(config.ai_budget, config.cost_fx);
      root.querySelector("[data-settings-price-book]").innerHTML = renderPriceBook(cost);
    } catch (error) {
      for (const selector of ["[data-settings-providers]", "[data-settings-accounts]", "[data-settings-budget-form]"]) {
        const el = root.querySelector(selector);
        if (el) el.innerHTML = '<p class="muted">Local PAPER runtime unavailable. Start it with <code>python3 -m crypto_eval paper-server</code>.</p>';
      }
      feedback(error.message || "Runtime unavailable", "error");
    }
  }

  root.addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-form]");
    if (!form || !["provider-secret", "exchange-account", "price-entry", "cost-controls"].includes(form.dataset.form)) return;
    event.preventDefault();
    try {
      if (form.dataset.form === "provider-secret") {
        const input = form.elements.namedItem("secret_value");
        const value = input.value;
        input.value = "";
        if (!value.trim()) throw new Error("Enter a key to store.");
        const result = await api.saveProviderSecret(form.dataset.providerId, value);
        feedback(`Key stored in ${result.secret.backend} (${result.secret.masked}).`, "success");
      } else if (form.dataset.form === "exchange-account") {
        const data = new FormData(form);
        const apiKey = String(data.get("api_key") || "");
        const apiSecret = String(data.get("api_secret") || "");
        form.elements.namedItem("api_key").value = "";
        form.elements.namedItem("api_secret").value = "";
        const saved = await api.saveExchangeAccount({
          account_id: "gate-main",
          display_name: String(data.get("display_name") || "Gate.io"),
          environment: data.get("environment"),
          settle_currency: "USDT",
          expected_ip: String(data.get("expected_ip") || "") || null,
          enabled: data.get("enabled") === "on",
          sync_enabled: data.get("sync_enabled") === "on",
        });
        if (apiKey || apiSecret) {
          if (!apiKey || !apiSecret) throw new Error("Enter both API key and API secret to store credentials.");
          await api.saveAccountSecrets(saved.account.account_id, apiKey, apiSecret);
        }
        feedback("Gate account saved. Credentials (if entered) are in the OS credential store.", "success");
      } else if (form.dataset.form === "price-entry") {
        await api.addPrice(priceFromForm(form));
        feedback("Price-book version added.", "success");
      } else if (form.dataset.form === "cost-controls") {
        await api.saveCostControls(budgetFromForm(form, config.ai_budget));
        feedback("Cost controls saved and audited.", "success");
      }
      await refresh();
    } catch (error) {
      feedback(error.message || "Request failed safely.", "error");
    }
  });

  root.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-settings-action]");
    if (!button) return;
    button.disabled = true;
    try {
      const action = button.dataset.settingsAction;
      if (action === "test-provider") {
        feedback("Making a real provider request…");
        const result = await api.testProvider(button.dataset.providerId);
        const r = result.result;
        feedback(`Connected · ${r.returned_model || r.model} · ${Number(r.latency_ms).toFixed(0)} ms${r.reasoning_effort_echoed ? ` · reasoning ${r.reasoning_effort_echoed}` : ""}`, "success");
      } else if (action === "delete-provider-secret") {
        await api.deleteProviderSecret(button.dataset.providerId);
        feedback("Stored key removed.", "success");
      } else if (action === "sync-account") {
        feedback("Signed GET requests to Gate…");
        const result = await api.syncAccount(button.dataset.accountId);
        feedback(`Sync ${result.sync.status} · write execution: BLOCKED BY DESIGN`, result.sync.status === "passed" ? "success" : "error");
      } else if (action === "copy-equity") {
        const result = await api.copyAccountEquity(button.dataset.accountId);
        feedback(`Starting PAPER balance set to ${result.config.starting_balance_usdt} USDT (value only).`, "success");
      }
      await refresh();
    } catch (error) {
      feedback(error.message || "Request failed safely.", "error");
    } finally {
      button.disabled = false;
    }
  });

  refresh();
}
