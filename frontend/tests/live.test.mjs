// Live frames from the runtime stream update the open page in place; stale prices never overwrite.
import test from "node:test";
import assert from "node:assert/strict";

import { applyLiveFrame } from "../modules/live.js";
import { countdownText } from "../modules/components/motion.js";

function fakeDoc(keys, dayStart = null) {
  const els = Object.fromEntries(keys.map((k) => [k, { innerHTML: "old" }]));
  return {
    els,
    querySelectorAll(sel) {
      const m = /data-motion-key="([^"]+)"/.exec(sel);
      if (m) return els[m[1]] ? [els[m[1]]] : [];
      return [];
    },
    querySelector(sel) {
      if (sel === "[data-day-start]" && dayStart !== null) return { dataset: { dayStart: String(dayStart) } };
      return null;
    },
  };
}

test("a live frame updates equity, trading P&L, Today, and fresh position prices only", () => {
  const doc = fakeDoc(["kpi:equity", "kpi:trading", "today:equity", "today:pnl", "pos:perp:a:mark", "pos:perp:a:pnl", "pos:perp:b:mark"], 500);
  applyLiveFrame({
    portfolio: { total_equity_usdt: 501, trading_pnl_usdt: 1 },
    live: { equity_usdt: 503.25, next_decision_at: "2026-09-29T12:01:00Z", positions: [
      { position_ref: "perp:a", price: 84123.5, unrealized_pnl_usdt: 2.5, fresh: true },
      { position_ref: "perp:b", price: 1, unrealized_pnl_usdt: -9, fresh: false },
    ] },
  }, doc);
  assert.match(doc.els["kpi:equity"].innerHTML, /503\.25 USDT/);
  assert.match(doc.els["kpi:trading"].innerHTML, /\+3\.25 USDT/);      // 503.25 - (501 - 1)
  assert.match(doc.els["today:equity"].innerHTML, /503\.25 USDT/);
  assert.match(doc.els["today:pnl"].innerHTML, /\+3\.25 USDT/);         // vs 500 at 00:00 UTC
  assert.match(doc.els["pos:perp:a:mark"].innerHTML, /84,123\.5/);
  assert.match(doc.els["pos:perp:a:pnl"].innerHTML, /\+2\.50 USDT/);
  assert.equal(doc.els["pos:perp:b:mark"].innerHTML, "old");            // stale feed: keep the rendered value
});

test("frames without live data are ignored", () => {
  const doc = fakeDoc(["kpi:equity"]);
  applyLiveFrame({ portfolio: {} }, doc);
  applyLiveFrame(null, doc);
  assert.equal(doc.els["kpi:equity"].innerHTML, "old");
});

test("long countdowns switch to days", () => {
  const now = Date.parse("2026-09-29T10:00:00Z");
  assert.equal(countdownText("2026-10-06T12:30:00Z", now), "in 7d 2h 30m");
});
