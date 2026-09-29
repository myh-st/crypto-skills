import test from "node:test";
import assert from "node:assert/strict";

import { createPaperApi } from "../modules/paperApi.js";
import { NAV_ITEMS, PRIMARY_NAV, RESEARCH_NAV, renderNav } from "../modules/components/nav.js";
import { modeBadge, pct, pnl, signedText } from "../modules/components/ui.js";
import { renderKpiStrip } from "../modules/components/portfolioKpiStrip.js";
import { renderAttentionQueue } from "../modules/components/attentionQueue.js";
import { renderUnifiedPositions } from "../modules/components/positionsTable.js";
import { renderOrdersPanel } from "../modules/components/ordersPanel.js";
import { renderTimeline } from "../modules/components/activityTimeline.js";
import { renderReplanProposal } from "../modules/components/replanPanel.js";
import { previewReady, renderPreview, submitLabel } from "../modules/components/paperTradeTicket.js";
import { latestCycleFor, renderAiPlan } from "../modules/components/aiPlanPanel.js";
import { portfolioOverlays, quoteToStripState, renderLiveChartShell, renderQuoteStrip } from "../modules/components/liveChart.js";
import { renderInstrumentRows } from "../modules/components/marketSelector.js";
import { renderTradingStatusBar } from "../modules/components/tradingStatusBar.js";
import { renderLearning, renderTournament } from "../modules/components/strategyTournamentSummary.js";
import { renderPositionDetail } from "../modules/components/positionDetailDrawer.js";
import { withConfirmation } from "../modules/components/confirmDialog.js";
import { fillMarkers } from "../modules/views/trade.js";
import { nextScanAt, renderAutomation } from "../modules/views/cockpit.js";
import { renderBrainReview, renderEconomics } from "../modules/views/portfolio.js";
import { patchFromForm, renderPortfolioSettings } from "../modules/views/portfolioSettings.js";

// ------------------------------------------------------------------ fixtures
const perp = {
  schema_version: "unified-position-view.v1", position_ref: "perp:manual:abc:primary", market_type: "perpetual",
  instrument_id: "gate:perpetual:BTC_USDT", symbol: "BTCUSDT", display_symbol: "BTC/USDT Perp", base: "BTC", side: "long",
  status: "open", source: "USER", source_arm: "manual", quantity: 0.001, opened_quantity: 0.001, notional_usdt: 60, entry_price: 60000,
  mark_price: 60500, live_price: 60600, unrealized_pnl_usdt: 0.5, realized_pnl_usdt: 0, leverage: 3, margin_usdt: 20,
  liquidation_price: 40500, liquidation_buffer_pct: 0.33, stop_price: 59000,
  targets: [{ price: 61000, fraction: 0.5, quantity: 0.0005, final: false, hit: true }, { price: 62000, fraction: 0.5, quantity: null, final: true, hit: false }],
  target_price: 62000, funding_usdt: 0.01, fees_usdt: 0.05, slippage_usdt: 0.01, open_risk_usdt: 1.5, initial_risk_usdt: 1,
  r_multiple: 0.5, management_mode: "AUTO_PAPER", owner_source: "USER", thesis_status: "intact", plan_version: 2,
  last_ai_review_at: null, next_ai_review_at: null, last_user_override_at: null, pending_proposal: null,
  opened_at: "2026-09-28T12:00:00.000Z", closed_at: null, exit_reason: null, journal: [], fills: [], proposals: [], review: null, evidence: null,
};
const spot = {
  ...perp, position_ref: "spot:sh-1", holding_id: "sh-1", market_type: "spot", instrument_id: "gate:spot:ETH_USDT", symbol: "ETHUSDT",
  display_symbol: "ETH/USDT", base: "ETH", quote: "USDT", leverage: null, liquidation_price: null, liquidation_buffer_pct: null,
  funding_usdt: null, avg_cost: 2600, current_value_usdt: 26, allocation_pct: 0.26, portfolio_weight_pct: 0.13, available_quantity: 0.01,
  management_mode: "RECOMMEND_ONLY", stop_price: null, targets: [],
};
const proposal = {
  proposal_id: "rp-1", position_ref: perp.position_ref, market_type: "perpetual", symbol: "BTCUSDT", status: "proposed",
  headline: "Tighten stop", source: "luna", created_at: "2026-09-28T12:05:00.000Z", risk_increased: false,
  proposal: { action: "adjust", stop_price: 59500, target_prices: [62000], reduce_fraction: null, thesis_status: "intact", reason_codes: ["TIGHTEN_RISK"] },
  current: { stop_price: 59000, targets: [62000], remaining_pct: 1, risk_usdt: 1.5, exposure_pct: 0.3, management_mode: "AUTO_PAPER", locked_pnl_at_stop_usdt: -1 },
  proposed: { stop_price: 59500, targets: [62000], remaining_pct: 1, risk_usdt: 1.0, exposure_pct: 0.3, management_mode: "AUTO_PAPER", locked_pnl_at_stop_usdt: -0.5 },
  reason_codes: ["TIGHTEN_RISK", "MOMENTUM_WEAKENED"], evidence: { data_cutoff: "2026-09-28T12:00:00.000Z", price: 60500 }, ai: { jev: "completed", luna: "completed" },
};

