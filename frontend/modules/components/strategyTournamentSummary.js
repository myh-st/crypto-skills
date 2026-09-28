// Aligned-arm (strategy tournament) and leverage-cohort summaries.
import { escapeHtml, titleCase } from "../format.js";
import { money, percent, displayValue, numericValue, dataOriginLabel } from "../numbers.js";

export function renderArmRows(arms) {
  return Object.entries(arms || {}).map(([name, value]) => `
    <tr>
      <td>${escapeHtml(titleCase(name))}</td>
      <td>${escapeHtml(displayValue(value.closed_trade_count))}</td>
      <td>${escapeHtml(money(value.net_pnl_usdt))}</td>
      <td>${escapeHtml(money(value.expectancy_usdt))}</td>
      <td>${escapeHtml(percent(value.max_drawdown, 2))}</td>
      <td>${escapeHtml(dataOriginLabel(value.data_origin))}</td>
    </tr>
  `).join("");
}

export function renderPerformanceRows(rows, dimension) {
  if (!Array.isArray(rows) || !rows.length) {
    return '<tr><td colspan="6" class="table-empty">No performance breakdown is available.</td></tr>';
  }
  return rows.map((value) => `
    <tr>
      <td>${escapeHtml(displayValue(value?.[dimension]))}</td>
      <td>${escapeHtml(displayValue(value?.closed_trade_count))}</td>
      <td>${escapeHtml(money(value?.net_pnl_usdt))}</td>
      <td>${escapeHtml(money(value?.expectancy_usdt))}</td>
      <td>${escapeHtml(percent(value?.max_drawdown, 2))}</td>
      <td><span class="demo-tag">${escapeHtml(dataOriginLabel(value?.data_origin))}</span></td>
    </tr>
  `).join("");
}

export function renderLeverageRows(rows) {
  return (rows || []).map((value) => `
    <tr>
      <td>${escapeHtml(value.cohort.replace(/^x/, ""))}x</td>
      <td>${escapeHtml(value.closed_trade_count)}</td>
      <td>${escapeHtml(money(value.net_pnl_usdt))}</td>
      <td>${escapeHtml(value.open_position_count)}</td>
      <td>${escapeHtml(value.data_origin)}</td>
    </tr>
  `).join("");
}

export function renderComparisonChart(arms) {
  const rows = Object.entries(arms || {}).map(([name, value]) => ({
    name,
    value,
    pnl: numericValue(value.net_pnl_usdt),
  }));
  if (!rows.some(({ value }) => value.closed_trade_count > 0 || value.open_position_count > 0)) {
    return '<p class="muted">No arm positions have been recorded.</p>';
  }
  const recordedPnls = rows.filter((row) => row.pnl !== null);
  if (!recordedPnls.length) {
    return '<p class="muted">Net PnL is unavailable for the recorded experiment arms.</p>';
  }
  const maximum = Math.max(0, ...recordedPnls.map(({ pnl }) => Math.abs(pnl)));
  return `
    <div class="paper-comparison-chart" role="img" aria-label="Net paper PnL by experiment arm">
      ${rows.map(({ name, pnl }) => {
        const width = pnl === null ? 0 : maximum > 0 ? Math.max(2, Math.abs(pnl) / maximum * 100) : 2;
        const tone = pnl > 0 ? "positive" : pnl < 0 ? "negative" : "neutral";
        return `
          <div class="paper-bar-row">
            <span>${escapeHtml(titleCase(name))}</span>
            <div class="paper-bar-track">${pnl === null ? "" : `<span class="paper-bar-fill paper-bar-fill--${tone}" style="width:${width.toFixed(2)}%"></span>`}</div>
            <strong>${escapeHtml(money(pnl))}</strong>
          </div>
        `;
      }).join("")}
    </div>
    <p class="chart-caption">Separate virtual wallets · values reconcile to recorded arm positions</p>
  `;
}
