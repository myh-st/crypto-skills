// Campaign progress at a glance: day N of the frozen experiment, trades toward the target,
// next checkpoint, capital, FX and AI budget, and any active risk pause/halt.
import { escapeHtml, formatTimestamp } from "../format.js";
import { fmtNumber } from "./ui.js";

const words = (value) => String(value ?? "").replaceAll("_", " ");

export function renderCampaign(summary) {
  if (!summary) return "";
  const trades = Number(summary.completed_trades || 0);
  const target = Number(summary.target_trades || 0) || 1;
  const progress = Math.min(100, (trades / target) * 100);
  const capital = summary.capital_usdt || {};
  const budget = summary.ai_budget;
  const frozen = summary.manifest_version != null;
  const warnings = [];
  if (!summary.fx?.configured) warnings.push("FX policy not set: net economic PnL is unavailable and the gate cannot pass");
  if (summary.drift) warnings.push("configuration drift: record a new manifest version on Evaluations");
  if (budget?.exhausted) warnings.push(`AI budget exhausted: ${words(budget.limit_action)}`);
  for (const incident of summary.risk_incidents || []) warnings.push(`${words(incident.kind)}: ${incident.summary}`);
  return `
    <section class="panel campaign-panel" aria-labelledby="campaign-heading">
      <div class="section-heading">
        <h2 id="campaign-heading">${escapeHtml(summary.experiment_id)} campaign</h2>
        <span class="demo-tag">${frozen ? `manifest v${escapeHtml(summary.manifest_version)}` : "not started · manifest freezes on Start"}</span>
      </div>
      <div class="campaign-grid">
        <div><small>Day</small><strong>${frozen ? escapeHtml(fmtNumber(summary.elapsed_days, { digits: 1 })) : "—"}</strong><span>of ${escapeHtml(summary.min_days)} · ${escapeHtml(words(summary.checkpoint))}</span></div>
        <div><small>Completed trades</small><strong>${escapeHtml(trades)} / ${escapeHtml(summary.target_trades)}</strong>
          <span class="campaign-bar" role="img" aria-label="${escapeHtml(Math.round(progress))}% of the trade target"><span style="width:${progress.toFixed(1)}%"></span></span></div>
        <div><small>Next checkpoint</small><strong>${escapeHtml(words(summary.next_checkpoint?.name || "—"))}</strong><span>${summary.next_checkpoint?.due_at ? escapeHtml(formatTimestamp(summary.next_checkpoint.due_at)) : "after Start"}</span></div>
        <div><small>Capital</small><strong>${escapeHtml(fmtNumber((capital.perpetual || 0) + (capital.spot || 0), { digits: 0 }))} USDT</strong><span>perp ${escapeHtml(fmtNumber(capital.perpetual, { digits: 0 }))} · spot ${escapeHtml(fmtNumber(capital.spot, { digits: 0 }))}</span></div>
        <div><small>AI budget left</small><strong>${budget ? `$${escapeHtml(fmtNumber(budget.remaining_experiment_usd, { digits: 2 }))}` : "—"}</strong><span>${budget ? `of $${escapeHtml(fmtNumber(budget.experiment_cap_usd, { digits: 0 }))} · then ${escapeHtml(words(budget.limit_action).toLowerCase())}` : ""}</span></div>
        <div><small>FX (USDT per USD)</small><strong>${summary.fx?.configured ? escapeHtml(summary.fx.usdt_per_usd) : "not set"}</strong><span>for net economic PnL</span></div>
      </div>
      ${warnings.length ? `<ul class="campaign-warnings small">${warnings.map((w) => `<li>⚠ ${escapeHtml(w)}</li>`).join("")}</ul>` : ""}
    </section>`;
}
