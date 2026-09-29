// Co-Trader coin charts on the vendored, pinned Lightweight Charts™ (same library and loading as
// liveChart.js). Daily candles with the rule's trend line, SMA100, shaded HOLD periods, ▲BUY/▼SELL
// markers from the rule's round trips, and price lines for the next trend-line level, the user's
// average entry and the AI key levels (dashed). A second chart compares the rule's equity with buy &
// hold. The data-shaping functions are pure and exported for tests; nothing is synthesized.
import { CHART_LIBRARY_URL } from "./liveChart.js";
import { num } from "./ui.js";
import { fmtPct, fmtPrice } from "./cotraderBits.js";
import { LADDER_ACTIONS, cdcRibbon, ladderLevels, ladderTransitions } from "./cotraderLadder.js";

/** A theme colour from the CSS custom properties (dark/light), with a fallback outside a browser. */
function themeVar(name, fallback) {
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  } catch {
    return fallback;
  }
}


const FALLBACK = {
  up: "#1f7a4d", down: "#a3352a", trend: "#2f6fed", sma: "#8a94a3", avg: "#6b21a8",
  support: "#1f7a4d", resistance: "#a3352a", invalidation: "#8a5a08", hold: "rgba(31, 122, 77, 0.08)",
  watch: "rgba(138, 90, 8, 0.06)", rule: "#2f6fed", buyHold: "#8a94a3",
  add: "#12803f", trim: "#c2871c", exit: "#a3352a",
  ladderBuy: "#1f7a4d", ladderAdd: "#0b6b33", ladderTrim: "#c2871c", ladderSell: "#a3352a",
};

/** Unix seconds from a number (seconds), a YYYY-MM-DD date (00:00 UTC) or an ISO timestamp. */
export function toUnix(value) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "number") return Number.isFinite(value) ? Math.floor(value) : null;
  const text = String(value);
  const ms = Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(text) ? `${text}T00:00:00Z` : text);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

/** `[[t, o, h, l, c, v]]` → candlestick bars, ascending and de-duplicated; malformed rows dropped. */
export function candleBars(candles) {
  const out = [];
  for (const row of candles || []) {
    const [t, o, h, l, c] = (row || []).map(num);
    if ([t, o, h, l, c].some((v) => v === null)) continue;
    out.push({ time: Math.floor(t), open: o, high: h, low: l, close: c });
  }
  return dedupe(out);
}

/** `[[t, value]]` → line points, ascending and de-duplicated. */
export function linePoints(points) {
  const out = [];
  for (const row of points || []) {
    const t = num(row?.[0]);
    const v = num(row?.[1]);
    if (t === null || v === null) continue;
    out.push({ time: Math.floor(t), value: v });
  }
  return dedupe(out);
}

function dedupe(rows) {
  rows.sort((a, b) => a.time - b.time);
  return rows.filter((row, i) => i === 0 || row.time !== rows[i - 1].time);
}

/** Latest bar time at or before `t`; null when `t` is before the first bar. */
export function snapToBar(t, times) {
  if (t === null || !times.length || t < times[0]) return null;
  let lo = 0;
  let hi = times.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (times[mid] <= t) lo = mid;
    else hi = mid - 1;
  }
  return times[lo];
}

/**
 * ▲BUY at each entry and ▼SELL at each exit of the rule's round trips, snapped to the candle they
 * belong to. The exit marker carries the trade's return; an open trade says so. Trades that start
 * before the chart window are only drawn from the part that is visible.
 */
export function tradeMarkers(trades, times, colors = FALLBACK) {
  const markers = [];
  for (const trade of trades || []) {
    const entry = snapToBar(toUnix(trade.entry_at), times);
    const open = trade.exit_at === null || trade.exit_at === undefined;
    if (entry !== null) {
      markers.push({ time: entry, position: "belowBar", shape: "arrowUp", color: colors.up, text: open ? "BUY · open" : "BUY" });
    }
    if (!open) {
      const exit = snapToBar(toUnix(trade.exit_at), times);
      const ret = num(trade.return_pct);
      if (exit !== null) {
        markers.push({ time: exit, position: "aboveBar", shape: "arrowDown", color: colors.down, text: ret === null ? "SELL" : `SELL ${fmtPct(ret)}` });
      }
    }
  }
  return markers.sort((a, b) => a.time - b.time);
}

/** B / A / T / S markers for past ladder transitions (BUY_STARTER, ADD, TRIM, SELL_ALL). */
export function ladderMarkers(transitions, times, colors = FALLBACK) {
  const style = {
    BUY_STARTER: { position: "belowBar", color: colors.ladderBuy },
    ADD: { position: "belowBar", color: colors.ladderAdd },
    TRIM: { position: "aboveBar", color: colors.ladderTrim },
    SELL_ALL: { position: "aboveBar", color: colors.ladderSell },
  };
  const markers = [];
  for (const t of transitions || []) {
    const s = style[t.action];
    const time = snapToBar(toUnix(t.at), times);
    if (!s || time === null) continue;
    markers.push({ time, position: s.position, shape: "circle", color: s.color, text: LADDER_ACTIONS[t.action].letter });
  }
  return markers.sort((a, b) => a.time - b.time);
}

