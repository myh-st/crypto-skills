import { escapeHtml, formatTimestamp, titleCase } from "../format.js";
import { evaluationService, runService, runtimeService } from "../services.js";
import { paperApi } from "../paperApi.js";
import { renderLearning, renderTournament } from "../components/strategyTournamentSummary.js";
import { renderBenchmark } from "../components/lifecycleCard.js";

const RESULT_TONE = {
  pending: "neutral",
  confirmed: "positive",
  invalidated: "negative",
  mixed: "caution",
};

export function render(root, ctx) {
  root.innerHTML = `<div class="view view--evaluations">
    <section class="panel" aria-labelledby="tournament-heading">
      <div class="section-heading"><h1 id="tournament-heading">Strategy tournament</h1><span class="demo-tag">aligned frozen inputs · separate PAPER wallets</span></div>
      <div data-tournament><p class="muted">Loading…</p></div>
    </section>
    <section class="panel" aria-labelledby="benchmark-heading">
      <div class="section-heading"><h2 id="benchmark-heading">Spot lifecycle benchmark</h2><span class="demo-tag">aligned closed 4h bars · same fees and slippage</span></div>
      <form class="benchmark-form" data-benchmark-form>
        <label>Spot pair <input name="instrument_id" type="text" required placeholder="gate:spot:ETH_USDT" autocomplete="off" /></label>
        <button type="submit" class="btn btn--ghost btn--small">Run benchmark</button>
      </form>
      <div data-benchmark role="status" aria-live="polite"><p class="muted small">Loading…</p></div>
    </section>
    <section class="panel" aria-labelledby="learning-heading">
      <h2 id="learning-heading">Learning loop</h2>
      <div data-learning></div>
    </section>
    <div data-research-evaluations></div></div>`;
  const view = root.firstElementChild;
  Promise.all([paperApi.tournament(), paperApi.reviews()])
    .then(([tournament, reviews]) => {
      view.querySelector("[data-tournament]").innerHTML = renderTournament(tournament);
      view.querySelector("[data-learning]").innerHTML = renderLearning(reviews.reviews, reviews.hypotheses, tournament.learning_tags);
    })
    .catch((error) => {
      view.querySelector("[data-tournament]").innerHTML = `<p class="muted">PAPER runtime unavailable: ${escapeHtml(error.message)}</p>`;
    });
  const benchmarkHost = view.querySelector("[data-benchmark]");
  paperApi.lifecycleBenchmarks()
    .then(({ reports }) => { benchmarkHost.innerHTML = renderBenchmark(reports[0]); })
    .catch(() => { benchmarkHost.innerHTML = renderBenchmark(null); });
  view.querySelector("[data-benchmark-form]").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const id = String(new FormData(form).get("instrument_id") || "").trim();
    const button = form.querySelector("button");
    button.disabled = true;
    benchmarkHost.innerHTML = '<p class="muted small">Running aligned arms…</p>';
    try {
      const { report } = await paperApi.lifecycleBenchmark(id);
      benchmarkHost.innerHTML = renderBenchmark(report);
    } catch (error) {
      benchmarkHost.innerHTML = `<p class="paper-feedback paper-feedback--error">${escapeHtml(error.message)}</p>`;
    } finally {
      button.disabled = false;
    }
  });
  renderResearchEvaluations(view.querySelector("[data-research-evaluations]"), ctx);
}

function renderResearchEvaluations(root, ctx) {
  const { store } = ctx;
  const { decisions, evaluationDemo, forwardRuns } = evaluationService.snapshot(store);

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
      <h2>Research evaluations</h2>
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

    <section class="panel" data-role="forward-evaluation-list">
      <div class="section-heading">
        <h2>Forward paper evaluation</h2>
        <span class="demo-tag">Real Spot snapshots · no trade execution</span>
      </div>
      <div class="report-actions">
        <button type="button" class="btn btn--ghost" data-refresh-forward ${runtimeService.mode !== "live" ? "disabled" : ""}>
          Refresh forward status
        </button>
      </div>
      <p class="panel-subtitle">
        Frozen skill/control predictions remain pending until the configured horizon closes.
        Outcome candles are fetched separately and never enter either prompt.
      </p>
      <ul class="list list--table">
        ${forwardRuns.map((item) => `
          <li class="list-row">
            <div class="list-row-link">
              <span class="pill pill--${item.status === "scored" ? "positive" : item.status === "ready_to_score" ? "caution" : "neutral"}">${titleCase(item.status)}</span>
              <span class="list-row-title">${escapeHtml(item.symbol || `${item.asset} / USDT`)}</span>
              <span class="list-row-meta">${titleCase(item.horizon)} · cutoff ${formatTimestamp(item.data_cutoff)}</span>
              <span class="list-row-status">Horizon closes ${formatTimestamp(item.horizon_closes_at)}</span>
            </div>
            <div class="report-actions">
              ${item.run ? `<a class="btn btn--ghost" href="#/runs/${encodeURIComponent(item.run_id)}">Open analysis</a>` : ""}
              ${item.status === "scored"
                ? '<button type="button" class="btn btn--ghost" disabled>Scored ✓</button>'
                : `<button type="button" class="btn btn--primary" data-score-forward="${escapeHtml(item.run_id)}" ${item.status !== "ready_to_score" || runtimeService.mode !== "live" ? "disabled" : ""}>
                    ${item.status === "ready_to_score" ? "Fetch & score outcome" : "Awaiting horizon"}
                  </button>`}
            </div>
          </li>
        `).join("") || '<li class="list-empty">No forward paper cases have been created.</li>'}
      </ul>
      <p class="form-error" data-role="forward-evaluation-error" role="alert" hidden></p>
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

  const refreshButton = root.querySelector("[data-refresh-forward]");
  refreshButton?.addEventListener("click", async () => {
    const error = root.querySelector('[data-role="forward-evaluation-error"]');
    error.hidden = true;
    refreshButton.disabled = true;
    refreshButton.textContent = "Refreshing…";
    try {
  store.setForwardEvaluations(await evaluationService.listForward());
    } catch (requestError) {
  error.hidden = false;
  error.textContent = requestError instanceof Error
    ? requestError.message
    : "Could not refresh forward-evaluation status.";
  refreshButton.disabled = false;
  refreshButton.textContent = "Retry status refresh";
    }
  });

  root.querySelectorAll("[data-score-forward]").forEach((button) => {
    button.addEventListener("click", async () => {
      const runId = button.dataset.scoreForward;
      const error = root.querySelector('[data-role="forward-evaluation-error"]');
      error.hidden = true;
      button.disabled = true;
      button.textContent = "Fetching and scoring…";
      try {
        const response = await runtimeService.scoreForward(runId);
        const run = runService.get(store, runId);
        if (run) {
          store.updateRun(runId, {
            evaluationStatus: response.run.evaluationStatus,
            horizonClosesAt: response.run.horizonClosesAt,
          });
        }
        store.setForwardEvaluations(await evaluationService.listForward());
      } catch (requestError) {
        error.hidden = false;
        error.textContent = requestError instanceof Error
          ? requestError.message
          : "Could not score the matured forward outcome.";
        button.disabled = false;
        button.textContent = "Retry scoring";
      }
    });
  });
}
