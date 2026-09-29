// Strategy Search: the day-trade futures research (reports/day-trade) as charts. While the background
// search runs it shows live progress; when it is done it shows the verdict, coin and strategy rankings,
// the leverage grid, scenarios, and Monte Carlo odds. Read-only; auto-refreshes.
import { escapeHtml, formatTimestamp } from "../format.js";
import { paperApi } from "../paperApi.js";
import { autoRefreshBar, startAutoRefresh } from "../components/autoRefresh.js";
import { barList, fmtNum, fmtPct, heatGrid, rangeBar } from "../components/charts.js";
import { applyMotion, skeleton } from "../components/motion.js";

const WATCH = new Set(["BTCUSDT", "ETHUSDT", "NEARUSDT", "SEIUSDT", "SUIUSDT", "AVAXUSDT", "ENAUSDT"]);
const PRE_DECLARED = "K20_sharpe";

function progress(status) {
  const s = status || {};
  const total = Number(s.coins_total || 30);
  const bar = (n, label) => `<label class="small">${escapeHtml(label)} ${escapeHtml(n ?? 0)} / ${escapeHtml(total)}
    <span class="campaign-bar"><span style="width:${Math.min(100, ((Number(n) || 0) / total) * 100).toFixed(1)}%"></span></span></label>`;
  return `<section class="panel">
    <div class="section-heading"><h2>Search in progress</h2><span class="pill pill--caution">${escapeHtml(s.stage || "not started")}</span></div>
    <div class="exp-progress" data-motion-fill="ss:progress">${bar(s.coins_downloaded, "Coins downloaded")}${bar(s.coins_searched, "Coins searched")}</div>
    <p class="muted small">${escapeHtml(s.configs_per_coin ?? "—")} parameter sets per coin · last update ${escapeHtml(formatTimestamp(s.updated_at))}
      ${s.error ? `<br><span class="neg-text">error: ${escapeHtml(s.error)}</span>` : ""}${s.note ? `<br>${escapeHtml(s.note)}` : ""}</p>
    <p class="small">The page fills in automatically when the search finishes. An hourly scheduled check restarts the job if it stops.</p>
  </section>`;
}

function gateList(gate) {
  if (!gate?.checks?.length) return "";
  const mark = { pass: "✓", fail: "✗", unknown: "?" };
  const show = (v) => (v === null || v === undefined ? "not measured" : typeof v === "number" && Math.abs(v) < 1 && v !== 0 ? `${(v * 100).toFixed(1)}%` : String(v));
  const thr = (c) => (Math.abs(c.threshold) < 1 && c.threshold !== 0 ? `${(c.threshold * 100).toFixed(0)}%` : String(c.threshold));
  return `<ul class="gate-list">${gate.checks.map((c) => `<li class="gate-${escapeHtml(c.status)}">
    <span class="gate-mark" aria-label="${escapeHtml(c.status)}">${mark[c.status] || "?"}</span>
    <span>${escapeHtml(c.name)}: <strong>${escapeHtml(show(c.value))}</strong> <small class="muted">(needs ${escapeHtml(c.op)} ${escapeHtml(thr(c))})</small></span></li>`).join("")}</ul>`;
}

function verdict(data) {
  const w = data.wfo?.[PRE_DECLARED] || {};
  const gate = data.gate || {};
  const pass = gate.passed === true;   // the full pre-declared gate, never a subset of it
  const weak = !pass && Number(w.oos_ret) > 0;
  const tone = pass ? "pos" : weak ? "warn" : "neg";
  const head = pass ? "A day-trading edge passed the full research gate" : weak ? "Some edge, but it fails the research gate" : "No robust day-trading edge found yet";
  return `<section class="panel verdict verdict--${tone}" data-motion-enter="ss:verdict">
    <h2>${escapeHtml(head)}</h2>
    <p>Walk-forward (${escapeHtml(PRE_DECLARED)}, chosen before the run): out-of-sample Sharpe <strong>${escapeHtml(w.oos_sharpe ?? "—")}</strong>,
      return <strong>${escapeHtml(fmtPct(w.oos_ret, 1))}</strong>, green weeks ${escapeHtml(w.oos_pos_weeks != null ? `${Math.round(w.oos_pos_weeks * 100)}%` : "—")};
      final holdout Sharpe <strong>${escapeHtml(w.holdout_sharpe ?? "—")}</strong>, return <strong>${escapeHtml(fmtPct(w.holdout_ret, 1))}</strong>.</p>
    ${gateList(gate)}
    <p class="small muted">Gate from .goals/day-trade-futures/goal.md: every criterion must be measured and met (${escapeHtml(gate.failed ?? 0)} failed, ${escapeHtml(gate.unknown ?? 0)} not measured).
      ${escapeHtml(Number(data.search?.n_runs || 0).toLocaleString("en-US"))} backtests (${escapeHtml(data.search?.n_configs)} configs × ${escapeHtml(data.search?.n_coins)} coins).</p>
  </section>`;
}

