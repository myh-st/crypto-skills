// First-glance trader view: capital, equity growth, PnL, positions and assets from the
// local PAPER runtime. Only persisted runtime values are shown; a missing runtime or empty
// sample is stated explicitly instead of being filled with demo numbers.
import { escapeHtml, formatTimestamp } from "../format.js";

function num(value) {
  const number = Number(value);
  return value === null || value === undefined || value === "" || !Number.isFinite(number) ? null : number;
}

export function usdt(value, digits = 2, { signed = false } = {}) {
  const number = num(value);
  if (number === null) return "—";
  const text = Math.abs(number).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const sign = number < 0 ? "−" : signed && number > 0 ? "+" : "";
  return `${sign}${text} USDT`;
}

function pct(value, { signed = false } = {}) {
  const number = num(value);
  if (number === null) return "—";
  const sign = number < 0 ? "−" : signed && number > 0 ? "+" : "";
  return `${sign}${Math.abs(number * 100).toFixed(2)}%`;
}

function tone(value) {
  const number = num(value);
  if (number === null || number === 0) return "neutral";
  return number > 0 ? "positive" : "negative";
}

export function unrealizedPnl(position) {
  const qty = num(position.quantity);
  const entry = num(position.entry_price);
  const mark = num(position.mark_price);
  if (qty === null || entry === null || mark === null) return null;
  return (position.side === "short" ? -1 : 1) * (mark - entry) * qty;
}

export function assetBreakdown(positions = [], byAsset = []) {
  const rows = new Map();
  const row = (symbol) => {
    if (!rows.has(symbol)) rows.set(symbol, { symbol, open: 0, exposure: 0, unrealized: 0, realized: 0, closed: 0 });
    return rows.get(symbol);
  };
  for (const position of positions) {
    if (position.status !== "open") continue;
    const item = row(position.symbol);
    item.open += 1;
    item.exposure += (num(position.quantity) ?? 0) * (num(position.mark_price) ?? 0);
    item.unrealized += unrealizedPnl(position) ?? 0;
  }
  for (const bucket of byAsset) {
    const item = row(bucket.symbol);
    item.realized += num(bucket.net_pnl_usdt) ?? 0;
    item.closed += num(bucket.closed_trade_count) ?? 0;
  }
  return [...rows.values()]
    .map((item) => ({ ...item, total: item.realized + item.unrealized }))
    .sort((a, b) => b.exposure - a.exposure || Math.abs(b.total) - Math.abs(a.total));
}

export function renderGrowthChart(points = [], startingBalance = null) {
  const series = points
    .map((point) => ({ at: point.as_of, equity: num(point.equity) }))
    .filter((point) => point.equity !== null);
  if (series.length < 2) {
    return '<p class="muted portfolio-growth-empty">Equity growth appears after the first persisted cycles.</p>';
  }
  const width = 560;
  const height = 120;
  const pad = 6;
  const values = series.map((point) => point.equity);
  const base = num(startingBalance) ?? values[0];
  const flat = Math.max(...values, base) === Math.min(...values, base);
  // A flat series is drawn mid-height around the starting capital instead of on the floor.
  const min = flat ? base - Math.max(Math.abs(base) * 0.01, 1) : Math.min(...values, base);
  const max = flat ? base + Math.max(Math.abs(base) * 0.01, 1) : Math.max(...values, base);
  const span = max - min;
  const x = (index) => pad + (index / (series.length - 1)) * (width - 2 * pad);
  const y = (value) => height - pad - ((value - min) / span) * (height - 2 * pad);
  const line = series.map((point, index) => `${x(index).toFixed(1)},${y(point.equity).toFixed(1)}`).join(" ");
  const area = `${pad},${height - pad} ${line} ${(width - pad).toFixed(1)},${height - pad}`;
  const direction = values[values.length - 1] >= base ? "up" : "down";
  return `
    <svg class="portfolio-growth-chart portfolio-growth-chart--${direction}" viewBox="0 0 ${width} ${height}"
      preserveAspectRatio="none" role="img"
      aria-label="PAPER equity from ${escapeHtml(usdt(values[0]))} to ${escapeHtml(usdt(values[values.length - 1]))} over ${series.length} observations">
      <line class="portfolio-growth-base" x1="${pad}" x2="${width - pad}" y1="${y(base).toFixed(1)}" y2="${y(base).toFixed(1)}"></line>
      <polygon class="portfolio-growth-area" points="${area}"></polygon>
      <polyline class="portfolio-growth-line" points="${line}"></polyline>
    </svg>
    <div class="portfolio-growth-caption">
      <span>${escapeHtml(formatTimestamp(series[0].at))}</span>
      <span>${flat ? "No equity change yet · " : ""}Dashed line = starting capital</span>
      <span>${escapeHtml(formatTimestamp(series[series.length - 1].at))}</span>
    </div>`;
}

