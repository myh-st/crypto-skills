// Trade workspace: Spot/Perpetual exchange-backed selector, live chart with PAPER plan
// overlays, current AI plan, risk-first PAPER ticket, and the selected instrument's position,
// orders, and activity. On narrow screens the workspace becomes Summary / Chart / Position /
// Actions tabs instead of a cramped terminal.
import { escapeHtml } from "../format.js";
import { paperApi } from "../paperApi.js";
import { renderTimeline } from "../components/activityTimeline.js";
import { latestCycleFor, renderAiPlan } from "../components/aiPlanPanel.js";
import { mountLiveChart, portfolioOverlays } from "../components/liveChart.js";
import { loadPicks, mountMarketSelector, rememberInstrument } from "../components/marketSelector.js";
import { handleOrderClick, handleOrderSubmit, renderOrdersPanel } from "../components/ordersPanel.js";
import { mountTradeTicket } from "../components/paperTradeTicket.js";
import { renderUnifiedPositions } from "../components/positionsTable.js";
import { renderTradingStatusBar } from "../components/tradingStatusBar.js";
import { feedback, marketBadge, pnl, price } from "../components/ui.js";
import { renderRestrictions, renderSafetyStrip } from "../components/safetyStrip.js";
import { nextScanAt } from "./cockpit.js";

const INTERVAL_SECONDS = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400 };

async function defaultInstrument(params) {
  if (params?.length) {
    const id = decodeURIComponent(params.join("/"));
    const { instrument } = await paperApi.instrument(id);
    return instrument;
  }
  const recent = loadPicks().recent[0];
  if (recent) {
    try {
      return (await paperApi.instrument(recent.instrument_id)).instrument;
    } catch {
      // A remembered instrument from another exchange source; fall through to BTC.
    }
  }
  const listing = await paperApi.markets({ marketType: "perpetual", q: "BTC", limit: 1 });
  return listing.instruments[0];
}

export function fillMarkers(fills, intervalSeconds) {
  return (fills || []).map((fill) => {
    const seconds = Math.floor(Date.parse(fill.as_of) / 1000);
    const buy = fill.side === "buy";
    return {
      time: seconds - (seconds % intervalSeconds),
      position: buy ? "belowBar" : "aboveBar",
      color: buy ? "#1f7a4d" : "#a3352a",
      shape: buy ? "arrowUp" : "arrowDown",
      text: `${buy ? "B" : "S"} ${Number(fill.quantity).toPrecision(3)}`,
    };
  }).sort((a, b) => a.time - b.time);
}

