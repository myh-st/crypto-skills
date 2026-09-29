// Spot AI Co-Trader UI: pure renderers against fixture payloads that follow the contract in the
// co-trader spec (main contract + Addenda A–D). Offline; no DOM, no network.
import test from "node:test";
import assert from "node:assert/strict";

import { createPaperApi } from "../modules/paperApi.js";
import {
  actionFor, fmtQty, fmtUsdt, pctChange, sizingLine, triggerText, cotSpark, stateChip,
} from "../modules/components/cotraderBits.js";
import {
  agreeMinusDisagree, jevHistory, latestAnalysis, renderAiCard, renderAnalysesList, renderJevPanel, renderJevRow, renderScorecard,
} from "../modules/components/cotraderAi.js";
import {
  candleBars, chartLevels, equitySummary, holdShading, ladderMarkers, linePoints, priceChartSummary, snapToBar, toUnix, tradeMarkers,
} from "../modules/components/cotraderChart.js";
import {
  LADDER_HINT_TH, cdcChip, cdcRibbon, ladderLevels, ladderOf, ladderTransitions, renderEvidence, renderLadderDetail, renderLadderRow,
} from "../modules/components/cotraderLadder.js";
import {
  journalEntry, renderBriefing, renderCoinCard, renderCoinGrid, renderCotraderOverview, renderDecisionCard, renderJournalList,
  renderRegimeStrip, renderSizing, renderTradesTable, ruleAgreed, ruleReason,
} from "../modules/views/cotrader.js";
import {
  alignmentWarning, allocationShares, renderHoldingRow, renderHoldingsData, renderHoldingsTotals, renderSyncPanel, renderValidation, validateHolding,
} from "../modules/views/cotraderHoldings.js";

const HOSTILE = '<img src=x onerror="alert(1)">';
const DAY = 86400;
const T0 = 1780012800;   // a 00:00 UTC open time

// ---------------------------------------------------------------- fixtures (contract shapes)

function coin(overrides = {}) {
  return {
    symbol: "BTC_USDT", base: "BTC", price: 84210.5, change_24h: 0.012,
    state: "HOLD", state_since: "2026-09-12", days_in_state: 17, changed_today: false,
    ret_60d: 0.084, dist_sma100: 0.031, vol_20d: 0.021,
    rule: { cagr: 0.41, max_dd: 0.33, time_in_market: 0.56, switches: 18, bh_cagr: 0.29, bh_max_dd: 0.52 },
    spark: [[T0, 83110.2], [T0 + DAY, 83890.0], [T0 + 2 * DAY, 84210.5]],
    last_ai: { as_of: "2026-09-12T00:10:40Z", trigger: "state_change", stance: "agree", conviction: "medium", summary_th: "แนวโน้มยังดี" },
    event: { from: "WATCH", to: "HOLD", at: "2026-09-12T00:00:00Z" },
    trend_line_next: 80120.4, distance_to_trend_line: 0.051,
    action: { type: "HOLD", text: "Keep. Exit if a daily close is below 80,120.4.", fresh: false, trigger: { kind: "close_below", price: 80120.4, distance_pct: 0.051 } },
    sizing: { method: "equal_weight", weight: 0.142857, target_usdt: 142.86, current_usdt: 100, delta_usdt: 42.86, delta_qty: 0.000509, risk_to_trend_line_usdt: 6.94 },
    held: true,
    jev: { as_of: "2026-09-29T00:05:10Z", trend_regime: "uptrend", trend_strength: 0.75, reversal_risk: 0.25, rule_agreement: "agree", entry_timing: "wait_pullback", key_risk: "overextended" },
    ...overrides,
  };
}

function overview(overrides = {}) {
  return {
    as_of: "2026-09-29T13:30:00Z",
    last_close: "2026-09-29T00:00:00Z",
    next_close: "2026-09-30T00:00:00Z",
    regime: { label: "MIXED", breadth: 0.57, hold: 4, total: 7, btc_state: "HOLD" },
    briefing: { as_of: "2026-09-29T00:10:03Z", stance_market: "mixed", summary_th: "ตลาดผสม", highlights: [{ coin: "NEAR", note: "เบรคขึ้น" }] },
    ai: { enabled: true, spent_today_usd: 0.03, daily_cap_usd: 0.5, spent_total_usd: 0.41, total_cap_usd: 15.0, blocked_reason: null,
      jev_calls_today: 7, luna_calls_today: 1, jev_spent_usd: 0.004, luna_spent_usd: 0.012, pricing_is_fallback: true },
    settings: { cotrader_capital_usdt: 1000, capital_source: "user", sizing_method: "equal_weight" },
    coins: [
      coin(),
      coin({ symbol: "SEI_USDT", base: "SEI", price: 1.18, change_24h: -0.034, state: "CASH", changed_today: false, held: false, jev: null,
        action: { type: "WAIT", text: "Stay out. Buy if a daily close is above 1.23.", fresh: false, trigger: { kind: "close_above", price: 1.23, distance_pct: -0.0407 } } }),
      coin({ symbol: "NEAR_USDT", base: "NEAR", price: 4.86, state: "HOLD", changed_today: true, held: false,
        action: { type: "BUY", text: "In trend. The rule holds this coin.", fresh: true, trigger: { kind: "close_below", price: 4.4, distance_pct: 0.105 } } }),
    ],
    ...overrides,
  };
}

