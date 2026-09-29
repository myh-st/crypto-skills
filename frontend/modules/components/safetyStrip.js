// Visible safety state: kill-switch level, market safety state, price confidence, what is
// allowed or blocked right now, and why. Deterministic code owns all of it; AI cannot change it.
import { escapeHtml } from "../format.js";

export const KILL_LEVELS = [
  ["NORMAL", "Normal"],
  ["NO_NEW_ENTRIES", "No new entries"],
  ["AI_MANAGEMENT_PAUSED", "AI management paused"],
  ["RISK_REDUCING_ONLY", "Risk-reducing only"],
  ["FULL_AUTOMATION_HALT", "Full automation halt"],
];
const STATE_TONE = { NORMAL: "ok", VOLATILITY_ALERT: "warn", RECOVERY: "warn", CRASH_MODE: "bad", MARKET_DATA_UNTRUSTED: "bad" };
const RESTRICTION_LABELS = {
  new_entries: "New entries",
  averaging_down: "Averaging down",
  leverage_increase: "Leverage increase",
  discretionary_full_exit: "Discretionary full exit",
  protective_reduce: "Protective reduce",
  emergency_liquidation_reduce: "Liquidation emergency reduce",
};

export function renderSafetyStrip({ killSwitch = null, assessment = null } = {}) {
  const pill = (label, value, tone) => `<span class="status-pill status-pill--${tone}"><small>${escapeHtml(label)}</small> ${escapeHtml(value)}</span>`;
  const level = killSwitch?.level || "NORMAL";
  const parts = [pill("Kill switch", level.replaceAll("_", " "), level === "NORMAL" ? "ok" : ["RISK_REDUCING_ONLY", "FULL_AUTOMATION_HALT"].includes(level) ? "bad" : "warn")];
  if (assessment) {
    parts.push(pill("Market", assessment.state.replaceAll("_", " "), STATE_TONE[assessment.state] || "idle"));
    parts.push(pill("Price confidence", assessment.price_confidence, assessment.price_confidence === "HIGH" ? "ok" : assessment.price_confidence === "UNTRUSTED" ? "bad" : "warn"));
  }
  const reasons = (assessment?.reasons || []).filter((reason) => reason !== "CRASH_MODE_RECOVERED");
  return `
    <div class="safety-strip" role="status" aria-label="Execution safety">
      <div class="health-strip">${parts.join("")}</div>
      ${reasons.length ? `<p class="small safety-reasons">${reasons.map((reason) => `<span class="cat-badge">${escapeHtml(reason)}</span>`).join(" ")}</p>` : ""}
    </div>`;
}

export function renderRestrictions(restrictions) {
  if (!restrictions) return "";
  return `
    <ul class="restriction-list">
      ${Object.entries(restrictions).map(([key, value]) => `
        <li><span>${escapeHtml(RESTRICTION_LABELS[key] || key)}</span>
          <strong class="restriction restriction--${value === "ALLOWED" ? "ok" : value === "BLOCKED" || value.startsWith("DEFERRED") ? "bad" : "warn"}">${value === "ALLOWED" ? "✓ allowed" : value === "BLOCKED" ? "✕ blocked" : `⚠ ${escapeHtml(value.replaceAll("_", " ").toLowerCase())}`}</strong></li>`).join("")}
    </ul>`;
}

export function renderKillSwitchControl(killSwitch) {
  const level = killSwitch?.level || "NORMAL";
  return `
    <form class="kill-switch-form" data-kill-switch-form>
      <label>Kill switch
        <select name="level">${KILL_LEVELS.map(([value, label]) => `<option value="${value}" ${value === level ? "selected" : ""}>${label}</option>`).join("")}</select>
      </label>
      <label class="kill-reason">Reason <input name="reason" type="text" maxlength="200" placeholder="optional" /></label>
      <button type="submit" class="btn btn--small">Apply</button>
      <small class="muted">Raising takes effect immediately. Lowering needs confirmation and a passing reconciliation. Liquidation safety and stop monitoring never stop.</small>
    </form>`;
}
