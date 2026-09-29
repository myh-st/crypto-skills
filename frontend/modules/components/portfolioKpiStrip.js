// Primary daily KPI strip: equity, trading PnL, economic PnL after AI cost, drawdown, open
// risk, AI spend. Unavailable values say why instead of showing a fabricated number.
import { escapeHtml } from "../format.js";
import { num, pct, pnl, signedText } from "./ui.js";

function tile(label, valueHtml, note = "", { id = "" } = {}) {
  return `
    <div class="kpi-tile"${id ? ` data-kpi="${id}"` : ""}>
      <span class="kpi-label">${escapeHtml(label)}</span>
      <strong class="kpi-value">${valueHtml}</strong>
      ${note ? `<small class="kpi-note">${escapeHtml(note)}</small>` : ""}
    </div>`;
}

function usdtPlain(value) {
  const number = num(value);
  return number === null ? "—" : `${number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT`;
}

export function renderKpiStrip(portfolio) {
  const paper = portfolio?.paper;
  if (!paper) return "";
  const budget = paper.ai_budget || {};
  const aiSpend = num(budget.spent_today_usd);
  const remaining = num(budget.remaining_today_usd);
  const economic = paper.economic_pnl_usdt;
  return `
    <div class="kpi-strip" role="group" aria-label="Portfolio summary">
      ${tile("Equity", escapeHtml(usdtPlain(paper.total_equity_usdt)), `start ${usdtPlain(paper.starting_capital_usdt)}`, { id: "equity" })}
      ${tile("Trading PnL", pnl(paper.trading_pnl_usdt), paper.trading_pnl_pct === null ? "" : `${pct(paper.trading_pnl_pct, { signed: true })} · after fees/funding/slippage`, { id: "trading" })}
      ${tile(
        "Economic PnL",
        economic === null || economic === undefined ? '<span class="pnl pnl--none">Unavailable</span>' : pnl(economic),
        economic === null || economic === undefined ? paper.economic_unavailable_reason || "AI cost incomplete" : "after AI cost",
        { id: "economic" },
      )}
      ${tile("Drawdown", escapeHtml(pct(-(paper.drawdown?.current || 0))), `max ${pct(-(paper.drawdown?.max || 0))}`, { id: "drawdown" })}
      ${tile("Open risk", escapeHtml(usdtPlain(paper.open_risk_usdt)), paper.open_risk_pct === null ? "" : `${pct(paper.open_risk_pct)} of equity at stops`, { id: "risk" })}
      ${tile(
        "AI spend today",
        escapeHtml(aiSpend === null ? "—" : `$${aiSpend.toFixed(4)}`),
        remaining === null ? "no daily limit" : `$${remaining.toFixed(2)} left${budget.exhausted ? " · EXHAUSTED" : ""}`,
        { id: "ai" },
      )}
    </div>`;
}

export function economicLine(paper) {
  if (!paper) return "";
  return `Trading ${signedText(paper.trading_pnl_usdt)} · AI ${paper.ai_cost_usd == null ? "—" : `$${Number(paper.ai_cost_usd).toFixed(4)}`}`;
}