function detail(overrides = {}) {
  return {
    ...coin(),
    candles: [[T0, 83000.0, 84500.0, 82100.0, 84210.5, 12345.6], [T0 + DAY, 84210.5, 85000, 83000, 84800, 10000], [T0 + 2 * DAY, 84800, 86000, 84000, 85500, 9000]],
    sma100: [[T0, 80120.4], [T0 + DAY, 80200.1]],
    periods: [{ from: "2026-05-29", to: "2026-05-30", state: "HOLD" }],
    equity: { rule: [[T0, 1.8], [T0 + DAY, 1.84]], buy_hold: [[T0, 1.5], [T0 + DAY, 1.52]] },
    analyses: [
      { source: "luna", as_of: "2026-09-20T10:00:00Z", trigger: "manual", stance: "caution", conviction: "low", summary_th: "ระวัง",
        bull_points: ["Higher lows"], bear_points: ["Volume fading"], key_levels: { support: [80000], resistance: [88000], invalidation: 76500 },
        risks: ["Macro"], change_my_mind: "A close above 88k", model: "gpt-6-luna", cost_usd: 0.012 },
      { source: "jev", as_of: "2026-09-28T00:05:00Z", trigger: "daily", answers: { trend_regime: "uptrend", trend_strength: 0.6, reversal_risk: 0.3, rule_agreement: "agree", entry_timing: "good_now", key_risk: "none" }, cost_usd: 0.0006 },
      { source: "jev", as_of: "2026-09-29T00:05:00Z", trigger: "daily", answers: { trend_regime: "uptrend", trend_strength: 0.75, reversal_risk: 0.25, rule_agreement: "agree", entry_timing: "wait_pullback", key_risk: "overextended" }, cost_usd: 0.0006 },
    ],
    journal: [{ id: "j1", at: "2026-09-13T08:00:00Z", action: "buy", price: 84000, amount_usdt: 50, note: "first entry", rule_state: "HOLD", ai_stance: "agree",
      price_now: 84210.5, change_since: 0.0025 }],
    trend_line_series: [[T0, 79900], [T0 + DAY, 80120.4]],
    trades: [
      { entry_at: "2026-05-29", entry_price: 83000, exit_at: "2026-05-30T00:00:00Z", exit_price: 84800, return_pct: 0.0217, days: 1 },
      { entry_at: T0 + 2 * DAY, entry_price: 85500, exit_at: null, exit_price: null, return_pct: null, days: 3 },
    ],
    ...overrides,
  };
}

function holdings(overrides = {}) {
  return {
    as_of: "2026-09-29T13:30:00Z",
    sources: { gate: { account_id: "gate-main", synced_at: "2026-09-29T13:15:00Z", status: "OK", error: null }, manual_count: 2 },
    share_holdings_with_ai: false,
    totals: { value_usdt: 1234.5, cost_usdt: 1100.0, unrealized_usdt: 134.5, unrealized_pct: 0.1223, realized_usdt: 12.3, cash_usdt: 250.0 },
    holdings: [
      { base: "NEAR", qty: 50.0, qty_source: "gate", avg_price: 4.21, avg_source: "gate_trades", price: 4.86, value_usdt: 243.0, cost_usdt: 210.5,
        unrealized_usdt: 32.5, unrealized_pct: 0.154, realized_usdt: 3.1, allocation: 0.197, rule_state: "HOLD", alignment: "aligned",
        trades_counted: 14, cost_basis_note: null, manual_id: null },
      { base: "SEI", qty: 400, qty_source: "manual", avg_price: 1.4, avg_source: "manual", price: 1.18, value_usdt: 472, cost_usdt: 560,
        unrealized_usdt: -88, unrealized_pct: -0.157, realized_usdt: 0, allocation: 0.382, rule_state: "CASH", alignment: "holding_in_cash_state",
        trades_counted: 0, cost_basis_note: null, manual_id: "m-7" },
      { base: "SUI", qty: 20, qty_source: "gate", avg_price: null, avg_source: "unknown", price: 3.1, value_usdt: 62, cost_usdt: null,
        unrealized_usdt: null, unrealized_pct: null, realized_usdt: null, allocation: 0.05, rule_state: null, alignment: null,
        trades_counted: 0, cost_basis_note: "20 SUI deposited; no trades explain the balance", manual_id: null },
    ],
    cash: [{ currency: "USDT", qty: 250.0 }],
    ...overrides,
  };
}

// ---------------------------------------------------------------- API client

test("paperApi co-trader and holdings methods hit the contract endpoints with relative URLs", async () => {
  const calls = [];
  const api = createPaperApi(async (url, options) => {
    calls.push({ url, method: options.method ?? "GET", body: options.body === undefined ? undefined : JSON.parse(options.body) });
    return { ok: true, status: 200, json: async () => ({}) };
  });
  await api.cotrader();
  await api.cotraderCoin("BTC");
  await api.cotraderAnalyze("BTC");
  await api.cotraderAnalyze("BTC", { confirm: true });
  await api.cotraderScorecard();
  await api.cotraderJournal({ coin: "BTC", action: "buy", price: null, amount_usdt: null, note: "" });
  await api.saveCotraderSettings({ sizing_method: "inverse_vol" });
  await api.holdings();
  await api.saveManualHolding({ base: "NEAR", qty: 1, avg_price: 4 });
  await api.deleteManualHolding("m/7");
  await api.syncHoldings();
  await api.syncHoldings("gate-main");
  await api.validateGateKey();
  await api.saveHoldingsSettings({ share_holdings_with_ai: true });
  assert.deepEqual(calls.map((c) => `${c.method} ${c.url}`), [
    "GET /api/cotrader", "GET /api/cotrader/BTC", "POST /api/cotrader/BTC/analyze", "POST /api/cotrader/BTC/analyze", "GET /api/cotrader/scorecard",
    "POST /api/cotrader/journal", "POST /api/cotrader/settings", "GET /api/holdings", "POST /api/holdings/manual", "POST /api/holdings/manual/m%2F7/delete",
    "POST /api/holdings/sync", "POST /api/holdings/sync", "POST /api/holdings/gate/validate", "POST /api/holdings/settings",
  ]);
  assert.deepEqual(calls[2].body, { confirm: false });
  assert.deepEqual(calls[3].body, { confirm: true });
  assert.deepEqual(calls[10].body, {});
  assert.deepEqual(calls[11].body, { account_id: "gate-main" });
});

