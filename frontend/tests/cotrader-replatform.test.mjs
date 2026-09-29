// Co-Trader re-platform: the spot-only navigation, the Thai "what next" summary built from the
// ladder, and the watchlist editor helpers. Pure functions only; offline.
import test from "node:test";
import assert from "node:assert/strict";

import { renderNav } from "../modules/components/nav.js";
import { nearestTriggers, nextSteps, renderNextStepsTh } from "../modules/components/cotraderSummary.js";
import { addedMessage, normaliseSymbol, renderWatchlist } from "../modules/views/cotraderWatchlist.js";

const HOSTILE = '<img src=x onerror="alert(1)">';

function coin(base, state, action = "HOLD", { fresh = false, price = 100, trim = null, add = null, exit = null, fraction = 0.1 } = {}) {
  return {
    base, symbol: `${base}_USDT`, price,
    ladder: { state, action: { type: action, fresh, trim_below: trim, add_above: add, exit_below: exit, capital_fraction: fraction } },
  };
}

test("co-trader navigation shows only the spot essentials and hides the futures lab", () => {
  const html = renderNav("cotrader", { mode: "cotrader", params: [] });
  assert.match(html, /Spot Co-Trader/);
  assert.doesNotMatch(html.split("<details")[0], /[◎★⚙]/); // SVG icons, no glyph icons
  const primary = html.split("<details")[0];
  assert.deepEqual([...primary.matchAll(/class="nav-label">([^<]+)</g)].map((m) => m[1]), ["Signals", "Watchlist", "AI · Settings"]);
  assert.match(html, /<details class="sidebar-group sidebar-group--lab">/); // collapsed
  assert.doesNotMatch(primary, /Experiments|Portfolio|>Trade</);
  const watch = renderNav("cotrader", { mode: "cotrader", params: ["watchlist"] });
  assert.match(watch, /href="#\/cotrader\/watchlist" class="nav-link nav-link--active"/);
  assert.match(renderNav("experiments", { mode: "cotrader" }), /sidebar-group--lab" open/);
  assert.match(renderNav("overview"), /PORTFOLIO OS/); // lab servers keep the old navigation
});

test("next steps: fresh actions become Thai to-dos with amounts; holds and cash are grouped", () => {
  const d = {
    settings: { cotrader_capital_usdt: 1000 },
    coins: [
      coin("NEAR", "FULL", "ADD", { fresh: true, fraction: 0.2 }),
      coin("SUI", "STARTER", "TRIM", { fresh: true, fraction: 0.05 }),
      coin("BTC", "FULL", "HOLD", { fraction: 0.2 }),
      coin("SEI", "OUT", "SELL_ALL", { fresh: false }),
    ],
  };
  const s = nextSteps(d);
  assert.equal(s.todo.length, 2);
  assert.match(s.todo[0], /^เติม NEAR เป็นเต็มไม้/);
  assert.match(s.todo[0], /200\.00 USDT/);
  assert.match(s.todo[1], /^ลด SUI กลับเหลือครึ่งไม้/);
  assert.deepEqual(s.hold.FULL.map((t) => t.split(" ")[0]), ["NEAR", "BTC"]);
  assert.deepEqual(s.out, ["SEI"]);
  const html = renderNextStepsTh(d);
  assert.match(html, /ทำอะไรต่อ/);
  assert.match(html, /วันนี้มีสิ่งที่ต้องทำ/);
  assert.doesNotMatch(html, /ใส่เงินทุน Spot/);
});

test("next steps with nothing fresh says hold, and asks for capital when it is missing", () => {
  const html = renderNextStepsTh({ settings: {}, coins: [coin("BTC", "FULL"), coin("ETH", "OUT")] });
  assert.match(html, /วันนี้ไม่ต้องซื้อขายเพิ่ม/);
  assert.match(html, /ใส่เงินทุน Spot/);
  assert.doesNotMatch(html, /≈/);
  assert.equal(renderNextStepsTh({ coins: [] }), "");
});

test("nearest triggers pick the closest trim / add / exit levels by distance", () => {
  const coins = [
    coin("A", "FULL", "HOLD", { price: 100, trim: 95, exit: 60 }),
    coin("B", "STARTER", "HOLD", { price: 10, add: 10.2, exit: 8 }),
    coin("C", "OUT", "HOLD", { price: 1, add: 1.5 }),
  ];
  const t = nearestTriggers(coins, 3);
  assert.deepEqual(t.map((x) => `${x.base}:${x.kind}`), ["B:add", "A:trim", "B:exit"]);
  assert.ok(Math.abs(t[0].move - 0.02) < 1e-9);
});

test("the summary escapes coin names", () => {
  const html = renderNextStepsTh({ coins: [coin(HOSTILE, "FULL", "ADD", { fresh: true })] });
  assert.doesNotMatch(html, /<img/);
});

test("watchlist helpers normalise symbols, render rows safely and explain warnings in Thai", () => {
  assert.equal(normaliseSymbol(" near "), "NEAR");
  assert.equal(normaliseSymbol("sol_usdt"), "SOL");
  assert.equal(normaliseSymbol("PEPEUSDT"), "PEPE");
  assert.equal(normaliseSymbol("BTC/USDT"), "BTC");
  assert.equal(normaliseSymbol("<b>"), null);
  assert.equal(normaliseSymbol(""), null);
  const html = renderWatchlist(["BTC", "NEAR"], { NEAR: { ladder: { state: "FULL" } } });
  assert.match(html, /data-watch-remove="NEAR"/);
  assert.match(html, /FULL/);
  assert.match(renderWatchlist(["BTC"]), /disabled/); // the last coin cannot be removed
  const msg = addedMessage({ base: "NEWC", thin: true, quote_volume_24h_usdt: 120000, history_ok: false, daily_bars: 90 });
  assert.match(msg, /เพิ่ม NEWC แล้ว/);
  assert.match(msg, /\$120,000/);
  assert.match(msg, /90 วัน/);
});

// ---------------------------------------------------------------- MYH layout: coin list + icons
import { nextLevel, renderCoinTable, sortCoins, verdictOf, verdictPill } from "../modules/components/cotraderTable.js";
import { ICON_NAMES, icon } from "../modules/components/icons.js";

test("coin list: exits then buys first, next level is the nearest change, rows are links and escaped", () => {
  const coins = [
    coin("OUTC", "OUT", "HOLD", { price: 10, add: 11 }),
    coin("FULLC", "FULL", "HOLD", { price: 100, trim: 90, exit: 70 }),
    coin("NEWC", "STARTER", "BUY_STARTER", { fresh: true, price: 5, add: 5.5, exit: 4 }),
    coin("EXITC", "OUT", "SELL_ALL", { fresh: true, price: 3 }),
  ];
  assert.deepEqual(sortCoins(coins).map((c) => c.base), ["EXITC", "NEWC", "FULLC", "OUTC"]);
  assert.deepEqual(nextLevel(coins[1]).kind, "trim");
  assert.equal(nextLevel(coins[0]).kind, "add");
  assert.ok(Math.abs(nextLevel(coins[2]).move - 0.1) < 1e-9); // add at +10% is nearer than exit at -20%
  const html = renderCoinTable(coins);
  assert.match(html, /href="#\/cotrader\/NEWC"[^]*cot-verdict--buy[^]*เริ่มซื้อ/);
  assert.match(html, /href="#\/cotrader\/EXITC"[^]*cot-verdict--sell[^]*ขายออก/);
  assert.match(html, /Trim below[^]*90\.00/);
  assert.match(html, /No Jev score yet/);
  assert.doesNotMatch(renderCoinTable([coin(HOSTILE, "FULL")]), /<img/);
  assert.match(renderCoinTable([]), /No coins yet/);
});

test("verdict: plain Thai buy / add / trim / sell / hold / wait, with a near-level warning", () => {
  const v = (c) => verdictOf(c);
  assert.equal(v(coin("A", "FULL", "ADD", { fresh: true })).th, "ซื้อเพิ่ม");
  assert.equal(v(coin("A", "STARTER", "TRIM", { fresh: true })).th, "ลดครึ่ง");
  assert.equal(v(coin("A", "OUT", "SELL_ALL", { fresh: true })).th, "ขายออก");
  assert.equal(v(coin("A", "STARTER", "BUY_STARTER", { fresh: true })).th, "เริ่มซื้อ");
  const hold = v(coin("A", "FULL", "HOLD", { price: 100, trim: 80 }));
  assert.equal(hold.th, "ถือต่อ");
  assert.equal(hold.near, false);
  const nearTrim = v(coin("A", "FULL", "HOLD", { price: 100, trim: 98 }));
  assert.equal(nearTrim.near, true);
  assert.match(nearTrim.detail, /ใกล้จุดลด \(-2\.0%\)/);
  const wait = v({ ...coin("A", "OUT"), trend_line_next: 10.2, price: 10 });
  assert.equal(wait.th, "รอก่อน");
  assert.match(wait.detail, /ใกล้สัญญาณซื้อ/);
  assert.equal(verdictOf({ base: "X" }), null);
  assert.match(verdictPill(hold), /cot-verdict--hold[^>]*lang="th"><span aria-hidden="true">■<\/span> ถือต่อ/);
});

test("icons are one SVG family, decorative unless labelled", () => {
  for (const name of ICON_NAMES) assert.match(icon(name), /^<svg class="icon"[^>]*aria-hidden="true"/);
  assert.match(icon("refresh", { label: "Refresh" }), /role="img" aria-label="Refresh"/);
  assert.equal(icon("nope"), "");
});

// ---------------------------------------------------------------- ui-ux-pro-max: drill-down, bounded lists
import { filterCoins, normaliseView, renderCoinFilters } from "../modules/components/cotraderTable.js";
import { hashQuery } from "../modules/router.js";
import { renderJournalList, renderTradesTable } from "../modules/views/cotrader.js";

test("coin filters: Thai verdict groups with counts, active chip in the URL, clear link and an empty state", () => {
  const coins = [coin("A", "FULL"), coin("B", "STARTER"), coin("C", "OUT"), coin("D", "FULL", "ADD", { fresh: true }), coin("E", "STARTER", "TRIM", { fresh: true })];
  assert.deepEqual(filterCoins(coins, "hold").map((c) => c.base), ["A", "B"]);
  assert.deepEqual(filterCoins(coins, "buy").map((c) => c.base), ["D"]);
  assert.deepEqual(filterCoins(coins, "sell").map((c) => c.base), ["E"]);
  assert.deepEqual(filterCoins(coins, "wait").map((c) => c.base), ["C"]);
  assert.equal(normaliseView("bogus"), "all");
  const chips = renderCoinFilters(coins, "buy");
  assert.match(chips, /class="cot-chip cot-chip--buy is-active" href="#\/cotrader\?view=buy" lang="th" aria-current="true">ซื้อ \/ ซื้อเพิ่ม <span class="cot-chip-n">1</);
  assert.match(chips, /href="#\/cotrader" lang="th">ทั้งหมด <span class="cot-chip-n">5</);
  const table = renderCoinTable(coins, { view: "wait" });
  assert.match(table, /แสดง 1 จาก 5 เหรียญ · รอก่อน <a href="#\/cotrader">ล้างตัวกรอง<\/a>/);
  assert.doesNotMatch(table, /data-cot-coin="A"/);
  assert.match(renderCoinTable([coin("A", "FULL")], { view: "sell" }), /ตอนนี้ไม่มีเหรียญในกลุ่ม “ลด \/ ขายออก”/);
  assert.equal(hashQuery("#/cotrader?view=hold").get("view"), "hold");
  assert.equal(hashQuery("#/cotrader").get("view"), null);
});

test("next-steps items link to their coin", () => {
  const html = renderNextStepsTh({ coins: [coin("NEAR", "FULL", "ADD", { fresh: true, price: 5, trim: 4.9 })] });
  assert.match(html, /<a href="#\/cotrader\/NEAR">เติม NEAR/);
  assert.match(html, /<a href="#\/cotrader\/NEAR">NEAR: ถ้าปิดวันต่ำกว่า/);
});

test("long ledgers are bounded with a show-all control", () => {
  const trades = Array.from({ length: 13 }, (_, i) => ({ entry_at: 1780012800 + i * 86400, entry_price: 1, exit_at: 1780012800 + (i + 1) * 86400, exit_price: 1.1, return_pct: 0.1, days: 1 }));
  const html = renderTradesTable(trades);
  assert.equal((html.match(/cot-extra/g) || []).length, 3);
  assert.match(html, /data-cot-expand="trades" data-label="Show all 13 trades" aria-expanded="false">Show all 13 trades/);
  assert.doesNotMatch(renderTradesTable(trades.slice(0, 5)), /data-cot-expand/);
  const journal = Array.from({ length: 7 }, (_, i) => ({ id: `j${i}`, at: `2026-09-${10 + i}T00:00:00Z`, action: "hold" }));
  assert.match(renderJournalList(journal), /Show all 7 decisions/);
});