/** Background histogram that shades each candle by the rule state of its day (HOLD green, WATCH amber). */
export function holdShading(bars, periods, colors = FALLBACK) {
  const spans = (periods || []).map((p) => ({ from: toUnix(p.from), to: toUnix(p.to), state: p.state })).filter((p) => p.from !== null && p.to !== null);
  return bars.map(({ time }) => {
    const period = spans.find((p) => time >= p.from && time <= p.to);
    if (period?.state === "HOLD") return { time, value: 1, color: colors.hold };
    if (period?.state === "WATCH") return { time, value: 1, color: colors.watch };
    return { time, value: 0, color: "rgba(0,0,0,0)" };
  });
}

/**
 * Horizontal levels: the next trend-line level, the user's avg entry when held, and the AI key levels
 * (support, resistance, invalidation), which are dashed.
 */
export function chartLevels({ trendLineNext = null, avgPrice = null, keyLevels = null, ladder = [] } = {}) {
  const levels = [];
  const add = (price, title, kind, style) => {
    const value = num(price);
    if (value !== null && value > 0) levels.push({ price: value, title, kind, style });
  };
  // The ladder's "exit below" is the trend line (Addendum D); draw that level once, as the exit line.
  const exit = (ladder || []).find((l) => l.kind === "exit");
  const trend = num(trendLineNext);
  const sameAsExit = exit && trend !== null && Math.abs(exit.price - trend) <= Math.abs(trend) * 1e-9;
  if (!sameAsExit) add(trendLineNext, "Trend line (next close)", "trend", "solid");
  for (const level of ladder || []) levels.push(level);
  add(avgPrice, "Your avg entry", "avg", "solid");
  for (const value of keyLevels?.support || []) add(value, "AI support", "support", "dashed");
  for (const value of keyLevels?.resistance || []) add(value, "AI resistance", "resistance", "dashed");
  add(keyLevels?.invalidation, "AI invalidation", "invalidation", "dashed");
  return levels;
}

/** Plain-text legend for the price chart (also the chart's accessible description). */
export function priceChartSummary(detail, levels) {
  const bars = candleBars(detail?.candles);
  const trades = detail?.trades || [];
  const parts = [`${bars.length} closed daily candles`, `${trades.length} rule trades`];
  // Ladder titles already carry their price ("Add above 5.40"); the others get it appended.
  for (const level of levels) parts.push(/\d/.test(level.title) ? level.title : `${level.title} ${fmtPrice(level.price)}`);
  return parts.join(" · ");
}

/** "Rule ×1.84 vs buy & hold ×1.52" plus the rule stats, for the equity chart's caption. */
export function equitySummary(equity, rule = null) {
  const last = (series) => {
    const points = linePoints(series);
    return points.length ? points[points.length - 1].value : null;
  };
  const r = last(equity?.rule);
  const b = last(equity?.buy_hold);
  const x = (v) => (v === null ? "—" : `×${v.toFixed(2)}`);
  let text = `Rule ${x(r)} vs buy & hold ${x(b)}`;
  if (rule) {
    text += ` · CAGR ${fmtPct(rule.cagr, 0)} vs ${fmtPct(rule.bh_cagr, 0)} · max drawdown ${fmtPct(-Math.abs(num(rule.max_dd) ?? NaN), 0)} vs ${fmtPct(-Math.abs(num(rule.bh_max_dd) ?? NaN), 0)}`;
    const tim = num(rule.time_in_market);
    if (tim !== null) text += ` · in market ${Math.round(tim * 100)}% of days`;
    if (num(rule.switches) !== null) text += ` · ${rule.switches} switches`;
  }
  return text.replace(/NaN%|−NaN%/g, "—");
}

// ---------------------------------------------------------------- mounting (browser only)

function palette(doc = globalThis.document) {
  try {
    const css = getComputedStyle(doc.documentElement);
    const read = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
    return { ...FALLBACK, up: read("--color-positive-text", FALLBACK.up), down: read("--color-negative-text", FALLBACK.down), trend: read("--color-accent", FALLBACK.trend) };
  } catch {
    return FALLBACK;
  }
}

async function loadLibrary() {
  return import(CHART_LIBRARY_URL);
}

