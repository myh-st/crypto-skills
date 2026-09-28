import { ASSET_CATALOG, QUICK_START_PROMPTS } from "../demoData.js";
import { escapeHtml, formatPrice, formatRange, relativeTime, titleCase } from "../format.js";
import { STATE_TONE } from "../contracts.js";
import { MARKET_SERIES, renderMarketOverviewChart } from "../components/marketOverviewChart.js";
import { renderSparkline } from "../components/sparkline.js";
import { analysisService, decisionService, marketDataService, runService, runtimeService } from "../services.js";

function assetOptions(selected) {
  return ASSET_CATALOG
    .map((asset) => `<option value="${asset.symbol}" ${asset.symbol === selected ? "selected" : ""}>${asset.symbol} / USDT</option>`)
    .join("");
}

function marketPrice(value) {
  const decimals = value >= 10000 ? 1 : value >= 1 ? 2 : value >= 0.1 ? 3 : 4;
  return `$${value.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`;
}

function changeLabel(value) {
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function marketCard(item) {
  const positive = item.change24h >= 0;
  return `
    <article class="market-snapshot-card">
      <div class="market-snapshot-asset">
        <span class="asset-symbol-icon asset-symbol-icon--${item.asset.toLowerCase()}" aria-hidden="true">${escapeHtml(item.asset.slice(0, 1))}</span>
        <strong>${escapeHtml(item.asset)} / USDT</strong>
      </div>
      <div class="market-snapshot-value">
        <strong>${marketPrice(item.price)}</strong>
        <span class="market-change ${positive ? "market-change--positive" : "market-change--negative"}">${changeLabel(item.change24h)}</span>
      </div>
      ${renderSparkline(item.asset, item.trend, item.change24h)}
      <span class="market-snapshot-caption">24h · fixture</span>
    </article>
  `;
}

function decisionRows(decisions) {
  if (!decisions.length) {
    return '<tr><td colspan="6" class="table-empty">No decisions yet. Run an analysis to create one.</td></tr>';
  }

  return decisions.map((decision) => {
    const tone = STATE_TONE[decision.decision_state] ?? "neutral";
    const firstTarget = decision.targets?.[0];
    return `
      <tr>
        <td><a class="table-asset-link" href="#/runs/${encodeURIComponent(decision.runId)}">${escapeHtml(decision.asset)} / USDT</a></td>
        <td><span class="pill pill--${tone}">${titleCase(decision.decision_state)}</span></td>
        <td>${formatRange(decision.entry_zone)}</td>
        <td>${firstTarget === undefined ? "—" : formatPrice(firstTarget)}</td>
        <td><span class="confidence-dot confidence-dot--${escapeHtml(decision.confidence)}">${titleCase(decision.confidence)}</span></td>
        <td>${relativeTime(decision.savedAt)}</td>
      </tr>
    `;
  }).join("");
}

function watchlistRows(entries, store, marketSnapshot) {
  if (!entries.length) {
    return '<tr><td colspan="5" class="table-empty">No assets on the watchlist.</td></tr>';
  }

  return entries.map((entry) => {
    const market = marketSnapshot.find((item) => item.asset === entry.asset);
    const linkedRun = runService.get(store, entry.linkedRunId);
    const tone = linkedRun ? STATE_TONE[linkedRun.report.state] ?? "neutral" : "neutral";
    return `
      <tr>
        <td><strong>${escapeHtml(entry.asset)} / USDT</strong></td>
        <td>${market ? marketPrice(market.price) : "—"}</td>
        <td class="${market?.change24h >= 0 ? "market-change--positive" : "market-change--negative"}">${market ? changeLabel(market.change24h) : "—"}</td>
        <td><span class="pill pill--${tone}">${linkedRun ? titleCase(linkedRun.report.state) : "Watching"}</span></td>
        <td>${escapeHtml(entry.note)}</td>
      </tr>
    `;
  }).join("");
}

function activityRows(runs) {
  if (!runs.length) {
    return '<tr><td colspan="5" class="table-empty">No analyses yet. Start one above.</td></tr>';
  }

  return runs.slice(0, 5).map((run) => `
    <tr>
      <td><span class="activity-status"><span class="activity-status-dot"></span>${titleCase(run.status)}</span></td>
      <td><a class="table-asset-link" href="#/runs/${encodeURIComponent(run.id)}">${escapeHtml(run.asset)} / USDT</a></td>
      <td>${titleCase(run.horizon)} analysis</td>
      <td>${relativeTime(run.createdAt)}</td>
      <td class="fixture-duration">${run.runtimeMode === "live"
        ? `Forward · ${escapeHtml(titleCase((run.evaluationStatus || "waiting_for_outcome").replaceAll("_", " ")))}`
        : "Fixture · instant"}</td>
    </tr>
  `).join("");
}

async function runAnalysis(store, navigate, input, button, error) {
  const asset = input.asset || "BTC";
  const catalogEntry = ASSET_CATALOG.find((entry) => entry.symbol === asset);
  const originalLabel = button?.innerHTML;
  if (button) {
    button.disabled = true;
    button.textContent = analysisService.mode === "live"
      ? "Fetching closed candles and analyzing…"
      : "Generating fixture analysis…";
  }
  if (error) {
    error.hidden = true;
    error.textContent = "";
  }
  try {
    const run = await analysisService.create({
      asset,
      analysisType: input.analysisType || "spot",
      horizon: input.horizon || "swing",
      question: input.question || "",
      riskStyle: store.getState().settings.defaultRiskStyle,
      capital: null,
      basePrice: catalogEntry?.basePrice,
    });
    runService.add(store, run);
    navigate(`runs/${run.id}`);
  } catch (requestError) {
    if (error) {
      error.hidden = false;
      error.textContent = requestError instanceof Error
        ? requestError.message
        : "Analysis request failed. Check the local runtime and try again.";
    }
    if (button) {
      button.disabled = analysisService.mode === "unavailable"
        || (analysisService.mode === "live" && !runtimeService.status?.model_configured);
      button.innerHTML = originalLabel;
    }
  }
}

export function render(root, ctx) {
  const { store, navigate } = ctx;
  const state = store.getState();
  const liveMode = analysisService.mode === "live";
  const submitDisabled = analysisService.mode === "unavailable"
    || (liveMode && !runtimeService.status?.model_configured);
  const marketSnapshot = marketDataService.getSnapshot();
  const recentDecisions = decisionService.list(store).slice(0, 5);
  const recentRuns = runService.list(store).slice(0, 5);
  const now = new Date();
  const headerTime = now.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  const marketChartState = {
    metric: "price",
    range: "1D",
    visibleAssets: new Set(MARKET_SERIES.map(({ asset }) => asset)),
  };

  root.innerHTML = `
    <header class="page-header">
      <div>
        <h1>Overview</h1>
        <p>Research and decision support for your crypto portfolio</p>
      </div>
      <time class="page-header-date" datetime="${now.toISOString()}">${headerTime}</time>
    </header>

    <section class="panel panel--overview-prompt">
      <h2>What do you want to analyze?</h2>
      <form class="overview-analysis-form" data-role="overview-prompt">
        <label class="sr-only" for="overview-asset">Asset</label>
        <select id="overview-asset" name="asset">${assetOptions("SEI")}</select>
        <label class="sr-only" for="overview-question">Research question</label>
        <input id="overview-question" name="question" type="text" placeholder="Analyze SEI / USDT for a 3–6 month position..." required />
        <button type="submit" class="btn btn--primary" ${submitDisabled ? "disabled" : ""}>
          ${liveMode ? "Analyze with Luna" : "Run analysis"} <span aria-hidden="true">→</span>
        </button>
      </form>
      <p class="form-error" data-role="overview-error" role="alert" hidden></p>
      <div class="quick-start-grid quick-start-grid--compact" aria-label="Quick start">
        ${QUICK_START_PROMPTS.map((prompt) => `
          <button type="button" class="quick-start-card" data-quick-start="${prompt.id}" ${submitDisabled ? "disabled" : ""}>
            <strong>${escapeHtml(prompt.label)}</strong>
            <span>${escapeHtml(prompt.asset)} / USDT · ${titleCase(prompt.horizon)}</span>
          </button>
        `).join("")}
        <button type="button" class="quick-start-card quick-start-card--portfolio" data-action="portfolio-review">
          <strong>Portfolio review</strong>
          <span>Review saved decisions and risk</span>
        </button>
      </div>
    </section>

    <section class="market-snapshot-section">
      <div class="section-heading">
        <h2>Market snapshot</h2>
        <span class="demo-tag">Fixture prices · 24h</span>
      </div>
      <div class="market-snapshot-grid">
        ${marketSnapshot.slice(0, 6).map(marketCard).join("")}
      </div>
    </section>

    <section class="panel market-overview-panel" data-role="market-overview-chart">
      ${renderMarketOverviewChart(marketChartState)}
    </section>

    <section class="panel overview-table-panel">
      <div class="section-heading">
        <h2>Recent decisions</h2>
        <a class="panel-link" href="#/decisions">View all →</a>
      </div>
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr><th>Asset</th><th>Decision</th><th>Entry</th><th>Target</th><th>Confidence</th><th>Updated</th></tr>
          </thead>
          <tbody>${decisionRows(recentDecisions)}</tbody>
        </table>
      </div>
    </section>

    <div class="overview-bottom-grid">
      <section class="panel overview-table-panel">
        <div class="section-heading">
          <h2>Watchlist <span class="demo-tag">Agent-aware</span></h2>
          <a class="panel-link" href="#/watchlist">View all →</a>
        </div>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr><th>Asset</th><th>Price</th><th>24h</th><th>Agent state</th><th>Condition</th></tr>
            </thead>
            <tbody>${watchlistRows(state.watchlist, store, marketSnapshot)}</tbody>
          </table>
        </div>
      </section>

      <section class="panel overview-table-panel">
        <div class="section-heading">
          <h2>Agent activity</h2>
          <a class="panel-link" href="#/runs">View all →</a>
        </div>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr><th>Status</th><th>Asset</th><th>Request</th><th>Started</th><th>Duration</th></tr>
            </thead>
            <tbody>${activityRows(recentRuns)}</tbody>
          </table>
        </div>
      </section>
    </div>
  `;

  const marketChartPanel = root.querySelector('[data-role="market-overview-chart"]');
  marketChartPanel.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-market-metric], button[data-market-range], button[data-market-asset]");
    if (!button) return;

    if (button.dataset.marketMetric) marketChartState.metric = button.dataset.marketMetric;
    if (button.dataset.marketRange) marketChartState.range = button.dataset.marketRange;
    if (button.dataset.marketAsset) {
      if (marketChartState.visibleAssets.has(button.dataset.marketAsset)) {
        marketChartState.visibleAssets.delete(button.dataset.marketAsset);
      } else {
        marketChartState.visibleAssets.add(button.dataset.marketAsset);
      }
    }

    marketChartPanel.innerHTML = renderMarketOverviewChart(marketChartState);
  });

  root.querySelector('[data-role="overview-prompt"]').addEventListener("submit", (event) => {
    event.preventDefault();
    const formData = new FormData(event.target);
    runAnalysis(store, navigate, {
      asset: formData.get("asset"),
      horizon: "swing",
      question: formData.get("question"),
    }, event.target.querySelector('button[type="submit"]'), root.querySelector('[data-role="overview-error"]'));
  });

  root.querySelectorAll("[data-quick-start]").forEach((button) => {
    button.addEventListener("click", () => {
      const prompt = QUICK_START_PROMPTS.find((entry) => entry.id === button.dataset.quickStart);
      if (!prompt) return;
      runAnalysis(
        store,
        navigate,
        prompt,
        button,
        root.querySelector('[data-role="overview-error"]'),
      );
    });
  });

  root.querySelector('[data-action="portfolio-review"]').addEventListener("click", () => {
    navigate("decisions");
  });
}
