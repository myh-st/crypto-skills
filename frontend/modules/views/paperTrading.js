import { escapeHtml, formatPrice, formatTimestamp, titleCase } from "../format.js";
import { paperApi } from "../paperApi.js";
import { mountLiveChart, overlayLines } from "../components/liveChart.js";
import { renderAccountMirror } from "./runtimeSettings.js";
import { money, usd, displayValue, numericValue, fixedNumber, percent, dataOriginLabel, yesNo, usdCost } from "../numbers.js";
import { metric } from "../components/ui.js";
import { renderProviderUsage, renderEconomics, renderLedgerRows, renderBudgetEvents } from "../components/aiCostSummary.js";
import { renderArmRows, renderPerformanceRows, renderLeverageRows, renderComparisonChart } from "../components/strategyTournamentSummary.js";
import { renderHealthStrip } from "../components/tradingStatusBar.js";
import { renderPositions } from "../components/positionsTable.js";
import { renderActivity } from "../components/activityTimeline.js";

// Re-exported for existing importers/tests; implementations live in components/.
export { renderEconomics, renderLedgerRows, renderHealthStrip };

const PROVIDER_KINDS = [
  ["fixture_jev", "Jev fixture"],
  ["fixture_gpt", "GPT fixture"],
  ["typesafe_jev", "TypeSafe Jev"],
  ["openai_responses", "OpenAI Responses"],
  ["foundry_responses", "Microsoft Foundry / Azure"],
  ["compatible_responses", "Compatible Responses endpoint"],
];
const ARMS = [
  ["quant", "Quant"],
  ["jev", "Jev"],
  ["luna", "Luna"],
  ["luna_skill", "Luna + Skill"],
  ["quant_jev", "Quant + Jev"],
  ["hybrid", "Hybrid"],
];
const SHADOW_LEVERAGE = [1, 2, 3, 5, 10];
const MARKET_DATA_RETENTION_DAYS = [30, 90, 365];

function normalizeRetentionDays(value) {
  if (value === null || value === undefined || value === "") return "";
  const days = Number(value);
  return MARKET_DATA_RETENTION_DAYS.includes(days) ? String(days) : "";
}

function retentionDaysFromValue(value) {
  const days = normalizeRetentionDays(value);
  return days === "" ? null : Number(days);
}

function lunaResultLabel(result) {
  if (!result || typeof result !== "object") return "Unknown";
  switch (String(result.status || "").toLowerCase()) {
    case "not_evaluated":
      return "Not evaluated";
    case "not_invoked":
      return "Not invoked";
    case "invoked":
      return `Invoked · ${displayValue(result.decision)}`;
    case "failed":
      return `Failed · ${displayValue(result.decision)}`;
    default:
      return "Unknown";
  }
}

function riskDecisionLabel(risk) {
  if (
    String(risk.status || "").toUpperCase() === "NOT_EVALUATED"
    || risk.approved === null
    || risk.approved === undefined
  ) {
    return "Not evaluated";
  }
  if (risk.approved === true) return "Approved";
  if (risk.approved === false) return "Rejected";
  return displayValue(risk.decision);
}

export function renderRetentionSection(retentionDays = null) {
  const selectedDays = normalizeRetentionDays(retentionDays);
  return `
    <section class="paper-subpanel">
      <div class="section-heading">
        <h3>Export / Data Retention</h3>
        <span class="demo-tag">Archived market bars only</span>
      </div>
      <div class="paper-form-grid">
        <label>Archived raw market bar retention
          <select name="market_data_retention_days">
            <option value="" ${selectedDays === "" ? "selected" : ""}>Keep indefinitely</option>
            ${MARKET_DATA_RETENTION_DAYS.map((days) => `
              <option value="${days}"${selectedDays === String(days) ? " selected" : ""}>${days} days</option>
            `).join("")}
          </select>
        </label>
      </div>
      <p class="field-help">Retention prunes archived raw market bars only; decision, trade, order, risk, and equity ledger records are preserved.</p>
      <p class="field-help"><strong>Pruning warning:</strong> Any non-null period permits archived raw market bars older than the selected period to be pruned.</p>
      <p class="field-help">Save the selection with EXP-001 using “Save experiment settings” below.</p>
      <div class="paper-runtime-actions">
        <button class="btn btn--ghost" type="button" data-action="export">Export bundle</button>
      </div>
    </section>
  `;
}

function setValue(form, name, value) {
  const control = form.elements.namedItem(name);
  if (!control) return;
  if (control.type === "checkbox") control.checked = Boolean(value);
  else control.value = value ?? "";
}

