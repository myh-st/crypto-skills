// Structured AI re-plan: before/after diff, reason codes, evidence on demand. No transcript,
// no chain-of-thought — only typed fields the server validated.
import { escapeHtml, formatTimestamp } from "../format.js";
import { MODE_LABELS, fmtNumber, pct, pnl, price } from "./ui.js";

export const REASON_LABELS = {
  MOMENTUM_INTACT: "Momentum intact",
  MOMENTUM_WEAKENED: "Momentum weakened",
  BTC_RISK_INCREASED: "BTC risk increased",
  REGIME_SHIFT: "Regime shifted against position",
  VOLATILITY_EXPANSION: "Volatility expanding",
  VOLATILITY_CONTRACTION: "Volatility contracting",
  PROTECT_PROFIT: "Protect open profit",
  THESIS_INTACT: "Thesis intact",
  THESIS_WEAKENED: "Thesis weakened",
  THESIS_BROKEN: "Thesis broken",
  NEAR_STOP: "Price near stop",
  NEAR_TARGET: "Price near target",
  REDUCE_EXPOSURE: "Reduce exposure",
  PORTFOLIO_CONCENTRATION: "Portfolio concentration",
  FUNDING_ADVERSE: "Funding adverse",
  GIVE_ROOM: "Give the trade more room",
  TIGHTEN_RISK: "Tighten risk",
  NO_CHANGE_NEEDED: "No change needed",
  USER_REQUEST: "Your request",
  AI_BUDGET_BLOCK: "AI budget blocked the model call",
  AI_UNAVAILABLE: "AI unavailable",
};

export const QUICK_INTENTS = [
  ["tighten_risk", "Tighten risk"],
  ["protect_profit", "Protect profit"],
  ["give_more_room", "Give it more room"],
  ["reduce_exposure", "Reduce exposure"],
  ["exit_if_thesis_weakened", "Exit if thesis weakened"],
  ["reassess", "Reassess from scratch"],
];

function targets(list) {
  return (list || []).length ? list.map((value) => price(value)).join(" · ") : "—";
}

function row(label, before, after, changed) {
  return `<tr class="${changed ? "diff-changed" : ""}"><th scope="row">${escapeHtml(label)}</th><td>${before}</td><td aria-hidden="true">→</td><td>${after}${changed ? ' <span class="sr-only">(changed)</span>' : ""}</td></tr>`;
}

