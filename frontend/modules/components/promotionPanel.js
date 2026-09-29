// Experiment identity + promotion gate. Concise decision first; evidence stays in the export.
// A PASS only makes a separate live-execution review eligible; nothing here enables live trading.
import { escapeHtml, relativeTime } from "../format.js";
import { fmtNumber, pct } from "./ui.js";

const STATUS_TONE = { PASS: "ok", CONTINUE_COLLECTING_DATA: "idle", FAIL_STRATEGY: "bad", FAIL_SAFETY: "bad", FAIL_RELIABILITY: "bad", INVALID_EXPERIMENT: "bad" };
const words = (value) => String(value ?? "").replaceAll("_", " ");
const money = (value) => (value == null ? "unavailable" : `${fmtNumber(value, { digits: 2 })} USDT`);

export function renderPromotion({ manifest = null, review = null, reviews = [] } = {}) {
  const status = manifest?.status;
  const report = review?.report;
  const gate = review?.gate;
  const economics = report?.economics || {};
  const identity = report?.identity || {};
  const drift = status?.drift || [];
  return `
    <div class="promotion" data-promotion>
      <p class="small"><strong>Live trading: DISABLED</strong> · a PASS only makes a separate, human-reviewed live-execution phase eligible for review.</p>
      <dl class="kv">
        <div><dt>Experiment</dt><dd>${escapeHtml(identity.experiment_id || "—")} · manifest v${escapeHtml(status?.version ?? "—")}</dd></div>
        <div><dt>Frozen config</dt><dd><code>${escapeHtml((status?.material_sha256 || "not frozen").slice(0, 12))}</code>${status?.frozen_at ? ` · ${escapeHtml(relativeTime(status.frozen_at))}` : ""}</dd></div>
        <div><dt>Elapsed</dt><dd>${escapeHtml(fmtNumber(identity.elapsed_days ?? 0, { digits: 1 }))} days · ${escapeHtml(words(identity.checkpoint || "—"))}</dd></div>
        <div><dt>Completed trades</dt><dd>${escapeHtml(economics.completed_trades ?? 0)} <small class="muted">(perp ${escapeHtml(economics.perp_trades ?? 0)} · spot ${escapeHtml(economics.spot_round_trips ?? 0)})</small></dd></div>
        <div><dt>Net economic PnL</dt><dd>${escapeHtml(money(economics.net_economic_pnl_usdt))} <small class="muted">after AI cost</small></dd></div>
        <div><dt>Profit factor / max DD</dt><dd>${escapeHtml(economics.profit_factor == null ? "—" : fmtNumber(economics.profit_factor, { digits: 2 }))} / ${escapeHtml(economics.max_drawdown == null ? "—" : pct(economics.max_drawdown))}</dd></div>
      </dl>
      ${drift.length ? `
        <div class="paper-feedback paper-feedback--error" role="alert">Configuration changed since manifest v${escapeHtml(status.version)}: ${escapeHtml(drift.slice(0, 6).join(", "))}. Reviews are INVALID until you record a new version.</div>
        <form class="manifest-form" data-manifest-form>
          <label>Reason <input name="reason" type="text" required minlength="4" maxlength="200" placeholder="why the treatment changed" /></label>
          <button type="submit" class="btn btn--ghost btn--small">Record new version</button>
        </form>` : ""}
      ${gate ? `
        <div class="gate-result">
          <p><span class="status-pill status-pill--${STATUS_TONE[gate.status] || "idle"}"><small>Gate</small> ${escapeHtml(words(gate.status))}</span></p>
          ${gate.blockers.length ? `<p class="small">Blockers: ${gate.blockers.map((b) => `<span class="cat-badge">${escapeHtml(b)}</span>`).join(" ")}</p>` : ""}
          <ul class="small">${gate.reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>
          <p class="small muted">Next: ${escapeHtml(gate.next_step)}</p>
        </div>` : '<p class="muted small">No checkpoint review yet.</p>'}
      <div class="button-row"><button type="button" class="btn btn--primary btn--small" data-promotion-review>Run checkpoint review</button></div>
      ${reviews.length ? `<ul class="small review-history">${reviews.slice(0, 5).map((item) => `
        <li><span class="cat-badge">${escapeHtml(words(item.status))}</span> ${escapeHtml(words(item.checkpoint))} · v${escapeHtml(item.manifest_version ?? "—")} · <code>${escapeHtml(item.report_sha256.slice(0, 10))}</code> <span class="muted">${escapeHtml(relativeTime(item.created_at))}</span></li>`).join("")}</ul>` : ""}
    </div>`;
}
