// Motion helpers: number parsing/formatting keeps the original style; countdowns; skeletons; opt-in hooks.
import test from "node:test";
import assert from "node:assert/strict";

import { countdownText, formatLike, parseDisplayNumber, skeleton, applyMotion } from "../modules/components/motion.js";
import { renderToday } from "../modules/views/today.js";
import { barList, lineChart, calendarHeatmap } from "../modules/components/charts.js";

test("numbers keep their display style while tweening (sign, grouping, decimals)", () => {
  for (const [text, value, back] of [
    ["▼ −2.00 USDT", -2, "−2.00"], ["+22.6%", 22.6, "+22.6"], ["1,234.50 USDT", 1234.5, "1,234.50"], ["12", 12, "12"], ["-0.21 USDT", -0.21, "-0.21"],
  ]) {
    const style = parseDisplayNumber(text);
    assert.equal(style.value, value, text);
    assert.equal(formatLike(value, style), back, text);
  }
  const style = parseDisplayNumber("+1.50 USDT");
  assert.equal(formatLike(-0.25, style), "-0.25");     // crossing zero keeps a readable sign
  assert.equal(parseDisplayNumber("—"), null);
});

test("countdown text ticks down to 'now'", () => {
  const now = Date.parse("2026-09-29T10:00:00Z");
  assert.equal(countdownText("2026-09-29T12:01:05Z", now), "in 2h 01m 05s");
  assert.equal(countdownText("2026-09-29T10:03:09Z", now), "in 3m 09s");
  assert.equal(countdownText("2026-09-29T09:00:00Z", now), "now");
  assert.equal(countdownText("nope", now), "—");
});

test("skeletons are accessible loading placeholders", () => {
  const html = skeleton(3);
  assert.match(html, /aria-busy="true"/);
  assert.equal((html.match(/class="skeleton"/g) || []).length, 3);
});

test("views opt elements into motion with stable keys", () => {
  const html = renderToday({ label: "EXP-002", pnl_today_usdt: 1, pnl_today_pct: 0.002, day_start_equity_usdt: 500, equity_usdt: 501, pnl_total_usdt: 1,
    closed_today: {}, daily_loss_limit: {}, ai: {}, next_decision_at: "2026-09-29T12:01:00Z", trading_day_utc: "2026-09-29",
    intraday: [["2026-09-29T00:00:00Z", 500], ["2026-09-29T01:00:00Z", 501]], calendar: [{ date: "2026-09-29", pnl_usdt: 1, pnl_pct: 0.002, trades: 0 }], summary: {} });
  assert.match(html, /data-motion-key="today:pnl"/);
  assert.match(html, /data-motion-key="today:equity"/);
  assert.match(html, /data-countdown="2026-09-29T12:01:00Z"/);
  assert.match(html, /data-motion-fill="today:gauge"/);
  assert.match(html, /data-motion-enter="today:chart:2026-09-29"/);
  assert.match(barList([{ label: "a", value: 1 }], { motionKey: "k" }), /data-motion-fill="k"/);
  assert.match(lineChart([["2026-09-29T00:00:00Z", 1], ["2026-09-29T01:00:00Z", 2]], { motionKey: "c" }), /data-motion-enter="c"/);
  assert.match(calendarHeatmap([{ date: "2026-09-29", pnl_usdt: 1, pnl_pct: 0.01, trades: 1 }], { motionKey: "cal" }), /style="--i:0"/);
  applyMotion(null);   // a missing root is a no-op, never an error
});