export function renderReplanProposal(proposal, { editable = true } = {}) {
  if (!proposal) return "";
  if (proposal.status === "blocked") {
    return `<div class="replan replan--blocked" role="note"><strong>Re-plan blocked · ${escapeHtml(proposal.code)}</strong><p>${escapeHtml(proposal.reason || "")}</p></div>`;
  }
  const current = proposal.current || {};
  const next = proposal.proposed || {};
  const reasons = (proposal.reason_codes || []).map((code) => `<li>${escapeHtml(REASON_LABELS[code] || code)}</li>`).join("");
  const pending = proposal.status === "proposed";
  const evidence = proposal.evidence || {};
  const ai = proposal.ai || {};
  return `
    <div class="replan replan--${escapeHtml(proposal.status)}" data-proposal="${escapeHtml(proposal.proposal_id)}">
      <div class="replan-head">
        <strong>${escapeHtml(proposal.headline || "Re-plan")}</strong>
        <span class="pill pill--${pending ? "caution" : proposal.status === "applied" ? "positive" : "neutral"}">${escapeHtml(proposal.status.replace("_", " "))}</span>
        <small class="muted">${escapeHtml(proposal.source || "")} · ${escapeHtml(formatTimestamp(proposal.created_at))}${proposal.risk_increased ? " · increases risk" : ""}</small>
      </div>
      <table class="diff-table">
        <thead><tr><th scope="col"><span class="sr-only">Field</span></th><th scope="col">Current</th><th scope="col"><span class="sr-only">to</span></th><th scope="col">Proposed</th></tr></thead>
        <tbody>
          ${row("Stop", escapeHtml(price(current.stop_price)), escapeHtml(price(next.stop_price)), current.stop_price !== next.stop_price)}
          ${row("Targets", escapeHtml(targets(current.targets)), escapeHtml(targets(next.targets)), JSON.stringify(current.targets) !== JSON.stringify(next.targets))}
          ${row("Size", escapeHtml(pct(current.remaining_pct, { digits: 0 })), escapeHtml(pct(next.remaining_pct, { digits: 0 })), current.remaining_pct !== next.remaining_pct)}
          ${row("Risk at stop", escapeHtml(`${fmtNumber(current.risk_usdt, { digits: 2 })} USDT`), escapeHtml(`${fmtNumber(next.risk_usdt, { digits: 2 })} USDT`), current.risk_usdt !== next.risk_usdt)}
          ${row("Locked at stop", pnl(current.locked_pnl_at_stop_usdt), pnl(next.locked_pnl_at_stop_usdt), current.locked_pnl_at_stop_usdt !== next.locked_pnl_at_stop_usdt)}
          ${row("Portfolio exposure", escapeHtml(pct(current.exposure_pct, { digits: 1 })), escapeHtml(pct(next.exposure_pct, { digits: 1 })), current.exposure_pct !== next.exposure_pct)}
          ${row("Control", escapeHtml(MODE_LABELS[current.management_mode] || "—"), escapeHtml(MODE_LABELS[next.management_mode] || "—"), false)}
        </tbody>
      </table>
      <div class="replan-why"><span class="muted small">Why</span><ul>${reasons}</ul></div>
      <details class="replan-evidence">
        <summary>Evidence</summary>
        <dl class="kv">
          <div><dt>Data cutoff</dt><dd>${escapeHtml(formatTimestamp(evidence.data_cutoff))}</dd></div>
          <div><dt>Price</dt><dd>${escapeHtml(price(evidence.price))}</dd></div>
          <div><dt>ATR / RSI</dt><dd>${escapeHtml(price(evidence.atr))} / ${escapeHtml(fmtNumber(evidence.rsi14, { digits: 1 }))}</dd></div>
          <div><dt>Quant regime</dt><dd>${escapeHtml(evidence.quant_regime || "—")}</dd></div>
          <div><dt>Jev regime / conflict</dt><dd>${escapeHtml(evidence.jev_regime || "—")} / ${escapeHtml(fmtNumber(evidence.jev_conflict, { digits: 2 }))}</dd></div>
          <div><dt>AI route</dt><dd>Jev ${escapeHtml(ai.jev || "—")} · Luna ${escapeHtml(ai.luna || "—")}${ai.budget_block ? ` · budget ${escapeHtml(ai.budget_block)}` : ""}</dd></div>
          ${evidence.summary ? `<div><dt>Summary</dt><dd>${escapeHtml(evidence.summary)}</dd></div>` : ""}
        </dl>
      </details>
      ${pending && editable ? `
        <div class="replan-actions">
          <button type="button" class="btn btn--primary btn--small" data-replan-apply="${escapeHtml(proposal.proposal_id)}">Apply</button>
          <button type="button" class="btn btn--ghost btn--small" data-replan-edit="${escapeHtml(proposal.proposal_id)}" aria-expanded="false">Edit</button>
          <button type="button" class="btn btn--ghost btn--small" data-replan-reject="${escapeHtml(proposal.proposal_id)}">Reject</button>
        </div>
        <form class="replan-edit" data-replan-edit-form="${escapeHtml(proposal.proposal_id)}" hidden>
          <label>Action
            <select name="action">
              ${["hold", "adjust", "reduce", "close"].map((action) => `<option value="${action}" ${proposal.proposal?.action === action ? "selected" : ""}>${action}</option>`).join("")}
            </select>
          </label>
          <label>Stop <input name="stop_price" type="number" step="any" value="${escapeHtml(proposal.proposal?.stop_price ?? "")}" /></label>
          <label>Targets <input name="target_prices" type="text" inputmode="decimal" value="${escapeHtml((proposal.proposal?.target_prices || []).join(", "))}" placeholder="comma separated" /></label>
          <label>Reduce fraction <input name="reduce_fraction" type="number" min="0.05" max="0.95" step="0.05" value="${escapeHtml(proposal.proposal?.reduce_fraction ?? "")}" /></label>
          <button type="submit" class="btn btn--primary btn--small">Apply edited plan</button>
        </form>` : ""}
    </div>`;
}

export function editsFromForm(form) {
  const data = new FormData(form);
  const numberOrNull = (value) => (value === null || String(value).trim() === "" ? null : Number(value));
  const targetsText = String(data.get("target_prices") || "").trim();
  const action = data.get("action");
  return {
    action,
    stop_price: action === "close" ? null : numberOrNull(data.get("stop_price")),
    target_prices: action === "close" || !targetsText ? [] : targetsText.split(",").map((value) => Number(value.trim())).filter(Number.isFinite),
    reduce_fraction: action === "reduce" ? numberOrNull(data.get("reduce_fraction")) : null,
  };
}
