import { ANALYSIS_TYPES, ASSET_CATALOG, DATA_SOURCES, QUICK_START_PROMPTS } from "../demoData.js";
import { escapeHtml, relativeTime, titleCase } from "../format.js";
import { renderSparkline } from "../components/sparkline.js";
import { analysisService, marketDataService, runService, runtimeService } from "../services.js";

const HORIZON_OPTIONS = [
  { value: "intraday", label: "Short term", detail: "1–7 days" },
  { value: "swing", label: "Swing", detail: "2–8 weeks" },
  { value: "position", label: "Position", detail: "2–6 months" },
  { value: "long_term", label: "Long term", detail: "6+ months" },
];

const QUICK_QUESTION_CHIPS = [
  { label: "Entry setup", question: "Identify entry zones, invalidation, and realistic targets." },
  { label: "Long-term outlook", question: "Assess the long-term thesis, valuation, and key risks." },
  { label: "Compare assets", question: "Compare relative strength and risk across the selected assets." },
  { label: "Portfolio risk", question: "Review portfolio exposure, leverage, and downside risk." },
];

function radioButtons(items, selected, attribute, disabledValues = []) {
  return items.map((item) => `
    <button type="button" class="choice-card${item.value === selected ? " is-active" : ""}" role="radio" aria-checked="${item.value === selected}" data-${attribute}="${item.value}" ${disabledValues.includes(item.value) ? "disabled" : ""}>
      <strong>${item.label}</strong>
      ${item.detail ? `<span>${item.detail}</span>` : ""}
    </button>
  `).join("");
}

function recentSearches(runs) {
  if (!runs.length) return '<li class="recent-search-empty">No recent analyses.</li>';
  return runs.slice(0, 4).map((run) => `
    <li>
      <button type="button" class="recent-search-button" data-recent-run="${escapeHtml(run.id)}">
        <strong>${escapeHtml(run.asset)} / USDT · ${titleCase(run.horizon)}</strong>
        <span>${relativeTime(run.createdAt)}</span>
      </button>
    </li>
  `).join("");
}

