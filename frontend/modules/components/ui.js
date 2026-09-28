// Small shared UI primitives.
import { escapeHtml } from "../format.js";

export function metric(label, value, note = "") {
  return `
    <div class="paper-metric">
      <span class="paper-metric-value">${escapeHtml(value)}</span>
      <span class="paper-metric-label">${escapeHtml(label)}</span>
      ${note ? `<small>${escapeHtml(note)}</small>` : ""}
    </div>
  `;
}