function baseOptions() {
  return {
    autoSize: true,
    layout: { background: { color: themeVar("--chart-bg", "#ffffff") }, textColor: themeVar("--chart-text", "#1c2230"), attributionLogo: true },
    grid: { vertLines: { color: themeVar("--chart-grid", "#eef1f4") }, horzLines: { color: themeVar("--chart-grid", "#eef1f4") } },
    rightPriceScale: { borderColor: themeVar("--color-border", "#e1e4e9") },
    timeScale: { borderColor: themeVar("--color-border", "#e1e4e9"), timeVisible: false },
    crosshair: { mode: 0 },
  };
}

/** Mount the candlestick chart. Returns {update(detail, opts), destroy()}. */
export async function mountCotraderChart(host, detail, opts = {}) {
  let lib;
  try {
    lib = await loadLibrary();
  } catch {
    host.innerHTML = '<p class="muted">The pinned chart library could not be loaded.</p>';
    return { update() {}, destroy() {} };
  }
  const colors = palette();
  host.innerHTML = "";
  const chart = lib.createChart(host, baseOptions());
  const shade = chart.addSeries(lib.HistogramSeries, { priceScaleId: "state", priceLineVisible: false, lastValueVisible: false, base: 0 });
  shade.priceScale().applyOptions({ scaleMargins: { top: 0, bottom: 0 } });
  // Thin CDC Action Zone ribbon under the price (reference only), on its own hidden scale.
  const ribbon = chart.addSeries(lib.HistogramSeries, { priceScaleId: "cdc", priceLineVisible: false, lastValueVisible: false, base: 0 });
  ribbon.priceScale().applyOptions({ scaleMargins: { top: 0.95, bottom: 0 } });
  const candles = chart.addSeries(lib.CandlestickSeries, {
    upColor: colors.up, downColor: colors.down, borderVisible: false, wickUpColor: colors.up, wickDownColor: colors.down,
  });
  candles.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.1 } });
  const sma = chart.addSeries(lib.LineSeries, { color: colors.sma, lineWidth: 1, lineStyle: lib.LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false, title: "SMA100" });
  const trend = chart.addSeries(lib.LineSeries, { color: colors.trend, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, title: "Trend line" });
  let markers = null;
  let lines = [];
  let fitted = false;

  function update(next, nextOpts = {}) {
    const bars = candleBars(next?.candles);
    const times = bars.map((b) => b.time);
    candles.setData(bars);
    shade.setData(holdShading(bars, next?.periods, colors));
    sma.setData(linePoints(next?.sma100));
    trend.setData(linePoints(next?.trend_line_series));
    ribbon.setData(cdcRibbon(next));
    const list = [...tradeMarkers(next?.trades, times, colors), ...ladderMarkers(ladderTransitions(next), times, colors)].sort((a, b) => a.time - b.time);
    if (typeof lib.createSeriesMarkers === "function") {
      if (!markers) markers = lib.createSeriesMarkers(candles, list);
      else markers.setMarkers(list);
    }
    for (const line of lines) candles.removePriceLine(line);
    lines = chartLevels({ trendLineNext: next?.trend_line_next, avgPrice: nextOpts.avgPrice, keyLevels: nextOpts.keyLevels, ladder: ladderLevels(next) }).map((level) => candles.createPriceLine({
      price: level.price,
      color: colors[level.kind] || colors.trend,
      lineWidth: ["trend", "avg", "add", "trim", "exit"].includes(level.kind) ? 2 : 1,
      lineStyle: level.style === "dashed" ? lib.LineStyle.Dashed : lib.LineStyle.Solid,
      axisLabelVisible: true,
      title: level.title,
    }));
    if (!fitted && bars.length) {
      chart.timeScale().fitContent();
      fitted = true;
    }
  }

  update(detail, opts);
  return {
    update,
    destroy() {
      chart.remove();
    },
  };
}

/** Mount the rule vs buy & hold equity chart. Returns {update(equity), destroy()}. */
export async function mountEquityChart(host, equity) {
  let lib;
  try {
    lib = await loadLibrary();
  } catch {
    host.innerHTML = '<p class="muted">The pinned chart library could not be loaded.</p>';
    return { update() {}, destroy() {} };
  }
  const colors = palette();
  host.innerHTML = "";
  const chart = lib.createChart(host, { ...baseOptions(), rightPriceScale: { borderColor: themeVar("--color-border", "#e1e4e9"), mode: 1 } });
  const buyHold = chart.addSeries(lib.LineSeries, { color: colors.buyHold, lineWidth: 2, title: "Buy & hold", priceLineVisible: false });
  const rule = chart.addSeries(lib.LineSeries, { color: colors.rule, lineWidth: 2, title: "Rule", priceLineVisible: false });
  let fitted = false;
  function update(next) {
    rule.setData(linePoints(next?.rule));
    buyHold.setData(linePoints(next?.buy_hold));
    if (!fitted) {
      chart.timeScale().fitContent();
      fitted = true;
    }
  }
  update(equity);
  return { update, destroy() { chart.remove(); } };
}
