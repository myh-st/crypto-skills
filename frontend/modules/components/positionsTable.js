// PAPER perpetual positions table (legacy research dashboard variant).
import { escapeHtml, formatPrice, titleCase } from "../format.js";
import { money } from "../numbers.js";

export function renderPositions(positions) {
  if (!positions.length) return '<p class="muted">No primary paper positions are open.</p>';
  return `
    <div class="table-scroll">
      <table class="data-table">
        <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Mark</th><th>Stop / target</th><th>Margin</th><th>Action</th></tr></thead>
        <tbody>
          ${positions.filter((position) => position.status === "open").map((position) => `
            <tr>
              <td>${escapeHtml(position.symbol)}</td>
              <td><span class="pill pill--${position.side === "long" ? "positive" : "caution"}">${escapeHtml(titleCase(position.side))} · ${escapeHtml(position.leverage)}x</span></td>
              <td>${escapeHtml(Number(position.quantity).toFixed(8))}</td>
              <td>${escapeHtml(formatPrice(Number(position.entry_price)))}</td>
              <td>${escapeHtml(formatPrice(Number(position.mark_price)))}</td>
              <td>${escapeHtml(formatPrice(Number(position.stop_price)))} / ${escapeHtml(formatPrice(Number(position.target_price)))}</td>
              <td>${escapeHtml(money(position.margin))}</td>
              <td class="paper-inline-actions">
                <button class="btn btn--ghost btn--small" data-action="reduce-position"
                  data-position-id="${escapeHtml(position.position_id)}" data-fraction="0.5">Reduce 50%</button>
                <button class="btn btn--ghost btn--small" data-action="close-position"
                  data-position-id="${escapeHtml(position.position_id)}">Close</button>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}