export function render(root, ctx) {
  const { store, navigate, params } = ctx;
  const state = store.getState();
  const runtimeMode = analysisService.mode;
  const liveMode = runtimeMode === "live";
  const unavailableMode = runtimeMode === "unavailable";
  const runtimeStatus = runtimeService.status;
  const marketSnapshot = marketDataService.getSnapshot();
  const assetShortcuts = ["SEI", "SUI", "BTC", "ETH", "AVAX", "PYTH"]
    .map((symbol) => marketSnapshot.find((asset) => asset.asset === symbol))
    .filter(Boolean);
  const prefillAsset = ASSET_CATALOG.some((asset) => asset.symbol === params?.[0])
    ? params[0]
    : "SEI";
  const defaultHorizon = HORIZON_OPTIONS.some((item) => item.value === state.settings.defaultHorizon)
    ? state.settings.defaultHorizon
    : "swing";
  const riskValue = state.settings.defaultRiskStyle === "conservative" ? 20 : state.settings.defaultRiskStyle === "aggressive" ? 80 : 50;
  const asOfDefault = new Date().toISOString().slice(0, 10);
  const submitDisabled = unavailableMode || (liveMode && !runtimeStatus?.model_configured);
  const modeDescription = liveMode
    ? "Live paper analysis uses the latest closed Binance Spot snapshot and a server-side paired Luna run."
    : unavailableMode
      ? "The local runtime is unavailable. Restore its API before submitting an analysis."
      : "Configure a research request using synthetic fixture data.";

  root.innerHTML = `
    <header class="page-header">
      <div>
        <h1>New Analysis</h1>
        <p>${escapeHtml(modeDescription)}</p>
      </div>
    </header>

    <div class="analysis-workspace">
      <section class="panel panel--composer">
        <form class="composer-form" data-role="composer-form">
          <section class="composer-section">
            <div class="composer-section-heading">
              <span class="composer-step">1</span>
              <div><h2>Asset</h2><p>Select the market to analyze.</p></div>
            </div>
            <label class="asset-search-label" for="analysis-asset">Search assets</label>
            <input id="analysis-asset" name="asset" type="search" list="analysis-assets" value="${escapeHtml(prefillAsset)}" autocomplete="off" required aria-describedby="asset-help" />
            <datalist id="analysis-assets">
              ${ASSET_CATALOG.map((asset) => `<option value="${escapeHtml(asset.symbol)}">${escapeHtml(asset.name)} / USDT</option>`).join("")}
            </datalist>
            <p class="field-help" id="asset-help">
              ${liveMode
                ? "Enter a Binance USDT Spot asset (for example BTC or BTCUSDT). The live price snapshot is fetched on the server."
                : "Choose a listed asset. Prices and charts are fixture data."}
            </p>
            ${liveMode
              ? '<p class="field-help">The quote cards are hidden in live mode so synthetic prices cannot be mistaken for market data.</p>'
              : `<div class="asset-shortcuts" role="group" aria-label="Common assets">
                  ${assetShortcuts.map((asset) => `
                    <button type="button" class="asset-shortcut${asset.asset === prefillAsset ? " is-active" : ""}" data-asset-shortcut="${asset.asset}">
                      <span class="asset-symbol-icon asset-symbol-icon--${asset.asset.toLowerCase()}" aria-hidden="true">${asset.asset.slice(0, 1)}</span>
                      <span>${asset.asset} / USDT</span>
                      <strong class="${asset.change24h >= 0 ? "market-change--positive" : "market-change--negative"}">${asset.change24h > 0 ? "+" : ""}${asset.change24h.toFixed(1)}%</strong>
                      ${renderSparkline(asset.asset, asset.trend, asset.change24h)}
                    </button>
                  `).join("")}
                </div>`}
          </section>

          <section class="composer-section">
            <div class="composer-section-heading">
              <span class="composer-step">2</span>
              <div><h2>Analysis type</h2><p>Choose the focus of your research.</p></div>
            </div>
            <input type="hidden" name="analysisType" value="spot" />
            <div class="choice-grid choice-grid--three" role="radiogroup" aria-label="Analysis type">
              ${radioButtons(
                ANALYSIS_TYPES,
                "spot",
                "analysis-type",
                liveMode ? ["futures", "investment"] : [],
              )}
            </div>
          </section>

          <section class="composer-section">
            <div class="composer-section-heading">
              <span class="composer-step">3</span>
              <div><h2>Horizon</h2><p>How long do you plan to hold, if applicable?</p></div>
            </div>
            <input type="hidden" name="horizon" value="${defaultHorizon}" />
            <div class="choice-grid choice-grid--four" role="radiogroup" aria-label="Analysis horizon">
              ${radioButtons(HORIZON_OPTIONS, defaultHorizon, "horizon")}
            </div>
          </section>

          <section class="composer-section">
            <div class="composer-section-heading">
              <span class="composer-step">4</span>
              <div><h2>What would you like to know?</h2><p>Be as specific as possible.</p></div>
            </div>
            <label class="sr-only" for="analysis-question">Research question</label>
            <textarea id="analysis-question" name="question" rows="4" maxlength="1200" required placeholder="Should I accumulate SEI around the current price?&#10;&#10;Include your context, constraints, and what you need to decide."></textarea>
            <div class="question-footer">
              <div class="example-chips" role="group" aria-label="Question examples">
                ${QUICK_QUESTION_CHIPS.map((chip) => `<button type="button" class="example-chip" data-question="${escapeHtml(chip.question)}">${escapeHtml(chip.label)}</button>`).join("")}
              </div>
              <span class="question-count" data-role="question-count">0 / 1200</span>
            </div>
          </section>

          <section class="composer-section">
            <div class="composer-section-heading">
              <span class="composer-step">5</span>
              <div><h2>Portfolio context</h2><p>${liveMode ? "Optional context is not sent to the forward evaluator." : "Optional context for this fixture request."}</p></div>
            </div>
            <div class="portfolio-context-grid">
              <label class="capital-field">
                Portfolio capital (optional)
                <span class="capital-input-row">
                  <select name="referenceCurrency" aria-label="Reference currency">
                    <option value="THB" selected>THB</option>
                    <option value="USD">USD</option>
                  </select>
                  <input type="number" name="capital" min="0" step="any" placeholder="100,000" />
                </span>
              </label>
              <div class="risk-control">
                <label for="risk-level">Risk preference</label>
                <div class="risk-control-heading"><span>Conservative</span><strong data-role="risk-label">Balanced</strong><span>Aggressive</span></div>
                <input id="risk-level" name="riskLevel" type="range" min="0" max="100" value="${riskValue}" />
                <input type="hidden" name="riskStyle" value="${escapeHtml(state.settings.defaultRiskStyle)}" />
              </div>
            </div>
          </section>

          <details class="composer-advanced">
            <summary>Advanced settings <span>Venue, data sources, and reference details</span></summary>
            <div class="advanced-settings-grid">
              <label>
                Venue
                <select name="venue" ${liveMode ? "disabled" : ""}>
                  <option value="Binance" selected>Binance</option>
                  <option value="OKX">OKX</option>
                  <option value="Bybit">Bybit</option>
                </select>
              </label>
              <label>
                Instrument
                <select name="instrument" ${liveMode ? "disabled" : ""}>
                  <option value="spot" selected>Spot</option>
                  <option value="perpetual">USDT perpetual</option>
                </select>
              </label>
              <div class="advanced-model">
                <span>Model</span>
                <strong>${liveMode
                  ? escapeHtml(runtimeStatus?.model_id || "gpt-6-luna")
                  : "Fixture generator"}</strong>
                <small>${liveMode
                  ? "Server-side OpenAI Responses API; no credentials are sent to the browser."
                  : "No live model call is connected."}</small>
              </div>
              <label>
                Data as of
                <input type="date" name="asOf" value="${asOfDefault}" max="${asOfDefault}" ${liveMode ? "disabled" : ""} />
                ${liveMode ? '<small>Live analysis uses the latest fully closed candle; use archive-binance for historical ranges.</small>' : ""}
              </label>
              <fieldset class="provider-fieldset">
                <legend>Requested data sources</legend>
                <p class="field-help">${liveMode
                  ? "Live mode uses Binance public Spot klines; other fixture feeds are not requested."
                  : "Recorded with this request; fixture mode does not fetch providers."}</p>
                ${DATA_SOURCES.map((source, index) => `
                  <label class="checkbox-row">
                    <input type="checkbox" name="providers" value="${escapeHtml(source.name)}" ${index < 3 ? "checked" : ""} ${liveMode ? "disabled" : ""} />
                    ${escapeHtml(source.name)}
                  </label>
                `).join("")}
              </fieldset>
            </div>
          </details>

          <div class="composer-actions">
            <p class="fixture-action-note">${liveMode
              ? runtimeStatus?.model_configured
                ? "Live paper mode · creates a frozen skill/control pair. No orders are placed."
                : runtimeStatus?.configuration_error
                  || "Live runtime is missing OPENAI_API_KEY in the server environment; no model request will be made."
              : unavailableMode
                ? escapeHtml(runtimeService.status?.configuration_error || "Local runtime API is unavailable.")
                : "Fixture mode · creates a local example run; no live market request is sent."}</p>
            <button type="submit" class="btn btn--primary" ${submitDisabled ? "disabled" : ""}>
              ${liveMode ? "Analyze with Luna" : "Run analysis"} <span aria-hidden="true">→</span>
            </button>
          </div>
          <p class="form-error" data-role="form-error" role="alert" hidden></p>
        </form>
      </section>

      <aside class="analysis-context-column">
        <section class="panel context-panel">
          <div class="section-heading"><h2>Quick templates</h2></div>
          <ul class="quick-template-list">
            ${QUICK_START_PROMPTS.map((prompt) => `
              <li>
                <button type="button" class="quick-template-button" data-template="${prompt.id}">
                  <strong>${escapeHtml(prompt.label)}</strong>
                  <span>${escapeHtml(prompt.question)}</span>
                </button>
              </li>
            `).join("")}
            <li>
              <button type="button" class="quick-template-button" data-template="portfolio-review">
                <strong>Portfolio Review</strong>
                <span>Review saved decisions, concentration, and risk.</span>
              </button>
            </li>
          </ul>
        </section>
        <section class="panel context-panel">
          <div class="section-heading">
            <h2>Recent searches</h2>
            <a class="panel-link" href="#/runs">View all</a>
          </div>
          <ul class="recent-search-list">${recentSearches(runService.list(store))}</ul>
        </section>
      </aside>
    </div>
  `;

  const form = root.querySelector('[data-role="composer-form"]');
  const assetInput = form.querySelector('[name="asset"]');
  const updateRisk = () => {
    const slider = form.querySelector('[name="riskLevel"]');
    const riskStyle = slider.value < 34 ? "conservative" : slider.value > 66 ? "aggressive" : "neutral";
    form.querySelector('[name="riskStyle"]').value = riskStyle;
    form.querySelector('[data-role="risk-label"]').textContent = riskStyle === "neutral" ? "Balanced" : titleCase(riskStyle);
  };
  updateRisk();

  form.addEventListener("input", (event) => {
    if (event.target.name === "question") {
      form.querySelector('[data-role="question-count"]').textContent = `${event.target.value.length} / 1200`;
    }
    if (event.target.name === "riskLevel") updateRisk();
  });

  form.querySelectorAll("[data-analysis-type]").forEach((button) => {
    button.addEventListener("click", () => {
      form.querySelector('[name="analysisType"]').value = button.dataset.analysisType;
      form.querySelectorAll("[data-analysis-type]").forEach((item) => {
        const selected = item === button;
        item.classList.toggle("is-active", selected);
        item.setAttribute("aria-checked", String(selected));
      });
    });
  });

  form.querySelectorAll("[data-horizon]").forEach((button) => {
    button.addEventListener("click", () => {
      form.querySelector('[name="horizon"]').value = button.dataset.horizon;
      form.querySelectorAll("[data-horizon]").forEach((item) => {
        const selected = item === button;
        item.classList.toggle("is-active", selected);
        item.setAttribute("aria-checked", String(selected));
      });
    });
  });

  form.querySelectorAll("[data-asset-shortcut]").forEach((button) => {
    button.addEventListener("click", () => {
      assetInput.value = button.dataset.assetShortcut;
      form.querySelectorAll("[data-asset-shortcut]").forEach((item) => item.classList.toggle("is-active", item === button));
    });
  });

  form.querySelectorAll("[data-question]").forEach((button) => {
    button.addEventListener("click", () => {
      const question = form.querySelector('[name="question"]');
      question.value = `${button.dataset.question}\n\n`;
      question.focus();
      question.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });

  root.querySelectorAll("[data-template]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.template === "portfolio-review") {
        navigate("decisions");
        return;
      }
      const prompt = QUICK_START_PROMPTS.find((entry) => entry.id === button.dataset.template);
      if (!prompt) return;
      assetInput.value = prompt.asset;
      form.querySelector('[name="question"]').value = prompt.question;
      form.querySelector('[name="question"]').dispatchEvent(new Event("input", { bubbles: true }));
      if (prompt.analysisType) {
        form.querySelector('[name="analysisType"]').value = prompt.analysisType;
        form.querySelectorAll("[data-analysis-type]").forEach((item) => {
          const selected = item.dataset.analysisType === prompt.analysisType;
          item.classList.toggle("is-active", selected);
          item.setAttribute("aria-checked", String(selected));
        });
      }
      const horizon = HORIZON_OPTIONS.some((item) => item.value === prompt.horizon) ? prompt.horizon : "swing";
      form.querySelector('[name="horizon"]').value = horizon;
      form.querySelectorAll("[data-horizon]").forEach((item) => {
        const selected = item.dataset.horizon === horizon;
        item.classList.toggle("is-active", selected);
        item.setAttribute("aria-checked", String(selected));
      });
    });
  });

  root.querySelectorAll("[data-recent-run]").forEach((button) => {
    button.addEventListener("click", () => {
      const run = runService.get(store, button.dataset.recentRun);
      if (!run) return;
      assetInput.value = run.asset;
      form.querySelector('[name="question"]').value = run.question;
      form.querySelector('[name="question"]').dispatchEvent(new Event("input", { bubbles: true }));
      form.querySelector('[name="analysisType"]').value = ANALYSIS_TYPES.some((item) => item.value === run.analysisType)
        ? run.analysisType
        : "spot";
      form.querySelectorAll("[data-analysis-type]").forEach((item) => {
        const selected = item.dataset.analysisType === form.querySelector('[name="analysisType"]').value;
        item.classList.toggle("is-active", selected);
        item.setAttribute("aria-checked", String(selected));
      });
      const horizon = HORIZON_OPTIONS.some((item) => item.value === run.horizon) ? run.horizon : "swing";
      form.querySelector('[name="horizon"]').value = horizon;
      form.querySelectorAll("[data-horizon]").forEach((item) => {
        const selected = item.dataset.horizon === horizon;
        item.classList.toggle("is-active", selected);
        item.setAttribute("aria-checked", String(selected));
      });
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(form);
    const asset = String(formData.get("asset") || "").trim().toUpperCase().replace(/\s*\/\s*USDT$/, "").trim();
    const catalogEntry = ASSET_CATALOG.find((entry) => entry.symbol === asset);
    const error = form.querySelector('[data-role="form-error"]');
    const validLiveSymbol = /^[A-Z0-9]{2,16}(USDT)?$/.test(asset);
    if ((liveMode && !validLiveSymbol) || (!liveMode && !catalogEntry)) {
      error.hidden = false;
      error.textContent = liveMode
        ? "Enter a Binance USDT Spot asset or symbol using letters and digits."
        : "Choose an asset from the suggested list.";
      assetInput.setAttribute("aria-invalid", "true");
      assetInput.focus();
      return;
    }
    const analysisType = String(formData.get("analysisType") || "spot");
    const venue = liveMode ? "Binance" : String(formData.get("venue") || "Binance");
    const instrument = liveMode ? "spot" : String(formData.get("instrument") || "spot");
    if (liveMode && (analysisType !== "spot" || venue !== "Binance" || instrument !== "spot")) {
      error.hidden = false;
      error.textContent = "Live runtime currently supports Binance Spot analysis only.";
      return;
    }
    assetInput.removeAttribute("aria-invalid");
    error.hidden = true;
    error.textContent = "";

    const asOf = formData.get("asOf");
    const requestSettings = {
      venue,
      instrument,
      model: "fixture",
      dataAsOf: asOf ? new Date(`${asOf}T00:00:00.000Z`).toISOString() : null,
      dataProviders: formData.getAll("providers"),
      referenceCurrency: formData.get("referenceCurrency"),
    };
    const submitButton = form.querySelector('button[type="submit"]');
    const originalLabel = submitButton.innerHTML;
    submitButton.disabled = true;
    submitButton.textContent = liveMode
      ? "Fetching closed candles and analyzing…"
      : "Generating fixture analysis…";
    try {
      const run = await analysisService.create({
        asset,
        analysisType,
        horizon: formData.get("horizon"),
        question: String(formData.get("question") || "").trim(),
        riskStyle: formData.get("riskStyle"),
        capital: formData.get("capital") ? Number(formData.get("capital")) : null,
        basePrice: catalogEntry?.basePrice,
        requestSettings,
      });
      runService.add(store, run);
      navigate(`runs/${run.id}`);
    } catch (requestError) {
      error.hidden = false;
      error.textContent = requestError instanceof Error
        ? requestError.message
        : "Analysis request failed. Check the local runtime and try again.";
      submitButton.disabled = submitDisabled;
      submitButton.innerHTML = originalLabel;
    }
  });
}
