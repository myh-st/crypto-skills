// Position / holding manager drawer: full detail, protection edit, reduce/close, authority,
// structured re-plan, evidence and journal. Every mutation goes to the server, which validates,
// journals, and returns the authoritative state; the drawer re-renders from that state.
import { escapeHtml, formatTimestamp, relativeTime } from "../format.js";
import { withConfirmation } from "./confirmDialog.js";
import { renderLifecycle } from "./lifecycleCard.js";
import { renderRestrictions, renderSafetyStrip } from "./safetyStrip.js";
import { QUICK_INTENTS, editsFromForm, renderReplanProposal } from "./replanPanel.js";
import { MODE_LABELS, feedback, fmtNumber, marketBadge, modeBadge, pct, pnl, price, sideBadge, sourceBadge, uid } from "./ui.js";

function kv(label, value) {
  return `<div><dt>${escapeHtml(label)}</dt><dd>${value}</dd></div>`;
}

function targetInputs(targets) {
  const open = (targets || []).filter((target) => !target.hit);
  const hit = (targets || []).filter((target) => target.hit);
  return `
    ${hit.map((target) => `<p class="muted small">TP ${escapeHtml(price(target.price))} hit${target.hit_at ? ` ${escapeHtml(relativeTime(target.hit_at))}` : ""}</p>`).join("")}
    ${[0, 1, 2].map((index) => `
      <label>TP${hit.length + index + 1}${index === 0 ? "" : " (optional)"}
        <input name="target_${index}" type="number" step="any" min="0" value="${escapeHtml(open[index]?.price ?? "")}" />
      </label>`).join("")}`;
}

