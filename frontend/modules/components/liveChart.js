// Interactive Gate futures chart (TradingView Lightweight Charts™, vendored + pinned).
// The browser never talks to Gate: candles and quotes come from the local backend's
// normalized REST endpoint and SSE stream. Missing data is shown as missing — this
// module never synthesizes continuation candles.
import { escapeHtml } from "../format.js";

export const CHART_LIBRARY_URL = "../../vendor/lightweight-charts-5.2.1/lightweight-charts.standalone.production.mjs";
export const CHART_INTERVALS = ["1m", "5m", "15m", "1h", "4h"];
export const STREAM_STATES = ["LIVE", "RECONNECTING", "STALE", "OFFLINE"];
const STALE_AFTER_MS = 20_000;

const COLORS = {
  up: "#1f7a4d",
  down: "#a3352a",
  upCurrent: "rgba(31, 122, 77, 0.45)",
  downCurrent: "rgba(163, 53, 42, 0.45)",
  volume: "rgba(47, 111, 237, 0.28)",
  entry: "#2f6fed",
  stop: "#a3352a",
  target: "#1f7a4d",
  liquidation: "#8a5a08",
  real: "#6b21a8",
};

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function toChartBar(candle) {
  const time = Math.floor(Date.parse(candle.open_time) / 1000);
  const open = finite(candle.open);
  const high = finite(candle.high);
  const low = finite(candle.low);
  const close = finite(candle.close);
  if (!Number.isFinite(time) || [open, high, low, close].some((value) => value === null)) return null;
  const bar = { time, open, high, low, close, closed: candle.closed !== false };
  if (!bar.closed) {
    const color = close >= open ? COLORS.upCurrent : COLORS.downCurrent;
    bar.color = color;
    bar.wickColor = color;
    bar.borderColor = color;
  }
  return bar;
}

export function toVolumeBar(candle) {
  const time = Math.floor(Date.parse(candle.open_time) / 1000);
  const value = finite(candle.volume);
  if (!Number.isFinite(time) || value === null) return null;
  return { time, value, color: COLORS.volume };
}

// Update-in-place for the current window, append for a newer window, ignore stale
// or out-of-order bars. Gaps are left as gaps.
export function mergeBar(bars, bar) {
  if (!bar) return { bars, action: "ignored" };
  const last = bars[bars.length - 1];
  if (!last || bar.time > last.time) return { bars: [...bars, bar], action: "appended" };
  if (bar.time === last.time) {
    if (last.closed && !bar.closed) return { bars, action: "ignored" };
    return { bars: [...bars.slice(0, -1), bar], action: "updated" };
  }
  return { bars, action: "ignored" };
}

export function streamStatusLabel({ serverState = null, eventSourceState = "closed", lastEventAt = null, now = Date.now() } = {}) {
  if (eventSourceState === "connecting") return serverState === "LIVE" ? "RECONNECTING" : serverState === "OFFLINE" ? "OFFLINE" : "RECONNECTING";
  if (eventSourceState !== "open") return "OFFLINE";
  if (serverState === "RECONNECTING" || serverState === "CONNECTING") return "RECONNECTING";
  if (serverState === "OFFLINE" || !serverState) return "OFFLINE";
  if (serverState === "STALE") return "STALE";
  if (lastEventAt !== null && now - lastEventAt > STALE_AFTER_MS) return "STALE";
  return "LIVE";
}

// PAPER overlays come from the PAPER wallet only; REAL ACCOUNT overlays come from the
// read-only Gate mirror and are always labeled as such. They are never merged.
export function overlayLines(symbol, paperPositions = [], realPositions = []) {
  const lines = [];
  for (const position of paperPositions) {
    if (position.symbol !== symbol || position.status !== "open") continue;
    const side = String(position.side || "").toUpperCase();
    const add = (price, title, kind) => {
      const value = finite(price);
      if (value !== null) lines.push({ price: value, title, kind, source: "PAPER" });
    };
    add(position.entry_price, `PAPER ${side} avg entry`, "entry");
    add(position.stop_price, "PAPER stop", "stop");
    add(position.target_price, "PAPER target", "target");
    add(position.liquidation_price, "PAPER liq. estimate", "liquidation");
  }
  for (const position of realPositions) {
    if (position.symbol !== symbol) continue;
    const entry = finite(position.entry_price);
    if (entry !== null) {
      lines.push({ price: entry, title: `REAL ACCOUNT ${String(position.side).toUpperCase()} entry`, kind: "real", source: "REAL_ACCOUNT" });
    }
    const liquidation = finite(position.liquidation_price);
    if (liquidation !== null) {
      lines.push({ price: liquidation, title: "REAL ACCOUNT liq.", kind: "real", source: "REAL_ACCOUNT" });
    }
  }
  return lines;
}

