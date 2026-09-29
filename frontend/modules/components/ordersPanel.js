// Unified PAPER order list. Cancel applies to pending orders only; Close applies to positions.
import { escapeHtml, formatTimestamp } from "../format.js";
import { emptyState, marketBadge, price, sideBadge, sourceBadge } from "./ui.js";

const STATUS_TONE = { pending: "caution", partially_filled: "caution", working: "caution", filled: "positive", cancelled: "neutral", rejected: "negative" };

export function renderOrdersPanel(orders, { filter = "all", instrumentId = null, limit = 40 } = {}) {
  let rows = orders || [];
  if (instrumentId) rows = rows.filter((order) => order.instrument_id === instrumentId);
  if (filter === "open") rows = rows.filter((order) => ["pending", "partially_filled", "working"].includes(order.status));
  if (!rows.length) return emptyState(filter === "open" ? "No pending PAPER orders." : "No PAPER orders yet.");
  return `
    <div class="table-scroll">
      <table class="data-table orders-table">
        <thead><tr>
          <th scope="col">Instrument</th><th scope="col">Side</th><th scope="col">Type</th><th scope="col">Requested</th>
          <th scope="col">Filled</th><th scope="col">Limit</th><th scope="col">Status</th><th scope="col">Source</th>
          <th scope="col">Created</th><th scope="col"><span class="sr-only">Actions</span></th>
        </tr></thead>
        <tbody>
          ${rows.slice(0, limit).map((order) => `
            <tr>
              <td>${marketBadge(order.market_type)} ${escapeHtml(order.display_symbol)}${order.reduce_only ? ' <small class="muted">reduce-only</small>' : ""}</td>
              <td>${sideBadge(order.market_type === "spot" ? order.side : order.side)}</td>
              <td>${escapeHtml(order.order_type)}</td>
              <td>${escapeHtml(price(order.requested_quantity))}</td>
              <td>${escapeHtml(price(order.filled_quantity))} <small class="muted">(${Math.round((order.filled_pct || 0) * 100)}%)</small></td>
              <td>${escapeHtml(price(order.limit_price))}</td>
              <td><span class="pill pill--${STATUS_TONE[order.status] || "neutral"}">${escapeHtml(order.status.replace("_", " "))}</span></td>
              <td>${sourceBadge(order.source)}</td>
              <td><small>${escapeHtml(formatTimestamp(order.created_at))}</small></td>
              <td class="order-actions">
                ${order.amendable ? `<button type="button" class="btn btn--ghost btn--small" data-amend-order="${escapeHtml(order.order_ref)}" data-limit="${escapeHtml(order.limit_price ?? "")}">Amend</button>` : ""}
                ${order.cancellable ? `<button type="button" class="btn btn--ghost btn--small" data-cancel-order="${escapeHtml(order.order_ref)}" aria-label="Cancel pending ${escapeHtml(order.display_symbol)} order">Cancel</button>` : ""}
                ${order.position_ref ? `<button type="button" class="btn btn--ghost btn--small" data-open-position="${escapeHtml(order.position_ref)}">Position</button>` : ""}
              </td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
}
