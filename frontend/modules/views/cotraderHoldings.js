// Co-Trader › Holdings: what the user actually holds (manual entries + read-only Gate spot sync), with
// entry price, P&L, allocation, the rule state next to each coin and an alignment warning. The Gate
// sync is GET-only on the server; this page never sends or receives a key. Pure renderers are
// exported for tests; `mountHoldings` wires the forms.
import { escapeHtml, relativeTime } from "../format.js";
import { coinColor, coinIcon } from "../components/coinBook.js";
import { confirmAction } from "../components/confirmDialog.js";
import { applyMotion, skeleton } from "../components/motion.js";
import { feedback, num, pnl } from "../components/ui.js";
import { METHOD_LABELS, fmtPrice, fmtQty, fmtUsdt, pctChange, stateChip, unavailableHtml } from "../components/cotraderBits.js";

// ---------------------------------------------------------------- pure renderers

export function renderHoldingsTotals(totals = {}) {
  const t = totals || {};
  const cell = (label, html, key = "") => `<div class="cot-total"><span class="cot-kicker">${escapeHtml(label)}</span>
    <strong${key ? ` data-motion-key="hold:total:${key}"` : ""}>${html}</strong></div>`;
  return `<div class="cot-totals">
    ${cell("Value", escapeHtml(fmtUsdt(t.value_usdt)), "value")}
    ${cell("Cost", escapeHtml(fmtUsdt(t.cost_usdt)), "cost")}
    ${cell("Unrealized P&L", `${pnl(t.unrealized_usdt)} ${pctChange(t.unrealized_pct)}`, "unrealized")}
    ${cell("Realized P&L", pnl(t.realized_usdt), "realized")}
    ${cell("Cash", escapeHtml(fmtUsdt(t.cash_usdt)), "cash")}
  </div>`;
}

/** Alignment of the holding with the rule, as a sentence (never colour alone), or null. */
export function alignmentWarning(row) {
  const base = row?.base || "";
  if (row?.alignment === "holding_in_cash_state") {
    const tail = row.rule_state === "WATCH" ? " (the rule holds nothing until the trend confirms)" : "";
    return { tone: "warn", text: `You hold ${base} but the rule says ${row.rule_state || "CASH"}${tail}.` };
  }
  if (row?.alignment === "not_held_in_hold_state") return { tone: "info", text: `The rule holds ${base} but you don't.` };
  if (row?.alignment === "aligned") return { tone: "ok", text: "Aligned with the rule." };
  return null;
}

export function sourceChips(row) {
  const chips = [];
  if (row?.qty_source === "gate") chips.push('<span class="cot-src cot-src--gate">Gate</span>');
  else if (row?.qty_source === "manual") chips.push('<span class="cot-src cot-src--manual">Manual</span>');
  if (row?.avg_source === "unknown") chips.push('<span class="cot-src cot-src--unknown"><span aria-hidden="true">?</span> cost unknown</span>');
  else if (row?.avg_source === "manual" && row?.qty_source === "gate") chips.push('<span class="cot-src cot-src--manual">Manual avg</span>');
  return chips.join(" ");
}

