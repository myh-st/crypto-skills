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

const ARM_LABELS = {
  quant: "Quant", jev: "Jev", luna: "Luna", luna_skill: "Luna + Skill", quant_jev: "Quant + Jev",
  hybrid: "Hybrid", hybrid_brain: "Hybrid + Portfolio Brain",
};

function signedUsdt(value, digits = 2) {
  const number = numericValue(value);
  if (number === null) return "—";
  return `${number > 0 ? "+" : number < 0 ? "−" : ""}${Math.abs(number).toFixed(digits)}`;
}

// Prospective, aligned strategy tournament. Economic PnL subtracts attributed AI cost; nothing
// is promoted on headline PnL, and every row shows its sample size.
export function renderTournament(tournament) {
  if (!tournament?.arms?.length) return '<p class="muted">No aligned arms recorded yet.</p>';
  const rows = tournament.arms.map((arm) => `
    <tr>
      <th scope="row">${escapeHtml(ARM_LABELS[arm.arm] || arm.arm)}</th>
      <td class="num">${escapeHtml(displayValue(arm.closed_trades))}</td>
      <td class="num">${escapeHtml(signedUsdt(arm.net_trading_pnl_usdt))}</td>
      <td class="num">${escapeHtml(signedUsdt(arm.expectancy_usdt, 3))}</td>
      <td class="num">${escapeHtml(percent(arm.win_rate))}</td>
      <td class="num">${arm.profit_factor == null ? "—" : escapeHtml(Number(arm.profit_factor).toFixed(2))}</td>
      <td class="num">${escapeHtml(percent(arm.max_drawdown))}</td>
      <td class="num">${escapeHtml((numericValue(arm.fees_usdt) ?? 0).toFixed(3))} / ${escapeHtml((numericValue(arm.funding_usdt) ?? 0).toFixed(3))} / ${escapeHtml((numericValue(arm.slippage_usdt) ?? 0).toFixed(3))}</td>
      <td class="num">${escapeHtml(displayValue(arm.ai_calls))} · $${escapeHtml((numericValue(arm.ai_cost_usd) ?? 0).toFixed(4))}${arm.ai_cost_unpriced_calls ? ` · ${escapeHtml(arm.ai_cost_unpriced_calls)} unpriced` : ""}</td>
      <td class="num">${arm.economic_pnl_usdt == null ? "—" : escapeHtml(signedUsdt(arm.economic_pnl_usdt))}</td>
      <td class="num">${arm.incremental_vs_quant ? `${escapeHtml(signedUsdt(arm.incremental_vs_quant.trading_pnl_usdt))} / $${escapeHtml(Number(arm.incremental_vs_quant.ai_cost_usd).toFixed(4))}` : "baseline"}</td>
      <td><span class="pill pill--${arm.promotion_status === "BASELINE" ? "neutral" : "caution"}">${escapeHtml(String(arm.promotion_status || "—").replaceAll("_", " ").toLowerCase())}</span></td>
    </tr>`).join("");
  const breakdown = tournament.arms.map((arm) => `
    <details class="arm-breakdown"><summary>${escapeHtml(ARM_LABELS[arm.arm] || arm.arm)} by regime / asset / session</summary>
      ${["by_regime", "by_asset", "by_session"].map((key) => `
        <p class="small"><strong>${escapeHtml(key.replace("by_", ""))}</strong> ${(arm[key] || []).map((row) => `${escapeHtml(row.key)} n=${escapeHtml(row.closed_trades)} ${escapeHtml(signedUsdt(row.net_trading_pnl_usdt))}`).join(" · ") || "—"}</p>`).join("")}
    </details>`).join("");
  return `
    <p class="muted small">${escapeHtml(tournament.aligned_cycles)} aligned cycles · ${escapeHtml(tournament.eligible_cases)} eligible cases · AI filter helped ${escapeHtml(tournament.ai_filter?.AI_FILTER_HELPED ?? 0)} / hurt ${escapeHtml(tournament.ai_filter?.AI_FILTER_HURT ?? 0)} (n=${escapeHtml(tournament.ai_filter?.sample ?? 0)})${tournament.fx?.available ? "" : " · economic PnL needs an FX policy"}</p>
    <div class="table-scroll"><table class="data-table tournament-table">
      <thead><tr><th scope="col">Arm</th><th scope="col">Closed n</th><th scope="col">Net PnL</th><th scope="col">Expectancy</th><th scope="col">Win rate</th>
        <th scope="col">PF</th><th scope="col">Max DD</th><th scope="col">Fees / funding / slip</th><th scope="col">AI calls · cost</th>
        <th scope="col">Economic PnL</th><th scope="col">Δ vs Quant (PnL / AI $)</th><th scope="col">Promotion</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    ${breakdown}
    <p class="small">${escapeHtml(tournament.promotion_rule)}</p>`;
}

export function renderLearning(reviews = [], hypotheses = [], tags = {}) {
  const tagList = Object.entries(tags).sort((a, b) => b[1] - a[1]);
  return `
    <div class="learning-grid">
      <div>
        <h3>Learning tags</h3>
        ${tagList.length ? `<ul class="tag-counts">${tagList.map(([tag, count]) => `<li><span class="cat-badge">${escapeHtml(tag)}</span> ${escapeHtml(count)}</li>`).join("")}</ul>` : '<p class="muted small">No closed trades reviewed yet.</p>'}
        <h3>Improvement hypotheses</h3>
        ${hypotheses.length ? `<ul>${hypotheses.map((item) => `<li><strong>${escapeHtml(item.tag)}</strong> (${escapeHtml(item.evidence_count)} · ${escapeHtml(item.status)}) ${escapeHtml(item.statement)}</li>`).join("")}</ul>` : '<p class="muted small">None yet.</p>'}
        <p class="muted small">Hypotheses are candidates for the next experiment version; the active strategy is never rewritten automatically.</p>
      </div>
      <div>
        <h3>Recent post-trade reviews</h3>
        ${reviews.length ? `<ol class="journal">${reviews.slice(0, 12).map((review) => `<li><strong>${escapeHtml(review.symbol)}</strong> ${escapeHtml(review.outcome)} · ${escapeHtml(review.lesson)} ${(review.tags || []).map((tag) => `<span class="cat-badge">${escapeHtml(tag)}</span>`).join(" ")}
          ${review.position_ref ? `<button type="button" class="link-button small" data-open-position="${escapeHtml(review.position_ref)}">position</button>` : ""}</li>`).join("")}</ol>` : '<p class="muted small">Reviews are generated after positions close.</p>'}
      </div>
    </div>`;
}
