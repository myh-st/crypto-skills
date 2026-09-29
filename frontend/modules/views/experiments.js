// Experiments: every running PAPER experiment on this machine in one place (EXP-001, EXP-002, EXP-003…).
// The server reads its loopback peers (GET only); the browser never talks to another origin.
import { escapeHtml } from "../format.js";
import { paperApi } from "../paperApi.js";
import { autoRefreshBar, startAutoRefresh } from "../components/autoRefresh.js";
import { calendarHeatmap, fmtNum, fmtPct } from "../components/charts.js";
import { applyMotion, skeleton, startCountdowns } from "../components/motion.js";

const ENGINES = { sleeves_v1: "Trend sleeves", breakout_15m: "15m breakout + AI" };

function card(e) {
  if (e.error) {
    return `<article class="panel exp-card exp-card--error"><h2>Port ${escapeHtml(e.port)}</h2><p class="neg-text small">${escapeHtml(e.error)}</p></article>`;
  }
  const numOrNaN = (v) => (v === null || v === undefined ? NaN : Number(v));  // missing is "—", never a fake 0
  const total = numOrNaN(e.pnl_total_usdt);
  const today = numOrNaN(e.pnl_today_usdt);
  const start = Number(e.starting_capital_usdt) || null;
  const trades = Number(e.completed_trades || 0);
  const target = Number(e.target_trades || 0) || 1;
  const days = Number(e.day || 0);
  const minDays = Number(e.min_days || 0) || 1;
  const ai = e.ai || null;
  const health = e.health || "—";
  return `
    <article class="panel exp-card" data-motion-enter="exp:${escapeHtml(e.port)}">
      <header class="exp-card-head">
        <div><h2>${escapeHtml(e.label)}</h2><span class="muted small">${escapeHtml(ENGINES[e.engine] || e.engine)} · port ${escapeHtml(e.port)}${e.self ? " · this server" : ""}</span></div>
        <div class="exp-pills">
          <span class="pill ${e.status === "running" ? "pill--positive" : "pill--caution"}">${escapeHtml(e.status || "?")}</span>
          <span class="pill ${health === "OK" ? "pill--positive" : "pill--caution"}">health ${escapeHtml(health)}</span>
          ${e.self ? "" : `<a class="btn btn--ghost btn--small" href="${escapeHtml(e.url)}" target="_blank" rel="noopener">Open ↗</a>`}
        </div>
      </header>
      <div class="exp-numbers">
        <div><small>Total P&L</small><strong data-motion-key="exp:${escapeHtml(e.port)}:total" class="${!Number.isFinite(total) ? "" : total >= 0 ? "pos-text" : "neg-text"}">${Number.isFinite(total) ? `${escapeHtml(fmtNum(total))} USDT` : "—"}</strong>
          <span class="muted small">${start && Number.isFinite(total) ? escapeHtml(fmtPct(total / start, 2)) : ""}${e.equity_usdt != null ? ` · equity ${escapeHtml(Number(e.equity_usdt).toFixed(2))}` : ""}</span></div>
        <div><small>Today</small><strong data-motion-key="exp:${escapeHtml(e.port)}:today" class="${!Number.isFinite(today) ? "" : today >= 0 ? "pos-text" : "neg-text"}">${Number.isFinite(today) ? `${escapeHtml(fmtNum(today))} USDT` : "—"}</strong>
          <span class="muted small">${Number.isFinite(numOrNaN(e.pnl_today_pct)) ? escapeHtml(fmtPct(e.pnl_today_pct, 2)) : ""}</span></div>
        <div><small>Next decision</small><strong>${e.next_decision_at ? `<span data-countdown="${escapeHtml(e.next_decision_at)}">…</span>` : "—"}</strong>
          <span class="muted small">${e.status === "running" ? "engine tick" : "not running"}</span></div>
        <div><small>Open positions</small><strong>${escapeHtml(e.open_positions ?? "—")}</strong>
          <span class="muted small">${e.drawdown != null ? `drawdown ${escapeHtml((Number(e.drawdown) * 100).toFixed(1))}%` : ""}</span></div>
        <div><small>AI</small><strong>${ai ? (ai.enabled ? `${escapeHtml(ai.calls_total)} calls` : "Not used") : "—"}</strong>
          <span class="muted small">${ai?.enabled ? `$${escapeHtml(Number(ai.cost_usd_estimate || 0).toFixed(4))} so far` : ai ? "rule-based" : "older server"}</span></div>
      </div>
      <div class="exp-progress" data-motion-fill="exp:${escapeHtml(e.port)}:progress">
        <label class="small">Day ${escapeHtml(days.toFixed(1))} of ${escapeHtml(minDays)}<span class="campaign-bar"><span style="width:${Math.min(100, (days / minDays) * 100).toFixed(1)}%"></span></span></label>
        <label class="small">Trades ${escapeHtml(trades)} / ${escapeHtml(target)}<span class="campaign-bar"><span style="width:${Math.min(100, (trades / target) * 100).toFixed(1)}%"></span></span></label>
      </div>
      ${e.calendar?.length ? calendarHeatmap(e.calendar, { title: "Last 14 days", motionKey: `exp:${e.port}:calendar` }) : ""}
      ${(e.risk_incidents || []).length ? `<ul class="campaign-warnings small">${e.risk_incidents.map((i) => `<li>⚠ ${escapeHtml(i.kind)}: ${escapeHtml(i.summary)}</li>`).join("")}</ul>` : ""}
    </article>`;
}

export function renderExperiments(data) {
  const rows = data?.experiments || [];
  if (!rows.length) return '<p class="muted">No PAPER experiment servers found on this machine.</p>';
  return `<div class="exp-grid">${rows.map(card).join("")}</div>
    <p class="muted small">Looks for PAPER servers on loopback ports ${escapeHtml((data.probed_ports || []).join(", "))}. Read-only.</p>`;
}

export function render(root) {
  root.innerHTML = `<div class="view view--experiments">
    <header class="page-header"><div><h1>Experiments</h1><p>All PAPER experiments side by side</p></div>${autoRefreshBar(30000)}</header>
    <div data-experiments>${skeleton(4)}</div></div>`;
  const view = root.firstElementChild;
  return startAutoRefresh(view, async () => {
    view.querySelector("[data-experiments]").innerHTML = renderExperiments(await paperApi.experiments());
    applyMotion(view);
    startCountdowns();
  }, { intervalMs: 30000 });
}