export function render(root, ctx) {
  root.innerHTML = `<div class="view view--trade">
    <div data-status-bar></div>
    <div data-safety-strip></div>
    <div class="trade-toolbar">
      <div data-selector></div>
      <div class="trade-instrument" data-instrument-summary aria-live="off"></div>
    </div>
    <div class="paper-feedback" data-trade-feedback role="status" aria-live="polite"></div>
    <div class="mobile-tabs" role="tablist" aria-label="Trade sections">
      ${["summary", "chart", "position", "actions"].map((tab, index) => `<button type="button" role="tab" id="tab-${tab}" aria-controls="pane-${tab}" aria-selected="${index === 0}" data-mobile-tab="${tab}">${tab[0].toUpperCase()}${tab.slice(1)}</button>`).join("")}
    </div>
    <div class="trade-grid" data-active-tab="summary">
      <section class="panel trade-chart" id="pane-chart" data-pane="chart" role="tabpanel" aria-labelledby="tab-chart">
        <div class="live-chart" data-live-chart><p class="muted">Select an instrument.</p></div>
      </section>
      <aside class="trade-side">
        <section class="panel" id="pane-summary" data-pane="summary" role="tabpanel" aria-labelledby="tab-summary">
          <div class="section-heading"><h2>Current AI plan</h2></div>
          <div data-ai-plan></div>
        </section>
        <section class="panel" id="pane-actions" data-pane="actions" role="tabpanel" aria-labelledby="tab-actions">
          <div class="section-heading"><h2>PAPER ticket</h2><span class="demo-tag">simulated · server-sized</span></div>
          <div data-ticket></div>
        </section>
      </aside>
      <section class="panel trade-bottom" id="pane-position" data-pane="position" role="tabpanel" aria-labelledby="tab-position">
        <div class="segmented" role="tablist" aria-label="Instrument details">
          <button type="button" role="tab" data-bottom-tab="position" aria-selected="true">Position</button>
          <button type="button" role="tab" data-bottom-tab="orders" aria-selected="false">Orders</button>
          <button type="button" role="tab" data-bottom-tab="activity" aria-selected="false">Activity</button>
        </div>
        <div data-bottom></div>
      </section>
    </div></div>`;
  const view = root.firstElementChild;
  const note = view.querySelector("[data-trade-feedback]");
  const state = {
    instrument: null, positions: [], orders: [], activity: [], cycles: [], realPositions: [], fills: [],
    bottom: "position", chart: null, ticket: null, lastPrice: null, disposed: false,
  };

  function renderBottom() {
    const host = view.querySelector("[data-bottom]");
    const id = state.instrument?.instrument_id;
    if (state.bottom === "orders") host.innerHTML = renderOrdersPanel(state.orders, { instrumentId: id });
    else if (state.bottom === "activity") host.innerHTML = renderTimeline(state.activity.slice(0, 40));
    else host.innerHTML = renderUnifiedPositions(state.positions.filter((p) => p.instrument_id === id), { emptyMessage: "No open PAPER position on this instrument." });
  }

  function renderSummary() {
    const instrument = state.instrument;
    if (!instrument) return;
    const position = state.positions.find((p) => p.instrument_id === instrument.instrument_id && p.status === "open");
    view.querySelector("[data-instrument-summary]").innerHTML = `
      ${marketBadge(instrument.market_type)} <strong>${escapeHtml(instrument.display_symbol)}</strong>
      <span class="muted small">${escapeHtml(instrument.status)} · tick ${escapeHtml(price(instrument.price_tick))} · min ${escapeHtml(price(instrument.min_quantity))}${instrument.max_leverage ? ` · max ${escapeHtml(instrument.max_leverage)}x` : ""}</span>
      ${position ? `<span class="trade-position-chip">${pnl(position.unrealized_pnl_usdt)} open</span>` : ""}`;
    const cycle = instrument.market_type === "perpetual" ? latestCycleFor(state.cycles, instrument.symbol) : null;
    view.querySelector("[data-ai-plan]").innerHTML = renderAiPlan({ cycle, position, instrument });
  }

  async function refreshData() {
    if (!state.instrument) return;
    try {
      const [positions, orders, activity, dashboardLite] = await Promise.all([
        paperApi.positions("open"),
        paperApi.orders(),
        paperApi.activity({ symbol: state.instrument.symbol, limit: 60 }),
        paperApi.runtimeSummary(state.instrument.market_type === "perpetual" ? state.instrument.symbol : null).catch(() => null),
      ]);
      if (state.disposed) return;
      state.positions = positions.positions || [];
      state.orders = orders.orders || [];
      state.activity = (activity.events || []).filter((event) => !event.market_type || event.market_type === state.instrument.market_type);
      if (dashboardLite) {
        state.cycles = dashboardLite.cycles || [];
        state.realPositions = (dashboardLite.exchange_accounts || []).flatMap((account) => account.last_sync?.positions || []);
        view.querySelector("[data-status-bar]").innerHTML = renderTradingStatusBar({
          experiment: dashboardLite.experiment, marketStream: dashboardLite.market_stream, portfolio: null,
          nextCycleAt: nextScanAt(dashboardLite.experiment),
        });
      }
      const [assessment, safety] = await Promise.all([
        paperApi.assessInstrument(state.instrument.instrument_id).catch(() => null),
        paperApi.safety().catch(() => null),
      ]);
      if (assessment || safety) {
        view.querySelector("[data-safety-strip]").innerHTML = renderSafetyStrip({ killSwitch: safety?.kill_switch, assessment: assessment?.assessment })
          + (assessment?.assessment && assessment.assessment.state !== "NORMAL" ? `<details class="small"><summary>What is allowed now</summary>${renderRestrictions(assessment.assessment.restrictions)}</details>` : "");
      }
      const open = state.positions.find((p) => p.instrument_id === state.instrument.instrument_id);
      state.fills = open ? ((await paperApi.position(open.position_ref)).position.fills || []) : [];
      renderSummary();
      renderBottom();
      state.chart?.refreshOverlays();
    } catch (error) {
      feedback(note, error.message, "error");
    }
  }

  async function selectInstrument(item) {
    try {
      const { instrument } = await paperApi.instrument(item.instrument_id);
      state.instrument = instrument;
      state.lastPrice = instrument.last_price;
      rememberInstrument(instrument);
      if (location.hash !== `#/trade/${encodeURIComponent(instrument.instrument_id)}`) {
        history.replaceState(null, "", `#/trade/${encodeURIComponent(instrument.instrument_id)}`);
      }
      state.ticket = mountTradeTicket(view.querySelector("[data-ticket]"), {
        api: paperApi,
        instrument,
        lastPrice: instrument.last_price,
        onResult: () => { refreshData(); ctx.notifyPortfolioChange?.(); },
      });
      const chartHost = view.querySelector("[data-live-chart]");
      state.chart = await mountLiveChart(chartHost, {
        api: paperApi,
        instrument,
        symbol: instrument.symbol,
        getOverlays: () => portfolioOverlays(instrument.instrument_id, state.positions, state.orders, state.realPositions),
        getMarkers: () => fillMarkers(state.fills, INTERVAL_SECONDS[chartHost.querySelector('[aria-pressed="true"][data-live-interval]')?.dataset.liveInterval || "1m"]),
        onQuote: (quote) => {
          const value = quote?.last_price ?? quote?.mid_price;
          if (value) state.ticket?.setPrice(value);
        },
      });
      await refreshData();
    } catch (error) {
      feedback(note, `Instrument unavailable: ${error.message}`, "error");
    }
  }

  view.addEventListener("click", async (event) => {
    const tab = event.target.closest("[data-mobile-tab]");
    if (tab) {
      view.querySelector(".trade-grid").dataset.activeTab = tab.dataset.mobileTab;
      view.querySelectorAll("[data-mobile-tab]").forEach((item) => item.setAttribute("aria-selected", String(item === tab)));
      return;
    }
    const bottom = event.target.closest("[data-bottom-tab]");
    if (bottom) {
      state.bottom = bottom.dataset.bottomTab;
      view.querySelectorAll("[data-bottom-tab]").forEach((item) => item.setAttribute("aria-selected", String(item === bottom)));
      renderBottom();
      return;
    }
    await handleOrderClick(event, { api: paperApi, root: view, note, onDone: refreshData });
  });
  view.addEventListener("submit", (event) => handleOrderSubmit(event, { api: paperApi, note, onDone: refreshData }));

  (async () => {
    try {
      const instrument = await defaultInstrument(ctx.params);
      mountMarketSelector(view.querySelector("[data-selector]"), {
        api: paperApi, marketType: instrument?.market_type || "perpetual", selected: instrument, onSelect: selectInstrument,
      });
      if (instrument) await selectInstrument(instrument);
    } catch (error) {
      feedback(note, `Market catalog unavailable: ${error.message}`, "error");
    }
  })();
  const timer = setInterval(() => {
    if (!view.querySelector(".amend-row:not([hidden])")) refreshData();
  }, 10000);
  ctx.onPortfolioChange?.(refreshData);
  return () => {
    state.disposed = true;
    clearInterval(timer);
    state.chart?.destroy();
  };
}
