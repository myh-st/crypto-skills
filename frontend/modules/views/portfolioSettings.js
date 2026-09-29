// Portfolio policy (operational, audited server-side): Spot risk limits, manual perp risk cap,
// default AI/user authority, Portfolio Brain limits, and the autonomous review cadence.
import { escapeHtml } from "../format.js";
import { paperApi } from "../paperApi.js";
import { feedback } from "../components/ui.js";
import { withConfirmation } from "../components/confirmDialog.js";

const MODES = [["AUTO_PAPER", "AI managed (AUTO_PAPER)"], ["RECOMMEND_ONLY", "Recommend only"], ["MANUAL_OVERRIDE", "Manual override"], ["PAUSED", "AI paused"]];

const PERCENT_FIELDS = [
  ["spot_max_allocation_pct", "Spot max allocation per asset %"],
  ["spot_max_deployed_pct", "Spot max deployed capital %"],
  ["spot_min_cash_reserve_pct", "Spot minimum cash reserve %"],
  ["perp_manual_max_risk_pct", "Manual perp max risk per trade %"],
  ["ai_spot.allocation_pct", "AI Spot allocation per entry % of Spot equity"],
  ["brain.max_asset_risk_pct", "Brain: max risk per asset %"],
  ["brain.max_correlated_risk_pct", "Brain: max correlated (BTC/ETH/ALT) risk %"],
  ["brain.max_direction_risk_pct", "Brain: max same-direction risk %"],
  ["lifecycle.default_core_fraction", "Lifecycle: default Core share for AI holdings %"],
  ["lifecycle.distribute_step_fraction", "Lifecycle: Tactical sold per distribution step %"],
  ["lifecycle.protect_giveback_pct", "Lifecycle: profit giveback that triggers protection %"],
  ["lifecycle.strong_trend_return_pct", "Lifecycle: strong-trend cycle return %"],
  ["safety.crash_pct_5m", "Safety: 5-minute crash threshold %"],
  ["safety.crash_pct_15m", "Safety: 15-minute crash threshold %"],
  ["safety.sell_velocity_max_fraction", "Safety: max discretionary sell per window %"],
  ["safety.liquidation_emergency_buffer_pct", "Safety: liquidation emergency buffer %"],
];
const NUMBER_FIELDS = [
  ["spot_slippage_bps", "Spot slippage (bps)"],
  ["brain.max_gross_exposure_x", "Brain: max gross exposure (x equity)"],
  ["brain.protect_profit_r", "Protect-profit threshold (R)"],
  ["review.management_interval_minutes", "AI review interval (minutes)"],
  ["review.min_minutes_between_reviews", "Min minutes between AI reviews"],
  ["lifecycle.review_interval_minutes", "Lifecycle: review interval (minutes)"],
  ["lifecycle.exit_confirm_reviews", "Lifecycle: breakdown reviews before Core exit"],
  ["safety.spread_vacuum_bps", "Safety: liquidity-vacuum spread (bps)"],
  ["safety.max_slippage_bps_reduce", "Safety: max slippage on reductions (bps)"],
  ["safety.preview_ttl_seconds", "Safety: order preview TTL (seconds)"],
  ["safety.spot_stop_confirm_closes", "Safety: Spot stop confirmation closes"],
];

function get(settings, path) {
  return path.split(".").reduce((value, key) => value?.[key], settings);
}

function setPath(target, path, value) {
  const keys = path.split(".");
  let node = target;
  keys.slice(0, -1).forEach((key) => {
    node[key] = node[key] || {};
    node = node[key];
  });
  node[keys[keys.length - 1]] = value;
}

export function renderPortfolioSettings(settings) {
  return `
    <form class="paper-form" data-portfolio-settings>
      <div class="paper-form-grid">
        ${PERCENT_FIELDS.map(([path, label]) => `<label>${escapeHtml(label)}<input type="number" step="0.1" min="0" name="${path}" data-percent value="${escapeHtml((Number(get(settings, path)) * 100).toFixed(2))}" /></label>`).join("")}
        ${NUMBER_FIELDS.map(([path, label]) => `<label>${escapeHtml(label)}<input type="number" step="any" min="0" name="${path}" value="${escapeHtml(get(settings, path))}" /></label>`).join("")}
        <label>Default authority for AI-opened positions<select name="default_ai_management_mode">${MODES.map(([value, label]) => `<option value="${value}" ${settings.default_ai_management_mode === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        <label>Default authority for your positions<select name="default_user_management_mode">${MODES.map(([value, label]) => `<option value="${value}" ${settings.default_user_management_mode === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      </div>
      <label class="checkbox-row"><input type="checkbox" name="ai_spot.enabled" ${settings.ai_spot.enabled ? "checked" : ""} /> AI may open PAPER Spot allocations from long decisions (AUTO_PAPER, Brain-gated)</label>
      <label>AI Spot trigger <select name="ai_spot.trigger">
        <option value="prefer_spot" ${settings.ai_spot.trigger === "prefer_spot" ? "selected" : ""}>Only when the Brain prefers Spot (adverse funding)</option>
        <option value="all_long" ${settings.ai_spot.trigger === "all_long" ? "selected" : ""}>Every approved long decision</option>
      </select></label>
      <label class="checkbox-row"><input type="checkbox" name="brain.enabled" ${settings.brain.enabled ? "checked" : ""} /> Apply Portfolio Brain to AI entries (it can only shrink or block, never size up)</label>
      <label class="checkbox-row"><input type="checkbox" name="review.enabled" ${settings.review.enabled ? "checked" : ""} /> Autonomous position review queue (deterministic trigger → Jev → optional Luna)</label>
      <label class="checkbox-row"><input type="checkbox" name="review.use_luna" ${settings.review.use_luna ? "checked" : ""} /> Allow Luna escalation in re-plans (budget guard still applies)</label>
      <p class="muted small">Automation: new entries ${settings.automation.new_entries_paused ? "PAUSED" : "on"} · AI management ${settings.automation.ai_management_paused ? "PAUSED" : "on"} · emergency stop ${settings.automation.emergency_stop ? "ON" : "off"} — change these from Overview.</p>
      <button type="submit" class="btn btn--primary">Save portfolio policy</button>
      <div class="paper-feedback" data-portfolio-settings-feedback role="status" aria-live="polite"></div>
    </form>`;
}

export function patchFromForm(form) {
  const patch = {};
  for (const element of form.elements) {
    if (!element.name) continue;
    if (element.type === "checkbox") setPath(patch, element.name, element.checked);
    else if (element.tagName === "SELECT") setPath(patch, element.name, element.value);
    else if (element.type === "number") {
      const value = Number(element.value);
      setPath(patch, element.name, element.dataset.percent !== undefined ? value / 100 : value);
    }
  }
  return patch;
}

export async function mountPortfolioSettings(host) {
  try {
    const { settings } = await paperApi.portfolioSettings();
    host.innerHTML = renderPortfolioSettings(settings);
    const form = host.querySelector("[data-portfolio-settings]");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const note = form.querySelector("[data-portfolio-settings-feedback]");
      try {
        const patch = patchFromForm(form);
        const result = await withConfirmation((confirm) => paperApi.savePortfolioSettings(patch, { confirm }));
        if (result === null) {
          feedback(note, "Cancelled · nothing changed.", "neutral");
          return;
        }
        feedback(note, "Portfolio policy saved and audited.", "success");
      } catch (error) {
        feedback(note, error.message, "error");
      }
    });
  } catch (error) {
    host.innerHTML = `<p class="muted">PAPER runtime unavailable: ${escapeHtml(error.message)}</p>`;
  }
}
