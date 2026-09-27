import { escapeHtml, relativeTime, titleCase } from "../format.js";
import { STATE_TONE } from "../contracts.js";
import { runService } from "../services.js";

export function render(root, ctx) {
  const { store } = ctx;
  const runs = runService.list(store);

  root.innerHTML = `
    <section class="panel">
      <h1>Runs</h1>
      <p class="panel-subtitle">Every demo analysis run in this browser session, most recent first.</p>
      <ul class="list list--table">
        ${runs.map((run) => `
          <li class="list-row">
            <a href="#/runs/${run.id}" class="list-row-link">
              <span class="pill pill--${STATE_TONE[run.report.state] ?? "neutral"}">${titleCase(run.report.state)}</span>
              <span class="list-row-title">${escapeHtml(run.asset)}</span>
              <span class="list-row-meta">${titleCase(run.analysisType)} · ${titleCase(run.horizon)} · ${relativeTime(run.createdAt)}</span>
              <span class="list-row-status">${escapeHtml(run.status)}</span>
            </a>
          </li>
        `).join("") || '<li class="list-empty">No runs yet. Start one from Overview or New Analysis.</li>'}
      </ul>
    </section>
  `;
}
