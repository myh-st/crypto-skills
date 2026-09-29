// Prioritized, deduplicated attention items derived server-side from runtime state.
import { escapeHtml, relativeTime } from "../format.js";
import { emptyState, severityBadge } from "./ui.js";

export function attentionActionAttrs(item) {
  const action = item.action || {};
  if (action.position_ref) {
    return `data-open-position="${escapeHtml(action.position_ref)}"${action.replan_intent ? ` data-replan-intent="${escapeHtml(action.replan_intent)}"` : ""}`;
  }
  const route = { portfolio: "portfolio", settings: "settings", trade: "trade", orders: "portfolio", activity: "activity", overview: "overview" }[action.route];
  return route ? `data-route-to="${route}"` : "";
}

export function renderAttentionQueue(attention, { limit = 8 } = {}) {
  const items = (attention?.items || []).filter((item) => item.status !== "resolved");
  if (!items.length) {
    return emptyState("Nothing needs your attention. Automation and positions are within policy.");
  }
  return `
    <ul class="attention-list" aria-label="Needs attention">
      ${items.slice(0, limit).map((item) => `
        <li class="attention-item attention-item--${escapeHtml(item.severity.toLowerCase())}${item.status === "acknowledged" ? " is-acknowledged" : ""}">
          ${severityBadge(item.severity)}
          <div class="attention-text">
            <strong>${escapeHtml(item.title)}</strong>
            <small>${escapeHtml(item.summary || "")}${item.occurrences > 1 ? ` · seen ${escapeHtml(item.occurrences)}×` : ""} · ${escapeHtml(relativeTime(item.first_seen_at))}</small>
          </div>
          <div class="attention-actions">
            <button type="button" class="btn btn--small" ${attentionActionAttrs(item)}>Review</button>
            ${item.status === "open" ? `<button type="button" class="btn btn--ghost btn--small" data-ack-attention="${escapeHtml(item.attention_id)}" aria-label="Acknowledge: ${escapeHtml(item.title)}">Ack</button>` : '<span class="muted small">acknowledged</span>'}
          </div>
        </li>`).join("")}
    </ul>
    ${items.length > limit ? `<p class="muted small">${items.length - limit} more in Activity</p>` : ""}`;
}

export function attentionCounts(counts = {}) {
  return ["CRITICAL", "ACTION", "WATCH", "INFO"]
    .filter((key) => counts[key])
    .map((key) => `<span class="sev-count sev-count--${key.toLowerCase()}">${counts[key]} ${key.toLowerCase()}</span>`)
    .join(" ");
}