export function renderStrategySearch(data) {
  if (!data) return '<p class="muted">Loading…</p>';
  if (!data.available) return progress(data.status);
  const coins = (data.coins || []).slice(0, 20).map((c) => ({
    label: `${WATCH.has(c.coin) ? "★ " : ""}${c.coin.replace(/USDT$/, "")}`, value: c.pct_cfg_persist ?? 0,
    note: `OOS Sharpe ${c.median_oos_sharpe_is_top50}`,
  }));
  const fams = data.families || [];
  const famNames = [...new Set(fams.map((f) => f.family))];
  const tfs = [...new Set(fams.map((f) => f.tf))].sort((a, b) => a - b);
  const famCell = (f, tf) => {
    const row = fams.find((x) => x.family === f && x.tf === tf);
    return row ? { value: row.pct_oos_net_pos - 0.5, text: `${Math.round(row.pct_oos_net_pos * 100)}%`, title: `${row.runs} runs · gross>0 ${Math.round(row.pct_oos_gross_pos * 100)}%` } : null;
  };
  const deep = data.deep || {};
  const grid = deep.leverage_grid || [];
  const risks = [...new Set(grid.map((g) => g.risk))];
  const levs = [...new Set(grid.map((g) => g.lev))];
  const levCell = (r, l) => {
    const g = grid.find((x) => x.risk === r && x.lev === l);
    if (!g || !g.n) return null;
    return { value: g.cagr, text: `${fmtPct(g.cagr, 0)} · DD ${Math.round(g.maxdd * 100)}%`, bad: g.liquidations > 0 || g.maxdd > 0.2,
      title: `liquidations ${g.liquidations} · worst day ${fmtPct(g.worst_day, 1)} · Sharpe ${g.sharpe}` };
  };
  const scen = Object.entries(deep.scenarios || {}).filter(([, v]) => v?.n).map(([k, v]) => ({ label: k.replaceAll("_", " "), value: v.cagr, note: `DD ${Math.round(v.maxdd * 100)}%` }));
  const mc = (deep.monte_carlo || []).filter(Boolean);
  const wfo = Object.entries(data.wfo || {}).map(([k, v]) => ({ label: `${k}${k === PRE_DECLARED ? " ★" : ""}`, value: v.oos_sharpe ?? 0, note: `holdout ${v.holdout_sharpe ?? "—"}` }));
  const best = deep.best_within_20pct_dd;
  return `
    ${verdict(data)}
    <div class="lab-grid">
      <section class="panel"><div class="section-heading"><h2>Easiest coins to day-trade</h2><span class="muted small">share of setups profitable in-sample AND out-of-sample · ★ = your watchlist</span></div>
        ${barList(coins, { signed: false, format: (v) => `${Math.round(v * 100)}%`, motionKey: "ss:coins" })}</section>
      <section class="panel"><div class="section-heading"><h2>Walk-forward variants</h2><span class="muted small">out-of-sample Sharpe · ★ pre-declared</span></div>
        ${barList(wfo, { format: (v) => Number(v).toFixed(2), motionKey: "ss:wfo" })}</section>
    </div>
    <section class="panel"><div class="section-heading"><h2>Strategy × timeframe</h2><span class="muted small">share of setups profitable out-of-sample after costs (green above 50%)</span></div>
      ${heatGrid(famNames, tfs, famCell, { colLabel: (tf) => (tf ? `${tf}m` : "daily"), corner: "family" })}</section>
    ${grid.length ? `<section class="panel"><div class="section-heading"><h2>Leverage × risk per trade</h2>
        <span class="muted small">CAGR · max drawdown (compounding, isolated margin). Grey = liquidations or DD over 20%.</span></div>
      ${heatGrid(risks, levs, levCell, { rowLabel: (r) => `${(r * 100).toFixed(2)}% risk`, colLabel: (l) => `${l}x`, corner: "risk / leverage" })}
      <p class="small">${best ? `Best without liquidations and with DD ≤ 20%: <strong>${escapeHtml((best.risk * 100).toFixed(2))}% risk per trade at ${escapeHtml(best.lev)}x</strong> → CAGR ${escapeHtml(fmtPct(best.cagr, 1))}, max DD ${escapeHtml(fmtPct(-best.maxdd, 1))}.` : "No risk/leverage combination stayed within 20% drawdown without liquidations."}</p></section>` : ""}
    <div class="lab-grid">
      ${scen.length ? `<section class="panel"><div class="section-heading"><h2>Stress scenarios</h2><span class="muted small">CAGR under each scenario</span></div>
        ${barList(scen, { format: (v) => fmtPct(v, 1), motionKey: "ss:scenarios" })}</section>` : ""}
      ${mc.length ? `<section class="panel"><div class="section-heading"><h2>Odds over the next…</h2><span class="muted small">Monte Carlo of daily returns · bar = 5th–95th percentile</span></div>
        <table class="data-table mc-table"><thead><tr><th scope="col">Horizon</th><th scope="col">Chance of profit</th><th scope="col">Range</th><th scope="col">Median</th></tr></thead><tbody>
        ${mc.map((m) => `<tr><td>${escapeHtml(m.horizon_days)} days</td><td><strong>${escapeHtml(Math.round(m.p_profit * 100))}%</strong></td><td>${rangeBar(m)}</td><td>${escapeHtml(fmtPct(m.median, 1))}</td></tr>`).join("")}
        </tbody></table></section>` : ""}
    </div>
    ${(deep.contribution || []).length ? `<section class="panel"><div class="section-heading"><h2>Where the walk-forward P&L came from</h2><span class="muted small">coin · strategy family</span></div>
      ${barList(deep.contribution.map((c) => ({ label: `${c.coin.replace(/USDT$/, "")} · ${c.family}`, value: c["net_at_0.5pct_risk"], note: `${c.trades} trades` })), { format: (v) => fmtNum(v, 3) })}</section>` : ""}
    ${(data.gate_crosscheck || []).length ? `<section class="panel"><div class="section-heading"><h2>Binance vs Gate prices (last ~30 days of 5m)</h2><span class="muted small">is Binance a fair proxy?</span></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th scope="col">Coin</th><th scope="col">5m return correlation</th><th scope="col">Median gap (bps)</th><th scope="col">Gate daily volume ($M)</th></tr></thead><tbody>
      ${data.gate_crosscheck.map((g) => `<tr><td>${escapeHtml(g.coin)}</td><td>${escapeHtml(g.ret_corr_5m ?? "—")}</td><td>${escapeHtml(g.median_abs_basis_bps ?? "—")}</td><td>${escapeHtml(g.gate_daily_usd_m ?? "—")}</td></tr>`).join("")}
      </tbody></table></div></section>` : ""}
    <p class="muted small">Caveats: coins picked by today's volume (survivorship); Binance prices stand in for Gate; maker results assume fills;
      many configurations were tried, so only the walk-forward rows measure the search honestly. PAPER research only.</p>`;
}

export function render(root) {
  root.innerHTML = `<div class="view view--strategy-search">
    <header class="page-header"><div><h1>Strategy Search</h1><p>Day-trade futures research · 30 coins × thousands of setups · walk-forward</p></div>${autoRefreshBar(60000)}</header>
    <div data-search>${skeleton(6)}</div></div>`;
  const view = root.firstElementChild;
  return startAutoRefresh(view, async () => {
    view.querySelector("[data-search]").innerHTML = renderStrategySearch(await paperApi.strategySearch());
    applyMotion(view);
  }, { intervalMs: 60000 });
}