function response(payload = {}, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload };
}

// ------------------------------------------------------------------ API client
test("portfolio API methods use same-origin PAPER endpoints and encode refs", async () => {
  const calls = [];
  const api = createPaperApi(async (url, options) => { calls.push({ url, options }); return response({ ok: true }); });
  await api.markets({ marketType: "spot", q: "eth", tradable: true, limit: 5 });
  await api.instrument("gate:spot:ETH_USDT");
  await api.quote("gate:spot:ETH_USDT");
  await api.portfolio();
  await api.position("perp:manual:x:primary");
  await api.previewOrder({ instrument_id: "gate:spot:ETH_USDT", action: "buy", quote_amount: 10 });
  await api.createOrder({ instrument_id: "gate:spot:ETH_USDT", action: "buy", quote_amount: 10 });
  await api.amendOrder("spot:so-1", { limit_price: 1 });
  await api.cancelOrder("spot:so-1");
  await api.updateProtection("perp:x", { stop_price: 1 });
  await api.closePosition("perp:x", { confirm: true });
  await api.setManagementMode("perp:x", "PAUSED");
  await api.requestReplan("perp:x", { intent: "tighten_risk" });
  await api.applyReplan("rp-1", { confirm: true });
  await api.rejectReplan("rp-1");
  await api.attention({ includeResolved: true });
  await api.activity({ source: "AI", symbol: "", limit: 5 });
  await api.automation({ emergency_stop: true });
  assert.deepEqual(calls.map((c) => [c.options.method || "GET", c.url]), [
    ["GET", "/api/markets?market_type=spot&q=eth&limit=5&sort=volume&tradable=true"],
    ["GET", "/api/markets/gate%3Aspot%3AETH_USDT"],
    ["GET", "/api/markets/gate%3Aspot%3AETH_USDT/quote"],
    ["GET", "/api/portfolio"],
    ["GET", "/api/positions/perp%3Amanual%3Ax%3Aprimary"],
    ["POST", "/api/orders/preview"],
    ["POST", "/api/orders"],
    ["PATCH", "/api/orders/spot%3Aso-1"],
    ["DELETE", "/api/orders/spot%3Aso-1"],
    ["PATCH", "/api/positions/perp%3Ax/protection"],
    ["POST", "/api/positions/perp%3Ax/close"],
    ["POST", "/api/positions/perp%3Ax/management-mode"],
    ["POST", "/api/positions/perp%3Ax/replan"],
    ["POST", "/api/replans/rp-1/apply"],
    ["POST", "/api/replans/rp-1/reject"],
    ["GET", "/api/attention?include_resolved=true"],
    ["GET", "/api/activity?source=AI&limit=5"],
    ["POST", "/api/automation"],
  ]);
  for (const { options } of calls) {
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
  }
  assert.deepEqual(JSON.parse(calls[11].options.body), { mode: "PAUSED" });
});

test("409 confirmation responses surface a typed confirmation, not a generic failure", async () => {
  const api = createPaperApi(async () => response({
    error: "CONFIRM_CLOSE: close the full PAPER BTCUSDT position?", code: "CONFIRM_CLOSE",
    confirmation_required: true, details: { instrument: "BTCUSDT" },
  }, 409));
  await assert.rejects(api.closePosition("perp:x"), (error) => {
    assert.equal(error.status, 409);
    assert.deepEqual(error.confirmation, { code: "CONFIRM_CLOSE", details: { instrument: "BTCUSDT" } });
    return true;
  });
});

