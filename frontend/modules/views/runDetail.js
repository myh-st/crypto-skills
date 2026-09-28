import { escapeHtml, formatPrice, formatRange, titleCase, formatTimestamp } from "../format.js";
import { STATE_TONE } from "../contracts.js";
import { renderPriceChart } from "../components/priceChart.js";
import { renderEvidenceDrawer } from "../components/evidenceDrawer.js";
import { decisionService, runService } from "../services.js";

function scenarioMapMarkup(scenarioMap) {
  if (!scenarioMap || scenarioMap.length === 0) return "<p class=\"muted\">No scenario map generated.</p>";
  return `
    <ul class="scenario-list">
      ${scenarioMap.map((scenario) => `
        <li class="scenario-item">
          <div class="scenario-item-header">
            <strong>${escapeHtml(scenario.scenario)}</strong>
            <span class="pill pill--quality-${escapeHtml(scenario.probability === "high" ? "high" : scenario.probability === "medium" ? "medium" : "low")}">${titleCase(scenario.probability)} likelihood</span>
          </div>
          <p><strong>Trigger:</strong> ${escapeHtml(scenario.trigger)}</p>
          <p><strong>Action:</strong> ${escapeHtml(scenario.action)}</p>
        </li>
      `).join("")}
    </ul>
  `;
}

