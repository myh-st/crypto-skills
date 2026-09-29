// Risk-first manual PAPER ticket. The browser sends intent (side, stop, targets, risk %);
// the server derives quantity, notional, margin, max loss, fees, slippage, and liquidation,
// using the same deterministic RiskEngine / SpotRiskEngine and fill path as AI trades.
import { escapeHtml } from "../format.js";
import { feedback, fmtNumber, pct, pnl, price, uid } from "./ui.js";

function field(label, input, help = "") {
  return `<label class="ticket-field"><span>${escapeHtml(label)}</span>${input}${help ? `<small>${escapeHtml(help)}</small>` : ""}</label>`;
}

export function ticketBody(form, instrument) {
  const data = new FormData(form);
  const numberOrNull = (name) => {
    const raw = String(data.get(name) ?? "").trim();
    return raw === "" ? null : Number(raw);
  };
  const orderType = data.get("order_type") || "market";
  const targets = ["tp1", "tp2", "tp3"].map(numberOrNull).filter((value) => value !== null && Number.isFinite(value));
  const body = { instrument_id: instrument.instrument_id, action: data.get("action"), order_type: orderType };
  if (orderType === "limit") body.limit_price = numberOrNull("limit_price");
  if (instrument.market_type === "perpetual") {
    body.risk_pct = (numberOrNull("risk_pct") ?? 1) / 100;
    body.stop_price = numberOrNull("stop_price");
    body.targets = targets;
    body.leverage = Number(data.get("leverage") || 3);
  } else {
    const mode = data.get("size_mode") || "quote";
    const amount = numberOrNull("amount");
    if (mode === "quote") body.quote_amount = amount;
    else if (mode === "base") body.quantity = amount;
    else body.allocation_target_pct = amount === null ? null : amount / 100;
    const stop = numberOrNull("stop_price");
    if (stop !== null && body.action === "buy") body.stop_price = stop;
    if (targets.length && body.action === "buy") body.targets = targets;
  }
  return body;
}

export function previewReady(body, instrument) {
  if (!body.action) return false;
  if (body.order_type === "limit" && !(body.limit_price > 0)) return false;
  if (instrument.market_type === "perpetual") return body.stop_price > 0 && body.targets.length > 0;
  return [body.quote_amount, body.quantity, body.allocation_target_pct].some((value) => value !== undefined && value !== null && Number.isFinite(value) && value >= 0);
}

export function submitLabel(instrument, action) {
  if (instrument.market_type === "perpetual") return action === "short" ? "Simulate Short" : "Simulate Long";
  return action === "sell" ? "Simulate Sell" : "Simulate Buy";
}

