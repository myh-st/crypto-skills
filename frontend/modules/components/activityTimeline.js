// Legacy low-level runtime event list.
import { escapeHtml, formatTimestamp, titleCase } from "../format.js";

export function renderActivity(events) {
  if (!events.length) return '<p class="muted">No activity has been recorded.</p>';
  return `
    <ul class="paper-event-list">
      ${events.slice(0, 12).map((event) => {
        const details = event.payload || {};
        const label = details.symbol || details.status || details.position_id || details.reason || "";
        return `
          <li>
            <strong>${escapeHtml(titleCase(event.event_type))}</strong>
            <span>${escapeHtml(String(label))}</span>
            <small>${escapeHtml(formatTimestamp(event.created_at))} · ${escapeHtml(String(event.cycle_id || "runtime"))}</small>
          </li>
        `;
      }).join("")}
    </ul>
  `;
}
