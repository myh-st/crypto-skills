// PAPER perpetual positions table (legacy research dashboard variant).
import { escapeHtml, formatPrice, titleCase } from "../format.js";
import { money } from "../numbers.js";
import { marketBadge, modeBadge, pct as pctText, pnl, price, sideBadge, emptyState as empty } from "./ui.js";

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

// Unified PAPER Spot + Perpetual positions with a Manage action (opens the detail drawer).
export function renderUnifiedPositions(positions, { compact = false, emptyMessage = "No open PAPER positions." } = {}) {
  const open = (positions || []).filter((position) => position.status === "open");
  if (!open.length) return empty(emptyMessage);
  return `
    <div class="table-scroll">
      <table class="data-table positions-table${compact ? " positions-table--compact" : ""}">
        <thead><tr>
          <th scope="col">Instrument</th><th scope="col">Side</th><th scope="col">Size</th>
          <th scope="col">Entry / avg</th><th scope="col">Mark</th><th scope="col">PnL</th>
          ${compact ? "" : '<th scope="col">Stop</th><th scope="col">Risk</th>'}
          <th scope="col">Control</th><th scope="col"><span class="sr-only">Action</span></th>
        </tr></thead>
        <tbody>
          ${open.map((position) => `
            <tr>
              <td>${marketBadge(position.market_type)} <strong>${escapeHtml(position.display_symbol)}</strong>${sleeveTag(position.cohort)}</td>
              <td>${sideBadge(position.side, position.leverage)}</td>
              <td>${escapeHtml(price(position.quantity))}<br><small class="muted">${escapeHtml(price(position.notional_usdt))} USDT</small></td>
              <td>${escapeHtml(price(position.entry_price))}</td>
              <td data-motion-key="pos:${escapeHtml(position.position_ref)}:mark">${escapeHtml(price(position.live_price ?? position.mark_price))}</td>
              <td data-motion-key="pos:${escapeHtml(position.position_ref)}:pnl">${pnl(position.unrealized_pnl_usdt)}${position.r_multiple == null ? "" : `<br><small class="muted">${Number(position.r_multiple).toFixed(2)}R</small>`}</td>
              ${compact ? "" : `<td>${escapeHtml(price(position.stop_price))}</td><td>${escapeHtml(price(position.open_risk_usdt))}${position.market_type === "perpetual" && position.liquidation_buffer_pct != null ? `<br><small class="muted">liq buffer ${escapeHtml(pctText(position.liquidation_buffer_pct))}</small>` : ""}</td>`}
              <td>${SLEEVE_TAGS[position.cohort] ? engineBadge() : modeBadge(position.management_mode)}${position.pending_proposal ? '<br><span class="proposal-flag">re-plan pending</span>' : ""}</td>
              <td><button type="button" class="btn btn--small" data-open-position="${escapeHtml(position.position_ref)}" aria-label="Manage ${escapeHtml(position.display_symbol)}">Manage</button></td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
}

// Trend sleeves (EXP-002) run each strategy in its own sub-account, so one coin can be long in
// one sleeve and short in another; the tag says which sleeve owns the position.
const SLEEVE_TAGS = { "sleeve-don": ["DON", "Donchian 4h breakout"], "sleeve-ts": ["TS", "Time-series momentum"], "sleeve-xs": ["XS", "Cross-sectional momentum"] };

// Sleeve positions are run by the rule-based trend-sleeves engine; AI position review is off for them,
// so they must never be labelled "AI managed".
export function engineBadge() {
  return '<span class="mode-badge mode-badge--engine" title="Entries, exits and trailing stops come from the trend-sleeves engine. AI review is off."><span aria-hidden="true">⚙</span> Engine managed</span>';
}

export function sleeveTag(cohort) {
  const tag = SLEEVE_TAGS[cohort];
  return tag ? ` <span class="sleeve-tag" title="${escapeHtml(tag[1])} sleeve">${escapeHtml(tag[0])}</span>` : "";
}