export function renderPreview(preview) {
  if (!preview) return '<p class="muted small">Enter levels to see the server-side risk preview.</p>';
  const perp = preview.market_type === "perpetual";
  const rows = perp
    ? [
      ["Quantity", price(preview.quantity)],
      ["Notional", `${price(preview.notional_usdt)} USDT`],
      ["Margin", `${price(preview.margin_usdt)} USDT · ${preview.leverage ?? "—"}x`],
      ["Max loss at stop", `${fmtNumber(preview.max_loss_usdt, { digits: 2 })} USDT`],
      ["Est. entry", price(preview.estimated_entry_price)],
      ["Est. fees (entry + stop exit)", `${fmtNumber((preview.estimated_entry_fee_usdt || 0) + (preview.estimated_exit_fee_at_stop_usdt || 0), { digits: 4 })} USDT`],
      ["Est. slippage", `${fmtNumber(preview.estimated_slippage_usdt, { digits: 4 })} USDT`],
      ["Liquidation", `${price(preview.liquidation_price)}${preview.liquidation_buffer_pct == null ? "" : ` (${pct(preview.liquidation_buffer_pct)} away)`}`],
      ["Reward : risk (TP1)", preview.reward_risk == null ? "—" : `${Number(preview.reward_risk).toFixed(2)} : 1`],
    ]
    : [
      ["Quantity", price(preview.quantity)],
      ["Quote amount", `${price(preview.quote_amount_usdt)} USDT`],
      ["Est. fill", price(preview.estimated_fill_price)],
      ["Fee", `${fmtNumber(preview.fee, { digits: 4 })} USDT`],
      ["Slippage", `${fmtNumber(preview.slippage_cost, { digits: 4 })} USDT`],
      ["Resulting allocation", pct(preview.resulting_allocation_pct)],
      ["Cash remaining", `${price(preview.cash_remaining_usdt)} USDT`],
    ];
  const brain = preview.portfolio_brain;
  return `
    <div class="ticket-preview ticket-preview--${preview.allowed ? "ok" : "blocked"}" aria-live="polite">
      <p class="ticket-verdict"><strong>${preview.allowed ? "✓ Risk approved" : `✕ ${escapeHtml(preview.code)}`}</strong>${preview.allowed ? "" : ` · ${escapeHtml(preview.reason)}`}</p>
      <dl class="kv">${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>
      ${brain ? `<p class="small">Portfolio Brain: <strong>${escapeHtml(brain.action)}</strong> · ${escapeHtml((brain.reason_codes || []).join(", "))}${brain.advisories?.length ? ` · ${escapeHtml(brain.advisories.join(", "))}` : ""}</p>` : ""}
      ${(preview.warnings || []).length ? `<p class="small warn-text">⚠ ${escapeHtml(preview.warnings.join(" · "))}</p>` : ""}
      <p class="muted small">Quote ${escapeHtml(preview.quote?.source || "—")} · ${preview.quote?.fresh ? "fresh" : "STALE"} · server-authoritative PAPER preview</p>
    </div>`;
}

