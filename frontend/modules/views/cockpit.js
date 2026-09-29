// Overview cockpit: "What is happening and what needs my decision?" — portfolio value, PnL
// after AI cost, drawdown, risk, attention, open positions, automation health, latest AI
// actions, and changes since the last visit. Low prose; every control is live.
import { applyMotion } from "../components/motion.js";
import { escapeHtml, formatTimestamp } from "../format.js";
import { renderHealth } from "../components/healthPanel.js";
import { renderCampaign } from "../components/campaignPanel.js";
import { paperApi } from "../paperApi.js";
import { renderTimeline } from "../components/activityTimeline.js";
import { attentionCounts, renderAttentionQueue } from "../components/attentionQueue.js";
import { confirmAction, withConfirmation } from "../components/confirmDialog.js";
import { renderKpiStrip } from "../components/portfolioKpiStrip.js";
import { renderUnifiedPositions } from "../components/positionsTable.js";
import { renderTradingStatusBar } from "../components/tradingStatusBar.js";
import { feedback } from "../components/ui.js";
import { renderKillSwitchControl, renderSafetyStrip } from "../components/safetyStrip.js";

const LAST_VISIT_KEY = "portfolio-os.last-visit.v1";

function lastVisit() {
  try {
    return window.localStorage.getItem(LAST_VISIT_KEY);
  } catch {
    return null;
  }
}

function markVisit(timestamp) {
  try {
    window.localStorage.setItem(LAST_VISIT_KEY, timestamp);
  } catch {
    // Per-browser convenience only.
  }
}

export function nextScanAt(experiment, now = new Date()) {
  // The trend-sleeves engine decides on 4h closes; the breakout engine on 15m closes.
  const delay = Number(experiment?.config?.schedule_delay_seconds ?? 60) * 1000;
  const slot = (experiment?.config?.strategy_engine === "sleeves_v1" ? 4 * 60 : 15) * 60 * 1000;
  const boundary = Math.floor(now.getTime() / slot) * slot;
  return new Date(now.getTime() < boundary + delay ? boundary + delay : boundary + slot + delay).toISOString();
}

export function renderAutomation(experiment, automation) {
  const status = experiment?.status || "stopped";
  const toggle = (key, on, labelOn, labelOff) => `
    <button type="button" class="btn btn--small ${on ? "btn--warn" : "btn--ghost"}" data-automation="${key}" aria-pressed="${on}">
      ${escapeHtml(on ? labelOn : labelOff)}</button>`;
  return `
    <div class="automation-controls">
      <div class="button-row" role="group" aria-label="Scheduler">
        ${status === "stopped" ? '<button type="button" class="btn btn--primary btn--small" data-runtime="start">Start</button>' : ""}
        ${status === "running" ? '<button type="button" class="btn btn--ghost btn--small" data-runtime="pause">Pause</button>' : ""}
        ${status === "paused" ? '<button type="button" class="btn btn--primary btn--small" data-runtime="resume">Resume</button>' : ""}
        ${status !== "stopped" ? '<button type="button" class="btn btn--ghost btn--small" data-runtime="stop">Stop experiment</button>' : ""}
      </div>
      <div class="button-row" role="group" aria-label="Automation policy">
        ${toggle("new_entries_paused", automation?.new_entries_paused, "New entries paused · resume", "Stop new entries")}
        ${toggle("ai_management_paused", automation?.ai_management_paused, "AI management paused · resume", "Pause AI management")}
        ${toggle("emergency_stop", automation?.emergency_stop, "Emergency stop ON · clear", "Emergency stop")}
      </div>
      <p class="muted small">Pausing never stops deterministic stop/target monitoring of open PAPER positions.</p>
    </div>`;
}

