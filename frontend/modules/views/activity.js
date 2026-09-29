// Activity: one human-readable AI / USER / SYSTEM journal with filters, plus attention history.
// Raw technical events stay available in Research › Paper Trading Lab for debugging.
import { escapeHtml, formatTimestamp } from "../format.js";
import { paperApi } from "../paperApi.js";
import { renderTimeline } from "../components/activityTimeline.js";
import { feedback, severityBadge } from "../components/ui.js";

const CATEGORIES = ["SIGNAL", "DECISION", "RISK", "ORDER", "FILL", "MANAGEMENT", "COST", "ALERT", "OUTCOME"];

function select(name, label, options) {
  return `<label>${escapeHtml(label)}<select name="${name}"><option value="">All</option>${options.map(([value, text]) => `<option value="${value}">${escapeHtml(text)}</option>`).join("")}</select></label>`;
}

export function render(root, ctx) {
  root.innerHTML = `<div class="view view--activity">
    <header class="page-header"><div><h1>Activity</h1><p>What the AI, you, and the system did — newest first</p></div></header>
    <section class="panel">
      <form class="filter-bar" data-activity-filters>
        <label>Symbol<input name="symbol" type="text" placeholder="BTCUSDT" autocomplete="off" /></label>
        ${select("source", "Source", [["AI", "AI"], ["USER", "User"], ["SYSTEM", "System"]])}
        ${select("category", "Event", CATEGORIES.map((value) => [value, value.toLowerCase()]))}
        ${select("market_type", "Market", [["spot", "Spot"], ["perpetual", "Perpetual"]])}
        ${select("severity", "Min severity", [["WATCH", "Watch+"], ["ACTION", "Action+"], ["CRITICAL", "Critical"]])}
        <button type="submit" class="btn btn--primary btn--small">Apply</button>
      </form>
      <div class="paper-feedback" data-activity-feedback role="status" aria-live="polite"></div>
      <div data-timeline><p class="muted">Loading…</p></div>
    </section>
    <section class="panel">
      <div class="section-heading"><h2>Attention history</h2><span class="muted small">deduplicated · auto-resolved when the condition clears</span></div>
      <div data-attention-history></div>
    </section></div>`;
  const view = root.firstElementChild;
  const form = view.querySelector("[data-activity-filters]");
  const note = view.querySelector("[data-activity-feedback]");
  let disposed = false;

  async function refresh() {
    const data = Object.fromEntries(new FormData(form).entries());
    try {
      const [activity, attention] = await Promise.all([
        paperApi.activity({ ...data, symbol: data.symbol?.trim().toUpperCase(), limit: 300 }),
        paperApi.attention({ includeResolved: true }),
      ]);
      if (disposed) return;
      view.querySelector("[data-timeline]").innerHTML = renderTimeline(activity.events);
      const items = attention.items || [];
      view.querySelector("[data-attention-history]").innerHTML = items.length ? `
        <div class="table-scroll"><table class="data-table">
          <thead><tr><th scope="col">Severity</th><th scope="col">Item</th><th scope="col">Status</th><th scope="col">First seen</th><th scope="col">Resolved</th><th scope="col">Seen</th></tr></thead>
          <tbody>${items.map((item) => `<tr>
            <td>${severityBadge(item.severity)}</td><td>${escapeHtml(item.title)}<br><small class="muted">${escapeHtml(item.summary)}</small></td>
            <td>${escapeHtml(item.status)}${item.resolution ? ` <small class="muted">(${escapeHtml(item.resolution.replaceAll("_", " "))})</small>` : ""}</td>
            <td>${escapeHtml(formatTimestamp(item.first_seen_at))}</td><td>${escapeHtml(formatTimestamp(item.resolved_at))}</td><td>${escapeHtml(item.occurrences)}×</td>
          </tr>`).join("")}</tbody></table></div>` : '<p class="muted small">No attention items yet.</p>';
    } catch (error) {
      if (!disposed) feedback(note, error.message, "error");
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    refresh();
  });
  refresh();
  const timer = setInterval(refresh, 20000);
  return () => {
    disposed = true;
    clearInterval(timer);
  };
}
