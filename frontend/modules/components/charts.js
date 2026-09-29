// Small dependency-free SVG/CSS charts for the day-trading views: equity line, P&L calendar heatmap,
// ranked bars, heat grid, progress gauge, and range bars. Every label is escaped; colors come from CSS
// tokens (positive/negative/muted) so light and dark themes both work.
import { escapeHtml } from "../format.js";

const fmtPct = (v, d = 1) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "—" : `${Number(v) >= 0 ? "+" : ""}${(Number(v) * 100).toFixed(d)}%`);
const fmtNum = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "—" : `${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(d)}`);

export { fmtPct, fmtNum };

// Equity line with a baseline (e.g. start-of-day equity): green above, red below.
export function lineChart(points, { baseline = null, height = 150, label = "Equity", unit = "USDT", motionKey = "" } = {}) {
  const values = (points || []).map((p) => Number(p[1])).filter((v) => Number.isFinite(v));
  if (values.length < 2) return `<div class="chart-empty muted small">Not enough data yet for the ${escapeHtml(label.toLowerCase())} chart.</div>`;
  const width = 600;
  const pad = { l: 8, r: 8, t: 10, b: 18 };
  const lo = Math.min(...values, baseline ?? Infinity);
  const hi = Math.max(...values, baseline ?? -Infinity);
  const span = hi - lo || Math.abs(hi) * 0.001 || 1;
  const x = (i) => pad.l + (i / (values.length - 1)) * (width - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - lo) / span) * (height - pad.t - pad.b);
  const path = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const base = baseline ?? values[0];
  const last = values[values.length - 1];
  const up = last >= base;
  const area = `${path} L${x(values.length - 1).toFixed(1)},${y(base).toFixed(1)} L${x(0).toFixed(1)},${y(base).toFixed(1)} Z`;
  const first = points[0][0];
  const lastT = points[points.length - 1][0];
  const t = (iso) => escapeHtml(new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }));
  return `
    <figure class="chart chart--line ${up ? "chart--up" : "chart--down"}"${motionKey ? ` data-motion-enter="${escapeHtml(motionKey)}"` : ""}>
      <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img"
        aria-label="${escapeHtml(label)} from ${escapeHtml(base.toFixed(2))} to ${escapeHtml(last.toFixed(2))} ${escapeHtml(unit)}">
        <path class="chart-area" d="${area}" />
        <line class="chart-baseline" x1="${pad.l}" x2="${width - pad.r}" y1="${y(base).toFixed(1)}" y2="${y(base).toFixed(1)}" />
        <path class="chart-line" d="${path}" vector-effect="non-scaling-stroke" />
        <circle class="chart-dot" cx="${x(values.length - 1).toFixed(1)}" cy="${y(last).toFixed(1)}" r="3.5" />
      </svg>
      <figcaption class="chart-axis small muted"><span>${t(first)}</span><span>high ${escapeHtml(hi.toFixed(2))} · low ${escapeHtml(lo.toFixed(2))}</span><span>${t(lastT)}</span></figcaption>
    </figure>`;
}

// GitHub-style calendar: one square per UTC day, colored by P&L %.
export function calendarHeatmap(days, { title = "Daily P&L", motionKey = "" } = {}) {
  if (!days?.length) return "";
  const scale = Math.max(0.002, ...days.map((d) => Math.abs(Number(d.pnl_pct) || 0)));
  const first = new Date(`${days[0].date}T00:00:00Z`);
  const offset = (first.getUTCDay() + 6) % 7; // Monday first
  const cells = [...Array(offset).fill(null), ...days];
  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  let order = 0;
  const cell = (d) => {
    if (!d) return '<span class="cal-cell cal-cell--pad"></span>';
    const i = order++;
    if (d.pnl_pct === null || d.pnl_pct === undefined) {
      return `<span class="cal-cell cal-cell--none" style="--i:${i}" title="${escapeHtml(d.date)} · no data"></span>`;
    }
    const v = Number(d.pnl_pct);
    const level = Math.min(4, Math.max(1, Math.ceil((Math.abs(v) / scale) * 4)));
    const tone = v > 0 ? "pos" : v < 0 ? "neg" : "flat";
    return `<span class="cal-cell cal-cell--${tone} cal-l${tone === "flat" ? 0 : level}${d.today ? " cal-cell--today" : ""}" style="--i:${i}"
      title="${escapeHtml(d.date)} · ${escapeHtml(fmtPct(v, 2))} (${escapeHtml(fmtNum(d.pnl_usdt))} USDT) · ${escapeHtml(d.trades)} trades"></span>`;
  };
  const green = days.filter((d) => Number(d.pnl_usdt) > 0).length;
  const red = days.filter((d) => Number(d.pnl_usdt) < 0).length;
  return `
    <figure class="chart chart--calendar" aria-label="${escapeHtml(title)}"${motionKey ? ` data-motion-enter="${escapeHtml(motionKey)}"` : ""}>
      <div class="cal-grid">${weeks.map((w) => `<div class="cal-week">${w.map(cell).join("")}</div>`).join("")}</div>
      <figcaption class="small muted">${escapeHtml(title)} · <span class="pos-text">${green} green</span> · <span class="neg-text">${red} red</span> days ·
        <span class="cal-legend"><span class="cal-cell cal-cell--neg cal-l4"></span><span class="cal-cell cal-cell--neg cal-l2"></span><span class="cal-cell cal-cell--flat cal-l0"></span><span class="cal-cell cal-cell--pos cal-l2"></span><span class="cal-cell cal-cell--pos cal-l4"></span></span></figcaption>
    </figure>`;
}

