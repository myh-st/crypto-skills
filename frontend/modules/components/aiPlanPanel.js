// Current AI plan for the selected instrument: latest decision (concise), the open position's
// plan and authority, and an entry point to the structured re-plan. Evidence is progressive.
import { escapeHtml, formatTimestamp, relativeTime } from "../format.js";
import { QUICK_INTENTS } from "./replanPanel.js";
import { emptyState, fmtNumber, modeBadge, price } from "./ui.js";

export function latestCycleFor(cycles, symbol) {
  return (cycles || []).find((cycle) => cycle.symbol === symbol && cycle.status === "complete") || null;
}

export function renderAiPlan({ cycle = null, position = null, instrument = null } = {}) {
  const parts = [];
  if (position) {
    parts.push(`
      <div class="plan-block">
        <div class="plan-head">${modeBadge(position.management_mode)}<span class="muted small">thesis ${escapeHtml(position.thesis_status || "unknown")}</span></div>
        <dl class="kv kv--compact">
          <div><dt>Stop</dt><dd>${escapeHtml(price(position.stop_price))}</dd></div>
          <div><dt>Targets</dt><dd>${escapeHtml((position.targets || []).map((t) => `${price(t.price)}${t.hit ? "✓" : ""}`).join(" · ") || "—")}</dd></div>
          <div><dt>Risk at stop</dt><dd>${escapeHtml(fmtNumber(position.open_risk_usdt, { digits: 2 }))} USDT</dd></div>
          <div><dt>Last review</dt><dd>${escapeHtml(position.last_ai_review_at ? relativeTime(position.last_ai_review_at) : "never")}</dd></div>
        </dl>
        <div class="button-row">
          ${QUICK_INTENTS.slice(0, 3).map(([id, label]) => `<button type="button" class="btn btn--ghost btn--small" data-open-position="${escapeHtml(position.position_ref)}" data-replan-intent="${id}">${escapeHtml(label)}</button>`).join("")}
          <button type="button" class="btn btn--small" data-open-position="${escapeHtml(position.position_ref)}">Manage</button>
        </div>
      </div>`);
  }
  if (cycle) {
    const primary = cycle.primary_decision || {};
    const intent = primary.intent;
    const risk = cycle.risk || {};
    const brain = cycle.portfolio_brain;
    const decision = primary.decision || "NO_TRADE";
    parts.push(`
      <div class="plan-block">
        <div class="plan-head"><span class="decision-tag decision-tag--${decision.startsWith("ENTER") ? (intent?.side === "short" ? "neg" : "pos") : "neutral"}">${escapeHtml(decision.replace("ENTER_", ""))}</span>
          <span class="muted small">${escapeHtml(cycle.primary_arm)} · ${escapeHtml(relativeTime(cycle.data_cutoff))}</span></div>
        ${intent ? `<dl class="kv kv--compact">
          <div><dt>Entry</dt><dd>${escapeHtml(price(intent.entry_price))}</dd></div>
          <div><dt>Stop</dt><dd>${escapeHtml(price(intent.stop_price))}</dd></div>
          <div><dt>Target</dt><dd>${escapeHtml(price(intent.target_price))}</dd></div>
          <div><dt>Risk</dt><dd>${escapeHtml(risk.code || "—")}</dd></div>
        </dl>` : `<p class="small">${escapeHtml(primary.reason || "No eligible setup on the last closed candle.")}</p>`}
        ${brain ? `<p class="small">Portfolio Brain ${escapeHtml(brain.action)}${brain.reason_codes?.length ? ` · ${escapeHtml(brain.reason_codes.join(", "))}` : ""}</p>` : ""}
        <details><summary>Evidence</summary>
          <dl class="kv kv--compact">
            <div><dt>Quant gate</dt><dd>${cycle.quant_gate?.eligible ? "eligible" : "not eligible"} · strength ${escapeHtml(fmtNumber(cycle.quant_gate?.strength, { digits: 2 }))}</dd></div>
            <div><dt>Jev</dt><dd>${escapeHtml(cycle.jev_status || "—")} · regime ${escapeHtml(cycle.market_regime || "—")}</dd></div>
            <div><dt>Route</dt><dd>${escapeHtml(primary.ai_path || "deterministic")}${primary.escalation?.escalate ? " · Luna escalated" : ""}</dd></div>
            <div><dt>Cutoff</dt><dd>${escapeHtml(formatTimestamp(cycle.data_cutoff))}</dd></div>
          </dl>
        </details>
      </div>`);
  }
  if (!parts.length) {
    return emptyState(
      instrument?.market_type === "spot"
        ? "No open holding. AI Spot allocations follow approved long decisions when enabled in Settings › Portfolio policy."
        : "No AI decision recorded for this instrument yet (it may be outside the scan universe).",
    );
  }
  return parts.join("");
}