function kpi(label, value, note = "", valueTone = "neutral") {
  return `
    <div class="portfolio-kpi">
      <span class="portfolio-kpi-label">${escapeHtml(label)}</span>
      <strong class="portfolio-kpi-value portfolio-kpi-value--${valueTone}">${escapeHtml(value)}</strong>
      ${note ? `<small>${escapeHtml(note)}</small>` : ""}
    </div>`;
}

function positionRows(positions) {
  const open = positions.filter((position) => position.status === "open");
  if (!open.length) return '<tr><td colspan="7" class="table-empty">No open PAPER positions.</td></tr>';
  return open.map((position) => {
    const pnl = unrealizedPnl(position);
    return `
      <tr>
        <td><strong>${escapeHtml(position.symbol)}</strong></td>
        <td><span class="pill pill--${position.side === "long" ? "positive" : "negative"}">${escapeHtml(String(position.side).toUpperCase())} ${escapeHtml(position.leverage)}x</span></td>
        <td>${escapeHtml(num(position.quantity)?.toPrecision(6) ?? "—")}</td>
        <td>${escapeHtml(num(position.entry_price)?.toLocaleString("en-US", { maximumFractionDigits: 6 }) ?? "—")}</td>
        <td>${escapeHtml(num(position.mark_price)?.toLocaleString("en-US", { maximumFractionDigits: 6 }) ?? "—")}</td>
        <td class="market-change--${tone(pnl) === "negative" ? "negative" : "positive"}">${escapeHtml(usdt(pnl, 4, { signed: true }))}</td>
        <td>${escapeHtml(num(position.stop_price)?.toLocaleString("en-US", { maximumFractionDigits: 6 }) ?? "—")} / ${escapeHtml(num(position.target_price)?.toLocaleString("en-US", { maximumFractionDigits: 6 }) ?? "—")}</td>
      </tr>`;
  }).join("");
}

function assetRows(rows) {
  if (!rows.length) return '<tr><td colspan="5" class="table-empty">No asset exposure or closed trades yet.</td></tr>';
  return rows.map((row) => `
    <tr>
      <td><strong>${escapeHtml(row.symbol)}</strong></td>
      <td>${row.open}</td>
      <td>${escapeHtml(usdt(row.exposure))}</td>
      <td>${row.closed}</td>
      <td class="market-change--${tone(row.total) === "negative" ? "negative" : "positive"}">${escapeHtml(usdt(row.total, 4, { signed: true }))}</td>
    </tr>`).join("");
}