// Horizontal bars around zero (signed) or from zero (unsigned). items: [{label, value, note}]
export function barList(items, { format = (v) => fmtNum(v), signed = true, max = null, motionKey = "" } = {}) {
  if (!items?.length) return '<p class="muted small">No data.</p>';
  const m = max ?? Math.max(1e-12, ...items.map((i) => Math.abs(Number(i.value) || 0)));
  return `<div class="bar-list${signed ? " bar-list--signed" : ""}"${motionKey ? ` data-motion-fill="${escapeHtml(motionKey)}"` : ""}>${items.map((i) => {
    const v = Number(i.value) || 0;
    const w = Math.min(100, (Math.abs(v) / m) * (signed ? 50 : 100));
    const style = signed ? (v >= 0 ? `left:50%;width:${w}%` : `left:${50 - w}%;width:${w}%`) : `left:0;width:${w}%`;
    return `<div class="bar-row">
      <span class="bar-label">${escapeHtml(i.label)}</span>
      <span class="bar-track"><span class="bar-fill ${v >= 0 ? "bar-fill--pos" : "bar-fill--neg"}" style="${style}"></span></span>
      <span class="bar-value">${escapeHtml(format(v))}${i.note ? ` <small class="muted">${escapeHtml(i.note)}</small>` : ""}</span>
    </div>`;
  }).join("")}</div>`;
}

// Heat grid: rows x cols; cellFn(row, col) -> {value, text, bad}
export function heatGrid(rows, cols, cellFn, { rowLabel = (r) => r, colLabel = (c) => c, corner = "" } = {}) {
  const values = rows.flatMap((r) => cols.map((c) => cellFn(r, c)?.value)).filter((v) => Number.isFinite(v));
  const m = Math.max(1e-12, ...values.map(Math.abs));
  return `<div class="table-scroll"><table class="heat-grid">
    <thead><tr><th scope="col">${escapeHtml(corner)}</th>${cols.map((c) => `<th scope="col">${escapeHtml(colLabel(c))}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr><th scope="row">${escapeHtml(rowLabel(r))}</th>${cols.map((c) => {
      const cell = cellFn(r, c);
      if (!cell || !Number.isFinite(cell.value)) return '<td class="heat-na">—</td>';
      const a = Math.min(1, Math.abs(cell.value) / m);
      const tone = cell.bad ? "bad" : cell.value >= 0 ? "pos" : "neg";
      return `<td class="heat-${tone}" style="--a:${(0.12 + a * 0.6).toFixed(2)}" title="${escapeHtml(cell.title || "")}">${escapeHtml(cell.text)}</td>`;
    }).join("")}</tr>`).join("")}</tbody></table></div>`;
}

// Progress gauge (0..1), e.g. how much of the daily loss limit is used.
export function gauge(used, { label = "", danger = 0.8, motionKey = "" } = {}) {
  const u = Math.max(0, Math.min(1, Number(used) || 0));
  const tone = u >= 1 ? "stop" : u >= danger ? "warn" : "ok";
  return `<div class="gauge gauge--${tone}"${motionKey ? ` data-motion-fill="${escapeHtml(motionKey)}"` : ""} role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(u * 100)}" aria-label="${escapeHtml(label)}">
    <span class="gauge-fill" style="width:${(u * 100).toFixed(1)}%"></span></div>`;
}

// Range bar for a distribution: p5 .. median .. p95 around zero.
export function rangeBar({ p5, median, p95 }, { span = null } = {}) {
  const s = span ?? Math.max(0.01, Math.abs(p5), Math.abs(p95));
  const pos = (v) => 50 + (Math.max(-s, Math.min(s, v)) / s) * 50;
  return `<span class="range-bar" title="5th ${escapeHtml(fmtPct(p5))} · median ${escapeHtml(fmtPct(median))} · 95th ${escapeHtml(fmtPct(p95))}">
    <span class="range-zero"></span>
    <span class="range-span" style="left:${pos(p5).toFixed(1)}%;width:${(pos(p95) - pos(p5)).toFixed(1)}%"></span>
    <span class="range-mid ${median >= 0 ? "range-mid--pos" : "range-mid--neg"}" style="left:${pos(median).toFixed(1)}%"></span></span>`;
}
