// Portfolio: PAPER Spot and PAPER Perpetual shown together but categorized, plus a visually
// and logically separate REAL Gate mirror (read-only; never merged into PAPER equity).
import { applyMotion } from "../components/motion.js";
import { escapeHtml, formatTimestamp } from "../format.js";
import { paperApi } from "../paperApi.js";
import { renderGrowthChart } from "../components/portfolioSummary.js";
import { renderKpiStrip } from "../components/portfolioKpiStrip.js";
import { handleOrderClick, handleOrderSubmit, renderOrdersPanel } from "../components/ordersPanel.js";
import { renderUnifiedPositions } from "../components/positionsTable.js";
import { feedback, fmtNumber, pct, pnl, price } from "../components/ui.js";
import { renderAccountMirror } from "./runtimeSettings.js";

function bars(rows, { label, value, note = () => "" }) {
  const total = rows.reduce((sum, row) => sum + Math.max(0, value(row)), 0);
  if (!rows.length || total <= 0) return '<p class="muted small">Nothing allocated yet.</p>';
  return `
    <table class="bar-table">
      <caption class="sr-only">${escapeHtml(label)}</caption>
      <tbody>
        ${rows.map((row) => {
          const share = Math.max(0, value(row)) / total;
          return `<tr>
            <th scope="row">${escapeHtml(row.__label)}</th>
            <td class="bar-cell"><span class="bar-fill" style="width:${(share * 100).toFixed(1)}%"></span></td>
            <td class="num">${escapeHtml(fmtNumber(value(row), { digits: 2 }))} USDT</td>
            <td class="num">${escapeHtml(pct(share, { digits: 1 }))}</td>
            <td class="muted small">${escapeHtml(note(row))}</td>
          </tr>`;
        }).join("")}
      </tbody>
    </table>`;
}

function kv(label, value) {
  return `<div><dt>${escapeHtml(label)}</dt><dd>${value}</dd></div>`;
}

export function renderEconomics(paper) {
  return `
    <dl class="kv kv--grid">
      ${kv("Trading PnL (USDT)", pnl(paper.trading_pnl_usdt))}
      ${kv("Realized", pnl(paper.realized_pnl_usdt))}
      ${kv("Unrealized", pnl(paper.unrealized_pnl_usdt))}
      ${kv("Fees", `${escapeHtml(fmtNumber(paper.fees_usdt, { digits: 4 }))} USDT`)}
      ${kv("AI cost (USD)", paper.ai_cost_usd == null ? "—" : `$${Number(paper.ai_cost_usd).toFixed(4)}${paper.ai_cost_complete ? "" : " · incomplete"}`)}
      ${kv("AI cost (USDT)", paper.ai_cost_usdt == null ? "needs FX policy" : escapeHtml(fmtNumber(paper.ai_cost_usdt, { digits: 4 })))}
      ${kv("Economic PnL", paper.economic_pnl_usdt == null ? `<span class="muted">${escapeHtml(paper.economic_unavailable_reason || "unavailable")}</span>` : pnl(paper.economic_pnl_usdt))}
      ${kv("Drawdown now / max", `${escapeHtml(pct(-(paper.drawdown?.current || 0)))} / ${escapeHtml(pct(-(paper.drawdown?.max || 0)))}`)}
    </dl>`;
}

export function renderBrainReview(review) {
  if (!review) return "";
  return `
    <div class="brain-review">
      <p class="small">Portfolio Brain · ${escapeHtml(formatTimestamp(review.as_of))} · gross ${escapeHtml(fmtNumber(review.exposure.gross_exposure_x, { digits: 2 }))}x · long risk ${escapeHtml(pct(review.exposure.long_risk_pct))} · short risk ${escapeHtml(pct(review.exposure.short_risk_pct))}</p>
      <ul class="brain-actions">
        ${review.actions.map((action) => `
          <li><strong>${escapeHtml(action.action.replaceAll("_", " "))}</strong>${action.symbol ? ` · ${escapeHtml(action.symbol)}` : ""}
            <small class="muted">${escapeHtml(action.reason_codes.join(", "))}</small>
            ${action.position_ref ? `<button type="button" class="btn btn--ghost btn--small" data-open-position="${escapeHtml(action.position_ref)}"${action.suggested_intent ? ` data-replan-intent="${escapeHtml(action.suggested_intent)}"` : ""}>Review</button>` : ""}
          </li>`).join("")}
      </ul>
      <p class="muted small">Typed recommendations only; the Portfolio Brain never changes an account and never bypasses the risk engine.</p>
    </div>`;
}