test("withConfirmation passes through success and rethrows non-confirmation errors", async () => {
  assert.equal(await withConfirmation(async (confirm) => (confirm ? "retried" : "ok")), "ok");
  await assert.rejects(withConfirmation(async () => { throw new Error("boom"); }), /boom/);
});

// ------------------------------------------------------------------ navigation
test("navigation is portfolio-first with research tools grouped", () => {
  assert.deepEqual(PRIMARY_NAV.map((item) => item.route), ["overview", "portfolio", "trade", "activity", "research", "evaluations", "settings"]);
  assert.ok(RESEARCH_NAV.some((item) => item.route === "paper-trading"));
  assert.equal(new Set(NAV_ITEMS.map((item) => item.route)).size, NAV_ITEMS.length);
  const html = renderNav("runs");
  assert.match(html, /data-route="research"[^]*?nav-link--active|nav-link--active[^]*?data-route="research"/);
  assert.match(html, /<details class="sidebar-group" open>/);
  assert.match(renderNav("trade"), /aria-current="page"\s+data-route="trade"/);
});

// ------------------------------------------------------------------ primitives
test("PnL is never conveyed by color alone and missing values are explicit", () => {
  assert.match(pnl(1.5), /▲.*\+1\.50 USDT/);
  assert.match(pnl(-2), /▼.*−2\.00 USDT/);
  assert.match(pnl(null), /—/);
  assert.equal(signedText(0), "0.00 USDT");
  assert.equal(pct(0.1234, { signed: true }), "+12.34%");
  assert.match(modeBadge("MANUAL_OVERRIDE"), /Manual override/);
  assert.match(modeBadge("bogus"), /Unassigned/);
});

test("KPI strip explains unavailable economic PnL instead of inventing it", () => {
  const html = renderKpiStrip({ paper: {
    total_equity_usdt: 200, starting_capital_usdt: 200, trading_pnl_usdt: -1, trading_pnl_pct: -0.005,
    economic_pnl_usdt: null, economic_unavailable_reason: "set a USD→USDT cost FX policy in Settings",
    drawdown: { current: 0.01, max: 0.02 }, open_risk_usdt: 1, open_risk_pct: 0.005, ai_budget: { spent_today_usd: 0.1, remaining_today_usd: 4.9 },
  } });
  for (const label of ["Equity", "Trading PnL", "Economic PnL", "Drawdown", "Open risk", "AI spend today"]) assert.match(html, new RegExp(label));
  assert.match(html, /Unavailable/);
  assert.match(html, /FX policy/);
  assert.equal(renderKpiStrip(null), "");
});

// ------------------------------------------------------------------ cockpit pieces
test("attention queue renders actionable, escaped items with review and acknowledge", () => {
  const html = renderAttentionQueue({ items: [
    { attention_id: "att-1", severity: "ACTION", status: "open", title: "BTC <near> stop", summary: "s", occurrences: 3,
      first_seen_at: "2026-09-28T12:00:00.000Z", action: { route: "position", position_ref: "perp:x", replan_intent: "tighten_risk" } },
    { attention_id: "att-2", severity: "CRITICAL", status: "acknowledged", title: "Feed stale", summary: "", occurrences: 1,
      first_seen_at: "2026-09-28T12:00:00.000Z", action: { route: "trade" } },
    { attention_id: "att-3", severity: "INFO", status: "resolved", title: "hidden", summary: "", occurrences: 1, first_seen_at: "x", action: {} },
  ] });
  assert.match(html, /BTC &lt;near&gt; stop/);
  assert.match(html, /data-open-position="perp:x" data-replan-intent="tighten_risk"/);
  assert.match(html, /data-ack-attention="att-1"/);
  assert.match(html, /data-route-to="trade"/);
  assert.match(html, /seen 3×/);
  assert.doesNotMatch(html, /hidden/);
  assert.match(renderAttentionQueue({ items: [] }), /Nothing needs your attention/);
});

test("unified positions table distinguishes Spot and Perp and offers Manage", () => {
  const html = renderUnifiedPositions([perp, spot, { ...perp, position_ref: "perp:closed", status: "closed" }]);
  assert.match(html, /PERP/);
  assert.match(html, /SPOT/);
  assert.match(html, /LONG 3x/);
  assert.match(html, /data-open-position="spot:sh-1"/);
  assert.doesNotMatch(html, /perp:closed/);
  assert.match(html, /AI managed/);
  assert.match(renderUnifiedPositions([]), /No open PAPER positions/);
});

