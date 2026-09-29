import { HORIZONS, RISK_STYLES } from "../contracts.js";
import { titleCase } from "../format.js";
import { buildSeedData } from "../demoData.js";
import { mountRuntimeSettings, renderRuntimeSettingsShell } from "./runtimeSettings.js";
import { mountPortfolioSettings } from "./portfolioSettings.js";

function options(list, selected) {
  return list
    .map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${titleCase(value)}</option>`)
    .join("");
}

export function render(root, ctx) {
  const { store } = ctx;
  const { settings } = store.getState();

  root.innerHTML = `
    <section class="panel">
      <h1>Settings</h1>
      <p class="panel-subtitle">
        Browser preferences below only affect this browser session and are stored locally.
        Runtime settings (AI providers, exchange accounts, cost &amp; budgets) are held by the
        local PAPER server; credentials go to the OS credential store, never to browser storage.
      </p>
      <form class="settings-form" data-role="settings-form">
        <label>
          Default horizon
          <select name="defaultHorizon">${options(HORIZONS, settings.defaultHorizon)}</select>
        </label>
        <label>
          Default risk lens
          <select name="defaultRiskStyle">${options(RISK_STYLES, settings.defaultRiskStyle)}</select>
        </label>
        <label class="checkbox-row">
          <input type="checkbox" name="compactDensity" ${settings.compactDensity ? "checked" : ""} />
          Compact list density
        </label>
        <label class="checkbox-row">
          <input type="checkbox" name="showAdvancedByDefault" ${settings.showAdvancedByDefault ? "checked" : ""} />
          Expand advanced composer settings by default
        </label>
        <div class="composer-actions">
          <button type="submit" class="btn btn--primary">Save preferences</button>
        </div>
      </form>
    </section>

    <section class="panel" id="settings-portfolio-policy">
      <div class="section-heading"><h2>Portfolio policy</h2><span class="demo-tag">PAPER · every change is journaled</span></div>
      <div data-portfolio-policy><p class="muted">Loading…</p></div>
      <p class="muted small">Experiment strategy, providers, arms, and leverage cohorts live in <a href="#/paper-trading">Research › Paper Trading Lab</a>.</p>
    </section>

    <div data-runtime-settings>${renderRuntimeSettingsShell()}</div>

    <section class="panel">
      <h2>Demo data</h2>
      <p class="panel-subtitle">Reset all runs and decisions back to the original seeded demo content.</p>
      <button type="button" class="btn btn--ghost" data-role="reset-demo">Reset demo data</button>
    </section>
  `;

  root.querySelector('[data-role="settings-form"]').addEventListener("submit", (event) => {
    event.preventDefault();
    const formData = new FormData(event.target);
    store.updateSettings({
      defaultHorizon: formData.get("defaultHorizon"),
      defaultRiskStyle: formData.get("defaultRiskStyle"),
      compactDensity: formData.get("compactDensity") === "on",
      showAdvancedByDefault: formData.get("showAdvancedByDefault") === "on",
    });
    document.body.classList.toggle("density-compact", store.getState().settings.compactDensity);
  });

  mountRuntimeSettings(root.querySelector("[data-runtime-settings]"));
  mountPortfolioSettings(root.querySelector("[data-portfolio-policy]"));

  root.querySelector('[data-role="reset-demo"]').addEventListener("click", () => {
    const seed = buildSeedData();
    store.resetDemoData(seed);
  });
}