export function mountTradeTicket(host, { api, instrument, lastPrice = null, onResult = () => {} }) {
  const perp = instrument.market_type === "perpetual";
  const maxLeverage = Math.min(10, Number(instrument.max_leverage) || 10);
  host.innerHTML = `
    <form class="trade-ticket" data-ticket novalidate>
      <div class="segmented segmented--full" role="radiogroup" aria-label="Side">
        ${(perp ? [["long", "Long"], ["short", "Short"]] : [["buy", "Buy"], ["sell", "Sell"]]).map(([value, label], index) => `
          <label class="segmented-option segmented-option--${index === 0 ? "pos" : "neg"}"><input type="radio" name="action" value="${value}" ${index === 0 ? "checked" : ""} /> ${label}</label>`).join("")}
      </div>
      <div class="segmented segmented--full" role="radiogroup" aria-label="Order type">
        <label class="segmented-option"><input type="radio" name="order_type" value="market" checked /> Market</label>
        <label class="segmented-option"><input type="radio" name="order_type" value="limit" /> Limit</label>
      </div>
      <div class="ticket-grid">
        ${field("Limit price", '<input name="limit_price" type="number" step="any" min="0" disabled />')}
        ${perp ? `
          ${field("Risk % of perp equity", '<input name="risk_pct" type="number" step="0.05" min="0.05" max="2" value="1" />', "size is derived from risk ÷ stop distance")}
          ${field("Stop", '<input name="stop_price" type="number" step="any" min="0" required />')}
          ${field("Leverage", `<select name="leverage">${[1, 2, 3, 5, 10].filter((value) => value <= maxLeverage).map((value) => `<option value="${value}" ${value === 3 ? "selected" : ""}>${value}x</option>`).join("")}</select>`)}
        ` : `
          ${field("Size by", '<select name="size_mode"><option value="quote">Quote (USDT)</option><option value="base">Base quantity</option><option value="allocation">Target allocation %</option></select>')}
          ${field("Amount", '<input name="amount" type="number" step="any" min="0" />')}
          ${field("Protection stop (optional)", '<input name="stop_price" type="number" step="any" min="0" />')}
        `}
        ${field("TP1", '<input name="tp1" type="number" step="any" min="0" />')}
        ${field("TP2 (optional)", '<input name="tp2" type="number" step="any" min="0" />')}
        ${field("TP3 (optional)", '<input name="tp3" type="number" step="any" min="0" />')}
      </div>
      <button type="button" class="link-button small" data-fill-levels>Fill ${perp ? "1.5% stop / 2% · 4% targets" : "5% stop / 5% target"} from last price</button>
      <div data-ticket-preview>${renderPreview(null)}</div>
      <button type="submit" class="btn btn--primary btn--block" data-ticket-submit disabled>${submitLabel(instrument, perp ? "long" : "buy")}</button>
      <p class="muted small">PAPER simulation · Gate live orders are blocked by design.</p>
      <div class="paper-feedback" data-ticket-feedback role="status" aria-live="polite"></div>
    </form>`;
  const form = host.querySelector("[data-ticket]");
  const submit = host.querySelector("[data-ticket-submit]");
  const previewHost = host.querySelector("[data-ticket-preview]");
  let timer = null;
  let lastPreview = null;
  let sequence = 0;
  let referencePrice = lastPrice;

  async function refreshPreview() {
    const body = ticketBody(form, instrument);
    submit.textContent = submitLabel(instrument, body.action);
    submit.classList.toggle("btn--danger", body.action === "short" || body.action === "sell");
    if (!previewReady(body, instrument)) {
      previewHost.innerHTML = renderPreview(null);
      submit.disabled = true;
      return;
    }
    const mine = ++sequence;
    try {
      const { preview } = await api.previewOrder(body);
      if (mine !== sequence) return;
      lastPreview = preview;
      previewHost.innerHTML = renderPreview(preview);
      submit.disabled = !preview.allowed;
    } catch (error) {
      if (mine !== sequence) return;
      lastPreview = null;
      previewHost.innerHTML = `<p class="paper-feedback paper-feedback--error">${escapeHtml(error.message)}</p>`;
      submit.disabled = true;
    }
  }

  form.addEventListener("input", (event) => {
    if (event.target.name === "order_type") {
      form.elements.namedItem("limit_price").disabled = event.target.value !== "limit";
    }
    clearTimeout(timer);
    timer = setTimeout(refreshPreview, 350);
  });
  form.addEventListener("click", (event) => {
    if (!event.target.closest("[data-fill-levels]")) return;
    const reference = Number(referencePrice);
    if (!Number.isFinite(reference) || reference <= 0) {
      feedback(host.querySelector("[data-ticket-feedback]"), "No live price yet.", "error");
      return;
    }
    const action = new FormData(form).get("action");
    const sign = action === "short" || action === "sell" ? -1 : 1;
    const round = (value) => Number(value.toPrecision(6));
    if (perp) {
      form.elements.namedItem("stop_price").value = round(reference * (1 - sign * 0.015));
      form.elements.namedItem("tp1").value = round(reference * (1 + sign * 0.02));
      form.elements.namedItem("tp2").value = round(reference * (1 + sign * 0.04));
    } else if (action === "buy") {
      form.elements.namedItem("stop_price").value = round(reference * 0.95);
      form.elements.namedItem("tp1").value = round(reference * 1.05);
      if (!form.elements.namedItem("amount").value) form.elements.namedItem("amount").value = 20;
    }
    refreshPreview();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!lastPreview?.allowed) return;
    submit.disabled = true;
    const note = host.querySelector("[data-ticket-feedback]");
    try {
      const { order } = await api.createOrder({ ...ticketBody(form, instrument), client_request_id: uid("ticket") });
      if (order.accepted) {
        feedback(note, `PAPER ${order.status}${order.position_ref ? " · position open" : ""}.`, "success");
      } else {
        feedback(note, `Rejected · ${order.code}: ${order.reason}`, "error");
      }
      onResult(order);
    } catch (error) {
      feedback(note, error.message, "error");
    } finally {
      refreshPreview();
    }
  });
  return {
    setPrice(value) { referencePrice = value; },
    refresh: refreshPreview,
  };
}

export { pnl };
