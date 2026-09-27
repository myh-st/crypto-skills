import { escapeHtml, titleCase } from "../format.js";
import { STATE_TONE } from "../contracts.js";
import { runService } from "../services.js";

export function render(root, ctx) {
  const { store } = ctx;
  const state = store.getState();

  root.innerHTML = `
    <section class="panel">
      <h1>Watchlist <span class="demo-tag">Agent-aware</span></h1>
      <p class="panel-subtitle">
        Assets being monitored by demo research agents, with the agent's latest note and
        linked run. Agents here are illustrative labels only; no autonomous process is
        actually running.
      </p>
      <ul class="list list--cards">
        ${state.watchlist.map((entry) => {
          const linkedRun = runService.get(store, entry.linkedRunId);
          const tone = linkedRun ? STATE_TONE[linkedRun.report.state] ?? "neutral" : "neutral";
          return `
            <li class="watchlist-card">
              <div class="watchlist-card-header">
                <span class="pill pill--${tone}">${escapeHtml(entry.asset)}</span>
                <span class="agent-tag">${escapeHtml(entry.agent)}</span>
              </div>
              <p>${escapeHtml(entry.note)}</p>
              <div class="watchlist-card-actions">
                ${linkedRun ? `<a href="#/runs/${linkedRun.id}" class="panel-link">View latest report →</a>` : ""}
                <a href="#/new-analysis/${entry.asset}" class="panel-link">Run new analysis →</a>
              </div>
            </li>
          `;
        }).join("")}
      </ul>
    </section>
  `;
}