export function renderHoldingRow(row, index = 0) {
  const base = String(row.base || "?");
  const warning = alignmentWarning(row);
  const costUnknown = row.avg_source === "unknown";
  const pnlHtml = costUnknown && num(row.unrealized_usdt) === null
    ? '<span class="muted">— cost unknown</span>'
    : `${pnl(row.unrealized_usdt)} ${pctChange(row.unrealized_pct)}`;
  const alloc = num(row.allocation);
  return `<li class="cot-hold${warning?.tone === "warn" ? " cot-hold--warn" : ""}">
    <div class="cot-hold-head">
      ${coinIcon(base, { size: 32 })}
      <div class="cot-hold-name"><a href="#/cotrader/${encodeURIComponent(base)}"><strong>${escapeHtml(base)}</strong></a>
        <div class="cot-chiprow">${sourceChips(row)} ${row.rule_state === null || row.rule_state === undefined ? "" : stateChip(row.rule_state, { withText: false })}</div></div>
      <div class="cot-hold-value"><strong data-motion-key="hold:${escapeHtml(base)}:value">${escapeHtml(fmtUsdt(row.value_usdt))}</strong>
        <span data-motion-key="hold:${escapeHtml(base)}:pnl">${pnlHtml}</span></div>
    </div>
    <dl class="cot-hold-grid">
      <div><dt>Qty</dt><dd>${escapeHtml(fmtQty(row.qty))}</dd></div>
      <div><dt>Avg entry</dt><dd>${escapeHtml(costUnknown && num(row.avg_price) === null ? "unknown" : fmtPrice(row.avg_price))}</dd></div>
      <div><dt>Price</dt><dd data-motion-key="hold:${escapeHtml(base)}:price">${escapeHtml(fmtPrice(row.price))}</dd></div>
      <div><dt>Cost</dt><dd>${escapeHtml(fmtUsdt(row.cost_usdt))}</dd></div>
      <div><dt>Realized</dt><dd>${pnl(row.realized_usdt)}</dd></div>
      <div><dt>Allocation</dt><dd>${escapeHtml(alloc === null ? "—" : `${(alloc * 100).toFixed(1)}%`)}</dd></div>
    </dl>
    ${warning && warning.tone !== "ok" ? `<p class="cot-align cot-align--${warning.tone}"><span aria-hidden="true">${warning.tone === "warn" ? "⚠" : "ℹ"}</span> ${escapeHtml(warning.text)}</p>` : ""}
    ${warning?.tone === "ok" ? `<p class="cot-align cot-align--ok small"><span aria-hidden="true">✓</span> ${escapeHtml(warning.text)}</p>` : ""}
    ${row.cost_basis_note ? `<p class="muted small">${escapeHtml(row.cost_basis_note)}</p>` : ""}
    <div class="paper-inline-actions">
      <button type="button" class="btn btn--ghost btn--small" data-hold-edit="${index}">${row.manual_id ? "Edit" : "Set avg manually"}</button>
      ${row.manual_id ? `<button type="button" class="btn btn--ghost btn--small" data-hold-delete="${escapeHtml(row.manual_id)}" data-hold-base="${escapeHtml(base)}">Delete</button>` : ""}
    </div>
  </li>`;
}

/** Shares for the allocation bar, computed from values (+ cash) so they always sum to 1. */
export function allocationShares(h) {
  const parts = (h?.holdings || [])
    .map((row) => ({ label: String(row.base || "?"), value: num(row.value_usdt), color: coinColor(String(row.base || "?")) }))
    .filter((p) => p.value !== null && p.value > 0);
  const cash = num(h?.totals?.cash_usdt) ?? (h?.cash || []).reduce((sum, c) => sum + (num(c.qty) || 0), 0);
  if (cash > 0) parts.push({ label: "Cash", value: cash, color: "#8a94a3" });
  const total = parts.reduce((sum, p) => sum + p.value, 0);
  if (!total) return [];
  return parts.map((p) => ({ ...p, share: p.value / total })).sort((a, b) => b.share - a.share);
}

export function renderAllocationBar(h) {
  const shares = allocationShares(h);
  if (!shares.length) return '<p class="muted small">No priced holdings yet.</p>';
  const text = shares.map((s) => `${s.label} ${(s.share * 100).toFixed(1)}%`).join(", ");
  return `<figure class="cot-alloc">
    <div class="cot-alloc-bar" role="img" aria-label="Allocation: ${escapeHtml(text)}">${shares.map((s) =>
      `<span style="width:${(s.share * 100).toFixed(2)}%;background:${escapeHtml(s.color)}" title="${escapeHtml(`${s.label} ${(s.share * 100).toFixed(1)}%`)}"></span>`).join("")}</div>
    <figcaption class="cot-alloc-legend small">${shares.map((s) =>
      `<span><i style="background:${escapeHtml(s.color)}" aria-hidden="true"></i>${escapeHtml(s.label)} ${escapeHtml((s.share * 100).toFixed(1))}%</span>`).join("")}</figcaption>
  </figure>`;
}

const SYNC_TONE = { OK: "ok", NOT_CONFIGURED: "warn", ERROR: "bad" };