export function renderCockpit(root, ctx) {
  root.innerHTML = `<div class="view view--cockpit">
    <header class="page-header">
      <div><h1>Overview</h1><p data-cockpit-sub>PAPER portfolio · loading…</p></div>
      <div class="button-row">
        <a class="btn btn--primary btn--small" href="#/trade">Trade</a>
        <a class="btn btn--ghost btn--small" href="#/portfolio">Portfolio</a>
      </div>
    </header>
    <div data-status-bar></div>
    <div data-safety-strip></div>
    <div data-kpis><div class="kpi-strip kpi-strip--loading" aria-busy="true"></div></div>
    <div data-campaign></div>
    <div class="paper-feedback" data-cockpit-feedback role="status" aria-live="polite"></div>
    <div class="cockpit-grid">
      <div class="cockpit-main">
        <section class="panel" aria-labelledby="attention-heading">
          <div class="section-heading"><h2 id="attention-heading">Needs attention</h2><span data-attention-counts></span></div>
          <div data-attention><p class="muted">Loading…</p></div>
        </section>
        <section class="panel" aria-labelledby="positions-heading">
          <div class="section-heading"><h2 id="positions-heading">Open positions</h2><a class="panel-link" href="#/portfolio">Portfolio →</a></div>
          <div data-positions><p class="muted">Loading…</p></div>
        </section>
      </div>
      <aside class="cockpit-side">
        <section class="panel" aria-labelledby="automation-heading">
          <h2 id="automation-heading">Automation</h2>
          <div data-automation-panel></div>
        </section>
        <section class="panel" aria-labelledby="health-heading">
          <h2 id="health-heading">System health</h2>
          <div data-health><p class="muted small">Loading…</p></div>
        </section>
        <section class="panel" aria-labelledby="since-heading">
          <div class="section-heading"><h2 id="since-heading">Since your last visit</h2><a class="panel-link" href="#/activity">Activity →</a></div>
          <div data-since></div>
        </section>
        <section class="panel" aria-labelledby="ai-heading">
          <h2 id="ai-heading">Latest AI actions</h2>
          <div data-ai-actions></div>
        </section>
      </aside>
    </div></div>`;
  const view = root.firstElementChild;
  const note = root.querySelector("[data-cockpit-feedback]");
  const previousVisit = lastVisit();
  let current = { experiment: null, automation: null };
  let disposed = false;

  async function refresh() {
    try {
      const [experimentPayload, portfolio, attention, activity, settings, market, safety] = await Promise.all([
        paperApi.experiment(),
        paperApi.portfolio(),
        paperApi.attention(),
        paperApi.activity({ limit: 80 }),
        paperApi.portfolioSettings(),
        paperApi.marketStatus().catch(() => ({ stream: null })),
        paperApi.safety().catch(() => null),
      ]);
      paperApi.campaign().then((summary) => {
        if (!disposed) {
          root.querySelector("[data-campaign]").innerHTML = renderCampaign(summary);
          applyMotion(root.querySelector("[data-campaign]"));
        }
      }).catch(() => {});
      paperApi.runtimeHealth().then((health) => {
        if (!disposed) root.querySelector("[data-health]").innerHTML = renderHealth(health);
      }).catch(() => {
        if (!disposed) root.querySelector("[data-health]").innerHTML = renderHealth(null);
      });
      if (disposed) return;
      const experiment = experimentPayload.experiment;
      current = { experiment, automation: settings.settings.automation };
      const paper = portfolio.paper;
      root.querySelector("[data-cockpit-sub]").textContent =
        `PAPER · ${paper.counts.open_positions} open · ${paper.counts.pending_orders} pending orders · ${experiment.config.market_data_mode === "fixture" ? "FIXTURE market data" : "Gate public market data"}`;
      root.querySelector("[data-status-bar]").innerHTML = renderTradingStatusBar({
        experiment, marketStream: market.stream, portfolio, nextCycleAt: nextScanAt(experiment), automation: current.automation,
      });
      root.querySelector("[data-kpis]").innerHTML = renderKpiStrip(portfolio);
      root.querySelector("[data-attention]").innerHTML = renderAttentionQueue(attention);
      root.querySelector("[data-attention-counts]").innerHTML = attentionCounts(attention.counts);
      root.querySelector("[data-positions]").innerHTML = renderUnifiedPositions(
        [...paper.perpetual.positions, ...paper.spot.holdings], { compact: true, emptyMessage: "No open PAPER positions. Use Trade to simulate one, or let the scheduler find setups." },
      );
      root.querySelector("[data-automation-panel]").innerHTML = renderAutomation(experiment, current.automation)
        + (safety ? renderKillSwitchControl(safety.kill_switch) : "");
      const unsafe = (safety?.market_states || []).filter((item) => item.state !== "NORMAL");
      root.querySelector("[data-safety-strip]").innerHTML = safety
        ? renderSafetyStrip({ killSwitch: safety.kill_switch })
          + (unsafe.length ? `<p class="small warn-text">${unsafe.map((item) => `${escapeHtml(item.instrument_id.split(":").slice(1).join(" "))}: ${escapeHtml(item.state.replaceAll("_", " "))}`).join(" · ")}</p>` : "")
          + (safety.reconciliation?.ok === false ? '<p class="paper-feedback paper-feedback--error">Ledger reconciliation failed — automation is restricted until it is resolved.</p>' : "")
        : "";
      const events = activity.events || [];
      const since = previousVisit ? events.filter((event) => event.timestamp > previousVisit) : events;
      root.querySelector("[data-since]").innerHTML = previousVisit
        ? `<p class="muted small">since ${escapeHtml(formatTimestamp(previousVisit))} · ${since.length} events</p>${renderTimeline(since.slice(0, 8), { compact: true })}`
        : renderTimeline(events.slice(0, 8), { compact: true });
      root.querySelector("[data-ai-actions]").innerHTML = renderTimeline(events.filter((event) => event.source === "AI").slice(0, 6), { compact: true });
      if (events[0]) markVisit(events[0].timestamp);
      applyMotion(root);
    } catch (error) {
      if (!disposed) feedback(note, `Local PAPER runtime unavailable: ${error.message}. Start it with python3 -m crypto_eval paper-server.`, "error");
    }
  }

  view.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-kill-switch-form]");
    if (!form) return;
    event.preventDefault();
    const level = form.elements.namedItem("level").value;
    const reason = form.elements.namedItem("reason").value;
    try {
      const result = await withConfirmation((confirm) => paperApi.setKillSwitch(level, { reason, confirm }));
      if (result) feedback(note, `Kill switch ${result.kill_switch.level.replaceAll("_", " ")}.`, "success");
      await refresh();
    } catch (error) {
      feedback(note, error.message, "error");
    }
  });

  view.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    try {
      if (button.dataset.runtime) {
        button.disabled = true;
        if (button.dataset.runtime === "stop") {
          const ok = await confirmAction({
            title: "Stop experiment",
            message: "New cycles stop. Open PAPER positions keep deterministic stop/target monitoring.",
            confirmLabel: "Stop experiment",
          });
          if (!ok) return;
        }
        await paperApi.runtime(button.dataset.runtime);
        feedback(note, `Scheduler ${button.dataset.runtime} requested.`, "success");
        await refresh();
      } else if (button.matches("[data-backup]")) {
        button.disabled = true;
        const { backup } = await paperApi.backup();
        feedback(note, `Backup saved: ${backup.file} (${(backup.bytes / 1048576).toFixed(1)} MB, verified, no credentials).`, "success");
      } else if (button.dataset.ackAttention) {
        await paperApi.acknowledgeAttention(button.dataset.ackAttention);
        await refresh();
      } else if (button.dataset.automation) {
        const key = button.dataset.automation;
        const next = !current.automation?.[key];
        if (key === "emergency_stop" && !next) {
          const ok = await confirmAction({ title: "Clear emergency stop", message: "New PAPER entries become possible again.", confirmLabel: "Clear", tone: "primary" });
          if (!ok) return;
        }
        const result = await withConfirmation((confirm) => paperApi.automation({ [key]: next }, { confirm }));
        if (result) feedback(note, "Automation updated.", "success");
        await refresh();
      }
    } catch (error) {
      feedback(note, error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  refresh();
  const timer = setInterval(refresh, 15000);
  ctx.onPortfolioChange?.(refresh);
  return () => {
    disposed = true;
    clearInterval(timer);
  };
}
