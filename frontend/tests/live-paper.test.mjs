import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { createPaperApi } from "../modules/paperApi.js";
import {
  CHART_LIBRARY_URL,
  mergeBar,
  overlayLines,
  renderLiveChartShell,
  renderQuoteStrip,
  streamStatusLabel,
  toChartBar,
} from "../modules/components/liveChart.js";
import {
  renderAccountMirror,
  renderBudgetStatus,
  renderExchangeAccounts,
  renderPriceBook,
  renderProviderCredentialCards,
} from "../modules/views/runtimeSettings.js";
import { renderEconomics, renderHealthStrip, renderLedgerRows } from "../modules/views/paperTrading.js";

const SENTINEL = "SENTINEL-FRONTEND-SECRET-NOT-REAL";

function candle(openTime, close, closed = true) {
  return { open_time: openTime, close_time: openTime, open: close - 1, high: close + 1, low: close - 2, close, volume: 3, closed };
}

test("chart bars keep exchange candles, mark the current window, and never synthesize", () => {
  const closed = toChartBar(candle("2026-09-28T12:00:00.000Z", 100));
  const current = toChartBar(candle("2026-09-28T12:01:00.000Z", 101, false));
  assert.equal(closed.time, Date.parse("2026-09-28T12:00:00.000Z") / 1000);
  assert.equal(closed.color, undefined);
  assert.match(current.color, /rgba/);
  assert.equal(toChartBar({ open_time: "bad", open: 1, high: 1, low: 1, close: 1 }), null);

  let bars = [closed];
  ({ bars } = mergeBar(bars, current));
  assert.equal(bars.length, 2);
  const update = mergeBar(bars, toChartBar(candle("2026-09-28T12:01:00.000Z", 102, false)));
  assert.equal(update.action, "updated");
  assert.equal(update.bars.length, 2);
  // An out-of-order/older bar is ignored; a late partial never reopens a closed window.
  assert.equal(mergeBar(update.bars, closed).action, "ignored");
  const closedWindow = mergeBar(update.bars, toChartBar(candle("2026-09-28T12:01:00.000Z", 103, true))).bars;
  assert.equal(mergeBar(closedWindow, toChartBar(candle("2026-09-28T12:01:00.000Z", 90, false))).action, "ignored");
  // A gap stays a gap: appending a later bar does not insert anything in between.
  const gap = mergeBar(closedWindow, toChartBar(candle("2026-09-28T12:05:00.000Z", 104, false)));
  assert.equal(gap.bars.length, 3);
});

test("stream status maps to LIVE / RECONNECTING / STALE / OFFLINE", () => {
  const now = 1_000_000;
  assert.equal(streamStatusLabel({ serverState: "LIVE", eventSourceState: "open", lastEventAt: now - 1000, now }), "LIVE");
  assert.equal(streamStatusLabel({ serverState: "LIVE", eventSourceState: "open", lastEventAt: now - 60_000, now }), "STALE");
  assert.equal(streamStatusLabel({ serverState: "STALE", eventSourceState: "open", now }), "STALE");
  assert.equal(streamStatusLabel({ serverState: "RECONNECTING", eventSourceState: "open", now }), "RECONNECTING");
  assert.equal(streamStatusLabel({ serverState: "LIVE", eventSourceState: "connecting", now }), "RECONNECTING");
  assert.equal(streamStatusLabel({ serverState: null, eventSourceState: "closed", now }), "OFFLINE");
});

test("PAPER and REAL ACCOUNT overlays stay separate and labeled", () => {
  const lines = overlayLines(
    "BTCUSDT",
    [
      { symbol: "BTCUSDT", status: "open", side: "long", entry_price: 100, stop_price: 95, target_price: 110, liquidation_price: 70 },
      { symbol: "ETHUSDT", status: "open", side: "long", entry_price: 5, stop_price: 4, target_price: 6, liquidation_price: 3 },
    ],
    [{ symbol: "BTCUSDT", side: "short", entry_price: 105, liquidation_price: 140 }],
  );
  assert.deepEqual(lines.map((line) => line.source), ["PAPER", "PAPER", "PAPER", "PAPER", "REAL_ACCOUNT", "REAL_ACCOUNT"]);
  assert.ok(lines.filter((line) => line.source === "REAL_ACCOUNT").every((line) => line.title.startsWith("REAL ACCOUNT")));
  assert.ok(lines.filter((line) => line.source === "PAPER").every((line) => line.title.startsWith("PAPER")));
});