function checkMark(value, label) {
  const state = value === true ? "pass" : value === false ? "fail" : "unknown";
  const mark = value === true ? "✓" : value === false ? "✗" : "–";
  const said = value === true ? "passed" : value === false ? "failed" : "not checked";
  return `<li class="gate-${state}"><span class="gate-mark" aria-hidden="true">${mark}</span> ${escapeHtml(label)} <span class="sr-only">${said}</span></li>`;
}

/** When the key was last validated, from the holdings payload if the backend reports it. */
export function lastValidatedAt(h) {
  const gate = h?.sources?.gate || {};
  return gate.last_validated_at ?? gate.validated_at ?? gate.last_validation?.checked_at ?? h?.gate_validation?.checked_at ?? null;
}

/**
 * Result of POST /api/holdings/gate/validate: a ✓/✗/– checklist (key stored, auth, spot read), the
 * number of assets with a balance and the read-only note. `reason: "no_key"` links to Settings.
 */
export function renderValidation(result) {
  if (!result) return "";
  const checks = result.checks || {};
  const noKey = result.reason === "no_key";
  const count = num(result.balances_nonzero);
  let head;
  if (noKey) {
    head = `<p class="cot-align cot-align--info small"><span aria-hidden="true">ℹ</span> No key yet. Add a READ-ONLY spot key in <a href="#/settings">Settings › Exchange Accounts</a>.</p>`;
  } else if (result.ok) {
    head = `<p class="cot-align cot-align--ok small"><span aria-hidden="true">✓</span> Key works${result.key_hint ? ` (key ${escapeHtml(result.key_hint)})` : ""}.</p>`;
  } else {
    const detail = [result.reason, result.error_code && `code ${result.error_code}`].filter(Boolean).join(" · ");
    head = `<p class="cot-align cot-align--warn small"><span aria-hidden="true">⚠</span> Validation failed${detail ? `: ${escapeHtml(detail)}` : ""}.</p>`;
  }
  return `<div class="cot-validation">
    ${head}
    <ul class="gate-list">
      ${checkMark(checks.credentials_present, "Key stored")}
      ${checkMark(checks.auth, "Auth")}
      ${checkMark(checks.spot_read, "Spot read")}
    </ul>
    ${count === null ? "" : `<p class="small">${escapeHtml(count)} ${count === 1 ? "asset" : "assets"} with balance</p>`}
    ${result.read_only_note ? `<p class="muted small">${escapeHtml(result.read_only_note)}</p>` : ""}
    ${result.checked_at ? `<p class="muted small">checked ${escapeHtml(relativeTime(result.checked_at))}</p>` : ""}
  </div>`;
}

export function renderSyncPanel(gate, { validatedAt = null } = {}) {
  const g = gate || { status: "NOT_CONFIGURED" };
  const status = String(g.status || "NOT_CONFIGURED");
  const guide = status === "NOT_CONFIGURED"
    ? `<div class="cot-guide">
        <strong>Connect Gate (read-only)</strong>
        <ol>
          <li>On Gate, create an API key with <strong>READ-ONLY spot</strong> permission — no trading, no withdrawals.</li>
          <li>Paste it in <a href="#/settings">Settings › Exchange Accounts</a>. The server keeps it in the OS credential store; the browser never sees it again.</li>
          <li>Come back here and press <em>Sync from Gate</em>.</li>
        </ol>
      </div>`
    : "";
  return `<div class="cot-sync">
    <div class="cot-sync-row">
      <span class="status-pill status-pill--${SYNC_TONE[status] || "warn"}">Gate: ${escapeHtml(status.replaceAll("_", " "))}</span>
      <span class="small muted">${g.synced_at ? `last sync ${escapeHtml(relativeTime(g.synced_at))}` : "never synced"}${g.account_id ? ` · ${escapeHtml(g.account_id)}` : ""}</span>
      <button type="button" class="btn btn--primary btn--small" data-hold-sync>Sync from Gate</button>
      <button type="button" class="btn btn--ghost btn--small" data-hold-validate>Validate key</button>
      <span class="small muted" data-hold-sync-feedback role="status" aria-live="polite"></span>
    </div>
    ${validatedAt ? `<p class="muted small">Key last validated ${escapeHtml(relativeTime(validatedAt))}.</p>` : ""}
    ${status === "ERROR" && g.error ? `<p class="cot-align cot-align--warn small"><span aria-hidden="true">⚠</span> ${escapeHtml(g.error)}</p>` : ""}
    <div data-hold-validation></div>
    ${guide}
  </div>`;
}