test("orders panel only offers Cancel for pending orders and never Close", () => {
  const base = { schema_version: "paper-order-view.v1", market_type: "spot", instrument_id: "gate:spot:ETH_USDT", display_symbol: "ETH/USDT",
    side: "buy", order_type: "limit", requested_quantity: 1, filled_quantity: 0, filled_pct: 0, limit_price: 10, source: "USER",
    created_at: "2026-09-28T12:00:00.000Z", position_ref: null, reduce_only: false };
  const orders = [
    { ...base, order_ref: "spot:p", status: "pending", cancellable: true, amendable: true },
    { ...base, order_ref: "spot:f", status: "filled", cancellable: false, amendable: false, position_ref: "spot:sh-1" },
    { ...base, order_ref: "perp:x", instrument_id: "gate:perpetual:BTC_USDT", market_type: "perpetual", status: "filled", cancellable: false, amendable: false },
  ];
  const html = renderOrdersPanel(orders);
  assert.equal((html.match(/data-cancel-order=/g) || []).length, 1);
  assert.match(html, /data-cancel-order="spot:p"/);
  assert.match(html, /data-amend-order="spot:p"/);
  assert.doesNotMatch(html, />Close</);
  assert.equal((renderOrdersPanel(orders, { filter: "open" }).match(/<tr>/g) || []).length, 2);
  assert.doesNotMatch(renderOrdersPanel(orders, { instrumentId: "gate:spot:ETH_USDT" }), /BTC/);
});

test("activity timeline shows source, category, and a drill-down per position", () => {
  const html = renderTimeline([
    { event_id: "a", timestamp: "2026-09-28T12:00:00.000Z", symbol: "BTCUSDT", title: "LONG plan proposed", summary: "entry 1",
      source: "AI", category: "DECISION", severity: "INFO", position_ref: "perp:x" },
    { event_id: "b", timestamp: "2026-09-28T12:01:00.000Z", symbol: null, title: "Feed stale", summary: "", source: "SYSTEM",
      category: "ALERT", severity: "CRITICAL", position_ref: null },
  ]);
  assert.match(html, /src-badge--ai/);
  assert.match(html, /src-badge--system/);
  assert.match(html, /data-open-position="perp:x"/);
  assert.match(html, /CRITICAL/);
  assert.doesNotMatch(html, /aria-live/);
});

// ------------------------------------------------------------------ re-plan
test("re-plan shows a before/after diff with Apply, Edit, and Reject only while pending", () => {
  const html = renderReplanProposal(proposal);
  assert.match(html, /Tighten stop/);
  assert.match(html, /59,000[^]*→[^]*59,500/);
  assert.match(html, /Risk at stop/);
  assert.match(html, /Portfolio exposure/);
  assert.match(html, /Tighten risk/);
  for (const action of ["apply", "edit", "reject"]) assert.match(html, new RegExp(`data-replan-${action}="rp-1"`));
  assert.doesNotMatch(renderReplanProposal({ ...proposal, status: "applied" }), /data-replan-apply/);
  assert.doesNotMatch(renderReplanProposal(proposal, { editable: false }), /data-replan-apply/);
  assert.match(renderReplanProposal({ ...proposal, status: "blocked", code: "STALE_DATA", reason: "no fresh quote" }), /blocked · STALE_DATA/);
  assert.doesNotMatch(html, /chain|thought/i);
});

