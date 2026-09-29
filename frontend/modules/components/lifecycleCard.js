// Spot lifecycle: state, regime, Core/Tactical split, pending proposal, and aligned benchmark
// results. The server owns every decision; the browser only renders and relays Apply/Dismiss.
import { escapeHtml, relativeTime } from "../format.js";
import { fmtNumber, pct, price } from "./ui.js";

export const LIFECYCLE_LABELS = {
  ACCUMULATE: "Accumulate",
  HOLD_CORE: "Hold core",
  ADD_ON_PULLBACK: "Add on pullback",
  TREND_EXPANSION: "Trend expansion",
  PROTECT_PROFIT: "Protect profit",
  DISTRIBUTE: "Distribute",
  REDUCE: "Reduce",
  EXIT: "Exit",
  CASH_WAIT: "Cash wait",
};
const STATE_TONE = { TREND_EXPANSION: "ok", HOLD_CORE: "ok", ACCUMULATE: "idle", ADD_ON_PULLBACK: "ok",
  PROTECT_PROFIT: "warn", DISTRIBUTE: "warn", REDUCE: "bad", EXIT: "bad", CASH_WAIT: "idle" };
const ARM_LABELS = {
  buy_hold: "Buy & Hold", tp_ladder: "Fixed TP ladder", rebalance: "Threshold rebalance", grid: "Simple grid",
  trailing_stop: "Trailing stop", ai_lifecycle: "Lifecycle manager", ai_lifecycle_brain: "Lifecycle + Portfolio Brain",
};

const words = (value) => String(value || "").replaceAll("_", " ").toLowerCase();

export function renderCoreSplit(position) {
  const quantity = Number(position.quantity) || 0;
  const core = Math.min(quantity, Number(position.core_quantity) || 0);
  const share = quantity > 0 ? core / quantity : 0;
  return `
    <div class="core-split" role="img" aria-label="Core ${Math.round(share * 100)}%, Tactical ${Math.round((1 - share) * 100)}%">
      <span class="core-split__core" style="width:${(share * 100).toFixed(1)}%"></span>
    </div>
    <p class="small muted">Core ${escapeHtml(price(core))} · Tactical ${escapeHtml(price(quantity - core))} ${escapeHtml(position.base || "")}</p>`;
}

export function renderLifecycle(position) {
  const data = position.lifecycle || {};
  const row = data.lifecycle;
  const pending = row?.pending_plan;
  const state = row?.state || position.lifecycle_state;
  const events = (data.events || []).slice(0, 3);
  return `
    <div class="lifecycle" data-lifecycle>
      <div class="health-strip">
        <span class="status-pill status-pill--${STATE_TONE[state] || "idle"}"><small>Lifecycle</small> ${escapeHtml(state ? LIFECYCLE_LABELS[state] : "Not reviewed")}</span>
        ${row?.regime ? `<span class="status-pill status-pill--idle"><small>Regime</small> ${escapeHtml(words(row.regime))}</span>` : ""}
      </div>
      ${renderCoreSplit(position)}
      <p class="small muted">${row?.last_review_at ? `Reviewed ${escapeHtml(relativeTime(row.last_review_at))}` : "No lifecycle review yet"}${row?.next_review_at ? ` · next ${escapeHtml(relativeTime(row.next_review_at))}` : ""}</p>
      ${pending ? `
        <div class="lifecycle-proposal" role="group" aria-label="Pending lifecycle proposal">
          <p><strong>${escapeHtml(words(pending.action))}</strong> · ${escapeHtml(LIFECYCLE_LABELS[pending.state_before] || pending.state_before)} → ${escapeHtml(LIFECYCLE_LABELS[pending.state_after] || pending.state_after)}</p>
          <p class="small">${pending.sell_quantity > 0 ? `Sell ${escapeHtml(price(pending.sell_quantity))} (${escapeHtml(pct(pending.sell_fraction))})${pending.sells_core ? " · includes Core" : " · Tactical only"}` : pending.add_fraction > 0 ? `Add ${escapeHtml(pct(pending.add_fraction))} of Spot equity` : "No size change"}</p>
          <p class="small muted">${escapeHtml((pending.reasons || []).join(", "))}</p>
          <div class="button-row">
            <button type="button" class="btn btn--primary btn--small" data-lifecycle-apply>Apply</button>
            <button type="button" class="btn btn--ghost btn--small" data-lifecycle-dismiss>Dismiss</button>
          </div>
        </div>` : ""}
      <div class="button-row"><button type="button" class="btn btn--ghost btn--small" data-lifecycle-review>Review lifecycle now</button></div>
      ${events.length ? `<ul class="lifecycle-events small">${events.map((event) => `
        <li><span class="cat-badge">${escapeHtml(event.status)}</span> ${escapeHtml(words(event.action))} · ${escapeHtml(words(event.regime))} <span class="muted">${escapeHtml(relativeTime(event.created_at))}</span></li>`).join("")}</ul>` : ""}
    </div>`;
}

const ratio = (value) => (value == null ? "—" : fmtNumber(value, { digits: 2 }));

export function renderBenchmark(report) {
  if (!report) return '<p class="muted small">Run a benchmark on a Spot pair to compare lifecycle management with simple baselines.</p>';
  const arms = report.arms || [];
  return `
    <p class="small muted">${escapeHtml(report.symbol)} · ${escapeHtml(report.interval || "")} · ${escapeHtml(report.evaluated_bars)} bars · ${escapeHtml(report.data_origin)} · fee ${escapeHtml(pct(report.assumptions?.fee_rate))} · slippage ${escapeHtml(report.assumptions?.slippage_bps)} bps</p>
    <div class="table-scroll">
      <table class="data-table benchmark-table">
        <thead><tr><th scope="col">Arm</th><th scope="col">Return</th><th scope="col">Max DD</th><th scope="col">Peak capture</th><th scope="col">Giveback</th><th scope="col">Up / down capture</th><th scope="col">Turnover</th><th scope="col">In cash</th><th scope="col">AI cost</th></tr></thead>
        <tbody>${arms.map((arm) => arm.status === "NOT_APPLICABLE"
          ? `<tr><th scope="row">${escapeHtml(ARM_LABELS[arm.arm] || arm.arm)}</th><td colspan="8" class="muted">Not applicable · ${escapeHtml((arm.notes || []).join(" "))}</td></tr>`
          : `<tr><th scope="row">${escapeHtml(ARM_LABELS[arm.arm] || arm.arm)}</th>
              <td>${escapeHtml(pct(arm.total_return))}</td><td>${escapeHtml(pct(arm.max_drawdown))}</td>
              <td>${escapeHtml(ratio(arm.peak_capture_ratio))}</td><td>${escapeHtml(arm.profit_giveback == null ? "—" : pct(arm.profit_giveback))}</td>
              <td>${escapeHtml(ratio(arm.upside_capture))} / ${escapeHtml(ratio(arm.downside_capture))}</td>
              <td>${escapeHtml(ratio(arm.turnover))}x</td><td>${escapeHtml(pct(arm.time_in_cash))}</td>
              <td>${arm.ai_cost_usdt == null ? '<span class="muted">unavailable</span>' : `${escapeHtml(fmtNumber(arm.ai_cost_usdt, { digits: 2 }))} USDT`}</td></tr>`).join("")}
        </tbody>
      </table>
    </div>
    <p class="small warn-text">${escapeHtml(report.claim)}</p>`;
}