export function renderPositionDetail(position, safety = null) {
  const perp = position.market_type === "perpetual";
  const open = position.status === "open";
  const mode = position.management_mode;
  const facts = perp
    ? [
      kv("Side", sideBadge(position.side, position.leverage)),
      kv("Quantity", escapeHtml(price(position.quantity))),
      kv("Notional", `${escapeHtml(price(position.notional_usdt))} USDT`),
      kv("Entry", escapeHtml(price(position.entry_price))),
      kv("Mark", escapeHtml(price(position.mark_price))),
      kv("Live", escapeHtml(price(position.live_price))),
      kv("Unrealized", pnl(position.unrealized_pnl_usdt)),
      kv("Realized", pnl(position.realized_pnl_usdt)),
      kv("R multiple", position.r_multiple == null ? "—" : `${Number(position.r_multiple).toFixed(2)}R`),
      kv("Margin", `${escapeHtml(price(position.margin_usdt))} USDT`),
      kv("Liquidation", `${escapeHtml(price(position.liquidation_price))}${position.liquidation_buffer_pct == null ? "" : ` <small>(${escapeHtml(pct(position.liquidation_buffer_pct))} away)</small>`}`),
      kv("Stop", escapeHtml(price(position.stop_price))),
      kv("Targets", escapeHtml((position.targets || []).map((t) => `${price(t.price)}${t.hit ? " ✓" : ""}`).join(" · ") || "—")),
      kv("Risk at stop", `${escapeHtml(price(position.open_risk_usdt))} USDT`),
      kv("Funding", pnl(position.funding_usdt == null ? null : -position.funding_usdt)),
      kv("Fees / slippage", `${escapeHtml(fmtNumber(position.fees_usdt, { digits: 4 }))} / ${escapeHtml(fmtNumber(position.slippage_usdt, { digits: 4 }))} USDT`),
    ]
    : [
      kv("Quantity", `${escapeHtml(price(position.quantity))} ${escapeHtml(position.base)}`),
      kv("Available", escapeHtml(price(position.available_quantity))),
      kv("Average cost", escapeHtml(price(position.avg_cost))),
      kv("Mark", escapeHtml(price(position.mark_price))),
      kv("Live", escapeHtml(price(position.live_price))),
      kv("Current value", `${escapeHtml(price(position.current_value_usdt))} USDT`),
      kv("Unrealized", pnl(position.unrealized_pnl_usdt)),
      kv("Realized", pnl(position.realized_pnl_usdt)),
      kv("Allocation", `${escapeHtml(pct(position.allocation_pct))} of Spot`),
      kv("Portfolio weight", escapeHtml(pct(position.portfolio_weight_pct))),
      kv("Plan stop", escapeHtml(price(position.stop_price))),
      kv("Plan targets", escapeHtml((position.targets || []).map((t) => `${price(t.price)}${t.hit ? " ✓" : ""}`).join(" · ") || "—")),
      kv("Fees", `${escapeHtml(fmtNumber(position.fees_usdt, { digits: 4 }))} USDT`),
    ];
  const evidence = position.evidence;
  const pending = (position.proposals || []).find((proposal) => proposal.status === "proposed");
  const latest = pending || (position.proposals || [])[0];
  const authorityButtons = open ? [
    mode !== "PAUSED" ? '<button type="button" class="btn btn--ghost btn--small" data-set-mode="PAUSED">Pause AI</button>' : "",
    mode === "PAUSED" ? '<button type="button" class="btn btn--ghost btn--small" data-set-mode="RECOMMEND_ONLY">Resume AI (recommend)</button>' : "",
    mode !== "MANUAL_OVERRIDE" ? '<button type="button" class="btn btn--ghost btn--small" data-set-mode="MANUAL_OVERRIDE">Manual override</button>' : "",
    mode !== "RECOMMEND_ONLY" && mode !== "PAUSED" ? '<button type="button" class="btn btn--ghost btn--small" data-set-mode="RECOMMEND_ONLY">Recommend only</button>' : "",
    mode !== "AUTO_PAPER" ? '<button type="button" class="btn btn--small" data-set-mode="AUTO_PAPER">Return control to AI</button>' : "",
  ].join("") : "";
  return `
    <header class="drawer-header">
      <div>
        <h2 id="drawer-title" tabindex="-1">${marketBadge(position.market_type)} ${escapeHtml(position.display_symbol)}</h2>
        <p class="muted small">${sourceBadge(position.source)} opened ${escapeHtml(formatTimestamp(position.opened_at))} · ${escapeHtml(position.status.toUpperCase())}${position.exit_reason ? ` · ${escapeHtml(position.exit_reason)}` : ""}</p>
      </div>
      <button type="button" class="btn btn--ghost btn--small" data-drawer-close aria-label="Close position details">✕</button>
    </header>
    <div class="drawer-mode">
      ${modeBadge(mode)}
      <span class="muted small">Thesis ${escapeHtml(position.thesis_status || "unknown")} · last AI review ${escapeHtml(position.last_ai_review_at ? relativeTime(position.last_ai_review_at) : "never")} · next ${escapeHtml(position.next_ai_review_at ? formatTimestamp(position.next_ai_review_at) : "—")}</span>
    </div>
    <div class="paper-feedback" data-drawer-feedback role="status" aria-live="polite"></div>
    <dl class="kv kv--grid">${facts.join("")}</dl>
    ${safety && open ? `<section class="drawer-section" aria-label="Execution safety">
      ${renderSafetyStrip({ killSwitch: safety.killSwitch, assessment: safety.assessment })}
      ${safety.assessment && safety.assessment.state !== "NORMAL" ? renderRestrictions(safety.assessment.restrictions) : ""}
    </section>` : ""}
    ${latest ? `<section class="drawer-section" aria-label="AI re-plan">${renderReplanProposal(latest, { editable: open })}</section>` : ""}
    ${open ? `
    <section class="drawer-section">
      <h3>Protection</h3>
      <form class="protection-form" data-protection-form>
        <label>Stop <input name="stop_price" type="number" step="any" min="0" value="${escapeHtml(position.stop_price ?? "")}" ${perp ? "required" : ""} /></label>
        ${targetInputs(position.targets)}
        <button type="submit" class="btn btn--primary btn--small">Save protection</button>
      </form>
      <p class="muted small">Risk-reducing changes apply immediately; risk-increasing changes ask for confirmation.</p>
    </section>
    <section class="drawer-section">
      <h3>Size</h3>
      <div class="button-row" role="group" aria-label="Reduce position">
        <button type="button" class="btn btn--ghost btn--small" data-reduce="0.25">Reduce 25%</button>
        <button type="button" class="btn btn--ghost btn--small" data-reduce="0.5">Reduce 50%</button>
        <button type="button" class="btn btn--ghost btn--small" data-reduce="0.75">Reduce 75%</button>
        <label class="inline-input">Custom % <input type="number" min="1" max="99" step="1" data-reduce-custom-value /></label>
        <button type="button" class="btn btn--ghost btn--small" data-reduce-custom>Reduce</button>
        <button type="button" class="btn btn--danger btn--small" data-close-position>${perp ? "Close position" : "Close holding"}</button>
      </div>
    </section>
    ${!perp ? `<section class="drawer-section" aria-label="Spot lifecycle">
      <h3>Lifecycle · Core / Tactical</h3>
      ${renderLifecycle(position)}
      <form class="core-form" data-core-form>
        <label>Core % (protected from plan stops and crash-state selling)
          <input name="core_percent" type="number" min="0" max="100" step="5" value="${escapeHtml(position.core_quantity != null && position.quantity ? Math.round(position.core_quantity / position.quantity * 100) : 0)}" />
        </label>
        <button type="submit" class="btn btn--ghost btn--small">Save Core</button>
      </form>
    </section>` : ""}
    <section class="drawer-section">
      <h3>Control</h3>
      <div class="button-row">${authorityButtons}</div>
      <p class="muted small">${escapeHtml({
        AUTO_PAPER: "AI may manage this PAPER position within deterministic policy; every action is journaled.",
        RECOMMEND_ONLY: "AI proposes; nothing changes until you apply.",
        MANUAL_OVERRIDE: "You manage this position. AI observes and may advise but cannot change it.",
        PAUSED: "No AI management. Deterministic stop/target monitoring continues.",
      }[mode] || "")}</p>
    </section>
    <section class="drawer-section">
      <h3>Ask AI to re-plan</h3>
      <div class="button-row">
        ${QUICK_INTENTS.map(([id, label]) => `<button type="button" class="btn btn--ghost btn--small" data-replan-intent-button="${id}">${escapeHtml(label)}</button>`).join("")}
      </div>
    </section>` : ""}
    ${position.review ? `
    <section class="drawer-section">
      <h3>Post-trade review</h3>
      <p><strong>${escapeHtml(position.review.outcome)}</strong> · ${escapeHtml(position.review.lesson)}</p>
      <p class="muted small">${(position.review.tags || []).map((tag) => `<span class="cat-badge">${escapeHtml(tag)}</span>`).join(" ") || "no tags"}</p>
      ${position.review.hypothesis ? `<p class="small">Hypothesis for the next experiment: ${escapeHtml(position.review.hypothesis)}</p>` : ""}
    </section>` : ""}
    <details class="drawer-section">
      <summary>Evidence${evidence ? ` · ${escapeHtml(evidence.decision || "")}` : ""}</summary>
      ${evidence ? `
        <dl class="kv">
          ${kv("Reason", escapeHtml(evidence.reason || "—"))}
          ${kv("AI path", escapeHtml(evidence.ai_path || "—"))}
          ${kv("Jev regime", escapeHtml(evidence.jev_regime || "—"))}
          ${kv("Portfolio Brain", escapeHtml(evidence.portfolio_brain?.action || "—"))}
          ${kv("Risk", escapeHtml(`${evidence.risk?.code || "—"}`))}
          ${Object.entries(evidence.key_signals || {}).map(([key, value]) => kv(key.replaceAll("_", " "), escapeHtml(typeof value === "number" ? fmtNumber(value, { digits: 4 }) : String(value ?? "—")))).join("")}
          ${kv("Data cutoff", escapeHtml(formatTimestamp(evidence.data_cutoff)))}
          ${kv("Snapshot", `<code>${escapeHtml(String(evidence.snapshot_hash || "").slice(0, 16))}</code>`)}
        </dl>` : '<p class="muted small">Manual PAPER trade — no AI decision evidence.</p>'}
    </details>
    <details class="drawer-section">
      <summary>Journal (${(position.journal || []).length})</summary>
      <ol class="journal">
        ${(position.journal || []).map((event) => `
          <li><time>${escapeHtml(formatTimestamp(event.created_at))}</time> ${sourceBadge(event.source)} <strong>${escapeHtml(event.action.replaceAll("_", " ").toLowerCase())}</strong>
          ${event.risk_before != null || event.risk_after != null ? `<small class="muted">risk ${escapeHtml(fmtNumber(event.risk_before, { digits: 2 }))} → ${escapeHtml(fmtNumber(event.risk_after, { digits: 2 }))}</small>` : ""}</li>`).join("")}
      </ol>
      <h4>Fills</h4>
      <ol class="journal">
        ${(position.fills || []).map((fill) => `<li><time>${escapeHtml(formatTimestamp(fill.as_of))}</time> ${escapeHtml(fill.side)} ${escapeHtml(price(fill.quantity))} @ ${escapeHtml(price(fill.price))} · fee ${escapeHtml(fmtNumber(fill.fee, { digits: 4 }))}</li>`).join("")}
      </ol>
    </details>
    <p class="muted small drawer-footnote">PAPER only · ${escapeHtml(MODE_LABELS[mode] || mode)} · Gate live orders blocked by design</p>`;
}