// ------------------------------------------------------------------ ticket
test("ticket labels say Simulate, readiness is server-preview driven, previews differ for Spot", () => {
  const perpInstrument = { market_type: "perpetual" };
  const spotInstrument = { market_type: "spot" };
  assert.equal(submitLabel(perpInstrument, "long"), "Simulate Long");
  assert.equal(submitLabel(perpInstrument, "short"), "Simulate Short");
  assert.equal(submitLabel(spotInstrument, "buy"), "Simulate Buy");
  assert.equal(submitLabel(spotInstrument, "sell"), "Simulate Sell");
  assert.equal(previewReady({ action: "long", order_type: "market", stop_price: 1, targets: [] }, perpInstrument), false);
  assert.equal(previewReady({ action: "long", order_type: "market", stop_price: 1, targets: [2] }, perpInstrument), true);
  assert.equal(previewReady({ action: "buy", order_type: "limit", quote_amount: 5 }, spotInstrument), false);
  assert.equal(previewReady({ action: "buy", order_type: "market", quote_amount: 5 }, spotInstrument), true);
  const perpPreview = renderPreview({ market_type: "perpetual", allowed: true, code: "APPROVED", reason: "", quantity: 0.001,
    notional_usdt: 60, margin_usdt: 20, leverage: 3, max_loss_usdt: 1, liquidation_price: 40000, liquidation_buffer_pct: 0.33,
    reward_risk: 2, warnings: ["WIDE_SPREAD"], quote: { source: "gate_ws", fresh: true }, portfolio_brain: { action: "RESIZE", reason_codes: ["CORRELATED_ALT_LONG_RESIZE"], advisories: [] } });
  assert.match(perpPreview, /Risk approved/);
  assert.match(perpPreview, /Liquidation/);
  assert.match(perpPreview, /Portfolio Brain: <strong>RESIZE/);
  assert.match(perpPreview, /WIDE_SPREAD/);
  const spotPreview = renderPreview({ market_type: "spot", allowed: false, code: "CASH_RESERVE", reason: "breach", quantity: 1, warnings: [], quote: { fresh: false } });
  assert.match(spotPreview, /✕ CASH_RESERVE/);
  assert.doesNotMatch(spotPreview, /Liquidation|Margin|Leverage/);
  assert.match(spotPreview, /STALE/);
});

// ------------------------------------------------------------------ chart / plan
test("chart overlays show open plan levels, pending limits, liquidation for perps only", () => {
  const orders = [{ instrument_id: "gate:perpetual:BTC_USDT", status: "pending", limit_price: 59800, side: "long" },
    { instrument_id: "gate:perpetual:BTC_USDT", status: "filled", limit_price: 1, side: "long" }];
  const lines = portfolioOverlays("gate:perpetual:BTC_USDT", [perp, spot], orders, [{ symbol: "BTCUSDT", side: "long", entry_price: 58000 }]);
  const titles = lines.map((line) => line.title);
  assert.ok(titles.includes("PAPER stop"));
  assert.ok(titles.includes("PAPER TP1"));
  assert.ok(!lines.some((line) => line.price === 61000), "hit targets are not drawn");
  assert.ok(titles.includes("PAPER liq."));
  assert.ok(titles.includes("PAPER LONG limit"));
  assert.ok(lines.some((line) => line.source === "REAL_ACCOUNT"));
  const spotLines = portfolioOverlays("gate:spot:ETH_USDT", [perp, spot], [], []);
  assert.ok(!spotLines.some((line) => line.kind === "liquidation"));
  const strip = renderQuoteStrip(quoteToStripState({ last_price: 10, best_bid: 9, best_ask: 11, spread_bps: 2, change_24h: 0.01, volume_24h_quote: 5 }), { spot: true });
  assert.doesNotMatch(strip, /Mark|Index|Funding/);
  assert.match(strip, /1\.00%/);
  assert.doesNotMatch(renderQuoteStrip(null), /<strong>0<\/strong>/);
  assert.doesNotMatch(renderLiveChartShell([], null, "1m", { showSymbols: false, label: "ETH/USDT" }).match(/data-live-quotes[^>]*>/)[0], /aria-live/);
});

test("AI plan panel shows the latest decision and the position's plan with re-plan entry points", () => {
  const cycles = [
    { symbol: "ETHUSDT", status: "complete" },
    { symbol: "BTCUSDT", status: "complete", primary_arm: "hybrid", data_cutoff: "2026-09-28T12:00:00.000Z",
      primary_decision: { decision: "ENTER_LONG", intent: { side: "long", entry_price: 1, stop_price: 0.9, target_price: 1.2 }, ai_path: "jev_fast_path" },
      risk: { code: "APPROVED" }, portfolio_brain: { action: "ALLOW", reason_codes: ["WITHIN_PORTFOLIO_LIMITS"] }, quant_gate: { eligible: true, strength: 0.7 } },
  ];
  const cycle = latestCycleFor(cycles, "BTCUSDT");
  const html = renderAiPlan({ cycle, position: perp, instrument: { market_type: "perpetual" } });
  assert.match(html, /LONG/);
  assert.match(html, /Portfolio Brain ALLOW/);
  assert.match(html, /data-replan-intent="tighten_risk"/);
  assert.match(renderAiPlan({ instrument: { market_type: "spot" } }), /Portfolio policy/);
});