test("quote strip shows missing values as — and escapes content", () => {
  const empty = renderQuoteStrip(null);
  assert.equal((empty.match(/—/g) || []).length >= 7, true);
  const html = renderQuoteStrip({ ticker: { last_price: 83000.5, mark_price: 83001, index_price: 82999, funding_rate: 0.0001, change_24h_pct: -1.5, volume_24h_quote: 123456 },
    book: { best_bid: 83000, best_ask: 83000.1, spread_bps: 0.012 } });
  assert.match(html, /0\.0100%/);
  assert.match(html, /0\.01 bps/);
  const shell = renderLiveChartShell(["BTCUSDT", "<x>"], "BTCUSDT", "15m");
  assert.match(shell, /aria-pressed="true">15m/);
  assert.doesNotMatch(shell, /<x>/);
  assert.match(CHART_LIBRARY_URL, /lightweight-charts-5\.2\.1/);
});

test("chart library is vendored and pinned, not a remote CDN", () => {
  const source = readFileSync(new URL("../modules/components/liveChart.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /https?:\/\//);
  const vendored = readFileSync(new URL("../vendor/lightweight-charts-5.2.1/lightweight-charts.standalone.production.mjs", import.meta.url), "utf8");
  assert.match(vendored, /Lightweight Charts™ v5\.2\.1/);
});

test("provider credential cards never render a secret value", () => {
  const html = renderProviderCredentialCards(
    [{ provider_id: "azure-gpt6-luna", kind: "foundry_responses", display_name: "Azure", model: "gpt-6-luna", reasoning_effort: "max",
       credential_status: "stored_in_os_credential_store", credential_secret_configured: true, last_validation_status: "passed",
       last_validated_at: "2026-09-28T00:00:00Z", last_validation_latency_ms: 3000,
       last_validation: { returned_model: "gpt-6-luna", reasoning_effort_echoed: "max", provider_request_id: "req-1" } },
     { provider_id: "fixture-jev", kind: "fixture_jev", display_name: "Fixture", model: "f" }],
    { backend: "macos-keychain" },
  );
  assert.match(html, /Connected/);
  assert.match(html, /echoed max/);
  assert.match(html, /type="password"/);
  assert.doesNotMatch(html, /value="[^"]+" *\/?>\s*<\/label>\s*<div class="composer-actions">/);
  assert.doesNotMatch(html, /Fixture/);
  assert.doesNotMatch(html, new RegExp(SENTINEL));
});

test("exchange account form and mirror show read-only capability with writes blocked", () => {
  const account = { account_id: "gate-main", display_name: "Gate", environment: "live", enabled: true, sync_enabled: true,
    credentials: { api_key: true, api_secret: true },
    last_sync: { source: "GATE_LIVE_READONLY", synced_at: "2026-09-28T00:00:00Z", balance: { equity: 250, currency: "USDT" },
      positions: [], open_orders: [], errors: {},
      capability: { authenticated: true, futures_read: true, balance_sync: true, positions_sync: true, orders_read: true, trades_read: false, write_execution: false } } };
  const html = renderExchangeAccounts([account]);
  assert.match(html, /REAL ACCOUNT · read-only mirror/);
  assert.match(html, /write execution: BLOCKED BY DESIGN/);
  assert.match(html, /Never merged with the PAPER wallet/);
  assert.match(html, /\(stored — enter to rotate\)/);
  assert.equal((html.match(/type="password"/g) || []).length, 2);
  assert.match(renderAccountMirror({}), /Not synced yet/);
});

test("cost settings show unknown prices as fail-closed and projections as ESTIMATE", () => {
  const priceHtml = renderPriceBook({ price_book: [], price_coverage: [{ provider_id: "azure-gpt6-luna", model: "gpt-6-luna", price_known: false, status: "UNKNOWN_PRICE_FAIL_CLOSED" }] });
  assert.match(priceHtml, /Unknown price is not zero/);
  assert.match(priceHtml, /UNKNOWN PRICE FAIL CLOSED/);
  const budget = { daily_usd: 5, experiment_usd: 30 };
  const status = renderBudgetStatus({ budget, spent_today_usd: 5, remaining_today_usd: 0, utilization_today: 1, spent_experiment_usd: 5,
    remaining_experiment_usd: 25, utilization_experiment: 0.1667, exhausted: true, limit_action: "PAUSE_NEW_ENTRIES",
    projection: { projected_today_usd: 7, days_until_experiment_budget_exhausted: 3.5 } });
  assert.match(status, /ESTIMATE/);
  assert.match(status, /no new paid call starts/);
  assert.match(status, /PAUSE_NEW_ENTRIES/);
});

test("economics keep USD cost and USDT PnL separate without an FX policy", () => {
  const economics = {
    trading: { gross_trading_pnl_usdt: 10, fees_usdt: 1, funding_usdt: 0.5, slippage_usdt: 0.2, unrealized_pnl_usdt: 0, net_trading_pnl_usdt: 8.3 },
    ai_cost: { jev_cost_usd: 0.1, gpt_cost_usd: 2, total_ai_cost_usd: 2.1, complete: true, calls_with_unavailable_cost: 0, cost_on_no_trade_usd: 1, gpt_escalation_cost_usd: 2 },
    kpis: { ai_cost_per_analysis_usd: 0.5, ai_cost_per_eligible_case_usd: 1, ai_cost_per_trade_usd: 1, ai_cost_per_winning_trade_usd: 2,
      ai_cost_pct_of_gross_profit: null, net_economic_expectancy_per_trade_usdt: null,
      denominators: { analyses: 4, eligible_cases: 2, closed_trades: 2, winning_trades: 1 } },
    fx: { mode: "none" },
    net_economic_pnl_usdt: null,
    net_economic_unavailable_reason: "no USD/USDT cost FX policy configured",
    aligned_arm_value: { status: "insufficient_sample", sample: { quant_closed: 2, hybrid_closed: 2 } },
  };
  const html = renderEconomics(economics);
  assert.match(html, /Net experiment economics<\/dt><dd><strong>Unavailable/);
  assert.match(html, /no USD\/USDT cost FX policy configured/);
  assert.match(html, /\$2\.1000/);
  const withFx = renderEconomics({ ...economics, fx: { mode: "manual", usdt_per_usd: 1, source: "operator" }, net_economic_pnl_usdt: 6.2, net_economic_unavailable_reason: null });
  assert.match(withFx, /6\.2000 USDT/);
});

test("health strip reports Gate, scheduler, PAPER, providers, budget, and blocked live orders", () => {
  const html = renderHealthStrip({
    experiment: { status: "running", config: { market_data_mode: "gate_usdt", jev_provider_id: "j", gpt_provider_id: "g" } },
    market_stream: { state: "STALE" },
    providers: [{ provider_id: "j", kind: "typesafe_jev", last_validation_status: "passed" }, { provider_id: "g", kind: "foundry_responses", last_validation_status: "failed" }],
    ai_cost: { budget_status: { remaining_today_usd: 1.25, utilization_today: 0.75, exhausted: false } },
  });
  assert.match(html, /status-pill--warn"><small>Gate<\/small> STALE/);
  assert.match(html, /<small>Mode<\/small> PAPER/);
  assert.match(html, /<small>Jev<\/small> connected/);
  assert.match(html, /<small>GPT<\/small> failed/);
  assert.match(html, /\$1\.25/);
  assert.match(html, /BLOCKED BY DESIGN/);
  assert.match(renderLedgerRows([]), /No AI calls recorded/);
});

test("new API methods post credentials once and never read them back", async () => {
  const calls = [];
  const api = createPaperApi(async (url, options) => {
    calls.push({ url, options });
    return { ok: true, status: 200, json: async () => ({ secret: { stored: true, masked: "••••" } }) };
  });
  await api.saveProviderSecret("azure-gpt6-luna", SENTINEL);
  await api.saveAccountSecrets("gate-main", "key", SENTINEL);
  await api.syncAccount("gate-main");
  await api.candles("BTCUSDT", "5m", 200);
  await api.saveCostControls({ ai_budget: {}, cost_fx: {} });
  assert.equal(calls[0].url, "/api/providers/azure-gpt6-luna/secret");
  assert.deepEqual(JSON.parse(calls[0].options.body), { value: SENTINEL });
  assert.equal(calls[1].url, "/api/exchange-accounts/gate-main/secrets");
  assert.equal(calls[2].url, "/api/exchange-accounts/gate-main/sync");
  assert.equal(calls[3].url, "/api/market/candles?symbol=BTCUSDT&interval=5m&limit=200");
  assert.equal(calls[4].url, "/api/cost-controls");
  assert.ok(calls.every(({ options }) => options.credentials === "same-origin" && options.cache === "no-store"));
  assert.equal(api.streamUrl(["BTCUSDT"], "1m"), "/api/market/stream?symbols=BTCUSDT&interval=1m");
  const source = readFileSync(new URL("../modules/views/runtimeSettings.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB/);
});

import { assetBreakdown, renderPortfolioSummary, renderPortfolioUnavailable, unrealizedPnl } from "../modules/components/portfolioSummary.js";

test("overview portfolio summary leads with capital, growth, PnL, positions and assets", () => {
  const dashboard = {
    experiment: { experiment_id: "EXP-001", status: "running" },
    data_safety: { origin: "GATE_USDT_PUBLIC" },
    metrics: {
      portfolio: { starting_balance_usdt: 100, ending_equity_usdt: 104, net_pnl_usdt: 4, realized_pnl_usdt: 3, unrealized_pnl_usdt: 1,
        open_position_count: 1, margin_used_usdt: 10, closed_trade_count: 2, win_rate: 0.5, win_count: 1, loss_count: 1,
        max_drawdown: 0.02, profit_factor: 2, fees_usdt: 0.1, funding_paid_usdt: 0, slippage_drag_usdt: 0.05, data_origin: "GATE_USDT_PUBLIC" },
      by_asset: [{ symbol: "ETHUSDT", closed_trade_count: 2, net_pnl_usdt: 3 }],
    },
    positions: [{ symbol: "BTCUSDT", status: "open", side: "short", quantity: 0.001, entry_price: 80000, mark_price: 79000, leverage: 3, stop_price: 81000, target_price: 77000 }],
    equity: [{ as_of: "2026-09-28T00:00:00Z", equity: 100 }, { as_of: "2026-09-28T01:00:00Z", equity: 104 }],
    economics: { ai_cost: { total_ai_cost_usd: 0.5 }, net_economic_pnl_usdt: null },
  };
  assert.equal(unrealizedPnl(dashboard.positions[0]), 1);
  const rows = assetBreakdown(dashboard.positions, dashboard.metrics.by_asset);
  assert.deepEqual(rows.map((row) => [row.symbol, row.total]), [["BTCUSDT", 1], ["ETHUSDT", 3]]);
  const html = renderPortfolioSummary(dashboard);
  assert.match(html, /104\.00 USDT/);
  assert.match(html, /\+4\.00%/);
  assert.match(html, /1W \/ 1L of 2 closed/);
  assert.match(html, /SHORT 3x/);
  assert.match(html, /portfolio-growth-chart--up/);
  assert.match(html, /After AI cost<\/span>\s*<strong[^>]*>Unavailable/);
  assert.match(html, /PAPER · GATE_USDT_PUBLIC/);
  const empty = renderPortfolioSummary({ ...dashboard, data_safety: { origin: "FIXTURE" }, positions: [], equity: [], metrics: { portfolio: { starting_balance_usdt: 100, ending_equity_usdt: 100, net_pnl_usdt: 0, data_origin: "FIXTURE" }, by_asset: [] } });
  assert.match(empty, /FIXTURE · synthetic mechanics/);
  assert.match(empty, /No open PAPER positions/);
  assert.match(empty, /Equity growth appears after/);
  assert.match(renderPortfolioUnavailable("down"), /No placeholder numbers/);
});
