// Day-trading views: Today, Experiments, Strategy Search, the chart kit and auto-refresh.
import test from "node:test";
import assert from "node:assert/strict";

import { barList, calendarHeatmap, gauge, heatGrid, lineChart, rangeBar } from "../modules/components/charts.js";
import { startAutoRefresh } from "../modules/components/autoRefresh.js";
import { renderToday } from "../modules/views/today.js";
import { renderExperiments } from "../modules/views/experiments.js";
import { renderStrategySearch } from "../modules/views/strategySearch.js";
import { nextScanAt } from "../modules/views/cockpit.js";

const day = (date, pnl, trades = 1) => ({ date, pnl_usdt: pnl, pnl_pct: pnl / 500, trades });

test("charts render accessible SVG/CSS, escape labels, and handle empty data", () => {
  const line = lineChart([["2026-09-29T00:00:00Z", 500], ["2026-09-29T01:00:00Z", 503], ["2026-09-29T02:00:00Z", 502]], { baseline: 500 });
  assert.match(line, /<svg[^>]+role="img"/);
  assert.match(line, /chart--up/);
  assert.match(lineChart([["2026-09-29T00:00:00Z", 500]]), /Not enough data/);
  const cal = calendarHeatmap([day("2026-09-28", 3), day("2026-09-29", -2)]);
  assert.match(cal, /1 green/);
  assert.match(cal, /1 red/);
  assert.match(cal, /\+0\.60%/);
  assert.match(cal, /role="img" aria-label="2026-09-28: \+0\.60%/);   // per-day values reach assistive tech
  assert.match(rangeBar({ p5: -0.05, median: 0.02, p95: 0.1 }), /role="img" aria-label="5th percentile -5\.0%, median \+2\.0%, 95th percentile \+10\.0%"/);
  assert.match(barList([{ label: "<b>x</b>", value: -1 }]), /&lt;b&gt;x&lt;\/b&gt;/);
  assert.match(heatGrid([1], [2], () => ({ value: 0.1, text: "10%" })), /heat-pos/);
  assert.match(heatGrid([1], [2], () => ({ value: 0.1, text: "10%", bad: true })), /heat-bad/);
  assert.match(gauge(1.2), /gauge--stop/);
  assert.match(gauge(0.5), /aria-valuenow="50"/);
  assert.match(rangeBar({ p5: -0.05, median: 0.02, p95: 0.1 }), /range-mid--pos/);
});

test("Today shows P&L with a sign, the loss-limit gauge, the next decision, and AI usage", () => {
  const html = renderToday({
    label: "EXP-002", engine: "sleeves_v1", status: "running", trading_day_utc: "2026-09-29", equity_usdt: 498.1, day_start_equity_usdt: 500,
    pnl_today_usdt: -1.9, pnl_today_pct: -0.0038, pnl_total_usdt: -1.9, open_positions: 11, next_decision_at: "2026-09-29T12:01:00Z",
    closed_today: { trades: 4, wins: 1, fees_usdt: 0.2 }, daily_loss_limit: { limit_pct: 0.08, loss_pct: 0.0038, used: 0.0475, paused: false },
    ai: { enabled: false, calls_today: 0, cost_usd_estimate: 0 }, intraday: [["2026-09-29T00:00:00Z", 500], ["2026-09-29T08:00:00Z", 498.1]],
    calendar: [day("2026-09-29", -1.9)], summary: { days: 1, green_days: 0, best_day_usdt: -1.9, worst_day_usdt: -1.9 },
  });
  assert.match(html, /-1\.90 USDT/);
  assert.match(html, /25% win/);
  assert.match(html, /of 8\.0% allowed/);
  assert.match(html, /Not used/);
  assert.match(html, /07:00 Bangkok/);
  assert.doesNotMatch(renderToday({ ...{ label: "x", pnl_today_usdt: 0, closed_today: {}, daily_loss_limit: { paused: true }, ai: {}, intraday: [], calendar: [], summary: {} } }), /undefined/);
});

test("Experiments shows every experiment card, escapes text, and marks this server", () => {
  const html = renderExperiments({ probed_ports: [8765, 8768], experiments: [
    { port: 8768, self: true, label: "EXP-002", engine: "sleeves_v1", status: "running", health: "OK", pnl_total_usdt: 4.2, starting_capital_usdt: 501,
      pnl_today_usdt: 1, pnl_today_pct: 0.002, open_positions: 11, day: 1.5, min_days: 90, completed_trades: 3, target_trades: 80,
      ai: { enabled: false }, calendar: [day("2026-09-29", 1)], risk_incidents: [{ kind: "RISK_PAUSE", summary: "<x>" }], url: "http://127.0.0.1:8768/frontend/#/overview" },
    { port: 8765, self: false, label: "EXP-001", engine: "breakout_15m", status: "running", health: "OK", ai: { enabled: true, calls_total: 0, cost_usd_estimate: 0 },
      url: "http://127.0.0.1:8765/frontend/#/overview" },
    { port: 8770, error: "campaign unavailable (URLError)" },
  ] });
  assert.match(html, /EXP-002/);
  assert.match(html, /this server/);
  assert.match(html, /Trend sleeves/);
  assert.match(html, /Open ↗/);
  assert.match(html, /\+4\.20 USDT/);
  assert.match(html, /Not used/);
  assert.match(html, /0 calls/);
  assert.match(html, /campaign unavailable/);
  const older = renderExperiments({ experiments: [{ port: 8765, label: "EXP-001", status: "running", pnl_total_usdt: null, pnl_today_usdt: null, url: "http://127.0.0.1:8765/" }] });
  assert.doesNotMatch(older, /\+0\.00 USDT/);   // unknown P&L is shown as "—", not a fabricated zero
  assert.doesNotMatch(html, /<x>/);
  assert.match(renderExperiments({ experiments: [] }), /No PAPER experiment servers/);
});

test("Strategy Search shows live progress, then the verdict, rankings, leverage grid, and odds", () => {
  assert.match(renderStrategySearch({ available: false, status: { stage: "search", coins_total: 30, coins_downloaded: 12, coins_searched: 5 } }), /12 \/ 30/);
  const html = renderStrategySearch({
    available: true, search: { n_configs: 6634, n_coins: 30, n_runs: 199020 },
    wfo: { K20_sharpe: { oos_sharpe: 1.8, oos_ret: 0.4, oos_pos_weeks: 0.6, holdout_sharpe: 1.1, holdout_ret: 0.05 }, K5_sharpe: { oos_sharpe: 0.3, oos_ret: 0.01 } },
    coins: [{ coin: "NEARUSDT", pct_cfg_persist: 0.12, median_oos_sharpe_is_top50: 0.8 }],
    families: [{ family: "MR", tf: 60, runs: 10, pct_oos_net_pos: 0.6, pct_oos_gross_pos: 0.8 }],
    deep: { leverage_grid: [{ risk: 0.005, lev: 3, n: 100, cagr: 0.3, maxdd: 0.1, liquidations: 0, worst_day: -0.02, sharpe: 1.5 },
                            { risk: 0.03, lev: 20, n: 100, cagr: 0.9, maxdd: 0.5, liquidations: 3, worst_day: -0.2, sharpe: 0.5 }],
            best_within_20pct_dd: { risk: 0.005, lev: 3, cagr: 0.3, maxdd: 0.1 },
            scenarios: { base: { n: 100, cagr: 0.3, maxdd: 0.1 }, cost_x2: { n: 100, cagr: -0.1, maxdd: 0.3 } },
            monte_carlo: [{ horizon_days: 14, p_profit: 0.64, median: 0.01, p5: -0.03, p95: 0.06 }], contribution: [] },
    gate_crosscheck: [{ coin: "BTCUSDT", ret_corr_5m: 0.99, median_abs_basis_bps: 1.2, gate_daily_usd_m: 900 }],
    gate: { passed: true, failed: 0, unknown: 0, checks: [{ name: "Profit factor", value: 1.3, op: ">=", threshold: 1.2, status: "pass" }] },
  });
  assert.match(html, /passed the full research gate/);
  assert.match(html, /gate-pass/);
  // a strong Sharpe alone is never a positive verdict: every pre-declared criterion must be measured and met
  const partial = renderStrategySearch({ available: true, search: {}, wfo: { K20_sharpe: { oos_sharpe: 2.5, oos_ret: 0.5, holdout_ret: 0.1 } },
    gate: { passed: false, failed: 1, unknown: 1, checks: [{ name: "Profit factor", value: null, op: ">=", threshold: 1.2, status: "unknown" },
                                                         { name: "Profitable at 2x costs (CAGR)", value: -0.1, op: ">", threshold: 0, status: "fail" }] } });
  assert.doesNotMatch(partial, /passed the full research gate/);
  assert.match(partial, /fails the research gate/);
  assert.match(partial, /not measured/);
  assert.match(html, /★ NEAR/);
  assert.match(html, /199,020 backtests/);
  assert.match(html, /0\.50% risk per trade at 3x/);
  assert.match(html, /heat-bad/);
  assert.match(html, /64%/);
  assert.match(html, /cost x2/);
  assert.match(html, /Binance vs Gate/);
});

test("next decision follows the engine: 4h for trend sleeves, 15m for the breakout", () => {
  const now = new Date("2026-09-29T08:30:00Z");
  assert.equal(nextScanAt({ config: { strategy_engine: "sleeves_v1", schedule_delay_seconds: 60 } }, now), "2026-09-29T12:01:00.000Z");
  assert.equal(nextScanAt({ config: { schedule_delay_seconds: 60 } }, new Date("2026-09-29T08:32:00Z")), "2026-09-29T08:46:00.000Z");
  assert.equal(nextScanAt({ config: { schedule_delay_seconds: 60 } }, new Date("2026-09-29T08:30:30Z")), "2026-09-29T08:31:00.000Z");
});

test("auto-refresh runs, pauses while hidden, and reports failures", async () => {
  let calls = 0;
  const listeners = {};
  const doc = { hidden: false, addEventListener: (k, f) => { listeners[k] = f; }, removeEventListener: () => {} };
  const text = { textContent: "" };
  const host = { querySelector: (sel) => (sel === "[data-auto-refresh-text]" ? text : null), addEventListener() {}, removeEventListener() {}, classList: { add() {}, remove() {}, toggle() {} } };
  const stop = startAutoRefresh(host, async () => { calls += 1; }, { intervalMs: 60000, doc });
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(calls, 1);
  assert.match(text.textContent, /updated just now/);
  doc.hidden = true;
  listeners.visibilitychange();
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(calls, 1);
  stop();
  const stop2 = startAutoRefresh(host, async () => { throw new Error("boom"); }, { intervalMs: 60000, doc: { hidden: false } });
  await new Promise((r) => setTimeout(r, 5));
  assert.match(text.textContent, /update failed: boom/);
  stop2();
});