test("fill markers bucket to the chart interval and mark side without color alone", () => {
  const markers = fillMarkers([
    { as_of: "2026-09-28T12:07:30.000Z", side: "sell", quantity: 0.5 },
    { as_of: "2026-09-28T12:01:10.000Z", side: "buy", quantity: 1 },
  ], 300);
  assert.equal(markers[0].time % 300, 0);
  assert.equal(markers[0].shape, "arrowUp");
  assert.match(markers[1].text, /^S /);
});

// ------------------------------------------------------------------ drawer
test("position drawer: perp shows leverage/liquidation and full actions; spot does not show futures fields", () => {
  const perpHtml = renderPositionDetail(perp);
  for (const text of ["Liquidation", "Margin", "Funding", "Reduce 25%", "Reduce 50%", "Reduce 75%", "Close position", "Save protection",
    "Pause AI", "Manual override", "Tighten risk", "Reassess from scratch", "Evidence", "Journal"]) assert.match(perpHtml, new RegExp(text));
  assert.doesNotMatch(perpHtml, /Return control to AI/, "already AI managed");
  assert.match(perpHtml, /role|drawer-title/);
  const spotHtml = renderPositionDetail({ ...spot, management_mode: "MANUAL_OVERRIDE" });
  assert.match(spotHtml, /Close holding/);
  assert.match(spotHtml, /Average cost/);
  assert.doesNotMatch(spotHtml, /Liquidation|Margin|Funding/);
  assert.match(spotHtml, /Return control to AI/);
  const closed = renderPositionDetail({ ...perp, status: "closed", review: { outcome: "LOSS", lesson: "LOSS −1R", tags: ["STOP_TOO_TIGHT"], hypothesis: "wider stop" } });
  assert.doesNotMatch(closed, /Save protection|data-close-position/);
  assert.match(closed, /STOP_TOO_TIGHT/);
  assert.match(closed, /wider stop/);
});

// ------------------------------------------------------------------ overview / portfolio / settings
test("automation controls reflect scheduler state and policy toggles", () => {
  assert.match(renderAutomation({ status: "stopped" }, {}), /data-runtime="start"/);
  const running = renderAutomation({ status: "running" }, { emergency_stop: true });
  assert.match(running, /data-runtime="pause"/);
  assert.match(running, /Emergency stop ON/);
  assert.match(running, /monitoring/);
  const next = new Date(nextScanAt({ config: { schedule_delay_seconds: 60 } }, new Date("2026-09-28T12:07:00Z")));
  assert.equal(next.toISOString(), "2026-09-28T12:16:00.000Z");
  const bar = renderTradingStatusBar({ experiment: { experiment_id: "EXP-001", status: "running", config: { market_data_mode: "gate_usdt" } },
    marketStream: { state: "LIVE" }, portfolio: null, automation: { new_entries_paused: true } });
  assert.match(bar, /BLOCKED BY DESIGN/);
  assert.match(bar, /New entries[^]*PAUSED/);
  assert.doesNotMatch(bar, /AI budget left/);
});

test("portfolio economics and brain review are explicit about unavailability and non-mutation", () => {
  const economics = renderEconomics({ trading_pnl_usdt: 1, realized_pnl_usdt: 1, unrealized_pnl_usdt: 0, fees_usdt: 0.1, ai_cost_usd: 0.2,
    ai_cost_complete: true, ai_cost_usdt: null, economic_pnl_usdt: null, economic_unavailable_reason: "set FX", drawdown: { current: 0, max: 0 } });
  assert.match(economics, /needs FX policy/);
  const review = renderBrainReview({ as_of: "2026-09-28T12:00:00.000Z", exposure: { gross_exposure_x: 1, long_risk_pct: 0.02, short_risk_pct: 0 },
    actions: [{ action: "PROTECT_PROFIT", symbol: "SOLUSDT", position_ref: "perp:x", reason_codes: ["UNREALIZED_ABOVE_THRESHOLD"], suggested_intent: "protect_profit" }] });
  assert.match(review, /PROTECT PROFIT/);
  assert.match(review, /data-replan-intent="protect_profit"/);
  assert.match(review, /never changes an account/);
});

