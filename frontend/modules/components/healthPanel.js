// Compact runtime health: what is working, what is degraded, and open incidents. Read-only;
// the only action offered is a verified, secret-free backup.
import { escapeHtml, relativeTime } from "../format.js";

export const HEALTH_COMPONENTS = [
  ["scheduler", "Scheduler"], ["monitor", "Monitor"], ["market_feed", "Market feed"], ["database", "Database"],
  ["storage", "Storage"], ["ai_providers", "AI providers"], ["budget_guard", "Budget guard"],
  ["reconciliation", "Reconciliation"], ["kill_switch", "Kill switch"],
];
const TONE = { OK: "ok", IDLE: "idle", OFF: "idle", ATTENTION: "warn", RESTRICTED: "warn", UNKNOWN: "warn", DEGRADED: "warn",
  BLOCKED: "bad", LOW: "bad", FAILED: "bad", CRITICAL: "bad" };

export function renderHealth(health) {
  if (!health) return '<p class="muted small">Health unavailable.</p>';
  const components = health.components || {};
  const pills = HEALTH_COMPONENTS.filter(([key]) => components[key]).map(([key, label]) => {
    const status = components[key].status;
    return `<span class="status-pill status-pill--${TONE[status] || "idle"}"><small>${escapeHtml(label)}</small> ${escapeHtml(status)}</span>`;
  }).join("");
  const incidents = health.open_incidents || [];
  const last = components.last_success || {};
  return `
    <div class="health-panel" data-health-overall="${escapeHtml(health.overall)}">
      <p><span class="status-pill status-pill--${TONE[health.overall] || "idle"}"><small>System</small> ${escapeHtml(health.overall)}</span></p>
      <div class="health-strip">${pills}</div>
      <p class="small muted">Last cycle ${last.cycle_at ? escapeHtml(relativeTime(last.cycle_at)) : "—"} · last position review ${last.position_review_at ? escapeHtml(relativeTime(last.position_review_at)) : "—"}${components.database?.size_bytes != null ? ` · DB ${escapeHtml((components.database.size_bytes / 1048576).toFixed(1))} MB` : ""}${components.storage?.free_mb != null ? ` · ${escapeHtml(Math.round(components.storage.free_mb))} MB free` : ""}</p>
      ${incidents.length ? `<ul class="incident-list small">${incidents.slice(0, 5).map((incident) => `
        <li><span class="cat-badge">${escapeHtml(incident.kind.replaceAll("_", " "))}</span> ${escapeHtml(incident.summary)}${incident.occurrences > 1 ? ` <span class="muted">×${escapeHtml(incident.occurrences)}</span>` : ""} <span class="muted">${escapeHtml(relativeTime(incident.last_seen_at))}</span></li>`).join("")}</ul>` : '<p class="small muted">No open incidents.</p>'}
      <button type="button" class="btn btn--ghost btn--small" data-backup>Create backup</button>
    </div>`;
}
