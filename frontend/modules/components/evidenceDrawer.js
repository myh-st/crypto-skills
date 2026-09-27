// Evidence drawer: a filterable panel presenting the demo evidence ledger
// separately from the main report, matching the requirement that evidence be
// inspected on demand rather than mixed into the primary narrative.

import { escapeHtml, formatTimestamp, titleCase } from "../format.js";
import { EVIDENCE_TYPES } from "../contracts.js";

const SUPPORTS_LABEL = { bull: "Supports bull", bear: "Supports bear", neutral: "Neutral" };

export function renderEvidenceDrawer(evidenceLedger, filters) {
  const { type = "all", supports = "all", quality = "all" } = filters;
  const filtered = evidenceLedger.filter((item) => (
    (type === "all" || item.evidence_type === type)
    && (supports === "all" || item.supports === supports)
    && (quality === "all" || item.quality === quality)
  ));

  const typeOptions = ["all", ...EVIDENCE_TYPES]
    .map((value) => `<option value="${value}" ${value === type ? "selected" : ""}>${value === "all" ? "All evidence types" : titleCase(value)}</option>`)
    .join("");

  const rows = filtered
    .map((item) => `
      <li class="evidence-item evidence-item--${escapeHtml(item.supports)}">
        <div class="evidence-item-header">
          <span class="pill pill--${escapeHtml(item.supports)}">${SUPPORTS_LABEL[item.supports] ?? item.supports}</span>
          <span class="pill pill--quality-${escapeHtml(item.quality)}">${titleCase(item.quality)} quality</span>
          <span class="evidence-type">${titleCase(item.evidence_type)}</span>
        </div>
        <p class="evidence-claim">${escapeHtml(item.claim)}</p>
        <dl class="evidence-meta">
          <div><dt>Metric</dt><dd>${escapeHtml(item.metric)}${item.value !== null && item.value !== undefined ? `: ${escapeHtml(item.value)}${item.unit ? (item.unit === "%" ? escapeHtml(item.unit) : ` ${escapeHtml(item.unit)}`) : ""}` : ""}</dd></div>
          <div><dt>Source</dt><dd>${escapeHtml(item.source.provider)} (${escapeHtml(item.source.type)})</dd></div>
          <div><dt>Observed</dt><dd>${formatTimestamp(item.observed_at)}</dd></div>
        </dl>
        ${item.notes ? `<p class="evidence-notes">${escapeHtml(item.notes)}</p>` : ""}
      </li>
    `)
    .join("");

  return `
    <div class="evidence-drawer">
      <div class="evidence-drawer-header">
        <h3>Evidence ledger <span class="demo-tag">Demo data</span></h3>
        <p class="evidence-drawer-subtitle">Evidence is shown separately from the report above. Filter by type, stance, or quality.</p>
      </div>
      <form class="evidence-filters" data-role="evidence-filters">
        <label>
          Type
          <select name="type">${typeOptions}</select>
        </label>
        <label>
          Stance
          <select name="supports">
            <option value="all" ${supports === "all" ? "selected" : ""}>All stances</option>
            <option value="bull" ${supports === "bull" ? "selected" : ""}>Bull</option>
            <option value="bear" ${supports === "bear" ? "selected" : ""}>Bear</option>
            <option value="neutral" ${supports === "neutral" ? "selected" : ""}>Neutral</option>
          </select>
        </label>
        <label>
          Quality
          <select name="quality">
            <option value="all" ${quality === "all" ? "selected" : ""}>All qualities</option>
            <option value="high" ${quality === "high" ? "selected" : ""}>High</option>
            <option value="medium" ${quality === "medium" ? "selected" : ""}>Medium</option>
            <option value="low" ${quality === "low" ? "selected" : ""}>Low</option>
          </select>
        </label>
      </form>
      <ul class="evidence-list">
        ${rows || '<li class="evidence-empty">No evidence matches the current filters.</li>'}
      </ul>
    </div>
  `;
}