test("portfolio policy form round-trips percentages, selects, and flags", () => {
  const settings = {
    spot_max_allocation_pct: 0.35, spot_max_deployed_pct: 0.9, spot_min_cash_reserve_pct: 0.1, perp_manual_max_risk_pct: 0.02,
    spot_slippage_bps: 5, default_ai_management_mode: "AUTO_PAPER", default_user_management_mode: "RECOMMEND_ONLY",
    ai_spot: { enabled: false, trigger: "prefer_spot", allocation_pct: 0.1 },
    automation: { new_entries_paused: false, ai_management_paused: false, emergency_stop: false },
    brain: { enabled: true, max_asset_risk_pct: 0.03, max_correlated_risk_pct: 0.04, max_direction_risk_pct: 0.05, max_gross_exposure_x: 6, protect_profit_r: 1.5 },
    review: { enabled: true, use_luna: true, management_interval_minutes: 60, min_minutes_between_reviews: 15 },
  };
  const html = renderPortfolioSettings(settings);
  assert.match(html, /name="ai_spot.enabled"/);
  assert.match(html, /value="35.00"/);
  const form = { elements: [
    { name: "spot_max_allocation_pct", type: "number", value: "20", dataset: { percent: "" } },
    { name: "brain.max_gross_exposure_x", type: "number", value: "3", dataset: {} },
    { name: "ai_spot.enabled", type: "checkbox", checked: true, dataset: {} },
    { name: "ai_spot.trigger", tagName: "SELECT", type: "select-one", value: "all_long", dataset: {} },
  ] };
  assert.deepEqual(patchFromForm(form), {
    spot_max_allocation_pct: 0.2, brain: { max_gross_exposure_x: 3 }, ai_spot: { enabled: true, trigger: "all_long" },
  });
});

test("tournament never implies promotion on headline PnL and shows sample sizes and AI cost", () => {
  const html = renderTournament({
    aligned_cycles: 10, eligible_cases: 4, fx: { available: false }, ai_filter: { AI_FILTER_HELPED: 1, AI_FILTER_HURT: 0, sample: 1 },
    promotion_rule: "No arm is promoted on headline PnL.",
    arms: [
      { arm: "quant", closed_trades: 2, net_trading_pnl_usdt: -1, ai_calls: 0, ai_cost_usd: 0, promotion_status: "BASELINE", incremental_vs_quant: null },
      { arm: "hybrid_brain", closed_trades: 2, net_trading_pnl_usdt: 3, ai_calls: 4, ai_cost_usd: 0.2, economic_pnl_usdt: null,
        promotion_status: "INSUFFICIENT_SAMPLE", incremental_vs_quant: { trading_pnl_usdt: 4, ai_cost_usd: 0.2 } },
    ],
  });
  assert.match(html, /Hybrid \+ Portfolio Brain/);
  assert.match(html, /insufficient sample/);
  assert.match(html, /\$0\.2000/);
  assert.match(html, /needs an FX policy/);
  assert.match(html, /headline PnL/);
  assert.match(renderLearning([], [], {}), /never rewritten automatically/);
});

test("market selector rows expose tradable state and favorites accessibly", () => {
  const html = renderInstrumentRows([
    { instrument_id: "gate:spot:ETH_USDT", display_symbol: "ETH/USDT", market_type: "spot", base: "ETH", quote: "USDT", last_price: 2600,
      change_24h: -0.01, volume_24h_quote: 3e8, spread_bps: 0.1, tradable: true, liquidity: "high", status: "tradable" },
    { instrument_id: "gate:spot:OLD_USDT", display_symbol: "OLD/USDT", market_type: "spot", base: "OLD", quote: "USDT", last_price: null,
      change_24h: null, volume_24h_quote: null, spread_bps: null, tradable: false, liquidity: "unknown", status: "untradable" },
  ], [{ instrument_id: "gate:spot:ETH_USDT" }]);
  assert.match(html, /role="option"/);
  assert.match(html, /aria-pressed="true"/);
  assert.match(html, /▼/);
  assert.match(html, /aria-disabled="true"[^]*untradable/);
  assert.match(html, /300\.0M/);
});
