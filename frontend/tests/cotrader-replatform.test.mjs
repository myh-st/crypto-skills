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
  assert.match(html, /SPOT CO-TRADER/);
  const primary = html.split("<details")[0];
  assert.deepEqual([...primary.matchAll(/class="nav-label">([^<]+)</g)].map((m) => m[1]), ["Signals", "Watchlist", "AI · Settings"]);
  assert.match(html, /<details class="sidebar-group sidebar-group--lab">/); // collapsed
  assert.doesNotMatch(primary, /Experiments|Portfolio|Trade/);
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