export function renderPortfolioSummary(dashboard) {
  const metrics = dashboard.metrics || {};
  const portfolio = metrics.portfolio || {};
  const economics = dashboard.economics || null;
  const start = num(portfolio.starting_balance_usdt);
  const equity = num(portfolio.ending_equity_usdt);
  const growth = start && equity !== null ? equity / start - 1 : null;
  const cycleOrigin = dashboard.data_safety?.origin;
  const origin = !cycleOrigin || cycleOrigin === "NO_CYCLE" ? "NO_CYCLE" : cycleOrigin;
  const closed = num(portfolio.closed_trade_count) ?? 0;
  const net = num(portfolio.net_pnl_usdt);
  const rows = assetBreakdown(dashboard.positions || [], metrics.by_asset || []);
  const aiCost = economics?.ai_cost?.total_ai_cost_usd;
  const netEconomic = economics?.net_economic_pnl_usdt;
  const originTag = origin === "FIXTURE" ? "FIXTURE · synthetic mechanics" : origin === "NO_CYCLE" ? "No cycles yet" : origin;
  return `
    <div class="section-heading">
      <h2>Portfolio performance</h2>
      <span class="demo-tag">PAPER · ${escapeHtml(originTag)} · ${escapeHtml(dashboard.experiment?.config?.label || dashboard.experiment?.experiment_id || "")} ${escapeHtml(dashboard.experiment?.status || "")}</span>
    </div>
    <div class="portfolio-kpis">
      ${kpi("Equity", usdt(equity), `Start ${usdt(start)}`)}
      ${kpi("Growth", pct(growth, { signed: true }), "vs starting capital", tone(growth))}
      ${kpi("Net PnL", usdt(net, 2, { signed: true }), `Realized ${usdt(portfolio.realized_pnl_usdt, 2, { signed: true })} · Unrealized ${usdt(portfolio.unrealized_pnl_usdt, 2, { signed: true })}`, tone(net))}
      ${kpi("Open positions", String(num(portfolio.open_position_count) ?? 0), `Margin used ${usdt(portfolio.margin_used_usdt)}`)}
      ${kpi("Win rate", closed ? pct(portfolio.win_rate) : "—", closed ? `${portfolio.win_count}W / ${portfolio.loss_count}L of ${closed} closed` : "No closed trades yet")}
      ${kpi("Max drawdown", pct(portfolio.max_drawdown), `Profit factor ${num(portfolio.profit_factor)?.toFixed(2) ?? "—"}`)}
      ${(() => {
        const costs = -(num(portfolio.fees_usdt) ?? 0) - (num(portfolio.funding_paid_usdt) ?? 0) - (num(portfolio.slippage_drag_usdt) ?? 0);
        return kpi("Costs", usdt(costs, 4), "Fees + funding + slippage", tone(costs));
      })()}
      ${kpi("After AI cost", netEconomic === null || netEconomic === undefined ? "Unavailable" : usdt(netEconomic, 2, { signed: true }), aiCost == null ? "" : `AI cost $${Number(aiCost).toFixed(4)} USD`, tone(netEconomic))}
    </div>
    <div class="portfolio-grid">
      <div class="portfolio-growth">
        <h3>Equity growth</h3>
        ${renderGrowthChart(dashboard.equity || [], start)}
      </div>
      <div class="portfolio-assets">
        <h3>By asset</h3>
        <div class="table-scroll"><table class="data-table data-table--compact">
          <thead><tr><th>Asset</th><th>Open</th><th>Exposure</th><th>Closed</th><th>PnL</th></tr></thead>
          <tbody>${assetRows(rows)}</tbody>
        </table></div>
      </div>
    </div>
    <h3>Open positions</h3>
    <div class="table-scroll"><table class="data-table data-table--compact">
      <thead><tr><th>Asset</th><th>Side</th><th>Qty</th><th>Entry</th><th>Mark</th><th>Unrealized</th><th>Stop / target</th></tr></thead>
      <tbody>${positionRows(dashboard.positions || [])}</tbody>
    </table></div>
    <p class="chart-caption"><a href="#/paper-trading">Open Paper Trading →</a> · live chart, AI routing, risk journal and exports. Real-money execution is disabled.</p>`;
}

export function renderPortfolioUnavailable(message) {
  return `
    <div class="section-heading"><h2>Portfolio performance</h2><span class="demo-tag">PAPER runtime offline</span></div>
    <p class="muted">${escapeHtml(message || "The local PAPER runtime is not reachable.")}
      Start it with <code>python3 -m crypto_eval paper-server</code> to see capital, PnL, growth and positions here.
      No placeholder numbers are shown.</p>`;
}

export async function loadPortfolioSummary(host, api) {
  host.innerHTML = '<div class="section-heading"><h2>Portfolio performance</h2></div><p class="muted">Loading PAPER portfolio…</p>';
  try {
    const dashboard = await api.dashboard();
    if (host.isConnected) host.innerHTML = renderPortfolioSummary(dashboard);
  } catch (error) {
    if (host.isConnected) host.innerHTML = renderPortfolioUnavailable(error?.message);
  }
}
