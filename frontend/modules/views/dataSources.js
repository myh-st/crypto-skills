import { escapeHtml } from "../format.js";

export function render(root, ctx) {
  const { store } = ctx;
  const { dataSources } = store.getState();

  root.innerHTML = `
    <section class="panel">
      <h1>Data Sources</h1>
      <p class="panel-subtitle demo-banner-inline">
        This prototype does not connect to any live market data provider. Every source
        below is a demo placeholder used only to generate illustrative evidence.
      </p>
      <ul class="list list--cards">
        ${dataSources.map((source) => `
          <li class="data-source-card">
            <div class="data-source-card-header">
              <strong>${escapeHtml(source.name)}</strong>
              <span class="pill pill--neutral">Demo · not connected</span>
            </div>
            <p class="muted">${escapeHtml(source.category)}</p>
            <p>${escapeHtml(source.description)}</p>
          </li>
        `).join("")}
      </ul>
    </section>
  `;
}