function renderEquityChart(points) {
  const values = (points || [])
    .map((point) => Number(point.equity))
    .filter((value) => Number.isFinite(value));
  if (values.length < 2) {
    return '<p class="muted">Equity curve appears after the first persisted cycle.</p>';
  }
  const width = 640;
  const height = 180;
  const padding = 12;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const coordinates = values.map((value, index) => {
    const x = padding + (index / Math.max(values.length - 1, 1)) * (width - 2 * padding);
    const y = height - padding - ((value - min) / span) * (height - 2 * padding);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return `
    <svg class="paper-equity-chart" viewBox="0 0 ${width} ${height}" role="img"
      aria-label="Recorded paper equity values from ${values.length} observations">
      <line x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}" class="paper-chart-axis"></line>
      <polyline points="${coordinates.join(" ")}" class="paper-chart-line"></polyline>
    </svg>
    <div class="paper-chart-caption">
      <span>Low ${escapeHtml(money(min))}</span>
      <span>High ${escapeHtml(money(max))}</span>
      <span>${values.length} persisted observations</span>
    </div>
  `;
}

function renderProviderCards(providers) {
  if (!providers.length) {
    return '<p class="muted">No provider configurations are available.</p>';
  }
  return providers.map((provider) => {
    const jevCompatible = ["fixture_jev", "typesafe_jev"].includes(provider.kind);
    const gptCompatible = [
      "fixture_gpt",
      "openai_responses",
      "foundry_responses",
      "compatible_responses",
    ].includes(provider.kind);
    return `
    <article class="paper-provider-card">
      <div class="section-heading">
        <strong>${escapeHtml(provider.display_name)}</strong>
        <span class="demo-tag">${escapeHtml(provider.credential_status.replaceAll("_", " "))}</span>
      </div>
      <div class="paper-provider-meta">
        <span>${escapeHtml(provider.kind)}</span>
        <span>Model: ${escapeHtml(provider.model)}</span>
        <span>${provider.enabled ? "Enabled" : "Disabled"}</span>
        <span>Validation: ${escapeHtml(titleCase(provider.last_validation_status))}</span>
        <span>Last test: ${escapeHtml(formatTimestamp(provider.last_validated_at))}</span>
        <span>Test latency: ${provider.last_validation_latency_ms === null ? "—" : `${Number(provider.last_validation_latency_ms).toFixed(1)} ms`}</span>
        ${provider.kind.startsWith("fixture_") ? "" : `<span>Reasoning: ${escapeHtml(provider.kind === "typesafe_jev" ? "n/a" : provider.reasoning_effort)}${provider.last_validation?.reasoning_effort_echoed ? ` (echoed ${escapeHtml(provider.last_validation.reasoning_effort_echoed)})` : ""}</span>
        <span>Returned model: ${escapeHtml(provider.last_validation?.returned_model || provider.last_validation?.model || "—")}</span>
        <span><a href="#/settings">Manage key in Settings</a></span>`}
      </div>
      <div class="paper-provider-actions">
        <button class="btn btn--ghost btn--small" type="button"
          data-action="edit-provider" data-provider-id="${escapeHtml(provider.provider_id)}">
          Edit settings
        </button>
        <button class="btn btn--ghost btn--small" type="button"
          data-action="test-provider" data-provider-id="${escapeHtml(provider.provider_id)}">
          Test connection
        </button>
        ${jevCompatible ? `<button class="btn btn--ghost btn--small" type="button"
          data-action="set-default-jev" data-provider-id="${escapeHtml(provider.provider_id)}">
          Use for Jev
        </button>` : ""}
        ${gptCompatible ? `<button class="btn btn--ghost btn--small" type="button"
          data-action="set-default-gpt" data-provider-id="${escapeHtml(provider.provider_id)}">
          Use for GPT
        </button>` : ""}
      </div>
    </article>
    `;
  }).join("");
}

function renderSchedulerMetrics(metrics) {
  const scheduler = metrics.scheduler_cycles;
  const operations = metrics.operational;
  return `
    <div class="paper-metric-grid">
      ${metric("Scheduled expected", String(scheduler.expected), "15m candle slots × configured symbols")}
      ${metric("Cycles completed", String(scheduler.completed), `${scheduler.processing} processing`)}
      ${metric("Cycles skipped", String(scheduler.schedule_skipped + scheduler.signal_skipped), `${scheduler.schedule_skipped} schedule · ${scheduler.signal_skipped} signal gate`)}
      ${metric("Cycles failed", String(scheduler.failed + scheduler.interrupted), `${operations.market_data_failure_count} data · ${operations.provider_error_count} AI provider errors`)}
    </div>
  `;
}

function renderCycles(cycles) {
  if (!Array.isArray(cycles) || !cycles.length) {
    return '<tr><td colspan="6" class="table-empty">No cycles recorded yet.</td></tr>';
  }
  return cycles.slice(0, 12).map((cycle) => {
    const routing = cycle.routing || null;
    const quantGate = routing?.quant_gate || {};
    const jevDecision = routing?.jev_decision || {};
    const lunaResult = routing?.luna_result;
    const risk = routing?.risk_decision || cycle.risk || {};
    const paperExecution = routing?.paper_execution || {};
    const outcome = cycle.primary_decision?.decision || cycle.status;
    const riskDecision = riskDecisionLabel(risk);
    const lunaLabel = lunaResultLabel(lunaResult);
    const snapshot = displayValue(cycle.snapshot_hash);
    return `
      <tr>
        <td>${escapeHtml(displayValue(cycle.symbol))}</td>
        <td>${escapeHtml(formatTimestamp(cycle.data_cutoff || cycle.cycle_slot))}</td>
        <td><span class="pill pill--neutral">${escapeHtml(displayValue(outcome))}</span></td>
        <td class="paper-cycle-routing"><small>
          <strong>Quant gate:</strong> ${escapeHtml(displayValue(quantGate.decision))} · signal strength ${escapeHtml(fixedNumber(quantGate.signal_strength))}
          <br /><strong>Jev:</strong> ${escapeHtml(displayValue(jevDecision.decision))} · confidence ${escapeHtml(percent(jevDecision.confidence))} · direction ${escapeHtml(displayValue(jevDecision.direction))}
          <br /><strong>Escalation:</strong> ${escapeHtml(yesNo(routing?.escalation_required))} · <strong>Luna:</strong> ${escapeHtml(lunaLabel)}
          <br /><strong>Risk:</strong> ${escapeHtml(riskDecision)} · code ${escapeHtml(displayValue(risk.code))} · reason ${escapeHtml(displayValue(risk.reason))}
          <br /><strong>Paper execution:</strong> ${escapeHtml(displayValue(paperExecution.status))} · orders ${escapeHtml(displayValue(paperExecution.order_count))} · fills ${escapeHtml(displayValue(paperExecution.fill_count))}
        </small></td>
        <td>${escapeHtml(dataOriginLabel(cycle.data_origin))}</td>
        <td>${escapeHtml(snapshot === "—" ? snapshot : snapshot.slice(0, 12))}</td>
      </tr>
    `;
  }).join("");
}

function renderRiskEvents(events) {
  if (!events.length) return '<p class="muted">No risk decisions have been recorded.</p>';
  return `
    <ul class="paper-event-list">
      ${events.slice(0, 10).map((event) => `
        <li>
          <span class="pill pill--${event.status === "blocked" ? "negative" : "positive"}">${escapeHtml(event.status)}</span>
          <strong>${escapeHtml(event.code)}</strong>
          <span>${escapeHtml(event.symbol || event.cohort)}</span>
          <small>${escapeHtml(event.reason)}</small>
        </li>
      `).join("")}
    </ul>
  `;
}

function renderMetrics(dashboard) {
  const { metrics } = dashboard;
  const portfolio = metrics.portfolio;
  const latency = metrics.latency_ms;
  const costValue = metrics.ai_cost_status === "fixture_only"
    ? "Local fixture"
    : metrics.ai_cost_estimate_usd === null
      ? "Unpriced"
      : `$${metrics.ai_cost_estimate_usd.toFixed(6)}`;
  return `
    <div class="paper-metric-grid">
      ${metric("Equity", money(portfolio.ending_equity_usdt), `${portfolio.open_position_count} open positions`)}
      ${metric("Net PnL", money(portfolio.net_pnl_usdt), `${portfolio.closed_trade_count} closed trades`)}
      ${metric("Risk blocks", String(metrics.risk_block_count), `${metrics.risk_approval_count} risk approvals`)}
      ${metric("Escalation rate", percent(metrics.escalation_rate), `${displayValue(metrics.sample_denominators?.eligible_quant_cases)} eligible cases`)}
      ${metric("AI calls", String(metrics.jev_calls + metrics.gpt_calls), `${metrics.jev_calls} Jev · ${metrics.gpt_calls} GPT`)}
      ${metric("AI cost", costValue, metrics.ai_cost_status.replaceAll("_", " "))}
      ${metric("Cost / eligible case", usd(metrics.ai_cost_per_eligible_case_usd), "versioned provider pricing")}
      ${metric("Cost / closed trade", usd(metrics.ai_cost_per_closed_trade_usd), "priced calls only")}
      ${metric("AI p50 / p95", latency.ai_sample_count ? `${latency.ai_p50.toFixed(1)} / ${latency.ai_p95.toFixed(1)} ms` : "—", `${latency.ai_sample_count} measured calls`)}
      ${metric("Cycle p50 / p95", latency.cycle_sample_count ? `${latency.cycle_p50.toFixed(1)} / ${latency.cycle_p95.toFixed(1)} ms` : "—", `${latency.cycle_sample_count} measured cycles`)}
    </div>
  `;
}

export function renderDashboard(host, dashboard) {
  const state = dashboard.experiment.status;
  const origin = dashboard.data_safety.origin;
  const { metrics } = dashboard;
  const { operational: operations } = metrics;
  host.innerHTML = `
    <section class="panel">
      <div class="page-header">
        <div><h2>Runtime dashboard</h2><p>EXP-001 · next cycle ${escapeHtml(formatTimestamp(dashboard.next_cycle_at))}</p></div>
        <span class="demo-tag">${escapeHtml(origin)} · ${escapeHtml(titleCase(state))}</span>
      </div>
      ${renderMetrics(dashboard)}
      <section class="paper-subpanel">
        <div class="section-heading"><h3>AI spend and economic PnL</h3><span class="demo-tag">USD cost · USDT PnL · no silent conversion</span></div>
        ${renderEconomics(dashboard.economics)}
      </section>
      <section class="paper-subpanel">
        <div class="section-heading"><h3>Scheduler and provider health</h3>
          <span class="demo-tag">${dashboard.data_safety.real_money_execution ? "Unsafe" : "PAPER only"}</span>
        </div>
        ${renderSchedulerMetrics(metrics)}
        <p class="chart-caption">
          ${escapeHtml(operations.monitor_failure_count)} monitor failures ·
          ${escapeHtml(operations.restart_recovery_count)} interrupted cycles recovered ·
          ${escapeHtml(metrics.scheduler_cycles.duplicate_preventions)} duplicate-cycle blocks
        </p>
      </section>
      <div class="paper-dashboard-grid">
        <section class="paper-subpanel">
          <h3>Equity curve</h3>
          ${renderEquityChart(dashboard.equity)}
        </section>
        <section class="paper-subpanel">
          <h3>Portfolio reconciliation</h3>
          <dl class="paper-definition-list">
            <div><dt>Cash</dt><dd>${escapeHtml(money(dashboard.metrics.portfolio.cash_balance_usdt))}</dd></div>
            <div><dt>Unrealized</dt><dd>${escapeHtml(money(dashboard.metrics.portfolio.unrealized_pnl_usdt))}</dd></div>
            <div><dt>Margin used</dt><dd>${escapeHtml(money(dashboard.metrics.portfolio.margin_used_usdt))}</dd></div>
            <div><dt>Available margin</dt><dd>${escapeHtml(money(dashboard.metrics.portfolio.available_margin_usdt))}</dd></div>
            <div><dt>Fees</dt><dd>${escapeHtml(money(dashboard.metrics.portfolio.fees_usdt))}</dd></div>
            <div><dt>Funding</dt><dd>${escapeHtml(money(dashboard.metrics.portfolio.funding_paid_usdt))}</dd></div>
            <div><dt>Slippage drag</dt><dd>${escapeHtml(money(dashboard.metrics.portfolio.slippage_drag_usdt))}</dd></div>
            <div><dt>Equity reconciles</dt><dd>${dashboard.metrics.reconciliation.ending_equity_equals_cash_plus_unrealized ? "Yes" : "No"}</dd></div>
          </dl>
        </section>
      </div>
    </section>
    <section class="panel">
      <div class="section-heading"><h2>Primary paper positions</h2><span class="demo-tag">PAPER wallet · risk-controlled · isolated margin</span></div>
      ${renderPositions(dashboard.positions)}
    </section>
    ${(dashboard.exchange_accounts || []).length ? `<section class="panel panel--real-account">
      <div class="section-heading"><h2>Gate account mirror</h2><span class="demo-tag demo-tag--real">REAL ACCOUNT · read-only · separate from PAPER</span></div>
      ${dashboard.exchange_accounts.map((account) => renderAccountMirror(account)).join("")}
    </section>` : ""}
    <section class="panel">
      <div class="section-heading"><h2>Aligned arm evaluation</h2><span class="demo-tag">Same frozen snapshot per cycle</span></div>
      <p class="panel-subtitle">${escapeHtml(metrics.decision_quality_status.replaceAll("_", " "))}. A missing sample is shown as —, not as zero.</p>
      <section class="paper-subpanel"><h3>Arm net PnL</h3>${renderComparisonChart(metrics.arms)}</section>
      <div class="table-scroll"><table class="data-table">
        <thead><tr><th>Arm</th><th>Closed n</th><th>Net PnL</th><th>Expectancy</th><th>Max drawdown</th><th>Origin</th></tr></thead>
        <tbody>${renderArmRows(metrics.arms)}</tbody>
      </table></div>
      <div class="paper-dashboard-grid">
        <section class="paper-subpanel">
          <h3>Performance by regime</h3>
          <div class="table-scroll"><table class="data-table">
            <thead><tr><th>Regime</th><th>Closed n</th><th>Net PnL</th><th>Expectancy</th><th>Max drawdown</th><th>Data origin</th></tr></thead>
            <tbody>${renderPerformanceRows(metrics.by_regime, "regime")}</tbody>
          </table></div>
        </section>
        <section class="paper-subpanel">
          <h3>Performance by asset</h3>
          <div class="table-scroll"><table class="data-table">
            <thead><tr><th>Asset</th><th>Closed n</th><th>Net PnL</th><th>Expectancy</th><th>Max drawdown</th><th>Data origin</th></tr></thead>
            <tbody>${renderPerformanceRows(metrics.by_asset, "symbol")}</tbody>
          </table></div>
        </section>
      </div>
    </section>
    <section class="panel">
      <div class="section-heading"><h2>Parallel leverage cohorts</h2><span class="demo-tag">Same intent · separate wallet</span></div>
      <div class="table-scroll"><table class="data-table">
        <thead><tr><th>Leverage</th><th>Closed n</th><th>Net PnL</th><th>Open</th><th>Origin</th></tr></thead>
        <tbody>${renderLeverageRows(metrics.shadow_leverage)}</tbody>
      </table></div>
    </section>
    <section class="paper-dashboard-grid">
      <section class="panel"><h2>Recent cycles</h2><div class="table-scroll"><table class="data-table">
        <thead><tr><th>Symbol</th><th>Cutoff</th><th>Primary outcome</th><th>Routing and execution</th><th>Origin</th><th>Snapshot</th></tr></thead>
        <tbody>${renderCycles(dashboard.cycles)}</tbody>
      </table></div></section>
      <section class="panel"><h2>Risk journal</h2>${renderRiskEvents(dashboard.risk_events)}</section>
    </section>
    <section class="paper-dashboard-grid">
      <section class="panel"><h2>Provider usage and cost</h2>${renderProviderUsage(metrics.provider_usage)}
        <h3>AI cost ledger by provider</h3>
        <div class="table-scroll"><table class="data-table">
          <thead><tr><th>Provider</th><th>Model</th><th>Reasoning</th><th>Calls</th><th>In / out tokens</th><th>Reasoning tokens</th><th>p50 / p95</th><th>Estimated</th><th>Billed</th><th>Budget blocks</th></tr></thead>
          <tbody>${renderLedgerRows(dashboard.ai_cost?.providers)}</tbody>
        </table></div>
        <h3>Budget events</h3>${renderBudgetEvents(dashboard.ai_cost?.recent_budget_events)}
      </section>
      <section class="panel"><h2>Activity</h2>${renderActivity(dashboard.activity)}</section>
    </section>
  `;
}

function renderExperimentOptions(form, providers) {
  const experiment = form.elements;
  const jevSelect = experiment.namedItem("jev_provider_id");
  const gptSelect = experiment.namedItem("gpt_provider_id");
  if (jevSelect) {
    const options = providers
      .filter((provider) => provider.kind.endsWith("_jev") || provider.kind === "typesafe_jev")
      .map((provider) => `<option value="${escapeHtml(provider.provider_id)}">${escapeHtml(provider.display_name)} · ${escapeHtml(provider.model)}</option>`)
      .join("");
    jevSelect.innerHTML = options || '<option value="">No Jev provider</option>';
  }
  if (gptSelect) {
    const options = providers
      .filter((provider) => provider.kind.includes("gpt") || provider.kind.includes("responses"))
      .map((provider) => `<option value="${escapeHtml(provider.provider_id)}">${escapeHtml(provider.display_name)} · ${escapeHtml(provider.model)}</option>`)
      .join("");
    gptSelect.innerHTML = options || '<option value="">No GPT provider</option>';
  }
}

function fillExperimentForm(form, config) {
  for (const key of [
    "market_data_mode",
    "starting_balance_usdt",
    "risk_per_trade",
    "max_positions",
    "primary_leverage",
    "entry_order_type",
    "minimum_notional_usdt",
    "max_leverage",
    "max_daily_loss",
    "max_drawdown_stop",
    "max_consecutive_losses",
    "schedule_delay_seconds",
    "monitor_interval_seconds",
    "minimum_signal_strength",
    "primary_arm",
    "jev_provider_id",
    "gpt_provider_id",
    "fallback_policy",
  ]) {
    setValue(form, key, config[key]);
  }
  setValue(
    form,
    "market_data_retention_days",
    normalizeRetentionDays(config.market_data_retention_days),
  );
  setValue(form, "risk_per_trade_percent", config.risk_per_trade * 100);
  setValue(form, "max_daily_loss_percent", config.max_daily_loss * 100);
  setValue(form, "max_drawdown_stop_percent", config.max_drawdown_stop * 100);
  setValue(form, "symbols", config.symbols.join(", "));
  setValue(form, "signal_gate_enabled", config.signal_gate_enabled);
  setValue(form, "jev_enabled", config.jev_enabled);
  setValue(form, "gpt_escalation_enabled", config.gpt_escalation_enabled);
  setValue(form, "force_escalation", config.force_escalation);
  setValue(form, "auto_resume", config.auto_resume);
  for (const arm of ARMS) {
    setValue(form, `arm_${arm[0]}`, config.evaluation_arms.includes(arm[0]));
  }
  for (const leverage of SHADOW_LEVERAGE) {
    setValue(form, `shadow_${leverage}`, config.shadow_leverage.includes(leverage));
  }
  const policy = config.escalation_policy;
  for (const key of [
    "minimum_confidence",
    "conflict_threshold",
    "setup_borderline_min",
    "setup_borderline_max",
    "funding_concern_threshold",
    "oi_conflict_score_threshold",
    "liquidity_score_threshold",
    "leverage_stress_score_threshold",
  ]) {
    setValue(form, `policy_${key}`, policy[key]);
  }
}

function experimentFromForm(form, original) {
  const data = new FormData(form);
  const checked = (name) => data.get(name) === "on";
  const arms = ARMS.map(([id]) => id).filter((id) => checked(`arm_${id}`));
  const leverage = SHADOW_LEVERAGE.filter((value) => checked(`shadow_${value}`));
  return {
    ...original,
    execution_mode: "PAPER",
    decision_timeframe: "15m",
    context_timeframes: ["1h", "4h"],
    symbols: String(data.get("symbols") || "")
      .split(",")
      .map((symbol) => symbol.trim().toUpperCase())
      .filter(Boolean),
    market_data_mode: data.get("market_data_mode"),
    market_data_retention_days: retentionDaysFromValue(data.get("market_data_retention_days")),
    starting_balance_usdt: Number(data.get("starting_balance_usdt")),
    risk_per_trade: Number(data.get("risk_per_trade_percent")) / 100,
    max_positions: Number(data.get("max_positions")),
    primary_leverage: Number(data.get("primary_leverage")),
    entry_order_type: data.get("entry_order_type"),
    minimum_notional_usdt: Number(data.get("minimum_notional_usdt")),
    max_leverage: Number(data.get("max_leverage")),
    shadow_leverage: leverage,
    max_daily_loss: Number(data.get("max_daily_loss_percent")) / 100,
    max_drawdown_stop: Number(data.get("max_drawdown_stop_percent")) / 100,
    max_consecutive_losses: Number(data.get("max_consecutive_losses")),
    schedule_delay_seconds: Number(data.get("schedule_delay_seconds")),
    monitor_interval_seconds: Number(data.get("monitor_interval_seconds")),
    signal_gate_enabled: checked("signal_gate_enabled"),
    minimum_signal_strength: Number(data.get("minimum_signal_strength")),
    primary_arm: data.get("primary_arm"),
    evaluation_arms: arms,
    jev_enabled: checked("jev_enabled"),
    jev_provider_id: data.get("jev_provider_id"),
    gpt_escalation_enabled: checked("gpt_escalation_enabled"),
    gpt_provider_id: data.get("gpt_provider_id"),
    fallback_policy: data.get("fallback_policy"),
    force_escalation: checked("force_escalation"),
    auto_resume: checked("auto_resume"),
    escalation_policy: {
      ...original.escalation_policy,
      minimum_confidence: Number(data.get("policy_minimum_confidence")),
      conflict_threshold: Number(data.get("policy_conflict_threshold")),
      setup_borderline_min: Number(data.get("policy_setup_borderline_min")),
      setup_borderline_max: Number(data.get("policy_setup_borderline_max")),
      funding_concern_threshold: Number(data.get("policy_funding_concern_threshold")),
      oi_conflict_score_threshold: Number(data.get("policy_oi_conflict_score_threshold")),
      liquidity_score_threshold: Number(data.get("policy_liquidity_score_threshold")),
      leverage_stress_score_threshold: Number(data.get("policy_leverage_stress_score_threshold")),
    },
  };
}

function providerFromForm(form) {
  const data = new FormData(form);
  const kind = data.get("kind");
  const credentialEnv = String(data.get("credential_env") || "").trim();
  const pricingVersion = String(data.get("pricing_version") || "").trim();
  const inputPrice = String(data.get("input_per_million") || "").trim();
  const outputPrice = String(data.get("output_per_million") || "").trim();
  const pricing = pricingVersion && inputPrice !== "" && outputPrice !== ""
    ? {
      version: pricingVersion,
      input_per_million: Number(inputPrice),
      output_per_million: Number(outputPrice),
    }
    : null;
  const fixture = String(kind).startsWith("fixture_");
  const provider = {
    provider_id: String(data.get("provider_id") || "").trim(),
    kind,
    display_name: String(data.get("display_name") || "").trim(),
    base_url: fixture ? "" : String(data.get("base_url") || "").trim(),
    model: String(data.get("model") || "").trim(),
    auth_scheme: data.get("auth_scheme"),
    timeout_seconds: Number(data.get("timeout_seconds")),
    reasoning_effort: data.get("reasoning_effort"),
    enabled: data.get("enabled") === "on",
    pricing,
  };
  if (credentialEnv) provider.credential_env = fixture ? null : credentialEnv;
  return provider;
}

function fillProviderForm(form, provider) {
  setValue(form, "kind", provider.kind);
  setValue(form, "provider_id", provider.provider_id);
  setValue(form, "display_name", provider.display_name);
  setValue(form, "base_url", provider.base_url);
  setValue(form, "model", provider.model);
  setValue(form, "auth_scheme", provider.auth_scheme || "bearer");
  const authField = form.querySelector("[data-auth-scheme-field]");
  if (authField) authField.hidden = provider.kind !== "typesafe_jev";
  setValue(form, "timeout_seconds", provider.timeout_seconds);
  setValue(form, "reasoning_effort", provider.reasoning_effort);
  setValue(form, "credential_env", "");
  setValue(form, "enabled", provider.enabled);
  setValue(form, "pricing_version", provider.pricing?.version || "");
  setValue(form, "input_per_million", provider.pricing?.input_per_million ?? "");
  setValue(form, "output_per_million", provider.pricing?.output_per_million ?? "");
}

function providerPreset(form, force = false) {
  const kind = form.elements.namedItem("kind").value;
  const id = form.elements.namedItem("provider_id");
  const display = form.elements.namedItem("display_name");
  const base = form.elements.namedItem("base_url");
  const model = form.elements.namedItem("model");
  const env = form.elements.namedItem("credential_env");
  const defaults = {
    fixture_jev: ["fixture-jev-custom", "Jev · offline fixture", "", "fixture-jev-v1", ""],
    fixture_gpt: ["fixture-gpt-custom", "GPT · offline fixture", "", "fixture-gpt-v1", ""],
    typesafe_jev: ["typesafe-jev", "TypeSafe Jev", "https://api.typesafe.ai", "jev-latest", "TYPESAFE_API_KEY"],
    openai_responses: ["openai-responses", "OpenAI Responses", "https://api.openai.com/v1", "gpt-6-luna", "OPENAI_API_KEY"],
    foundry_responses: ["foundry-responses", "Microsoft Foundry / Azure", "", "gpt-6-luna", "AZURE_OPENAI_API_KEY"],
    compatible_responses: ["compatible-responses", "Compatible Responses endpoint", "", "gpt-6-luna", "COMPATIBLE_AI_API_KEY"],
  };
  const [providerId, displayName, baseUrl, modelName, envName] = defaults[kind];
  if (force || !id.value) id.value = providerId;
  if (force || !display.value) display.value = displayName;
  if (force || !base.value) base.value = baseUrl;
  if (force || !model.value) model.value = modelName;
  if (force || !env.value) env.value = envName;
}

function setFeedback(root, message, tone = "neutral") {
  const output = root.querySelector("[data-feedback]");
  if (!output) return;
  output.className = `paper-feedback paper-feedback--${tone}`;
  output.textContent = message;
}

function setLoading(host, message) {
  if (host) host.innerHTML = `<section class="panel"><p class="muted">${escapeHtml(message)}</p></section>`;
}

export function render(root) {
  const priorHandler = root.__paperHandler;
  if (priorHandler) root.removeEventListener("click", priorHandler);
  if (root.__paperSubmitHandler) root.removeEventListener("submit", root.__paperSubmitHandler);
  root.innerHTML = `
    <div class="paper-page">
      <section class="panel">
        <div class="page-header">
          <div>
            <h1>Paper Futures Research</h1>
            <p>Local, loopback-only experiment · isolated-margin simulation · real-money execution is unavailable</p>
          </div>
          <span class="demo-tag">PAPER ONLY</span>
        </div>
        <p class="paper-safety-note">
          Gate mode reads live Gate USDT-perpetual public data through the local backend; fixture mode is labeled FIXTURE.
          Provider and exchange credentials live in the OS credential store (Settings) and are never sent back to this browser.
          Real Gate order placement is blocked by design; every trade here is a PAPER simulation.
        </p>
        <div class="paper-feedback" data-feedback role="status" aria-live="polite">Connecting to the local runtime…</div>
      </section>

      <div data-health-strip></div>

      <section class="panel">
        <div class="section-heading">
          <h2>Live market · Gate USDT perpetual</h2>
          <span class="demo-tag">Real exchange data · PAPER overlays</span>
        </div>
        <div class="live-chart" data-live-chart><p class="muted">Loading live chart…</p></div>
      </section>

      <section class="panel">
        <div class="section-heading">
          <h2>Runtime controls</h2>
          <span class="demo-tag">Closed 15m candle + configured delay</span>
        </div>
        <div class="paper-runtime-actions">
          <button class="btn btn--primary" type="button" data-action="start">Start</button>
          <button class="btn btn--ghost" type="button" data-action="pause">Pause</button>
          <button class="btn btn--ghost" type="button" data-action="resume">Resume</button>
          <button class="btn btn--ghost" type="button" data-action="stop">Stop</button>
          <button class="btn btn--ghost" type="button" data-action="run-cycle">Run closed-bar cycle now</button>
          <button class="btn btn--ghost" type="button" data-action="refresh">Refresh</button>
        </div>
        <p class="field-help">Stop the runtime before editing EXP-001 or provider settings. Pausing disables new cycles but keeps position monitoring active.</p>
      </section>

      <div class="paper-dashboard-grid">
        <section class="panel">
          <div class="section-heading"><h2>EXP-001 configuration</h2><span class="demo-tag">Risk defaults are frozen after the first cycle</span></div>
          <form class="paper-form" data-form="experiment">
            <div class="paper-form-grid">
              <label>Market data
                <select name="market_data_mode">
                  <option value="fixture">Deterministic fixture (offline)</option>
                  <option value="gate_usdt">Gate.io USDT perpetual (live REST + WebSocket)</option>
                  <option value="binance_usdm">Binance USD-M public data</option>
                </select>
              </label>
              <label>Symbols
                <input name="symbols" type="text" value="BTCUSDT, ETHUSDT, SOLUSDT, SUIUSDT, SEIUSDT" />
              </label>
              <label>Starting balance (USDT)
                <input name="starting_balance_usdt" type="number" min="1" step="any" value="100" />
              </label>
              <label>Risk per trade (%)
                <input name="risk_per_trade_percent" type="number" min="0.01" max="2" step="0.01" value="1" />
              </label>
              <label>Primary leverage
                <select name="primary_leverage">${SHADOW_LEVERAGE.map((value) => `<option value="${value}">${value}x</option>`).join("")}</select>
              </label>
              <label>Entry order simulation
                <select name="entry_order_type">
                  <option value="market">Market + configured slippage</option>
                  <option value="limit">Limit, wait for a future bar touch</option>
                </select>
              </label>
              <label>Maximum leverage
                <select name="max_leverage">${SHADOW_LEVERAGE.map((value) => `<option value="${value}" ${value === 10 ? "selected" : ""}>${value}x</option>`).join("")}</select>
              </label>
              <label>Maximum positions
                <input name="max_positions" type="number" min="1" max="20" step="1" value="3" />
              </label>
              <label>Minimum paper notional (USDT)
                <input name="minimum_notional_usdt" type="number" min="0.1" step="any" value="5" />
              </label>
              <label>Daily loss stop (%)
                <input name="max_daily_loss_percent" type="number" min="0.1" max="50" step="0.1" value="5" />
              </label>
              <label>Drawdown stop (%)
                <input name="max_drawdown_stop_percent" type="number" min="0.1" max="50" step="0.1" value="15" />
              </label>
              <label>Loss-streak pause
                <input name="max_consecutive_losses" type="number" min="1" max="100" step="1" value="5" />
              </label>
              <label>Schedule delay (seconds)
                <input name="schedule_delay_seconds" type="number" min="0" max="900" step="1" value="60" />
              </label>
              <label>Monitor interval (seconds)
                <input name="monitor_interval_seconds" type="number" min="5" max="3600" step="1" value="30" />
              </label>
              <label>Primary arm
                <select name="primary_arm">${ARMS.map(([id, label]) => `<option value="${id}">${label}</option>`).join("")}</select>
              </label>
              <label>Jev provider
                <select name="jev_provider_id"></select>
              </label>
              <label>GPT provider
                <select name="gpt_provider_id"></select>
              </label>
              <label>Jev failure behavior
                <select name="fallback_policy">
                  <option value="SKIP">Skip new exposure</option>
                  <option value="DEFER">Defer the case</option>
                  <option value="GPT_FALLBACK">Use configured GPT fallback</option>
                </select>
              </label>
              <label>Minimum signal strength
                <input name="minimum_signal_strength" type="number" min="0" max="1" step="0.01" value="0.55" />
              </label>
            </div>
            <fieldset class="paper-fieldset">
              <legend>Evaluation arms · identical frozen input per symbol and cutoff</legend>
              <div class="paper-checkbox-grid">
                ${ARMS.map(([id, label]) => `<label class="checkbox-row"><input type="checkbox" name="arm_${id}" checked />${label}</label>`).join("")}
              </div>
            </fieldset>
            <fieldset class="paper-fieldset">
              <legend>Parallel leverage cohorts</legend>
              <div class="paper-checkbox-grid">
                ${SHADOW_LEVERAGE.map((value) => `<label class="checkbox-row"><input type="checkbox" name="shadow_${value}" checked />${value}x</label>`).join("")}
              </div>
            </fieldset>
            <div class="paper-form-grid paper-form-grid--compact">
              <label>Jev escalation threshold
                <input name="policy_minimum_confidence" type="number" min="0" max="1" step="0.01" value="0.65" />
              </label>
              <label>Conflict threshold
                <input name="policy_conflict_threshold" type="number" min="0" max="1" step="0.01" value="0.65" />
              </label>
              <label>Borderline setup minimum (Score)
                <input name="policy_setup_borderline_min" type="number" min="0" max="4" step="0.1" value="0.4" />
              </label>
              <label>Borderline setup maximum (Score)
                <input name="policy_setup_borderline_max" type="number" min="0" max="4" step="0.1" value="1.2" />
              </label>
              <label>Funding concern threshold
                <input name="policy_funding_concern_threshold" type="number" min="0" max="1" step="0.01" value="0.8" />
              </label>
              <label>OI conflict threshold (Score)
                <input name="policy_oi_conflict_score_threshold" type="number" min="0" max="4" step="0.1" value="1" />
              </label>
              <label>Liquidity risk threshold (Score)
                <input name="policy_liquidity_score_threshold" type="number" min="0" max="4" step="0.1" value="3" />
              </label>
              <label>Leverage stress threshold (Score)
                <input name="policy_leverage_stress_score_threshold" type="number" min="0" max="4" step="0.1" value="3" />
              </label>
            </div>
            <label class="checkbox-row"><input type="checkbox" name="signal_gate_enabled" checked />Require deterministic signal gate for Quant and Hybrid</label>
            <label class="checkbox-row"><input type="checkbox" name="jev_enabled" checked />Enable TypeSafe Jev calls for Jev-dependent arms</label>
            <label class="checkbox-row"><input type="checkbox" name="gpt_escalation_enabled" checked />Enable routed GPT escalation for Hybrid</label>
            <label class="checkbox-row"><input type="checkbox" name="force_escalation" />Force one GPT escalation path for research</label>
            <label class="checkbox-row"><input type="checkbox" name="auto_resume" checked />Resume a running experiment after local server restart</label>
            <p class="field-help">Decision cadence is fixed at 15m with 1h/4h context. Risk sizing, margin, liquidation, costs and fills remain deterministic.</p>
            ${renderRetentionSection()}
            <div class="composer-actions">
              <button class="btn btn--primary" type="submit">Save experiment settings</button>
              <button class="btn btn--ghost" type="button" data-action="test-market">Test selected market source</button>
            </div>
          </form>
        </section>

        <section class="panel">
          <div class="section-heading"><h2>AI provider settings</h2><span class="demo-tag">Secret values stay server-side</span></div>
          <div class="paper-provider-list" data-provider-list><p class="muted">Loading provider metadata…</p></div>
          <form class="paper-form" data-form="provider">
            <h3>Add or update provider</h3>
            <div class="paper-form-grid">
              <label>Provider type
                <select name="kind">${PROVIDER_KINDS.map(([id, label]) => `<option value="${id}">${label}</option>`).join("")}</select>
              </label>
              <label>Provider ID
                <input name="provider_id" type="text" required value="typesafe-jev" />
              </label>
              <label>Display name
                <input name="display_name" type="text" required value="TypeSafe Jev" />
              </label>
              <label>Base URL / API base
                <input name="base_url" type="text" placeholder="https://api.typesafe.ai" />
              </label>
              <label>Model / deployment
                <input name="model" type="text" required value="jev-latest" />
              </label>
              <label data-auth-scheme-field>
                <select name="auth_scheme">
                  <option value="bearer" selected>Bearer token</option>
                  <option value="raw">Raw API key</option>
                </select>
              </label>
              <label>Timeout (seconds)
                <input name="timeout_seconds" type="number" min="1" max="120" step="1" value="20" />
              </label>
              <label>Reasoning effort
                <select name="reasoning_effort">
                  <option value="low">Low</option><option value="medium">Medium</option>
                  <option value="high" selected>High</option><option value="max">Max</option>
                </select>
              </label>
              <label>Credential environment-variable name
                <input name="credential_env" type="text" placeholder="TYPESAFE_API_KEY" autocomplete="off" />
              </label>
              <label>Pricing configuration version (optional)
                <input name="pricing_version" type="text" placeholder="provider-pricing-2026-09" />
              </label>
              <label>Input price per 1M tokens (optional)
                <input name="input_per_million" type="number" min="0" step="any" />
              </label>
              <label>Output price per 1M tokens (optional)
                <input name="output_per_million" type="number" min="0" step="any" />
              </label>
            </div>
            <label class="checkbox-row"><input type="checkbox" name="enabled" checked />Enable this provider</label>
            <p class="field-help">
              Enter an environment-variable name only, never the credential value. Define that variable before starting the local server.
              External connection tests and inference can incur vendor usage.
            </p>
            <button class="btn btn--primary" type="submit">Save provider</button>
          </form>
        </section>
      </div>

      <div data-paper-dashboard><section class="panel"><p class="muted">Loading local experiment state…</p></section></div>
    </div>
  `;

  const configForm = root.querySelector('[data-form="experiment"]');
  const providerForm = root.querySelector('[data-form="provider"]');
  const dashboardHost = root.querySelector("[data-paper-dashboard]");
  let currentConfig = null;
  let currentProviders = [];
  let currentDashboard = null;

  async function refresh({ initial = false } = {}) {
    try {
      const [health, dashboard, providerResponse] = await Promise.all([
        paperApi.health(),
        paperApi.dashboard(),
        paperApi.providers(),
      ]);
      if (!health.ok || health.execution_mode !== "PAPER") {
        throw new Error("Local runtime did not confirm PAPER mode");
      }
      currentConfig = dashboard.experiment.config;
      currentProviders = providerResponse.providers || [];
      renderExperimentOptions(configForm, currentProviders);
      if (initial) fillExperimentForm(configForm, currentConfig);
      root.querySelector("[data-provider-list]").innerHTML = renderProviderCards(currentProviders);
      renderDashboard(dashboardHost, dashboard);
      currentDashboard = dashboard;
      root.querySelector("[data-health-strip]").innerHTML = renderHealthStrip(dashboard);
      const chartHost = root.querySelector("[data-live-chart]");
      if (chartHost && !chartHost.__liveChart) {
        mountLiveChart(chartHost, {
          api: paperApi,
          symbols: currentConfig.symbols,
          symbol: currentConfig.symbols[0],
          getOverlays: (symbol) => overlayLines(
            symbol,
            currentDashboard?.positions || [],
            (currentDashboard?.exchange_accounts || []).flatMap((account) => account.last_sync?.positions || []),
          ),
        });
      } else if (chartHost?.__liveChart) {
        chartHost.__liveChart.refreshOverlays();
      }
      const origin = dashboard.data_safety.origin;
      setFeedback(
        root,
        `Connected · ${dashboard.data_safety.execution_mode} · ${origin} · no real-money path`,
        origin === "FIXTURE" ? "fixture" : "success",
      );
    } catch (error) {
      setLoading(dashboardHost, "Start the local server with: python3 -m crypto_eval paper-server");
      setFeedback(root, error.message || "Local runtime is unavailable.", "error");
    }
  }

  function statusMessage(result, fallback) {
    if (result?.result?.fixture) {
      return "Fixture adapter passed locally; no external request was made.";
    }
    if (result?.result?.ok) {
      return `Connection passed · ${result.result.model} · ${Number(result.result.latency_ms).toFixed(1)} ms`;
    }
    return fallback;
  }

  async function handleClick(event) {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    button.disabled = true;
    try {
      if (["start", "pause", "resume", "stop"].includes(action)) {
        const result = await paperApi.runtime(action);
        await refresh();
        setFeedback(root, `Runtime ${result.experiment.status}.`, "success");
      } else if (action === "run-cycle") {
        setFeedback(root, "Running deterministic cycle on the selected market feed…");
        const result = await paperApi.runCycle();
        const cycles = result.cycles || [result.cycle];
        const duplicateCount = cycles.filter((cycle) => cycle?.duplicate).length;
        await refresh();
        setFeedback(
          root,
          `${cycles.length} cycle result(s) · ${duplicateCount} duplicate slot(s) skipped.`,
          "success",
        );
      } else if (action === "refresh") {
        await refresh();
      } else if (action === "test-market") {
        const mode = configForm.elements.namedItem("market_data_mode").value;
        const result = await paperApi.testMarketData(mode);
        setFeedback(
          root,
          `Market source passed · ${result.result.provider} · ${result.result.data_cutoff}`,
          result.result.data_origin === "FIXTURE" ? "fixture" : "success",
        );
      } else if (action === "test-provider") {
        const result = await paperApi.testProvider(button.dataset.providerId);
        const message = statusMessage(result, "Provider test passed.");
        const tone = result.result.fixture ? "fixture" : "success";
        await refresh();
        setFeedback(root, message, tone);
      } else if (action === "edit-provider") {
        const provider = currentProviders.find(
          (item) => item.provider_id === button.dataset.providerId,
        );
        if (!provider) throw new Error("Provider metadata is unavailable.");
        fillProviderForm(providerForm, provider);
        providerForm.scrollIntoView({ behavior: "smooth", block: "center" });
        setFeedback(
          root,
          "Provider settings loaded. The existing credential reference is retained when its field is left blank.",
          "success",
        );
      } else if (action === "set-default-jev" || action === "set-default-gpt") {
        if (!currentConfig) throw new Error("Load the experiment before selecting a provider.");
        const field = action === "set-default-jev" ? "jev_provider_id" : "gpt_provider_id";
        setValue(configForm, field, button.dataset.providerId);
        const updated = experimentFromForm(configForm, currentConfig);
        await paperApi.saveExperiment(updated);
        currentConfig = updated;
        await refresh();
        setFeedback(root, "Provider default saved with the EXP-001 configuration.", "success");
      } else if (action === "reduce-position" || action === "close-position") {
        const fraction = action === "close-position" ? 1 : Number(button.dataset.fraction);
        const result = await paperApi.reducePosition(button.dataset.positionId, fraction);
        setFeedback(
          root,
          result.result.accepted
            ? `Reduce-only fill recorded · ${Number(result.result.filled_quantity).toFixed(8)} units.`
            : result.result.reason || "Reduce-only request was rejected.",
          result.result.accepted ? "success" : "error",
        );
        await refresh();
        setFeedback(
          root,
          result.result.accepted
            ? `Reduce-only fill recorded · ${Number(result.result.filled_quantity).toFixed(8)} units.`
            : result.result.reason || "Reduce-only request was rejected.",
          result.result.accepted ? "success" : "error",
        );
      } else if (action === "export") {
        const blob = await paperApi.downloadExport();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "exp-001-paper-export.zip";
        link.click();
        URL.revokeObjectURL(url);
        setFeedback(root, "Secret-free experiment bundle downloaded.", "success");
      }
    } catch (error) {
      setFeedback(root, error.message || "Request failed safely.", "error");
    } finally {
      button.disabled = false;
    }
  }

  async function handleSubmit(event) {
    const form = event.target.closest("form[data-form]");
    if (!form) return;
    event.preventDefault();
    const submit = form.querySelector('[type="submit"]');
    if (submit) submit.disabled = true;
    try {
      if (form.dataset.form === "experiment") {
        if (!currentConfig) throw new Error("Load the experiment before saving settings.");
        const updated = experimentFromForm(form, currentConfig);
        const response = await paperApi.saveExperiment(updated);
        currentConfig = response.experiment.config;
        fillExperimentForm(form, currentConfig);
        setFeedback(root, "PAPER experiment configuration saved.", "success");
      } else if (form.dataset.form === "provider") {
        const provider = providerFromForm(form);
        const response = await paperApi.saveProvider(provider);
        setValue(form, "provider_id", response.provider.provider_id);
        await refresh();
        setFeedback(
          root,
          `Provider saved · credential status: ${response.provider.credential_status}.`,
          "success",
        );
      }
    } catch (error) {
      setFeedback(root, error.message || "Settings were not saved.", "error");
    } finally {
      if (submit) submit.disabled = false;
    }
  }

  function onKindChange() {
    providerPreset(providerForm, true);
    const authField = providerForm.querySelector("[data-auth-scheme-field]");
    if (authField) authField.hidden = providerForm.elements.namedItem("kind").value !== "typesafe_jev";
  }

  providerForm.elements.namedItem("kind").addEventListener("change", onKindChange);
  const handler = (event) => { handleClick(event); };
  root.__paperSubmitHandler = handleSubmit;
  root.__paperHandler = handler;
  root.addEventListener("click", handler);
  root.addEventListener("submit", handleSubmit);
  refresh({ initial: true });
}
