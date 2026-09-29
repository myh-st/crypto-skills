// Legacy low-level runtime event list.
import { escapeHtml, formatTimestamp, titleCase } from "../format.js";
import { emptyState, severityBadge, sourceBadge } from "./ui.js";

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

// Unified human-readable journal (AI / USER / SYSTEM).
export function renderTimeline(events, { compact = false } = {}) {
  if (!events?.length) return emptyState("No activity recorded yet.");
  return `
    <ol class="timeline${compact ? " timeline--compact" : ""}">
      ${events.map((event) => `
        <li class="timeline-row timeline-row--${escapeHtml(String(event.severity).toLowerCase())}">
          <time datetime="${escapeHtml(event.timestamp)}">${escapeHtml(formatTimestamp(event.timestamp))}</time>
          <span class="timeline-symbol">${escapeHtml(event.symbol || "—")}</span>
          <span class="timeline-title">
            ${event.position_ref ? `<button type="button" class="link-button" data-open-position="${escapeHtml(event.position_ref)}">${escapeHtml(event.title)}</button>` : escapeHtml(event.title)}
            ${compact || !event.summary ? "" : `<small>${escapeHtml(event.summary)}</small>`}
          </span>
          <span class="timeline-meta">${sourceBadge(event.source)} <span class="cat-badge">${escapeHtml(event.category)}</span>${event.severity !== "INFO" ? ` ${severityBadge(event.severity)}` : ""}</span>
        </li>`).join("")}
    </ol>`;
}
