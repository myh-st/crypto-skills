// Today: the day-trading view. Today's P&L and trades, how much of the daily loss limit is used, the
// intraday equity curve, and a calendar of recent days. Auto-refreshes; read-only.
import { escapeHtml, relativeTime } from "../format.js";
import { paperApi } from "../paperApi.js";
import { autoRefreshBar, startAutoRefresh } from "../components/autoRefresh.js";
import { calendarHeatmap, fmtNum, fmtPct, gauge, lineChart } from "../components/charts.js";

const ENGINES = { sleeves_v1: "Trend sleeves", breakout_15m: "15m breakout + AI" };

function big(label, valueHtml, note = "", tone = "") {
  return `<div class="today-kpi${tone ? ` today-kpi--${tone}` : ""}"><span class="today-kpi-label">${escapeHtml(label)}</span>
    <strong class="today-kpi-value">${valueHtml}</strong>${note ? `<small class="muted">${escapeHtml(note)}</small>` : ""}</div>`;
}

export function renderToday(d) {
  if (!d) return '<p class="muted">Loading…</p>';
  const pnl = Number(d.pnl_today_usdt) || 0;
  const tone = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "";
  const c = d.closed_today || {};
  const winRate = c.trades ? `${Math.round((c.wins / c.trades) * 100)}% win` : "no closed trades yet";
  const limit = d.daily_loss_limit || {};
  const ai = d.ai || {};
  const s = d.summary || {};
  return `
    <section class="panel today-hero">
      <div class="today-kpis">
        ${big("P&L today", `<span class="${tone}-text">${escapeHtml(fmtNum(pnl))} USDT</span>`, `${fmtPct(d.pnl_today_pct, 2)} of ${Number(d.day_start_equity_usdt).toFixed(2)} at 00:00 UTC`, tone)}
        ${big("Equity", `${escapeHtml(Number(d.equity_usdt).toFixed(2))} USDT`, `total ${fmtNum(d.pnl_total_usdt)} since start`)}
        ${big("Closed today", `${escapeHtml(c.trades ?? 0)}`, `${winRate} · fees ${Number(c.fees_usdt || 0).toFixed(2)}`)}
        ${big("Open positions", `${escapeHtml(d.open_positions)}`)}
        ${big("Next decision", escapeHtml(relativeTime(d.next_decision_at)), new Date(d.next_decision_at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }))}
        ${big("AI", ai.enabled ? `${escapeHtml(ai.calls_today)} calls` : "Not used", ai.enabled ? `$${Number(ai.cost_usd_estimate || 0).toFixed(4)} total` : "rule-based engine")}
      </div>
      <div class="today-limit">
        <div class="today-limit-head"><span>Daily loss limit</span>
          <span class="small">${limit.paused ? '<strong class="neg-text">PAUSED for today</strong> · ' : ""}
          loss ${escapeHtml((Number(limit.loss_pct || 0) * 100).toFixed(2))}% of ${escapeHtml((Number(limit.limit_pct || 0) * 100).toFixed(1))}% allowed</span></div>
        ${gauge(limit.used, { label: "daily loss limit used" })}
      </div>
    </section>
    <section class="panel">
      <div class="section-heading"><h2>Equity today</h2><span class="muted small">trading day ${escapeHtml(d.trading_day_utc)} (UTC 00:00 = 07:00 Bangkok)</span></div>
      ${lineChart(d.intraday, { baseline: Number(d.day_start_equity_usdt), label: "Equity today" })}
    </section>
    <section class="panel">
      <div class="section-heading"><h2>Every day</h2><span class="muted small">${escapeHtml(s.green_days ?? 0)}/${escapeHtml(s.days ?? 0)} green days ·
        best ${escapeHtml(fmtNum(s.best_day_usdt))} · worst ${escapeHtml(fmtNum(s.worst_day_usdt))} USDT</span></div>
      ${calendarHeatmap(d.calendar, { title: "Daily P&L, last 6 weeks" })}
    </section>`;
}

export function render(root) {
  root.innerHTML = `<div class="view view--today">
    <header class="page-header"><div><h1>Today</h1><p data-today-sub>Day-trading view · PAPER</p></div>${autoRefreshBar(15000)}</header>
    <div data-today><p class="muted">Loading…</p></div></div>`;
  const view = root.firstElementChild;
  return startAutoRefresh(view, async () => {
    const d = await paperApi.today();
    view.querySelector("[data-today-sub]").textContent = `${d.label} · ${ENGINES[d.engine] || d.engine} · ${d.status} · PAPER`;
    view.querySelector("[data-today]").innerHTML = renderToday(d);
  }, { intervalMs: 15000 });
}
