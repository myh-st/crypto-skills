import test from "node:test";
import assert from "node:assert/strict";

import { renderDashboard, renderRetentionSection } from "../modules/views/paperTrading.js";

function routingFixture(overrides = {}) {
  return {
    quant_gate: { decision: "PASS & ALLOW", signal_strength: 0.72 },
    jev_decision: { decision: "LONG <careful>", confidence: 0.81, direction: "long" },
    escalation_required: true,
    luna_result: { status: "invoked", decision: "HOLD" },
    risk_decision: { approved: true, code: "RISK_OK", reason: "limit & within policy" },
    paper_execution: { status: "filled", order_count: 2, fill_count: 1 },
    ...overrides,
  };
}

function createDashboard({
  escalationRate = 0.25,
  routing = routingFixture(),
  byRegime = [{
    regime: "unstable <market>",
    closed_trade_count: 2,
    net_pnl_usdt: -1.25,
    expectancy_usdt: null,
    max_drawdown: null,
    data_origin: "FIXTURE",
  }],
  byAsset = [{
    symbol: "BTCUSDT",
    closed_trade_count: 1,
    net_pnl_usdt: 0.5,
    expectancy_usdt: 0.5,
    max_drawdown: 0.1,
    data_origin: "REAL",
  }],
  arms = {},
} = {}) {
  return {
    experiment: { status: "stopped" },
    next_cycle_at: "2026-09-28T07:00:00.000Z",
    data_safety: { origin: "FIXTURE", real_money_execution: false },
    equity: [],
    positions: [],
    cycles: [{
      symbol: "BTCUSDT",
      data_cutoff: "2026-09-28T06:45:00.000Z",
      status: "completed",
      primary_decision: { decision: "LONG" },
      data_origin: "FIXTURE",
      snapshot_hash: "snapshot-123456789",
      routing,
    }],
    risk_events: [],
    activity: [],
    metrics: {
      portfolio: {
        ending_equity_usdt: 100,
        open_position_count: 0,
        net_pnl_usdt: null,
        closed_trade_count: 0,
        cash_balance_usdt: 100,
        unrealized_pnl_usdt: null,
        margin_used_usdt: 0,
        available_margin_usdt: 100,
        fees_usdt: 0,
        funding_paid_usdt: 0,
        slippage_drag_usdt: 0,
      },
      latency_ms: {
        ai_sample_count: 0,
        cycle_sample_count: 0,
      },
      ai_cost_status: "fixture_only",
      ai_cost_estimate_usd: null,
      ai_cost_per_eligible_case_usd: null,
      ai_cost_per_closed_trade_usd: null,
      risk_block_count: 0,
      risk_approval_count: 0,
      escalation_rate: escalationRate,
      sample_denominators: { eligible_quant_cases: 4 },
      jev_calls: 0,
      gpt_calls: 0,
      decision_quality_status: "fixture",
      arms,
      by_regime: byRegime,
      by_asset: byAsset,
      shadow_leverage: [],
      scheduler_cycles: {
        expected: 0,
        completed: 0,
        processing: 0,
        schedule_skipped: 0,
        signal_skipped: 0,
        failed: 0,
        interrupted: 0,
        duplicate_preventions: 0,
      },
      operational: {
        market_data_failure_count: 0,
        provider_error_count: 0,
        monitor_failure_count: 0,
        restart_recovery_count: 0,
      },
      reconciliation: { ending_equity_equals_cash_plus_unrealized: true },
      provider_usage: [],
    },
  };
}

function renderFixture(dashboard) {
  const host = { innerHTML: "" };
  renderDashboard(host, dashboard);
  return host.innerHTML;
}

test("paper dashboard exposes cycle routing and regime/asset performance", () => {
  const html = renderFixture(createDashboard());

  assert.match(html, /Escalation rate/);
  assert.match(html, /25\.0%/);
  assert.match(html, /Quant gate:<\/strong> PASS &amp; ALLOW · signal strength 0\.72/);
  assert.match(html, /Jev:<\/strong> LONG &lt;careful&gt; · confidence 81\.0% · direction long/);
  assert.match(html, /Escalation:<\/strong> Yes · <strong>Luna:<\/strong> Invoked · HOLD/);
  assert.match(html, /Risk:<\/strong> Approved · code RISK_OK · reason limit &amp; within policy/);
  assert.match(html, /Paper execution:<\/strong> filled · orders 2 · fills 1/);
  assert.match(html, /Performance by regime/);
  assert.match(html, /Performance by asset/);
  assert.match(html, /<td>BTCUSDT<\/td>\s*<td>1<\/td>\s*<td>0\.50 USDT<\/td>\s*<td>0\.50 USDT<\/td>\s*<td>10\.00%<\/td>/);
  assert.match(html, /unstable &lt;market&gt;/);
  assert.match(html, /<span class="demo-tag">Fixture<\/span>/);
  assert.match(html, /<span class="demo-tag">Real<\/span>/);
  assert.doesNotMatch(html, /<careful>|<market>/);
});