function fmt(value, digits = 2) {
  const number = finite(value);
  if (number === null) return "—";
  const abs = Math.abs(number);
  const precision = abs >= 1000 ? digits : abs >= 1 ? 4 : 6;
  return number.toLocaleString("en-US", { maximumFractionDigits: precision, minimumFractionDigits: 0 });
}

export function renderQuoteStrip(state) {
  const ticker = state?.ticker || {};
  const book = state?.book || {};
  const spread = finite(book.spread_bps);
  const funding = finite(ticker.funding_rate);
  const change = finite(ticker.change_24h_pct);
  const cells = [
    ["Last", fmt(ticker.last_price)],
    ["Mark", fmt(ticker.mark_price)],
    ["Index", fmt(ticker.index_price)],
    ["Bid / Ask", `${fmt(book.best_bid)} / ${fmt(book.best_ask)}`],
    ["Spread", spread === null ? "—" : `${spread.toFixed(2)} bps`],
    ["Funding", funding === null ? "—" : `${(funding * 100).toFixed(4)}%`],
    ["24h change", change === null ? "—" : `${change.toFixed(2)}%`],
    ["24h volume", ticker.volume_24h_quote == null ? "—" : `${fmt(ticker.volume_24h_quote, 0)} USDT`],
  ];
  return cells
    .map(([label, value]) => `<div class="live-quote"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
    .join("");
}

export function renderLiveChartShell(symbols, selected, interval) {
  return `
    <div class="live-chart-toolbar">
      <label class="live-chart-symbol">Symbol
        <select data-live-symbol>${symbols
          .map((symbol) => `<option value="${escapeHtml(symbol)}" ${symbol === selected ? "selected" : ""}>${escapeHtml(symbol)}</option>`)
          .join("")}</select>
      </label>
      <div class="live-chart-intervals" role="group" aria-label="Timeframe">
        ${CHART_INTERVALS.map((value) => `<button type="button" class="chart-range-button ${value === interval ? "is-active" : ""}" data-live-interval="${value}" aria-pressed="${value === interval}">${value}</button>`).join("")}
      </div>
      <span class="live-status live-status--offline" data-live-status role="status" aria-live="polite">OFFLINE</span>
    </div>
    <div class="live-quote-strip" data-live-quotes>${renderQuoteStrip(null)}</div>
    <div class="live-chart-canvas" data-live-canvas aria-label="Gate USDT perpetual candlestick chart"></div>
    <p class="chart-caption" data-live-caption>Source: Gate USDT perpetual (backend-owned REST + WebSocket). Translucent candle = current, not yet closed.</p>
  `;
}

export async function mountLiveChart(host, { api, symbols, symbol, interval = "1m", getOverlays = () => [] }) {
  if (host.__liveChart) host.__liveChart.destroy();
  const state = { symbol: symbol || symbols[0], interval, bars: [], chart: null, series: null, volume: null,
    lines: [], source: null, serverState: null, lastEventAt: null, destroyed: false, quote: null, timer: null };
  host.innerHTML = renderLiveChartShell(symbols, state.symbol, state.interval);
  const statusEl = host.querySelector("[data-live-status]");
  const quotesEl = host.querySelector("[data-live-quotes]");
  const canvas = host.querySelector("[data-live-canvas]");
  const caption = host.querySelector("[data-live-caption]");

  let lib;
  try {
    lib = await import(CHART_LIBRARY_URL);
  } catch {
    canvas.innerHTML = '<p class="muted">The pinned chart library could not be loaded.</p>';
    return { destroy() {} };
  }

  function setStatus() {
    const readyState = state.source ? ["connecting", "open", "closed"][state.source.readyState] : "closed";
    const label = streamStatusLabel({ serverState: state.serverState, eventSourceState: readyState, lastEventAt: state.lastEventAt });
    statusEl.textContent = label;
    statusEl.className = `live-status live-status--${label.toLowerCase()}`;
  }

  function applyOverlays() {
    if (!state.series) return;
    for (const line of state.lines) state.series.removePriceLine(line);
    state.lines = getOverlays(state.symbol).map((line) => state.series.createPriceLine({
      price: line.price,
      color: COLORS[line.kind] || COLORS.entry,
      lineWidth: line.kind === "real" ? 2 : 1,
      lineStyle: line.kind === "entry" || line.kind === "real" ? lib.LineStyle.Solid : lib.LineStyle.Dashed,
      axisLabelVisible: true,
      title: line.title,
    }));
  }

  function buildChart() {
    if (state.chart) state.chart.remove();
    canvas.innerHTML = "";
    state.chart = lib.createChart(canvas, {
      autoSize: true,
      layout: { background: { color: "#ffffff" }, textColor: "#1c2230", attributionLogo: true },
      grid: { vertLines: { color: "#eef1f4" }, horzLines: { color: "#eef1f4" } },
      rightPriceScale: { borderColor: "#e1e4e9" },
      timeScale: { borderColor: "#e1e4e9", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    state.series = state.chart.addSeries(lib.CandlestickSeries, {
      upColor: COLORS.up, downColor: COLORS.down, borderVisible: false,
      wickUpColor: COLORS.up, wickDownColor: COLORS.down,
    });
    state.volume = state.chart.addSeries(lib.HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "" });
    state.volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    state.lines = [];
  }

  async function loadHistory() {
    buildChart();
    caption.textContent = "Loading real Gate candles…";
    try {
      const payload = await api.candles(state.symbol, state.interval, 400);
      if (payload.synthetic) throw new Error("synthetic data refused");
      state.bars = payload.candles.map(toChartBar).filter(Boolean);
      state.series.setData(state.bars);
      state.volume.setData(payload.candles.map(toVolumeBar).filter(Boolean));
      state.chart.timeScale().scrollToRealTime();
      caption.textContent = `${payload.data_origin} · ${payload.source} · ${state.bars.length} candles · translucent candle = current (not closed)`;
      applyOverlays();
    } catch (error) {
      state.bars = [];
      caption.textContent = `No chart data: ${error.message || "request failed"}. Nothing is simulated.`;
    }
    try {
      const quote = await api.ticker(state.symbol);
      quotesEl.innerHTML = renderQuoteStrip(quote);
    } catch {
      quotesEl.innerHTML = renderQuoteStrip(null);
    }
  }

  function connect() {
    if (state.source) state.source.close();
    if (typeof EventSource !== "function") return;
    state.source = new EventSource(api.streamUrl([state.symbol], state.interval));
    state.source.addEventListener("open", setStatus);
    state.source.addEventListener("error", setStatus);
    state.source.addEventListener("status", (event) => {
      const payload = JSON.parse(event.data);
      state.serverState = payload.status?.state || null;
      setStatus();
    });
    state.source.addEventListener("candle", (event) => {
      const payload = JSON.parse(event.data);
      if (payload.symbol !== state.symbol || payload.interval !== state.interval) return;
      const bar = toChartBar(payload.candle);
      const merged = mergeBar(state.bars, bar);
      if (merged.action !== "ignored") {
        state.bars = merged.bars;
        state.series.update(bar);
        const volume = toVolumeBar(payload.candle);
        if (volume) state.volume.update(volume);
      }
      state.lastEventAt = Date.now();
    });
    for (const type of ["ticker", "book"]) {
      state.source.addEventListener(type, (event) => {
        const payload = JSON.parse(event.data);
        if (payload.symbol !== state.symbol) return;
        state.quote = { ...(state.quote || {}), [type]: payload[type] };
        quotesEl.innerHTML = renderQuoteStrip(state.quote);
        state.lastEventAt = Date.now();
      });
    }
  }

  host.addEventListener("change", async (event) => {
    if (!event.target.matches("[data-live-symbol]")) return;
    state.symbol = event.target.value;
    state.quote = null;
    await loadHistory();
    connect();
  });
  host.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-live-interval]");
    if (!button) return;
    state.interval = button.dataset.liveInterval;
    for (const item of host.querySelectorAll("[data-live-interval]")) {
      const active = item === button;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-pressed", String(active));
    }
    await loadHistory();
    connect();
  });

  await loadHistory();
  connect();
  state.timer = setInterval(() => {
    if (!host.isConnected) controller.destroy();
    else setStatus();
  }, 2000);

  const controller = {
    destroy() {
      if (state.destroyed) return;
      state.destroyed = true;
      clearInterval(state.timer);
      if (state.source) state.source.close();
      if (state.chart) state.chart.remove();
    },
    refreshOverlays: applyOverlays,
    get symbol() {
      return state.symbol;
    },
  };
  host.__liveChart = controller;
  return controller;
}