export function render(root, ctx) {
  const { store, params, navigate } = ctx;
  const runId = params?.[0];
  const run = runService.get(store, runId);

  if (!run) {
    root.innerHTML = `
      <section class="panel">
        <h1>Run not found</h1>
        <p>This demo run is not in this browser session. <a href="#/runs">Back to Runs</a>.</p>
      </section>
    `;
    return;
  }

  const { report, priceLevels, marketStructure, leverage, evidence } = run;
  const requestSettings = run.requestSettings;
  const liveRun = run.runtimeMode === "live";
  const tone = STATE_TONE[report.state] ?? "neutral";
  const alreadySaved = decisionService.list(store).some((decision) => decision.runId === run.id);
  const fundingRate = Number.isFinite(leverage.fundingRate)
    ? `${(leverage.fundingRate * 100).toFixed(2)}%`
    : "Not covered by Spot candles";
  const evaluationStatus = run.evaluationStatus || "waiting_for_outcome";
  const evaluationMessage = evaluationStatus === "scored"
    ? "The configured horizon closed and separate outcome data was scored."
    : evaluationStatus === "ready_to_score"
      ? "The horizon is closed. Fetch and score the separate outcome when ready."
      : "Prediction frozen; outcome remains pending until the configured horizon closes.";

  let filters = { type: "all", supports: "all", quality: "all" };
  let drawerOpen = false;
  let chartRange = "1M";
  let fitAllLevels = !liveRun;

  function renderReport() {
    root.innerHTML = `
      <section class="panel panel--report-header">
        <div class="report-heading">
          <div>
            <span class="eyebrow">${escapeHtml(run.asset)} · ${titleCase(run.analysisType)}</span>
            <span class="demo-tag">${liveRun
              ? "Binance Spot snapshot · forward paper evaluation"
              : "Fixture output · not investment advice"}</span>
            <h1>
              <span class="pill pill--${tone} pill--large">${titleCase(report.state)}</span>
              <span class="confidence-label">Confidence: ${titleCase(report.confidence)}</span>
            </h1>
          </div>
          <div class="report-actions">
            ${liveRun
              ? '<button type="button" class="btn btn--primary" disabled>Frozen forward prediction ✓</button>'
              : `<button type="button" class="btn btn--primary" data-role="save-decision" ${alreadySaved ? "disabled" : ""}>
                  ${alreadySaved ? "Decision saved ✓" : "Save decision"}
                </button>`}
            <button type="button" class="btn btn--ghost" data-role="open-evidence">Open evidence (${evidence.evidence_ledger.length})</button>
          </div>
        </div>

        <div class="report-levels-grid">
          <div class="report-level">
            <span class="report-level-label">Entry zone</span>
            <span class="report-level-value">${formatRange(priceLevels.entryZone)}</span>
            <span class="report-level-sub">Secondary: ${formatRange(priceLevels.secondaryEntry)}</span>
          </div>
          <div class="report-level">
            <span class="report-level-label">Invalidation</span>
            <span class="report-level-value report-level-value--negative">${formatPrice(priceLevels.invalidation)}</span>
            <span class="report-level-sub">${escapeHtml(report.invalidation)}</span>
          </div>
          <div class="report-level">
            <span class="report-level-label">Targets</span>
            <span class="report-level-value">${priceLevels.targets.map(formatPrice).join(" · ") || "—"}</span>
            <span class="report-level-sub">Horizon: ${escapeHtml(run.horizon.replace("_", " "))}</span>
          </div>
        </div>
      </section>

      <section class="panel">
        <h2>Price-level chart <span class="demo-tag">${liveRun ? "Closed Binance Spot candles" : "Demo data"}</span></h2>
        ${renderPriceChart(run, chartRange, fitAllLevels)}
      </section>

      <section class="panel">
        <h2>Rationale</h2>
        <ol class="rationale-list">
          ${report.reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}
        </ol>
      </section>

      <div class="panel-grid">
        <section class="panel">
          <h2>Market structure</h2>
          <p><strong>Trend:</strong> ${escapeHtml(marketStructure.trend)}</p>
          <p>${escapeHtml(marketStructure.notes)}</p>
          <ul class="key-level-list">
            ${marketStructure.keyLevels.map((level) => `<li><span>${escapeHtml(level.label)}</span><span>${formatPrice(level.price)}</span></li>`).join("")}
          </ul>
        </section>

        <section class="panel">
          <h2>Leverage</h2>
          <p><strong>State:</strong> ${titleCase(leverage.state)}</p>
          <p><strong>Funding rate${liveRun ? "" : " (demo)"}:</strong> ${escapeHtml(fundingRate)}</p>
          <p><strong>Open interest trend:</strong> ${titleCase(leverage.openInterestTrend)}</p>
          <p>${escapeHtml(leverage.notes)}</p>
        </section>
      </div>

      <section class="panel">
        <h2>Scenario map</h2>
        ${scenarioMapMarkup(report.scenario_map)}
      </section>

      <section class="panel">
        <h2>Risk</h2>
        <p>${escapeHtml(report.risk)}</p>
        <p class="muted">Data quality: ${escapeHtml(report.data_quality)}</p>
        <p class="muted">Generated ${formatTimestamp(run.createdAt)}</p>
      </section>

      ${liveRun ? `
        <section class="panel" data-role="forward-evaluation">
          <div class="section-heading">
            <h2>Forward evaluation</h2>
            <span class="pill pill--neutral">${titleCase(evaluationStatus.replaceAll("_", " "))}</span>
          </div>
          <p>${escapeHtml(evaluationMessage)}</p>
          <dl class="request-context-grid">
            <div><dt>Frozen case</dt><dd>${escapeHtml(run.caseId)}</dd></div>
            <div><dt>Prediction</dt><dd>${escapeHtml(run.predictionId)}</dd></div>
            <div><dt>Data cutoff</dt><dd>${formatTimestamp(requestSettings.dataAsOf)}</dd></div>
            <div><dt>Outcome horizon closes</dt><dd>${formatTimestamp(run.horizonClosesAt)}</dd></div>
            <div><dt>Dataset hash</dt><dd>${escapeHtml(run.datasetHash)}</dd></div>
          </dl>
          <p class="muted">Skill/control use the same snapshot and model configuration; outcomes are kept separate from both prompts.</p>
        </section>
      ` : ""}

      ${requestSettings ? `
        <details class="panel report-request-context">
          <summary>Request context <span class="demo-tag">${liveRun ? "Runtime metadata" : "Fixture metadata"}</span></summary>
          <dl class="request-context-grid">
            <div><dt>Venue</dt><dd>${escapeHtml(requestSettings.venue)}</dd></div>
            <div><dt>Instrument</dt><dd>${escapeHtml(requestSettings.instrument)}</dd></div>
            <div><dt>Model</dt><dd>${escapeHtml(requestSettings.model)}${liveRun ? "" : " · no live model call"}</dd></div>
            <div><dt>Data as of</dt><dd>${formatTimestamp(requestSettings.dataAsOf)}</dd></div>
            <div><dt>Reference currency</dt><dd>${escapeHtml(requestSettings.referenceCurrency)}</dd></div>
            ${run.capital !== null && run.capital !== undefined ? `<div><dt>Portfolio capital</dt><dd>${escapeHtml(requestSettings.referenceCurrency)} ${Number(run.capital).toLocaleString("en-US")}</dd></div>` : ""}
            <div><dt>Risk preference</dt><dd>${titleCase(run.riskStyle)}</dd></div>
            <div><dt>Requested sources</dt><dd>${requestSettings.dataProviders.map(escapeHtml).join(", ") || "None selected"}</dd></div>
          </dl>
        </details>
      ` : ""}

      <aside class="evidence-panel${drawerOpen ? " evidence-panel--open" : ""}" data-role="evidence-panel">
        ${renderEvidenceDrawer(evidence.evidence_ledger, filters)}
        <button type="button" class="btn btn--ghost evidence-close" data-role="close-evidence">Close evidence</button>
      </aside>
    `;

    attachHandlers();
  }

  function attachHandlers() {
    root.querySelectorAll("[data-chart-range]").forEach((button) => {
      button.addEventListener("click", () => {
        chartRange = button.dataset.chartRange;
        renderReport();
      });
    });

    root.querySelector("[data-chart-fit-levels]")?.addEventListener("click", () => {
      fitAllLevels = !fitAllLevels;
      renderReport();
    });

    root.querySelector('[data-role="open-evidence"]').addEventListener("click", () => {
      drawerOpen = true;
      renderReport();
      root.querySelector('[data-role="evidence-panel"]').scrollIntoView({ behavior: "smooth", block: "start" });
    });
    const closeButton = root.querySelector('[data-role="close-evidence"]');
    if (closeButton) {
      closeButton.addEventListener("click", () => {
        drawerOpen = false;
        renderReport();
      });
    }
    const filterForm = root.querySelector('[data-role="evidence-filters"]');
    if (filterForm) {
      filterForm.addEventListener("change", (event) => {
        const formData = new FormData(filterForm);
        filters = {
          type: formData.get("type") || "all",
          supports: formData.get("supports") || "all",
          quality: formData.get("quality") || "all",
        };
        drawerOpen = true;
        renderReport();
      });
    }
    const saveButton = root.querySelector('[data-role="save-decision"]');
    if (saveButton && !alreadySaved) {
      saveButton.addEventListener("click", () => {
        const decision = decisionService.fromRun(run);
        decisionService.add(store, decision);
        navigate(`runs/${run.id}`);
      });
    }
  }

  renderReport();
}
