// AI cost ledger, budget events, and trading-vs-economic PnL rendering.
import { escapeHtml, formatTimestamp } from "../format.js";
import { money, usd, usdCost, percent, displayValue } from "../numbers.js";
import { metric } from "./ui.js";

export function renderProviderUsage(rows) {
  if (!rows?.length) return '<p class="muted">No provider calls have been recorded.</p>';
  return `
    <div class="table-scroll"><table class="data-table">
      <thead><tr><th>Provider</th><th>Model</th><th>Calls</th><th>Input / output tokens</th><th>p50 / p95</th><th>Cost</th></tr></thead>
      <tbody>${rows.map((provider) => `
        <tr>
          <td>${escapeHtml(provider.provider_id)}<br /><small>${escapeHtml(provider.provider_kind)}</small></td>
          <td>${escapeHtml(provider.model)}</td>
          <td>${escapeHtml(provider.successful_call_count)} / ${escapeHtml(provider.call_count)}</td>
          <td>${escapeHtml(provider.input_tokens)} / ${escapeHtml(provider.output_tokens)}</td>
          <td>${provider.latency_p50_ms === null ? "—" : `${provider.latency_p50_ms.toFixed(1)} / ${provider.latency_p95_ms.toFixed(1)} ms`}</td>
          <td>${provider.cost_estimate_usd === null ? "Unpriced" : usd(provider.cost_estimate_usd)}</td>
        </tr>
      `).join("")}</tbody>
    </table></div>
  `;
}

export function renderEconomics(economics) {
  if (!economics) return "";
  const t = economics.trading;
  const ai = economics.ai_cost;
  const k = economics.kpis;
  const net = economics.net_economic_pnl_usdt;
  return `
    <div class="paper-dashboard-grid">
      <section class="paper-subpanel">
        <h3>Trading PnL (USDT)</h3>
        <dl class="paper-definition-list">
          <div><dt>Gross trading PnL</dt><dd>${escapeHtml(money(t.gross_trading_pnl_usdt, 4))}</dd></div>
          <div><dt>Fees</dt><dd>${escapeHtml(money(-t.fees_usdt, 4))}</dd></div>
          <div><dt>Funding</dt><dd>${escapeHtml(money(-t.funding_usdt, 4))}</dd></div>
          <div><dt>Slippage</dt><dd>${escapeHtml(money(-t.slippage_usdt, 4))}</dd></div>
          <div><dt>Unrealized</dt><dd>${escapeHtml(money(t.unrealized_pnl_usdt, 4))}</dd></div>
          <div><dt>Net trading PnL</dt><dd><strong>${escapeHtml(money(t.net_trading_pnl_usdt, 4))}</strong></dd></div>
        </dl>
      </section>
      <section class="paper-subpanel">
        <h3>AI cost (USD) and net economics</h3>
        <dl class="paper-definition-list">
          <div><dt>Jev cost</dt><dd>${escapeHtml(usdCost(ai.jev_cost_usd))}</dd></div>
          <div><dt>GPT cost</dt><dd>${escapeHtml(usdCost(ai.gpt_cost_usd))}</dd></div>
          <div><dt>Total AI cost</dt><dd><strong>${escapeHtml(usdCost(ai.total_ai_cost_usd))}</strong>${ai.complete ? "" : ` · ${ai.calls_with_unavailable_cost} unpriced`}</dd></div>
          <div><dt>FX policy</dt><dd>${economics.fx.mode === "manual" ? `${escapeHtml(economics.fx.usdt_per_usd)} USDT/USD · ${escapeHtml(economics.fx.source)}` : "none"}</dd></div>
          <div><dt>Net experiment economics</dt><dd><strong>${net === null ? "Unavailable" : escapeHtml(money(net, 4))}</strong></dd></div>
        </dl>
        ${net === null ? `<p class="field-help">${escapeHtml(economics.net_economic_unavailable_reason || "")}</p>` : ""}
      </section>
    </div>
    <div class="paper-metric-grid">
      ${metric("AI cost / analysis", usdCost(k.ai_cost_per_analysis_usd), `${k.denominators.analyses} analyses`)}
      ${metric("AI cost / eligible case", usdCost(k.ai_cost_per_eligible_case_usd), `${k.denominators.eligible_cases} eligible`)}
      ${metric("AI cost / trade", usdCost(k.ai_cost_per_trade_usd), `${k.denominators.closed_trades} closed`)}
      ${metric("AI cost / winning trade", usdCost(k.ai_cost_per_winning_trade_usd), `${k.denominators.winning_trades} wins`)}
      ${metric("AI cost % of gross profit", k.ai_cost_pct_of_gross_profit == null ? "—" : percent(k.ai_cost_pct_of_gross_profit), "needs FX policy")}
      ${metric("Net economic expectancy / trade", k.net_economic_expectancy_per_trade_usdt == null ? "—" : money(k.net_economic_expectancy_per_trade_usdt, 4), "after AI cost")}
      ${metric("Cost on NO_TRADE", usdCost(ai.cost_on_no_trade_usd), "paid analyses without entry")}
      ${metric("GPT escalation cost", usdCost(ai.gpt_escalation_cost_usd), "hybrid route")}
    </div>
    <p class="chart-caption">Aligned-arm AI value: ${escapeHtml(economics.aligned_arm_value.status.replaceAll("_", " "))}
      (quant n=${escapeHtml(economics.aligned_arm_value.sample.quant_closed)}, hybrid n=${escapeHtml(economics.aligned_arm_value.sample.hybrid_closed)}; no causal claim).</p>`;
}

export function renderLedgerRows(rows) {
  if (!rows?.length) return '<tr><td colspan="10" class="muted">No AI calls recorded.</td></tr>';
  return rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.provider_id)}</td>
      <td>${escapeHtml(row.model)}${row.returned_models?.length ? `<br><small>${escapeHtml(row.returned_models.join(", "))}</small>` : ""}</td>
      <td>${escapeHtml(row.reasoning_effort || "—")}</td>
      <td>${escapeHtml(row.calls)} (${escapeHtml(row.real_external_calls)} real)</td>
      <td>${escapeHtml(displayValue(row.input_tokens))} / ${escapeHtml(displayValue(row.output_tokens))}</td>
      <td>${escapeHtml(displayValue(row.reasoning_tokens))}</td>
      <td>${row.latency_p50_ms == null ? "—" : `${Number(row.latency_p50_ms).toFixed(0)} / ${Number(row.latency_p95_ms).toFixed(0)} ms`}</td>
      <td>${escapeHtml(usdCost(row.estimated_cost_usd))}${row.calls_with_unavailable_cost ? ` · ${row.calls_with_unavailable_cost} unpriced` : ""}</td>
      <td>${escapeHtml(usdCost(row.billed_cost_usd))}</td>
      <td>${escapeHtml(row.budget_blocks)}</td>
    </tr>`).join("");
}

export function renderBudgetEvents(events) {
  const shown = (events || []).filter((event) => !["reserved", "reconciled", "released"].includes(event.event_type)).slice(0, 12);
  if (!shown.length) return '<p class="muted">No budget warnings, blocks, or fallbacks.</p>';
  return `<ul class="paper-activity-list">${shown.map((event) => `
    <li><strong>${escapeHtml(event.event_type)} · ${escapeHtml(event.code)}</strong>
      <span>${escapeHtml(event.scope || "")} · ${escapeHtml(event.limit_action || "")} · ${escapeHtml(formatTimestamp(event.created_at))}</span></li>`).join("")}</ul>`;
}
