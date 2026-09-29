// Unified PAPER order list. Cancel applies to pending orders only; Close applies to positions.
import { escapeHtml, formatTimestamp } from "../format.js";
import { withConfirmation } from "./confirmDialog.js";
import { emptyState, feedback, marketBadge, price, sideBadge, sourceBadge } from "./ui.js";

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
                ${order.amendable ? `<button type="button" class="btn btn--ghost btn--small" data-amend-order="${escapeHtml(order.order_ref)}" aria-expanded="false" aria-controls="amend-${escapeHtml(order.order_ref.replaceAll(":", "-"))}">Amend</button>` : ""}
                ${order.cancellable ? `<button type="button" class="btn btn--ghost btn--small" data-cancel-order="${escapeHtml(order.order_ref)}" aria-label="Cancel pending ${escapeHtml(order.display_symbol)} order">Cancel</button>` : ""}
                ${order.position_ref ? `<button type="button" class="btn btn--ghost btn--small" data-open-position="${escapeHtml(order.position_ref)}">Position</button>` : ""}
              </td>
            </tr>
            ${order.amendable ? `<tr class="amend-row" id="amend-${escapeHtml(order.order_ref.replaceAll(":", "-"))}" hidden>
              <td colspan="10">
                <form class="amend-form" data-amend-form="${escapeHtml(order.order_ref)}">
                  <label>New limit price <input name="limit_price" type="number" step="any" min="0" required value="${escapeHtml(order.limit_price ?? "")}" /></label>
                  ${order.market_type === "spot" ? `<label>Quantity <input name="quantity" type="number" step="any" min="0" value="${escapeHtml(order.requested_quantity ?? "")}" /></label>` : `<label>Stop <input name="stop_price" type="number" step="any" min="0" value="${escapeHtml(order.stop_price ?? "")}" /></label>`}
                  <button type="submit" class="btn btn--primary btn--small">Preview &amp; replace</button>
                  <button type="button" class="btn btn--ghost btn--small" data-amend-dismiss="${escapeHtml(order.order_ref)}">Keep order</button>
                  <small class="muted">Amend is cancel/replace: the server re-validates risk and balances before the old order is cancelled.</small>
                </form>
              </td>
            </tr>` : ""}`).join("")}
        </tbody>
      </table>
    </div>`;
}

function amendRow(root, ref) {
  return root.querySelector(`#amend-${CSS.escape(ref.replaceAll(":", "-"))}`);
}

// Shared order actions for any view that renders renderOrdersPanel: cancel (pending only),
// inline amend (cancel/replace, re-validated server-side). Returns true when handled.
export async function handleOrderClick(event, { api, root, note, onDone }) {
  const cancel = event.target.closest("[data-cancel-order]");
  const amend = event.target.closest("[data-amend-order]");
  const dismiss = event.target.closest("[data-amend-dismiss]");
  if (!cancel && !amend && !dismiss) return false;
  if (amend || dismiss) {
    const ref = (amend || dismiss).dataset.amendOrder || dismiss.dataset.amendDismiss;
    const row = amendRow(root, ref);
    const toggle = root.querySelector(`[data-amend-order="${CSS.escape(ref)}"]`);
    if (row) {
      row.hidden = dismiss ? true : !row.hidden;
      toggle?.setAttribute("aria-expanded", String(!row.hidden));
      if (!row.hidden) row.querySelector("input")?.focus();
      else toggle?.focus();
    }
    return true;
  }
  cancel.disabled = true;
  try {
    await api.cancelOrder(cancel.dataset.cancelOrder);
    feedback(note, "Pending PAPER order cancelled.", "success");
    await onDone();
  } catch (error) {
    feedback(note, error.message, "error");
  } finally {
    cancel.disabled = false;
  }
  return true;
}

export async function handleOrderSubmit(event, { api, note, onDone }) {
  const form = event.target.closest("[data-amend-form]");
  if (!form) return false;
  event.preventDefault();
  const patch = {};
  for (const name of ["limit_price", "quantity", "stop_price"]) {
    const input = form.elements.namedItem(name);
    if (input && String(input.value).trim() !== "") patch[name] = Number(input.value);
  }
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    const result = await withConfirmation(() => api.amendOrder(form.dataset.amendForm, patch));
    const order = result?.order;
    if (order && order.accepted === false) feedback(note, `Amend rejected · ${order.code}: ${order.reason}. The original order is unchanged.`, "error");
    else if (order) feedback(note, "Order amended (cancel/replace).", "success");
    await onDone();
  } catch (error) {
    feedback(note, error.message, "error");
  } finally {
    submit.disabled = false;
  }
  return true;
}