test("missing routing and evaluation metrics render as unavailable, not zero", () => {
  const html = renderFixture(createDashboard({
    escalationRate: null,
    routing: {
      quant_gate: { decision: null, signal_strength: null },
      jev_decision: { decision: null, confidence: null, direction: null },
      escalation_required: null,
      luna_result: null,
      risk_decision: { approved: null, status: "NOT_EVALUATED", code: "NOT_EVALUATED", reason: null },
      paper_execution: { status: null, order_count: null, fill_count: null },
    },
    byRegime: [{
      regime: "sideways",
      closed_trade_count: null,
      net_pnl_usdt: null,
      expectancy_usdt: null,
      max_drawdown: null,
      data_origin: null,
    }],
    byAsset: [],
    arms: {
      hybrid: {
        closed_trade_count: 1,
        open_position_count: 0,
        net_pnl_usdt: null,
        expectancy_usdt: null,
        max_drawdown: null,
        data_origin: "FIXTURE",
      },
    },
  }));

  assert.match(html, /paper-metric-value">—<\/span>\s*<span class="paper-metric-label">Escalation rate/);
  assert.match(html, /Escalation:<\/strong> Unknown · <strong>Luna:<\/strong> Unknown/);
  assert.match(html, /signal strength —/);
  assert.match(html, /confidence — · direction —/);
  assert.match(html, /Risk:<\/strong> Not evaluated · code NOT_EVALUATED · reason —/);
  assert.match(html, /Paper execution:<\/strong> — · orders — · fills —/);
  assert.match(html, /<td>sideways<\/td>\s*<td>—<\/td>\s*<td>—<\/td>\s*<td>—<\/td>\s*<td>—<\/td>\s*<td><span class="demo-tag">—<\/span><\/td>/);
  assert.match(html, /Net PnL is unavailable for the recorded experiment arms/);
});

test("cycle routing labels explicit non-escalation and an uninvoked Luna result", () => {
  const html = renderFixture(createDashboard({
    routing: routingFixture({
      escalation_required: false,
      luna_result: { status: "not_invoked", decision: null },
    }),
  }));

  assert.match(html, /Escalation:<\/strong> No · <strong>Luna:<\/strong> Not invoked/);
});

test("cycle routing distinguishes every persisted Luna result status", () => {
  const results = [
    ["not_evaluated", "Not evaluated"],
    ["not_invoked", "Not invoked"],
    ["invoked", "Invoked · SHORT"],
    ["failed", "Failed"],
  ];

  for (const [status, expected] of results) {
    const html = renderFixture(createDashboard({
      routing: routingFixture({
        luna_result: { status, decision: status === "invoked" ? "SHORT" : null },
      }),
    }));
    assert.ok(html.includes(`<strong>Luna:</strong> ${expected}`), `unexpected Luna label for ${status}`);
  }
});

test("Export / Data Retention defaults to indefinite and explains the limited pruning scope", () => {
  const html = renderRetentionSection();
  const finiteRetentionHtml = renderRetentionSection(90);

  assert.match(html, /Export \/ Data Retention/);
  assert.match(html, /<select name="market_data_retention_days">/);
  assert.match(html, /<option value="" selected>Keep indefinitely<\/option>/);
  for (const days of [30, 90, 365]) {
    assert.match(html, new RegExp(`<option value="${days}">${days} days<\\/option>`));
  }
  assert.match(finiteRetentionHtml, /<option value="90" selected>90 days<\/option>/);
  assert.match(html, /permits archived raw market bars older than the selected period to be pruned/);
  assert.match(html, /decision, trade, order, risk, and equity ledger records are preserved/);
  assert.match(html, /data-action="export"/);
  assert.doesNotMatch(html, /purge|delete all/i);
});
