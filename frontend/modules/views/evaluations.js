import { escapeHtml, formatTimestamp, titleCase } from "../format.js";
import { evaluationService } from "../services.js";

const RESULT_TONE = {
  pending: "neutral",
  confirmed: "positive",
  invalidated: "negative",
  mixed: "caution",
};

export function render(root, ctx) {
  const { store } = ctx;
  const { decisions, evaluationDemo } = evaluationService.snapshot(store);

  const countByResult = (result) => decisions.filter((decision) => decision.thesis_result === result).length;
  const metrics = [
    { label: "Demo decisions tracked", value: String(decisions.length) },
    { label: "Confirmed (demo)", value: String(countByResult("confirmed")) },
    { label: "Invalidated (demo)", value: String(countByResult("invalidated")) },
    { label: "Pending (demo)", value: String(countByResult("pending")) },
  ];
  const outcomeRows = [
    { label: "Confirmed", count: countByResult("confirmed"), tone: "positive" },
    { label: "Invalidated", count: countByResult("invalidated"), tone: "negative" },
    { label: "Mixed", count: countByResult("mixed"), tone: "caution" },
    { label: "Pending", count: countByResult("pending"), tone: "neutral" },
  ];
  const maxOutcomeCount = Math.max(1, ...outcomeRows.map((item) => item.count));

  root.innerHTML = `
    <section class="panel">
      <h1>Evaluations</h1>
      <p class="panel-subtitle demo-banner-inline">
        ${escapeHtml(evaluationDemo.summary)}
      </p>
      <div class="metric-row">
        ${metrics.map((metric) => `
          <div class="metric-card">
            <span class="metric-value">${escapeHtml(metric.value)}</span>
            <span class="metric-label">${escapeHtml(metric.label)}</span>
          </div>
        `).join("")}
      </div>
    </section>

    <section class="panel">
      <div class="section-heading">
        <h2>Recorded outcomes</h2>
        <span class="demo-tag">Local fixture decisions</span>
      </div>
      ${decisions.length ? `
        <div class="evaluation-distribution" role="img" aria-label="Decision outcome counts: ${outcomeRows.map((item) => `${item.label} ${item.count}`).join(", ")}">
          ${outcomeRows.map((item) => `
            <div class="evaluation-bar-row">
              <span>${item.label}</span>
              <div class="evaluation-bar-track" aria-hidden="true">
                <span class="evaluation-bar-fill evaluation-bar-fill--${item.tone}" style="width:${(item.count / maxOutcomeCount) * 100}%"></span>
              </div>
              <strong>${item.count}</strong>
            </div>
          `).join("")}
        </div>
      ` : '<p class="muted">No decision outcomes to chart yet.</p>'}
      <p class="chart-caption">Counts come from saved browser-local fixture decisions. No skill-vs-control comparison is connected.</p>
    </section>

    <section class="panel">
      <h2>Decision outcomes (demo)</h2>
      <ul class="list list--table">
        ${decisions.map((decision) => `
          <li class="list-row">
            <a href="#/runs/${decision.runId}" class="list-row-link">
              <span class="pill pill--${RESULT_TONE[decision.thesis_result] ?? "neutral"}">${titleCase(decision.thesis_result)}</span>
              <span class="list-row-title">${escapeHtml(decision.asset)}</span>
              <span class="list-row-meta">
                ${decision.raw_return !== null && decision.raw_return !== undefined ? `Demo return ${(decision.raw_return * 100).toFixed(1)}%` : "Outcome pending"}
              </span>
              <span class="list-row-status">${decision.outcome_known_at ? formatTimestamp(decision.outcome_known_at) : "—"}</span>
            </a>
          </li>
        `).join("") || '<li class="list-empty">No decisions to evaluate yet.</li>'}
      </ul>
    </section>
  `;
}