test("analyze 409 {confirmation_required, reason} surfaces as a typed confirmation", async () => {
  const api = createPaperApi(async () => ({ ok: false, status: 409, json: async () => ({ confirmation_required: true, reason: "manual_cap" }) }));
  await assert.rejects(api.cotraderAnalyze("BTC"), (error) => {
    assert.equal(error.status, 409);
    assert.deepEqual(error.confirmation, { code: "manual_cap", details: {} });
    return true;
  });
});

// ---------------------------------------------------------------- overview

test("regime strip shows the label, breadth, BTC state, countdown and the Jev/Luna budget split", () => {
  const html = renderRegimeStrip(overview());
  assert.match(html, /cot-regime--mixed/);
  assert.match(html, /MIXED/);
  assert.match(html, /4\/7 in trend/);
  assert.match(html, /data-countdown="2026-09-30T00:00:00Z"/);
  assert.match(html, /\$0\.47\* left today/);
  assert.match(html, /Jev<\/span> 7 calls · \$0\.0040\*/);
  assert.match(html, /Luna<\/span> 1 call · \$0\.0120\*/);
  assert.match(html, /AI prices are placeholders, set real prices in Settings › Cost &amp; Budgets/);
  const blocked = renderRegimeStrip(overview({ ai: { enabled: true, blocked_reason: `daily cap ${HOSTILE}` } }));
  assert.match(blocked, /AI unavailable/);
  assert.doesNotMatch(blocked, /<img/);
});

test("briefing card shows the market stance, Thai summary and highlights; null briefing says so", () => {
  const html = renderBriefing(overview().briefing);
  assert.match(html, /Market: mixed/);
  assert.match(html, /lang="th">ตลาดผสม/);
  assert.match(html, /href="#\/cotrader\/NEAR"/);
  assert.match(renderBriefing(null), /No briefing yet today/);
});

