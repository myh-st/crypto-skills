import { escapeHtml, formatPrice, formatRange, formatTimestamp, titleCase } from "../format.js";
import { STATE_TONE } from "../contracts.js";
import { decisionService } from "../services.js";

const RESULT_TONE = {
  pending: "neutral",
  confirmed: "positive",
  invalidated: "negative",
  mixed: "caution",
};

export function render(root, ctx) {
  const { store } = ctx;
  const decisions = decisionService.list(store);

  root.innerHTML = `
    <section class="panel">
      <h1>Decisions</h1>
      <p class="panel-subtitle">Decision records saved from demo reports, tracked toward a thesis result.</p>
      <ul class="list list--cards">
        ${decisions.map((decision) => `
          <li class="decision-card">
            <div class="decision-card-header">
              <span class="pill pill--${STATE_TONE[decision.decision_state] ?? "neutral"}">${titleCase(decision.decision_state)}</span>
              <span class="pill pill--${RESULT_TONE[decision.thesis_result] ?? "neutral"}">${titleCase(decision.thesis_result)}</span>
              <strong>${escapeHtml(decision.asset)}</strong>
              <span class="muted">${titleCase(decision.horizon)}</span>
            </div>
            <dl class="decision-meta-grid">
              <div><dt>Entry zone</dt><dd>${formatRange(decision.entry_zone)}</dd></div>
              <div><dt>Invalidation</dt><dd>${formatPrice(decision.invalidation)}</dd></div>
              <div><dt>Targets</dt><dd>${(decision.targets || []).map(formatPrice).join(" · ") || "—"}</dd></div>
              <div><dt>Confidence</dt><dd>${titleCase(decision.confidence)}</dd></div>
            </dl>
            <ul class="decisive-evidence-list">
              ${(decision.decisive_evidence || []).map((point) => `<li>${escapeHtml(point)}</li>`).join("")}
            </ul>
            <p class="muted">Saved ${formatTimestamp(decision.savedAt)}${decision.outcome_known_at ? ` · Outcome known ${formatTimestamp(decision.outcome_known_at)}` : ""}</p>
            <a href="#/runs/${decision.runId}" class="panel-link">Open source report →</a>
          </li>
        `).join("") || '<li class="list-empty">No decisions saved yet. Save one from a report.</li>'}
      </ul>
    </section>
  `;
}