export function renderShareToggle(value) {
  return `<label class="checkbox-row cot-share">
      <input type="checkbox" data-hold-share ${value ? "checked" : ""} />
      Share my holding of a coin with its AI review
    </label>
    <p class="muted small">When on, a coin's AI review may include whether you hold it, the qty, avg price and unrealized % — never account ids, keys or other coins' amounts. Off by default.</p>`;
}

export function renderHoldingsData(h) {
  const rows = h?.holdings || [];
  const cash = (h?.cash || []).filter((c) => num(c.qty) !== null);
  return `
    <section class="panel">
      <div class="section-heading"><h2>My holdings</h2><span class="muted small">${h?.as_of ? `as of ${escapeHtml(relativeTime(h.as_of))}` : ""} · values in USDT at Gate last price</span></div>
      ${renderHoldingsTotals(h?.totals)}
      ${renderAllocationBar(h)}
      ${rows.length ? `<ul class="cot-holds">${rows.map(renderHoldingRow).join("")}</ul>` : '<p class="muted">No holdings yet. Add a coin below or sync from Gate.</p>'}
      ${cash.length ? `<p class="small muted">Cash: ${cash.map((c) => `${escapeHtml(fmtQty(c.qty))} ${escapeHtml(c.currency)}`).join(" · ")}</p>` : ""}
    </section>
    <section class="panel">
      <div class="section-heading"><h2>Sources</h2><span class="muted small">${escapeHtml(num(h?.sources?.manual_count) ?? 0)} manual entries</span></div>
      ${renderSyncPanel(h?.sources?.gate, { validatedAt: lastValidatedAt(h) })}
      ${renderShareToggle(Boolean(h?.share_holdings_with_ai))}
    </section>`;
}

export function renderHoldingForm() {
  return `<form class="paper-form cot-hold-form" data-hold-form novalidate>
    <input type="hidden" name="id" />
    <div class="paper-form-grid">
      <label class="ticket-field">Coin (e.g. NEAR)<input name="base" required autocomplete="off" pattern="[A-Za-z0-9]+" maxlength="20" /></label>
      <label class="ticket-field">Quantity<input name="qty" type="number" inputmode="decimal" step="any" min="0" required /></label>
      <label class="ticket-field">Avg entry price (USDT)<input name="avg_price" type="number" inputmode="decimal" step="any" min="0" required /></label>
      <label class="ticket-field">Opened (optional)<input name="opened_at" type="date" /></label>
    </div>
    <label class="ticket-field">Note (optional)<input name="note" maxlength="500" /></label>
    <div class="paper-inline-actions">
      <button type="submit" class="btn btn--primary btn--small" data-hold-save>Add coin</button>
      <button type="button" class="btn btn--ghost btn--small" data-hold-cancel hidden>Cancel edit</button>
    </div>
    <p class="muted small">A manual avg price overrides a Gate-derived one for the same coin (useful for coins deposited from elsewhere).</p>
    <div data-hold-form-feedback role="status" aria-live="polite"></div>
  </form>`;
}

/** Client-side mirror of the server's validation: base uppercase alphanumeric, qty > 0, avg_price > 0. */
export function validateHolding(input) {
  const base = String(input.base || "").trim().toUpperCase();
  if (!/^[A-Z0-9]{1,20}$/.test(base)) return { ok: false, error: "Coin must be letters and digits only, e.g. NEAR." };
  const qty = num(input.qty);
  if (qty === null || qty <= 0) return { ok: false, error: "Quantity must be greater than 0." };
  const avg = num(input.avg_price);
  if (avg === null || avg <= 0) return { ok: false, error: "Avg entry price must be greater than 0." };
  const entry = { base, qty, avg_price: avg };
  if (input.id) entry.id = String(input.id);
  if (input.opened_at) entry.opened_at = String(input.opened_at);
  if (input.note) entry.note = String(input.note).slice(0, 500);
  return { ok: true, entry };
}