test("coin grid: action badges, rule-state Thai text, trigger text, days in state and the fresh pulse", () => {
  const html = renderCoinGrid(overview().coins);
  // fresh NEAR is sorted first
  assert.ok(html.indexOf('data-cot-coin="NEAR"') < html.indexOf('data-cot-coin="BTC"'));
  assert.match(html, /cot-action--hold[^>]*><span aria-hidden="true">■<\/span> HOLD/);
  assert.match(html, /cot-action--wait[^>]*><span aria-hidden="true">◷<\/span> WAIT/);
  assert.match(html, /cot-action--buy[^>]*><span aria-hidden="true">▲<\/span> BUY/);
  assert.match(html, /อยู่ในเทรนด์/);
  assert.match(html, /ถือเงินสด/);
  assert.match(html, /Buy if a daily close is above 1\.23 \(\+4\.2% away\)/);
  assert.match(html, /17 days in HOLD/);
  assert.match(html, /cot-card--fresh/);
  assert.match(html, /NEW at today's close/);
  assert.equal((html.match(/cot-card--fresh/g) || []).length, 1);
  const card = renderCoinCard(coin());
  assert.match(card, /AI agrees · medium/);
  assert.match(card, /cot-spark-level/);           // the trend line on the sparkline
  assert.match(card, /▲<\/span> \+1\.20%/);           // 24h change with an arrow and a sign
});

test("WATCH shows รอยืนยัน and an unknown holding yields IN / OUT from the deterministic table", () => {
  assert.match(stateChip("WATCH"), /รอยืนยัน/);
  const inTrend = actionFor({ state: "HOLD", held: null, trend_line_next: 10 });
  assert.equal(inTrend.type, "IN");
  assert.equal(actionFor({ state: "WATCH", held: null, trend_line_next: 10 }).type, "OUT");
  assert.equal(actionFor({ state: "CASH", held: true, trend_line_next: 10 }).type, "SELL");
  assert.equal(actionFor({ state: "CASH", held: false, trend_line_next: 10 }).text, "Stay out. Buy if a daily close is above 10.00.");
  assert.equal(actionFor({ state: "HOLD", held: false, changed_today: true, trend_line_next: 10 }).fresh, true);
  // the backend's action always wins
  assert.equal(actionFor(coin({ action: { type: "SELL", text: "x", trigger: null } })).type, "SELL");
});

test("trigger text: move away, already-past wording, missing trigger", () => {
  assert.equal(triggerText({ kind: "close_above", price: 1.23, distance_pct: -0.04 }, 1.18), "Buy if a daily close is above 1.23 (+4.2% away)");
  assert.equal(triggerText({ kind: "close_below", price: 80120.4 }, 84210.5), "Exit if a daily close is below 80,120.40 (−4.9% away)");
  assert.match(triggerText({ kind: "close_above", price: 1.0 }, 1.1), /already above it; only the daily close counts/);
  assert.equal(triggerText({ kind: "close_above", price: 2, distance_pct: 0.05 }), "Buy if a daily close is above 2.00 (+5.0% away)");
  assert.match(triggerText(null), /Trigger unavailable/);
});

test("overview renders every section, including the scorecard error state", () => {
  const html = renderCotraderOverview(overview(), { scorecardError: "HTTP 404" });
  assert.match(html, /Daily AI briefing/);
  assert.match(html, /AI Scorecard/);
  assert.match(html, /Scorecard unavailable: HTTP 404/);
  assert.doesNotMatch(html, /undefined|NaN/);
});

// ---------------------------------------------------------------- decision card

test("decision card: rule says, how much with a delta, how, and the AI", () => {
  const html = renderDecisionCard(detail(), { analysis: latestAnalysis(detail().analyses) });
  assert.match(html, /Rule says/);
  assert.match(html, /Keep\. Exit if a daily close is below 80,120\.4\./);
  assert.match(html, /Both conditions hold: 60-day return \+8\.4% and close 3\.1% above the 100-day average\./);
  assert.match(html, /Buy 42\.86 USDT ≈ 0\.000509 BTC/);
  assert.match(html, /Equal weight \(1\/N\) · 14\.3%/);
  assert.match(html, /Risk to trend line/);
  assert.match(html, /▼<\/span> −6\.94 USDT/);
  assert.match(html, /daily close<\/strong> \(00:00 UTC = 07:00 Bangkok\)/);
  assert.match(html, /NOT an intraday stop/);
  assert.match(html, /0\.1% fee \+ 5 bps/);
  assert.match(html, /SEI and ENA are thin on Gate/);
  assert.match(html, /Ask AI \(≈\$0\.01–0\.03\)/);
  assert.match(html, /not investment advice; you decide and trade manually/);
});

test("sizing: null sizing asks for spot capital; sell, on-target and unknown holding", () => {
  assert.match(renderSizing(coin({ sizing: null })), /Set your spot capital/);
  assert.match(renderSizing(coin({ sizing: { method: "equal_weight", weight: 0.14, target_usdt: null, current_usdt: null, delta_usdt: null, delta_qty: null, risk_to_trend_line_usdt: null } })), /Set your spot capital/);
  assert.deepEqual(sizingLine({ target_usdt: 0, current_usdt: 120, delta_usdt: -120, delta_qty: -0.0014 }, "BTC"), { kind: "sell", text: "Sell 120.00 USDT ≈ 0.0014 BTC" });
  assert.equal(sizingLine({ target_usdt: 100, delta_usdt: 0 }, "BTC").kind, "none");
  assert.equal(sizingLine({ target_usdt: 100, delta_usdt: null }, "BTC").kind, "unknown");
  const cash = renderSizing(coin({ state: "CASH", sizing: { method: "inverse_vol", weight: 0.2, target_usdt: 0, current_usdt: 50, delta_usdt: -50, delta_qty: -0.0006, risk_to_trend_line_usdt: 0 } }));
  assert.match(cash, /rule holds cash/);
  assert.match(cash, /Inverse volatility/);
  assert.doesNotMatch(cash, /Risk to trend line/);   // no exposure, no risk line
});

test("number formatting helpers", () => {
  assert.equal(fmtUsdt(1234.5), "1,234.50 USDT");
  assert.equal(fmtUsdt(null), "—");
  assert.equal(fmtQty(0.000509), "0.000509");
  assert.equal(fmtQty(0.0000001234), "0.00000012");
  assert.equal(fmtQty(1234.56789), "1,234.57");
  assert.match(pctChange(-0.034), /pnl--neg"><span aria-hidden="true">▼<\/span> −3\.40%/);
  assert.match(pctChange(null), /—/);
});

test("rule reason explains WATCH and CASH", () => {
  assert.match(ruleReason(coin({ state: "WATCH", ret_60d: 0.02, dist_sma100: -0.01 })), /conditions disagree/);
  assert.match(ruleReason(coin({ state: "CASH", ret_60d: -0.1, dist_sma100: -0.05 })), /Both conditions fail: 60-day return −10\.0% and close 5\.0% below/);
  assert.match(ruleReason(coin({ ret_60d: null })), /Not enough closed daily bars/);
});

// ---------------------------------------------------------------- AI (Luna)

test("AI card: no analysis, blocked analysis, full analysis", () => {
  const none = renderAiCard(null, { base: "BTC" });
  assert.match(none, /No AI analysis for BTC yet/);
  assert.match(none, /AI: no view yet/);
  assert.match(none, /data-cot-ask/);
  const blocked = renderAiCard(null, { blockedReason: "daily AI budget reached", base: "BTC" });
  assert.match(blocked, /AI unavailable: daily AI budget reached/);
  const full = renderAiCard(latestAnalysis(detail().analyses), { pricingFallback: true });
  assert.match(full, /AI: caution · low/);
  assert.match(full, /Bull case[^]*Higher lows/);
  assert.match(full, /Bear case[^]*Volume fading/);
  assert.match(full, /Support<\/dt><dd>80,000\.00/);
  assert.match(full, /Invalidation<\/dt><dd>76,500\.00/);
  assert.match(full, /What would change its mind:<\/strong> A close above 88k/);
  assert.match(full, /\$0\.0120\*/);
  assert.match(full, /AI prices are placeholders/);
  // last_ai summary is shown when there is no full analysis
  assert.match(renderAiCard(null, { lastAi: coin().last_ai }), /AI agrees · medium[^]*แนวโน้มยังดี/);
});

test("latestAnalysis picks the newest per source", () => {
  assert.equal(latestAnalysis(detail().analyses, "luna").stance, "caution");
  assert.equal(latestAnalysis(detail().analyses, "jev").as_of, "2026-09-29T00:05:00Z");
  assert.equal(latestAnalysis([], "luna"), null);
});

// ---------------------------------------------------------------- Jev

test("Jev chip row: bars, agreement and timing; hidden when jev is null", () => {
  const row = renderJevRow(coin().jev);
  assert.match(row, /aria-label="Trend 75 of 100"/);
  assert.match(row, /aria-label="Reversal 25 of 100"/);
  assert.match(row, /Jev agrees/);
  assert.match(row, /Entry: wait for pullback/);
  assert.equal(renderJevRow(null), "");
  assert.doesNotMatch(renderCoinCard(coin({ jev: null })), /cot-jev/);
});

test("Jev panel: history from source=jev analyses, null state, analyses list labelled by source", () => {
  const history = jevHistory(detail().analyses);
  assert.deepEqual(history.map((p) => p.trend), [0.6, 0.75]);
  const panel = renderJevPanel(detail().jev, detail().analyses);
  assert.match(panel, /Jev scores/);
  assert.match(panel, /cot-jev-line--trend/);
  assert.match(panel, /cot-jev-line--risk/);
  assert.match(panel, /overextended/);
  assert.match(renderJevPanel(null, []), /No Jev score yet/);
  const list = renderAnalysesList(detail().analyses);
  assert.equal((list.match(/cot-source--jev">Jev/g) || []).length, 2);
  assert.equal((list.match(/cot-source--luna">Luna/g) || []).length, 1);
  assert.ok(list.indexOf("2026-09-29") < list.indexOf("2026-09-20"));   // newest first
});

// ---------------------------------------------------------------- scorecard

const scorecard = (rows, extra = {}) => ({ as_of: "2026-09-29T01:00:00Z", horizons: [7, 30], rows,
  cost: { jev_usd: 0.1234, luna_usd: 0.41, calls: { jev: 84, luna: 12 } }, note_pricing_fallback: true, ...extra });

test("scorecard: small n says 'too few samples', never a verdict; cost per source and pricing note", () => {
  const html = renderScorecard(scorecard([
    { source: "jev", stance: "agree", n_7d: 12, avg_ret_7d: 0.02, hit_7d: 0.6, n_30d: 4, avg_ret_30d: 0.05, hit_30d: 0.75 },
    { source: "jev", stance: "disagree", n_7d: 3, avg_ret_7d: -0.01, hit_7d: 0.3, n_30d: 1, avg_ret_30d: -0.02, hit_30d: 0 },
  ]));
  assert.equal((html.match(/too few samples<\/td>/g) || []).length, 4);
  assert.match(html, /too few samples for a verdict \(agree n=4, disagree n=1\)/);
  assert.doesNotMatch(html, /\+2\.0%/);   // no averages shown for small buckets
  assert.match(html, /\$0\.1234\*/);
  assert.match(html, /84 calls/);
  assert.match(html, /AI prices are placeholders, set real prices in Settings › Cost &amp; Budgets/);
});

test("scorecard: enough samples shows averages, hit rate and agree − disagree", () => {
  const rows = [
    { source: "luna", stance: "agree", n_7d: 25, avg_ret_7d: 0.03, hit_7d: 0.64, n_30d: 22, avg_ret_30d: 0.08, hit_30d: 0.7 },
    { source: "luna", stance: "disagree", n_7d: 21, avg_ret_7d: -0.01, hit_7d: 0.4, n_30d: 20, avg_ret_30d: -0.02, hit_30d: 0.35 },
  ];
  const html = renderScorecard(scorecard(rows, { note_pricing_fallback: false }));
  assert.match(html, /▲ <\/span>\+3\.0%/);
  assert.match(html, /64%/);
  assert.match(html, /agree − disagree \(30d\): \+10\.0%/);
  assert.doesNotMatch(html, /placeholders/);
  assert.equal(agreeMinusDisagree(rows, "luna", 7).value.toFixed(2), "0.04");
  assert.match(renderScorecard(scorecard([])), /No scored stances yet/);
});

// ---------------------------------------------------------------- chart data

test("chart markers: ▲BUY at entries and ▼SELL with the return at exits, snapped to candles", () => {
  const d = detail();
  const bars = candleBars(d.candles);
  const times = bars.map((b) => b.time);
  assert.equal(toUnix("2026-05-29"), T0);
  const markers = tradeMarkers(d.trades, times);
  assert.deepEqual(markers.map((m) => m.text), ["BUY", "SELL +2.2%", "BUY · open"]);
  assert.deepEqual(markers.map((m) => m.shape), ["arrowUp", "arrowDown", "arrowUp"]);
  assert.equal(markers[0].time, T0);
  assert.equal(markers[1].time, T0 + DAY);
  // an entry before the chart window is not drawn; a later time snaps to the last bar
  assert.equal(snapToBar(T0 - DAY, times), null);
  assert.equal(snapToBar(T0 + 10 * DAY, times), T0 + 2 * DAY);
  assert.deepEqual(tradeMarkers([{ entry_at: "2020-01-01", exit_at: "2020-02-01", return_pct: -0.1 }], times), []);
});

test("chart data: candles/lines are sorted and de-duplicated; HOLD shading; levels with dashed AI lines", () => {
  assert.deepEqual(candleBars([[2, 1, 2, 0.5, 1.5, 9], [1, 1, 1, 1, 1, 1], [2, 9, 9, 9, 9, 9], [3, null, 1, 1, 1, 1]]).map((b) => b.time), [1, 2]);
  assert.deepEqual(linePoints([[3, 1], [1, 2], ["x", 3]]).map((p) => p.time), [1, 3]);
  const shade = holdShading(candleBars(detail().candles), detail().periods);
  assert.deepEqual(shade.map((s) => s.value), [1, 1, 0]);
  const levels = chartLevels({ trendLineNext: 80120.4, avgPrice: 84000, keyLevels: detail().analyses[0].key_levels });
  assert.deepEqual(levels.map((l) => `${l.kind}:${l.style}`), ["trend:solid", "avg:solid", "support:dashed", "resistance:dashed", "invalidation:dashed"]);
  assert.deepEqual(chartLevels({ trendLineNext: null, avgPrice: null }), []);
  assert.match(equitySummary(detail().equity, detail().rule), /^Rule ×1\.84 vs buy & hold ×1\.52 · CAGR \+41% vs \+29% · max drawdown −33% vs −52% · in market 56% of days · 18 switches$/);
});

// ---------------------------------------------------------------- journal and trades

test("journal list shows outcomes honestly; the form body is validated", () => {
  const html = renderJournalList([
    ...detail().journal,
    { id: "j2", at: "2026-09-20T08:00:00Z", action: "skip", price: null, amount_usdt: null, note: HOSTILE, rule_state: "HOLD", ai_stance: null, price_now: 84210.5, change_since: null },
  ]);
  assert.match(html, /I bought/);
  assert.match(html, /rule agreed/);
  assert.match(html, /now 84,210\.50 <span class="pnl pnl--pos"><span aria-hidden="true">▲<\/span> \+0\.25%/);
  assert.match(html, /against the rule/);
  assert.match(html, /no outcome \(no entry price\)/);
  assert.doesNotMatch(html, /<img/);
  assert.equal(ruleAgreed({ action: "sell", rule_state: "CASH" }), true);
  assert.equal(ruleAgreed({ action: "buy", rule_state: "WATCH" }), false);
  assert.equal(ruleAgreed({ action: "buy", rule_state: null }), null);
  assert.equal(ruleAgreed({ action: "buy", rule_state: "HOLD", rule_agreed: false }), false);
  assert.deepEqual(journalEntry("BTC", { action: "buy", price: "84000", amount_usdt: "", note: "x" }),
    { ok: true, entry: { coin: "BTC", action: "buy", price: 84000, amount_usdt: null, note: "x" } });
  assert.equal(journalEntry("BTC", { action: "", price: "" }).ok, false);
  assert.equal(journalEntry("BTC", { action: "sell", price: "-1" }).ok, false);
  assert.equal(journalEntry("BTC", { action: "hold", note: "n".repeat(900) }).entry.note.length, 500);
});

test("trade history table: newest first, open trade marked, returns signed", () => {
  const html = renderTradesTable(detail().trades);
  assert.match(html, /2 trades · 1\/1 winners/);
  assert.match(html, /cot-open-tag">open/);
  assert.match(html, /▲<\/span> \+2\.2%/);
  assert.ok(html.indexOf("cot-trade--open") < html.indexOf("2026-05-30"));
  assert.match(renderTradesTable([]), /no round trips/);
});

// ---------------------------------------------------------------- holdings

test("holdings totals and rows: P&L with signs, source chips, unknown cost basis, alignment warning", () => {
  const h = holdings();
  const totals = renderHoldingsTotals(h.totals);
  assert.match(totals, /1,234\.50 USDT/);
  assert.match(totals, /▲<\/span> \+134\.50 USDT/);
  assert.match(totals, /\+12\.23%/);
  assert.match(totals, /250\.00 USDT/);
  const sei = renderHoldingRow(h.holdings[1], 1);
  assert.match(sei, /You hold SEI but the rule says CASH\./);
  assert.match(sei, /cot-hold--warn/);
  assert.match(sei, /cot-src--manual">Manual/);
  assert.match(sei, /▼<\/span> −88\.00 USDT/);
  assert.match(sei, /data-hold-delete="m-7"/);
  const sui = renderHoldingRow(h.holdings[2], 2);
  assert.match(sui, /cost unknown/);
  assert.match(sui, /Avg entry<\/dt><dd>unknown/);
  assert.match(sui, /no trades explain the balance/);
  assert.doesNotMatch(sui, /data-hold-delete/);
  assert.equal(alignmentWarning({ base: "ENA", alignment: "holding_in_cash_state", rule_state: "WATCH" }).text,
    "You hold ENA but the rule says WATCH (the rule holds nothing until the trend confirms).");
  assert.equal(alignmentWarning({ base: "NEAR", alignment: "not_held_in_hold_state" }).text, "The rule holds NEAR but you don't.");
  assert.equal(alignmentWarning({ alignment: null }), null);
});

test("allocation shares include cash and sum to 1", () => {
  const shares = allocationShares(holdings());
  assert.equal(shares[0].label, "SEI");
  assert.ok(shares.some((s) => s.label === "Cash"));
  assert.equal(Number(shares.reduce((sum, s) => sum + s.share, 0).toFixed(10)), 1);
  assert.match(renderHoldingsData(holdings()), /role="img" aria-label="Allocation: SEI/);
  assert.match(renderHoldingsData(holdings({ holdings: [], totals: {}, cash: [] })), /No holdings yet/);
});

test("Gate sync panel: status, not-configured guide, error text", () => {
  const ok = renderSyncPanel(holdings().sources.gate);
  assert.match(ok, /Gate: OK/);
  assert.match(ok, /data-hold-sync/);
  assert.match(ok, /data-hold-validate/);
  assert.doesNotMatch(ok, /READ-ONLY spot/);
  const guide = renderSyncPanel({ account_id: null, synced_at: null, status: "NOT_CONFIGURED", error: null });
  assert.match(guide, /never synced/);
  assert.match(guide, /READ-ONLY spot<\/strong> permission/);
  assert.match(guide, /href="#\/settings">Settings › Exchange Accounts/);
  const err = renderSyncPanel({ status: "ERROR", error: `auth failed ${HOSTILE}` });
  assert.match(err, /status-pill--bad/);
  assert.doesNotMatch(err, /<img/);
  assert.match(renderSyncPanel(holdings().sources.gate, { validatedAt: "2026-09-29T13:00:00Z" }), /Key last validated/);
  assert.match(renderHoldingsData(holdings()), /Share my holding of a coin with its AI review/);
});

test("validate key: no_key, ok and auth failure (error code escaped)", () => {
  const noKey = renderValidation({ ok: false, reason: "no_key", checks: { credentials_present: false, auth: null, spot_read: null },
    balances_nonzero: null, key_hint: null, checked_at: "2026-09-29T13:00:00Z", read_only_note: "Read-only check; nothing is traded." });
  assert.match(noKey, /No key yet\. Add a READ-ONLY spot key in <a href="#\/settings">Settings › Exchange Accounts<\/a>/);
  assert.match(noKey, /gate-fail[^]*✗[^]*Key stored/);
  assert.match(noKey, /gate-unknown[^]*–[^]*Auth/);
  assert.match(noKey, /Read-only check; nothing is traded\./);
  const ok = renderValidation({ ok: true, checks: { credentials_present: true, auth: true, spot_read: true }, balances_nonzero: 5,
    key_hint: "…ab12", checked_at: "2026-09-29T13:00:00Z", read_only_note: "GET only" });
  assert.equal((ok.match(/gate-pass/g) || []).length, 3);
  assert.match(ok, /5 assets with balance/);
  assert.match(ok, /Key works \(key …ab12\)/);
  const fail = renderValidation({ ok: false, reason: "auth_failed", checks: { credentials_present: true, auth: false, spot_read: null },
    balances_nonzero: null, error_code: `INVALID_KEY${HOSTILE}`, read_only_note: null });
  assert.match(fail, /Validation failed: auth_failed · code INVALID_KEY&lt;img/);
  assert.doesNotMatch(fail, /<img/);
  assert.equal(renderValidation(null), "");
});

test("manual holding validation mirrors the server rules", () => {
  assert.deepEqual(validateHolding({ base: " near ", qty: "50", avg_price: "4.21", note: "", opened_at: "" }), { ok: true, entry: { base: "NEAR", qty: 50, avg_price: 4.21 } });
  assert.equal(validateHolding({ base: "NE-AR", qty: 1, avg_price: 1 }).ok, false);
  assert.equal(validateHolding({ base: "NEAR", qty: 0, avg_price: 1 }).ok, false);
  assert.equal(validateHolding({ base: "NEAR", qty: 1, avg_price: -1 }).ok, false);
  assert.equal(validateHolding({ id: "m-7", base: "SEI", qty: 1, avg_price: 1 }).entry.id, "m-7");
});

// ---------------------------------------------------------------- ladder, CDC and evidence (Addendum D)

const ladderCoin = (state, action, extra = {}) => coin({
  base: "NEAR", price: 5,
  ladder: { state, action: { type: action, target_weight: { OUT: 0, STARTER: 0.5, FULL: 2 }[state], delta_usdt: 71.43, delta_qty: 14.286, add_above: 5.4, trim_below: 4.7, exit_below: 4.4 } },
  trend_line_next: 4.4,
  ...extra,
});

test("ladder badge per state with the Thai hint and the action delta", () => {
  const starter = renderLadderRow(ladderCoin("STARTER", "BUY_STARTER"));
  assert.match(starter, /STARTER \(½\)/);
  assert.match(starter, /BUY STARTER/);
  assert.match(starter, /Buy 71\.43 USDT ≈ 14\.286 NEAR/);
  assert.match(starter, new RegExp(LADDER_HINT_TH.STARTER));
  const full = renderLadderRow(ladderCoin("FULL", "ADD"));
  assert.match(full, /FULL \(2×\)/);
  assert.match(full, /cot-ladder-action--add/);
  assert.match(full, /เทรนด์แรง เติมเต็มไม้/);
  const trim = renderLadderRow(ladderCoin("STARTER", "TRIM", { ladder: { state: "STARTER", action: "TRIM", delta_usdt: -60, delta_qty: -12 } }));
  assert.match(trim, /cot-ladder-action--trim/);
  assert.match(trim, /Sell 60\.00 USDT ≈ 12 NEAR/);
  assert.match(trim, /ลดกลับครึ่งไม้ เทรนด์ยังขึ้น/);
  const out = renderLadderRow(ladderCoin("OUT", "SELL_ALL"));
  assert.match(out, /cot-ladder-state--out[^>]*><span aria-hidden="true">○<\/span> OUT</);
  assert.match(out, /SELL ALL/);
  assert.match(out, /ถือเงินสด/);
  const hold = renderLadderRow(coin({ ladder: { state: "FULL", action: { type: "HOLD", delta_usdt: null } } }));
  assert.match(hold, /cot-ladder-action--hold/);
  assert.match(hold, /size — set your spot capital/);
  assert.equal(renderLadderRow(coin({ ladder: null })), "");
  assert.match(renderCoinCard(ladderCoin("FULL", "ADD")), /FULL \(2×\)/);
});

test("ladder detail and chart overlay levels: add above / trim below / exit below, exit replaces the trend line", () => {
  const c = ladderCoin("STARTER", "HOLD");
  assert.equal(ladderOf(c).addAbove, 5.4);
  const levels = ladderLevels(c);
  assert.deepEqual(levels.map((l) => `${l.kind}:${l.style}:${l.title}`), ["add:dashed:Add above 5.40", "trim:dashed:Trim below 4.70", "exit:dashed:Exit below 4.40"]);
  const all = chartLevels({ trendLineNext: 4.4, ladder: levels });
  assert.deepEqual(all.map((l) => l.kind), ["add", "trim", "exit"]);   // no duplicate trend line at the exit level
  assert.deepEqual(chartLevels({ trendLineNext: 4.3, ladder: levels }).map((l) => l.kind), ["trend", "add", "trim", "exit"]);
  const caption = priceChartSummary(detail(), all);
  assert.match(caption, /Add above 5\.40 · Trim below 4\.70 · Exit below 4\.40$/);   // price shown once per level
  const detailHtml = renderLadderDetail(c);
  assert.match(detailHtml, /Add above<\/dt><dd>5\.40 <span class="muted">\(\+8\.0% away\)/);
  assert.match(detailHtml, /Exit below<\/dt><dd>4\.40 <span class="muted">\(−12\.0% away\)/);
  assert.match(detailHtml, /0\.5 slots/);
  // levels on the ladder object directly, and under `levels`
  assert.equal(ladderOf(coin({ ladder: { state: "FULL", action: "HOLD", levels: { add_above: 1, trim_below: 2 } } })).trimBelow, 2);
});

test("ladder transition markers B/A/T/S and the CDC ribbon", () => {
  const d = detail({ ladder_transitions: [
    { at: T0, action: "BUY_STARTER" }, { at: T0 + DAY, action: "ADD" }, { at: T0 + 2 * DAY, action: "TRIM" }, { at: T0 + 2 * DAY, action: "SELL_ALL" }, { at: T0, action: "HOLD" },
  ], cdc_series: [[T0, "green"], [T0 + DAY, "blue"], [T0 + 2 * DAY, "purple"]] });
  const times = candleBars(d.candles).map((b) => b.time);
  const markers = ladderMarkers(ladderTransitions(d), times);
  assert.deepEqual(markers.map((m) => m.text), ["B", "A", "T", "S"]);
  assert.deepEqual(markers.map((m) => m.position), ["belowBar", "belowBar", "aboveBar", "aboveBar"]);
  assert.deepEqual(cdcRibbon(d).map((r) => r.zone), ["green", "blue"]);   // unknown zones dropped
  assert.match(cdcChip({ zone: "yellow", since: "2026-09-20" }), /CDC 1D: yellow \(bear, bouncing\) since 2026-09-20/);
  assert.match(cdcChip({ zone: "green" }), /\(reference\)/);
  assert.equal(cdcChip(null), "");
});

test("evidence card: table, insufficient_history, missing strategy", () => {
  const s = (trades, win, total, dd, sharpe) => ({ trades, win_rate: win, avg_win_pct: 0.1, avg_loss_pct: -0.05, total_return_pct: total, max_dd_pct: dd, sharpe, time_in_market_pct: 0.5 });
  const html = renderEvidence({ bars: 1000, from: "2023-12-01", to: "2026-09-28", insufficient_history: false,
    ladder: s(21, 0.52, 1.85, 0.355, 1.37), rule: s(18, 0.5, 1.2, 0.415, 1.22), cdc_1d: s(40, 0.4, 0.9, 0.495, 1.06), buy_hold: s(1, null, 0.4, 0.79, 0.51) });
  assert.match(html, /What would have happened on this coin \(past, not a promise\)/);
  assert.match(html, /1000 daily bars · 2023-12-01 → 2026-09-28/);
  assert.match(html, /Ladder<\/th>[^]*21[^]*52%[^]*\+185\.0%[^]*−35\.5%[^]*1\.37/);
  assert.match(html, /CDC 1D/);
  assert.match(html, /Buy &amp; hold<\/th>[^]*—/);
  const short = renderEvidence({ bars: 200, from: "2026-03-01", to: "2026-09-28", insufficient_history: true });
  assert.match(short, /Not enough history \(200 of 365 daily bars needed\) — no verdict\./);
  assert.doesNotMatch(short, /<table/);
  assert.match(renderEvidence(null), /No evidence computed yet/);
  assert.match(renderEvidence({ bars: 500, strategies: { ladder: s(3, 1, 0.2, 0.1, 2) } }), /Rule \(hold\/cash\)<\/th><td colspan="5"/);
});

// ---------------------------------------------------------------- escaping

test("every interpolated string is escaped", () => {
  const evil = coin({
    base: HOSTILE, symbol: HOSTILE, state: HOSTILE,
    action: { type: HOSTILE, text: HOSTILE, fresh: true, trigger: { kind: "close_above", price: 1, distance_pct: 0.1 } },
    last_ai: { stance: HOSTILE, conviction: HOSTILE, summary_th: HOSTILE },
    jev: { trend_strength: 0.5, reversal_risk: 0.5, rule_agreement: HOSTILE, entry_timing: HOSTILE, key_risk: HOSTILE, trend_regime: HOSTILE },
    ladder: { state: HOSTILE, action: { type: HOSTILE, text: HOSTILE } },
    cdc: { zone: HOSTILE },
  });
  const outputs = [
    renderCoinCard(evil),
    renderDecisionCard(evil, { analysis: { stance: "agree", summary_th: HOSTILE, bull_points: [HOSTILE], bear_points: [HOSTILE], risks: [HOSTILE], change_my_mind: HOSTILE, model: HOSTILE, trigger: HOSTILE, key_levels: { support: [HOSTILE] } }, blockedReason: HOSTILE }),
    renderBriefing({ stance_market: HOSTILE, summary_th: HOSTILE, highlights: [{ coin: HOSTILE, note: HOSTILE }] }),
    renderRegimeStrip({ regime: { label: HOSTILE, btc_state: HOSTILE }, next_close: HOSTILE, ai: { blocked_reason: HOSTILE } }),
    renderJevPanel(evil.jev, [{ source: "jev", as_of: HOSTILE, answers: { trend_regime: HOSTILE } }]),
    renderAnalysesList([{ source: HOSTILE, as_of: "2026-09-01", trigger: HOSTILE, summary_th: HOSTILE }]),
    renderScorecard({ rows: [{ source: HOSTILE, stance: HOSTILE, n_7d: 30, avg_ret_7d: 0.1, hit_7d: 0.5 }], cost: {} }),
    renderJournalList([{ at: HOSTILE, action: HOSTILE, note: HOSTILE, rule_state: HOSTILE, ai_stance: HOSTILE, price: 1, price_now: 2, change_since: 1 }]),
    renderTradesTable([{ entry_at: HOSTILE, exit_at: HOSTILE, entry_price: 1, exit_price: 2, return_pct: 1, days: HOSTILE }]),
    renderHoldingRow({ base: HOSTILE, qty_source: HOSTILE, avg_source: HOSTILE, rule_state: HOSTILE, alignment: "holding_in_cash_state", cost_basis_note: HOSTILE, manual_id: HOSTILE }),
    renderHoldingsData(holdings({ sources: { gate: { status: HOSTILE, account_id: HOSTILE, error: HOSTILE } }, cash: [{ currency: HOSTILE, qty: 1 }] })),
    renderValidation({ reason: HOSTILE, key_hint: HOSTILE, read_only_note: HOSTILE, checks: {}, error_code: HOSTILE }),
    renderLadderDetail(evil),
    renderEvidence({ bars: HOSTILE, from: HOSTILE, to: HOSTILE, ladder: { trades: HOSTILE } }),
    cdcChip({ zone: "green", since: HOSTILE }),
    cotSpark([[1, 1], [2, 2]], 1.5, { label: HOSTILE }),
  ];
  for (const html of outputs) {
    assert.doesNotMatch(html, /<img/, html.slice(0, 200));
    assert.doesNotMatch(html, /undefined/);
  }
});