export function render(root, ctx) {
  root.innerHTML = `<div class="view view--portfolio">
    <header class="page-header">
      <div><h1>Portfolio</h1><p>PAPER Spot + PAPER Perpetual · real account mirror is separate and read-only</p></div>
      <div class="button-row">
        <button type="button" class="btn btn--ghost btn--small" data-portfolio-review>Run portfolio review</button>
        <a class="btn btn--primary btn--small" href="#/trade">Trade</a>
      </div>
    </header>
    <div data-kpis></div>
    <div class="paper-feedback" data-portfolio-feedback role="status" aria-live="polite"></div>
    <div data-brain></div>
    <div class="portfolio-os-grid">
      <section class="panel"><h2>Allocation</h2><div data-allocation></div></section>
      <section class="panel"><h2>Exposure by asset</h2><div data-exposure></div></section>
      <section class="panel"><h2>Equity</h2><div data-equity></div></section>
      <section class="panel"><h2>Economics</h2><div data-economics></div></section>
    </div>
    <section class="panel panel--paper" aria-labelledby="perp-heading">
      <div class="section-heading"><h2 id="perp-heading">PAPER Perpetual</h2><span class="demo-tag">isolated margin · simulated</span></div>
      <div data-perp-wallet></div>
      <div data-perp-positions></div>
    </section>
    <section class="panel panel--paper" aria-labelledby="spot-heading">
      <div class="section-heading"><h2 id="spot-heading">PAPER Spot</h2><span class="demo-tag">no leverage · no funding · no liquidation</span></div>
      <div data-spot-wallet></div>
      <div data-spot-holdings></div>
    </section>
    <section class="panel" aria-labelledby="orders-heading">
      <div class="section-heading"><h2 id="orders-heading">PAPER orders</h2>
        <div class="segmented" role="group" aria-label="Order filter">
          <button type="button" data-order-filter="open" aria-pressed="true">Pending</button>
          <button type="button" data-order-filter="all" aria-pressed="false">All</button>
        </div>
      </div>
      <div data-orders></div>
    </section>
    <section class="panel panel--real-account" aria-labelledby="real-heading">
      <div class="section-heading"><h2 id="real-heading">REAL Gate Account Mirror — READ ONLY</h2><span class="demo-tag demo-tag--real">never merged with PAPER · writes BLOCKED BY DESIGN</span></div>
      <div data-real></div>
    </section></div>`;
  const view = root.firstElementChild;
  const note = view.querySelector("[data-portfolio-feedback]");
  let orderFilter = "open";
  let orders = [];
  let disposed = false;

  async function refresh() {
    try {
      const [portfolio, orderPayload, accounts] = await Promise.all([
        paperApi.portfolio(), paperApi.orders(), paperApi.exchangeAccounts().catch(() => ({ accounts: [] })),
      ]);
      if (disposed) return;
      const paper = portfolio.paper;
      orders = orderPayload.orders || [];
      view.querySelector("[data-kpis]").innerHTML = renderKpiStrip(portfolio);
      applyMotion(view.querySelector("[data-kpis]"));
      view.querySelector("[data-allocation]").innerHTML = bars(
        paper.allocation.map((row) => ({ ...row, __label: row.bucket })),
        { label: "Allocation", value: (row) => Number(row.value_usdt), note: (row) => row.market_type === "spot" ? "Spot" : "Perp" },
      );
      view.querySelector("[data-exposure]").innerHTML = paper.exposure.by_asset.length
        ? bars(paper.exposure.by_asset.map((row) => ({ ...row, __label: row.base })), {
          label: "Exposure", value: (row) => Number(row.notional_usdt), note: (row) => `${row.group} · ${pct(row.pct, { digits: 1 })} of equity`,
        }) + `<p class="muted small">Gross ${escapeHtml(fmtNumber(paper.exposure.gross_x, { digits: 2 }))}x · net ${escapeHtml(fmtNumber(paper.exposure.net_usdt, { digits: 2 }))} USDT · top asset ${escapeHtml(paper.exposure.top_asset || "—")} ${escapeHtml(pct(paper.exposure.top_asset_pct, { digits: 1 }))}</p>`
        : '<p class="muted small">No open exposure.</p>';
      view.querySelector("[data-equity]").innerHTML = renderGrowthChart(
        paper.equity_curve.map((point) => ({ as_of: point.as_of, equity: point.total_equity })), paper.starting_capital_usdt,
      ) + `<p class="muted small">Reconciles: perp ${paper.reconciliation.perp_equity_equals_cash_plus_unrealized ? "✓" : "✕"} · spot ${paper.reconciliation.spot_equity_equals_cash_plus_holdings ? "✓" : "✕"}</p>`;
      view.querySelector("[data-economics]").innerHTML = renderEconomics(paper);
      const perp = paper.perpetual.wallet;
      view.querySelector("[data-perp-wallet]").innerHTML = `
        <dl class="kv kv--grid">
          ${kv("Equity", `${escapeHtml(price(perp.equity_usdt))} USDT`)}
          ${kv("Cash", `${escapeHtml(price(perp.cash_balance_usdt))} USDT`)}
          ${kv("Margin used", `${escapeHtml(price(perp.margin_used_usdt))} USDT`)}
          ${kv("Available margin", `${escapeHtml(price(perp.available_margin_usdt))} USDT`)}
          ${kv("Unrealized", pnl(perp.unrealized_pnl_usdt))}
          ${kv("Net PnL", pnl(perp.net_pnl_usdt))}
          ${kv("Funding", pnl(perp.funding_usdt == null ? null : -perp.funding_usdt))}
        </dl>`;
      view.querySelector("[data-perp-positions]").innerHTML = renderUnifiedPositions(paper.perpetual.positions, { emptyMessage: "No open perpetual PAPER positions." });
      const spot = paper.spot.wallet;
      view.querySelector("[data-spot-wallet]").innerHTML = `
        <dl class="kv kv--grid">
          ${kv("Equity", `${escapeHtml(price(spot.equity_usdt))} USDT`)}
          ${kv("Cash (USDT)", `${escapeHtml(price(spot.cash_balance_usdt))}`)}
          ${kv("Reserved for limits", `${escapeHtml(price(spot.reserved_quote_usdt))}`)}
          ${kv("Holdings value", `${escapeHtml(price(spot.holdings_value_usdt))} USDT`)}
          ${kv("Realized", pnl(spot.realized_pnl_usdt))}
          ${kv("Unrealized", pnl(spot.unrealized_pnl_usdt))}
          ${kv("Fees", `${escapeHtml(fmtNumber(spot.fees_usdt, { digits: 4 }))} USDT`)}
        </dl>`;
      view.querySelector("[data-spot-holdings]").innerHTML = renderUnifiedPositions(paper.spot.holdings, { emptyMessage: "No PAPER spot holdings." });
      view.querySelector("[data-orders]").innerHTML = renderOrdersPanel(orders, { filter: orderFilter });
      const real = accounts.accounts || [];
      view.querySelector("[data-real]").innerHTML = real.length
        ? real.map((account) => renderAccountMirror(account)).join("")
        : '<p class="muted small">No Gate account connected. Settings › Exchange Accounts stores read-only keys in the OS credential store.</p>';
    } catch (error) {
      if (!disposed) feedback(note, error.message, "error");
    }
  }

  view.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    try {
      if (button.matches("[data-portfolio-review]")) {
        button.disabled = true;
        const { review } = await paperApi.portfolioReview();
        view.querySelector("[data-brain]").innerHTML = `<section class="panel">${renderBrainReview(review)}</section>`;
      } else if (button.dataset.orderFilter) {
        orderFilter = button.dataset.orderFilter;
        view.querySelectorAll("[data-order-filter]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
        view.querySelector("[data-orders]").innerHTML = renderOrdersPanel(orders, { filter: orderFilter });
      } else {
        await handleOrderClick(event, { api: paperApi, root: view, note, onDone: refresh });
      }
    } catch (error) {
      feedback(note, error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  view.addEventListener("submit", (event) => handleOrderSubmit(event, { api: paperApi, note, onDone: refresh }));

  refresh();
  const timer = setInterval(() => {
    // Do not re-render while the user is editing an inline amend form.
    if (!view.querySelector(".amend-row:not([hidden])")) refresh();
  }, 15000);
  ctx.onPortfolioChange?.(refresh);
  return () => {
    disposed = true;
    clearInterval(timer);
  };
}