export function createPositionDrawer({ api, onChange = () => {} }) {
  const backdrop = document.createElement("div");
  backdrop.className = "drawer-backdrop";
  backdrop.hidden = true;
  const drawer = document.createElement("aside");
  drawer.className = "drawer";
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-modal", "true");
  drawer.setAttribute("aria-labelledby", "drawer-title");
  drawer.hidden = true;
  document.body.append(backdrop, drawer);
  let currentRef = null;
  let opener = null;

  const note = (message, tone) => feedback(drawer.querySelector("[data-drawer-feedback]"), message, tone);

  async function load(focus = false) {
    const ref = currentRef;
    const { position } = await api.position(ref);
    // Render the position immediately; the live safety assessment follows without blocking.
    drawer.innerHTML = renderPositionDetail(position, null);
    if (focus) drawer.querySelector("#drawer-title")?.focus();
    if (position.status === "open" && api.assessInstrument) {
      Promise.all([
        api.assessInstrument(position.instrument_id).catch(() => null),
        api.safety().catch(() => null),
      ]).then(([assessment, overview]) => {
        if (currentRef !== ref || drawer.hidden || drawer.contains(document.activeElement) && document.activeElement.matches("input, select, textarea")) return;
        const safety = { assessment: assessment?.assessment || null, killSwitch: overview?.kill_switch || null };
        const feedbackText = drawer.querySelector("[data-drawer-feedback]")?.innerHTML;
        drawer.innerHTML = renderPositionDetail(position, safety);
        const note = drawer.querySelector("[data-drawer-feedback]");
        if (note && feedbackText) note.innerHTML = feedbackText;
      });
    }
    return position;
  }

  async function open(ref, { replanIntent = null } = {}) {
    opener = document.activeElement;
    currentRef = ref;
    drawer.hidden = false;
    backdrop.hidden = false;
    document.body.classList.add("drawer-open");
    drawer.innerHTML = '<p class="muted">Loading position…</p>';
    try {
      await load(true);
      if (replanIntent) await runReplan(replanIntent);
    } catch (error) {
      drawer.innerHTML = `<p class="paper-feedback paper-feedback--error">${escapeHtml(error.message)}</p><button type="button" class="btn btn--ghost" data-drawer-close>Close</button>`;
    }
  }

  function close() {
    drawer.hidden = true;
    backdrop.hidden = true;
    document.body.classList.remove("drawer-open");
    currentRef = null;
    if (opener && typeof opener.focus === "function" && opener.isConnected) opener.focus();
  }

  async function mutate(label, action) {
    try {
      const result = await withConfirmation(action);
      if (result === null) {
        note("Cancelled · nothing changed.", "neutral");
        return;
      }
      await load();
      note(label, "success");
      onChange();
    } catch (error) {
      note(error.message || "Request failed safely.", "error");
    }
  }

  async function runReplan(intent) {
    note("Requesting a structured re-plan…");
    try {
      const { proposal } = await api.requestReplan(currentRef, { intent });
      await load();
      note(proposal.status === "blocked" ? `Blocked: ${proposal.code}` : proposal.status === "no_change" ? "AI recommends no change." : "Proposal ready · review the diff.", proposal.status === "blocked" ? "error" : "success");
      onChange();
    } catch (error) {
      note(error.message, "error");
    }
  }

  drawer.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });
  backdrop.addEventListener("click", close);
  drawer.addEventListener("click", async (event) => {
    const target = event.target.closest("button");
    if (!target || !currentRef) {
      if (target?.matches("[data-drawer-close]")) close();
      return;
    }
    const ref = currentRef;
    if (target.matches("[data-drawer-close]")) return close();
    if (target.dataset.reduce) {
      const fraction = Number(target.dataset.reduce);
      return mutate(`Reduced ${Math.round(fraction * 100)}% (PAPER).`, (confirm) =>
        api.reducePosition(ref, fraction, { confirm, client_request_id: uid("reduce") }));
    }
    if (target.matches("[data-reduce-custom]")) {
      const value = Number(drawer.querySelector("[data-reduce-custom-value]")?.value);
      if (!Number.isFinite(value) || value <= 0 || value >= 100) return note("Enter a percentage between 1 and 99.", "error");
      return mutate(`Reduced ${value}% (PAPER).`, (confirm) =>
        api.reducePosition(ref, value / 100, { confirm, client_request_id: uid("reduce") }));
    }
    if (target.matches("[data-close-position]")) {
      return mutate("Closed (PAPER).", (confirm) => api.closePosition(ref, { confirm, client_request_id: uid("close") }));
    }
    if (target.dataset.setMode) {
      const mode = target.dataset.setMode;
      return mutate(`Control set to ${MODE_LABELS[mode]}.`, (confirm) => api.setManagementMode(ref, mode, confirm ? { confirm: true } : {}));
    }
    if (target.matches("[data-lifecycle-review]")) {
      note("Reviewing lifecycle…");
      try {
        const { review } = await api.lifecycleReview(ref);
        await load();
        note(review.status === "PROPOSED" ? `Proposal: ${review.plan.action.replaceAll("_", " ").toLowerCase()} · review and apply.` : `No action · ${review.plan.reasons.join(", ") || review.plan.regime}`, "success");
        onChange();
      } catch (error) {
        note(error.message, "error");
      }
      return undefined;
    }
    if (target.matches("[data-lifecycle-apply]")) {
      return mutate("Lifecycle proposal applied (PAPER).", (confirm) => api.lifecycleApply(ref, confirm ? { confirm: true } : {}));
    }
    if (target.matches("[data-lifecycle-dismiss]")) {
      return mutate("Lifecycle proposal dismissed.", () => api.lifecycleDismiss(ref));
    }
    if (target.dataset.replanIntentButton) return runReplan(target.dataset.replanIntentButton);
    if (target.dataset.replanApply) {
      const id = target.dataset.replanApply;
      return mutate("Re-plan applied.", (confirm) => api.applyReplan(id, confirm ? { confirm: true } : {}));
    }
    if (target.dataset.replanReject) {
      const id = target.dataset.replanReject;
      return mutate("Re-plan rejected.", () => api.rejectReplan(id, {}));
    }
    if (target.dataset.replanEdit) {
      const form = drawer.querySelector(`[data-replan-edit-form="${CSS.escape(target.dataset.replanEdit)}"]`);
      if (form) {
        form.hidden = !form.hidden;
        target.setAttribute("aria-expanded", String(!form.hidden));
        if (!form.hidden) form.querySelector("select, input")?.focus();
      }
    }
  });
  drawer.addEventListener("submit", (event) => {
    event.preventDefault();
    const ref = currentRef;
    const form = event.target;
    if (form.matches("[data-protection-form]")) {
      const data = new FormData(form);
      const stopRaw = String(data.get("stop_price") || "").trim();
      const targets = [0, 1, 2].map((index) => String(data.get(`target_${index}`) || "").trim()).filter(Boolean).map(Number);
      const body = { client_request_id: uid("protect") };
      body.stop_price = stopRaw === "" ? null : Number(stopRaw);
      if (targets.length) body.targets = targets;
      return mutate("Protection updated.", (confirm) => api.updateProtection(ref, { ...body, confirm_risk_increase: confirm }));
    }
    if (form.matches("[data-core-form]")) {
      const percent = Number(new FormData(form).get("core_percent"));
      return mutate("Core allocation saved.", () => api.setCoreFraction(ref, percent / 100));
    }
    if (form.dataset.replanEditForm) {
      const id = form.dataset.replanEditForm;
      const edits = editsFromForm(form);
      return mutate("Edited re-plan applied.", (confirm) => api.applyReplan(id, { edits, confirm }));
    }
    return undefined;
  });

  return { open, close, get ref() { return currentRef; }, refresh: () => (currentRef ? load() : null) };
}