export function renderCotraderSettingsForm(settings) {
  const s = settings || {};
  const capital = num(s.cotrader_capital_usdt);
  const sourceText = s.capital_source === "user" ? "set by you" : s.capital_source === "holdings" ? "from your Holdings total (value + cash)" : "not set — enter the USDT you use for spot";
  const method = s.sizing_method || "equal_weight";
  return `<form class="paper-form" data-cot-settings-form>
    <div class="paper-form-grid">
      <label class="ticket-field">Spot capital for the rule (USDT)
        <input name="cotrader_capital_usdt" type="number" inputmode="decimal" step="any" min="0" value="${capital === null ? "" : escapeHtml(capital)}" placeholder="e.g. 1000" />
      </label>
      <label class="ticket-field">Sizing method
        <select name="sizing_method">${Object.entries(METHOD_LABELS).map(([value, label]) => `<option value="${value}" ${value === method ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}</select>
      </label>
    </div>
    <p class="muted small">Current capital: <strong>${escapeHtml(capital === null ? "—" : fmtUsdt(capital))}</strong> (${escapeHtml(sourceText)}). Equal weight gives each coin 1/N of capital; inverse volatility gives calmer coins more.</p>
    <div class="paper-inline-actions"><button type="submit" class="btn btn--primary btn--small">Save sizing</button></div>
    <div data-cot-settings-feedback role="status" aria-live="polite"></div>
  </form>`;
}

// ---------------------------------------------------------------- mounting

export function renderHoldingsShell() {
  return `
    <div data-hold-data>${skeleton(4)}</div>
    <div class="cot-two">
      <section class="panel"><div class="section-heading"><h2>Add coin (manual)</h2></div>${renderHoldingForm()}</section>
      <section class="panel"><div class="section-heading"><h2>Co-Trader sizing</h2></div><div data-cot-settings><p class="muted">Loading…</p></div></section>
    </div>`;
}

/**
 * Mount the Holdings tab inside `host` (already filled with renderHoldingsShell()). Returns
 * {refresh, dispose}; `refresh` is what the page's auto-refresh calls.
 */
export function mountHoldings(host, { api }) {
  let rows = [];
  let settingsShown = false;
  const dataEl = host.querySelector("[data-hold-data]");
  const form = host.querySelector("[data-hold-form]");
  const formFeedback = host.querySelector("[data-hold-form-feedback]");
  const settingsEl = host.querySelector("[data-cot-settings]");

  function paintSettings(settings) {
    settingsEl.innerHTML = renderCotraderSettingsForm(settings);
    settingsShown = true;
  }

  let validation = null;   // last validate-key result, kept across refreshes
  const busy = { sync: false, validate: false };

  function paint(h) {
    rows = h?.holdings || [];
    dataEl.innerHTML = renderHoldingsData(h);
    paintValidation();
    applyMotion(dataEl);
  }

  function paintValidation() {
    const el = host.querySelector("[data-hold-validation]");
    if (el) el.innerHTML = renderValidation(validation);
  }

  async function refresh() {
    const [holdings, cot] = await Promise.allSettled([api.holdings(), api.cotrader()]);
    if (holdings.status === "rejected") {
      if (!rows.length) dataEl.innerHTML = unavailableHtml(holdings.reason);
      throw holdings.reason;
    }
    paint(holdings.value);
    if (!settingsShown) {
      if (cot.status === "fulfilled") paintSettings(cot.value.settings);
      else settingsEl.innerHTML = '<p class="muted">Co-Trader settings are unavailable on this server.</p>';
    }
  }

  function resetForm() {
    form.reset();
    form.elements.id.value = "";
    form.querySelector("[data-hold-save]").textContent = "Add coin";
    form.querySelector("[data-hold-cancel]").hidden = true;
  }

  async function onSubmit(event) {
    if (event.target.matches("[data-hold-form]")) {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(form).entries());
      const result = validateHolding(data);
      if (!result.ok) return feedback(formFeedback, result.error, "error");
      try {
        await api.saveManualHolding(result.entry);
        feedback(formFeedback, `Saved ${result.entry.base}.`, "success");
        resetForm();
        await refresh();
      } catch (error) {
        feedback(formFeedback, error.message, "error");
      }
      return;
    }
    if (event.target.matches("[data-cot-settings-form]")) {
      event.preventDefault();
      const f = event.target;
      const fb = f.querySelector("[data-cot-settings-feedback]");
      const raw = f.elements.cotrader_capital_usdt.value.trim();
      const capital = raw === "" ? null : num(raw);
      if (raw !== "" && (capital === null || capital <= 0)) return feedback(fb, "Capital must be a positive number, or blank to use the Holdings total.", "error");
      try {
        const saved = await api.saveCotraderSettings({ cotrader_capital_usdt: capital, sizing_method: f.elements.sizing_method.value });
        paintSettings(saved.settings);
        feedback(settingsEl.querySelector("[data-cot-settings-feedback]"), "Sizing saved. The Signals tab shows the new amounts.", "success");
      } catch (error) {
        feedback(fb, error.message, "error");
      }
    }
  }

  async function onClick(event) {
    const edit = event.target.closest("[data-hold-edit]");
    if (edit) {
      const row = rows[Number(edit.dataset.holdEdit)];
      if (!row) return;
      form.elements.id.value = row.manual_id || "";
      form.elements.base.value = row.base || "";
      form.elements.qty.value = row.qty ?? "";
      form.elements.avg_price.value = row.avg_price ?? "";
      form.querySelector("[data-hold-save]").textContent = row.manual_id ? "Save changes" : "Save manual avg";
      form.querySelector("[data-hold-cancel]").hidden = false;
      form.scrollIntoView?.({ behavior: "smooth", block: "center" });
      form.elements.avg_price.focus();
      return;
    }
    if (event.target.closest("[data-hold-cancel]")) return resetForm();
    const del = event.target.closest("[data-hold-delete]");
    if (del) {
      const ok = await confirmAction({
        title: "Delete manual holding",
        message: `Remove the manual entry for ${del.dataset.holdBase}? Gate-synced balances are not affected.`,
        confirmLabel: "Delete entry",
      });
      if (!ok) return;
      try {
        await api.deleteManualHolding(del.dataset.holdDelete);
        await refresh();
      } catch (error) {
        feedback(formFeedback, error.message, "error");
      }
      return;
    }
    const validate = event.target.closest("[data-hold-validate]");
    if (validate) {
      if (busy.validate) return;
      busy.validate = true;
      const fb = host.querySelector("[data-hold-sync-feedback]");
      validate.disabled = true;
      if (fb) fb.textContent = "Validating key (read-only)…";
      try {
        validation = await api.validateGateKey();
        if (fb) fb.textContent = "";
      } catch (error) {
        validation = null;
        const target = host.querySelector("[data-hold-sync-feedback]");
        if (target) target.textContent = `Validation request failed: ${error.message}`;
      } finally {
        busy.validate = false;
        paintValidation();
        const again = host.querySelector("[data-hold-validate]");
        if (again) again.disabled = false;
      }
      return;
    }
    const sync = event.target.closest("[data-hold-sync]");
    if (sync) {
      if (busy.sync) return;
      busy.sync = true;
      const fb = host.querySelector("[data-hold-sync-feedback]");
      sync.disabled = true;
      if (fb) fb.textContent = "Syncing (read-only)…";
      try {
        paint(await api.syncHoldings());
      } catch (error) {
        const target = host.querySelector("[data-hold-sync-feedback]");
        if (target) target.textContent = `Sync failed: ${error.message}`;
      } finally {
        busy.sync = false;
        const again = host.querySelector("[data-hold-sync]");
        if (again) again.disabled = false;
      }
    }
  }

  async function onChange(event) {
    const toggle = event.target.closest("[data-hold-share]");
    if (!toggle) return;
    toggle.disabled = true;
    try {
      const saved = await api.saveHoldingsSettings({ share_holdings_with_ai: toggle.checked });
      toggle.checked = Boolean(saved.share_holdings_with_ai);
    } catch (error) {
      toggle.checked = !toggle.checked;
      feedback(formFeedback, error.message, "error");
    } finally {
      toggle.disabled = false;
    }
  }

  host.addEventListener("submit", onSubmit);
  host.addEventListener("click", onClick);
  host.addEventListener("change", onChange);
  return {
    refresh,
    dispose() {
      host.removeEventListener("submit", onSubmit);
      host.removeEventListener("click", onClick);
      host.removeEventListener("change", onChange);
    },
  };
}
