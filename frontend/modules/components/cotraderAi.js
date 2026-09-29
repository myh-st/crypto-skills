// Co-Trader AI pieces (pure renderers): the Luna "AI says" card, Jev's structured daily scores (card
// chip row, detail panel with a score-history sparkline), the analyses history labelled by source, and
// the AI Scorecard (forward returns after each stance, with n on every bucket). AI output is advisory
// text only: it never changes the rule's action, sizing or any position.
import { escapeHtml, relativeTime } from "../format.js";
import { num } from "./ui.js";
import { fmtPct, fmtPrice, isoDate, stanceChip } from "./cotraderBits.js";

export const MIN_SAMPLES = 20;
export const PRICING_FALLBACK_NOTE = "AI prices are placeholders, set real prices in Settings › Cost & Budgets.";
export const ASK_AI_LABEL = "Ask AI (≈$0.01–0.03)";

const TIMING = {
  good_now: "Entry: good now",
  wait_pullback: "Entry: wait for pullback",
  extended_late: "Entry: extended / late",
  not_applicable: "Entry: n/a",
};
const KEY_RISK = {
  overextended: "overextended",
  weakening_momentum: "weakening momentum",
  high_volatility: "high volatility",
  market_risk_off: "market risk-off",
  none: "none",
};
const AGREEMENT = {
  agree: { label: "Jev agrees", glyph: "✓", tone: "agree" },
  caution: { label: "Jev: caution", glyph: "!", tone: "caution" },
  disagree: { label: "Jev disagrees", glyph: "✕", tone: "disagree" },
};

export function sourceOf(analysis) {
  return String(analysis?.source || "luna").toLowerCase() === "jev" ? "jev" : "luna";
}

export function sourceChip(source) {
  const jev = source === "jev";
  return `<span class="cot-source cot-source--${jev ? "jev" : "luna"}">${jev ? "Jev" : "Luna"}</span>`;
}

function asTime(value) {
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : -Infinity;
}

/** Newest analysis from `source` ("luna" includes entries without a source). */
export function latestAnalysis(analyses, source = "luna") {
  const list = (analyses || []).filter((a) => sourceOf(a) === source);
  if (!list.length) return null;
  return list.reduce((best, a) => (asTime(a.as_of) > asTime(best.as_of) ? a : best));
}

// ---------------------------------------------------------------- Luna card

function bulletList(items, className = "") {
  const list = (items || []).filter((item) => item !== null && item !== undefined && item !== "");
  if (!list.length) return '<p class="muted small">—</p>';
  return `<ul class="cot-bullets${className ? ` ${className}` : ""}">${list.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function keyLevelsHtml(levels) {
  if (!levels) return '<p class="muted small">No key levels.</p>';
  const join = (values) => ((values || []).map(num).filter((v) => v !== null).map(fmtPrice).join(" · ") || "—");
  const inv = num(levels.invalidation);
  return `<dl class="kv kv--compact cot-levels">
    <div><dt>Support</dt><dd>${escapeHtml(join(levels.support))}</dd></div>
    <div><dt>Resistance</dt><dd>${escapeHtml(join(levels.resistance))}</dd></div>
    <div><dt>Invalidation</dt><dd>${escapeHtml(inv === null ? "—" : fmtPrice(inv))}</dd></div>
  </dl>`;
}

/**
 * "AI says" (Luna). `analysis` is the newest Luna analysis or null; `lastAi` (the coin's summary) is
 * shown when no full analysis is available; `blockedReason` shows "AI unavailable: <reason>".
 */
export function renderAiCard(analysis, { blockedReason = null, lastAi = null, base = "", pricingFallback = false } = {}) {
  const shown = analysis || null;
  const head = shown ? stanceChip(shown.stance, shown.conviction) : lastAi ? stanceChip(lastAi.stance, lastAi.conviction) : stanceChip(null);
  const meta = shown
    ? [shown.trigger && `trigger: ${shown.trigger}`, shown.as_of && relativeTime(shown.as_of), shown.model, num(shown.cost_usd) !== null && `$${num(shown.cost_usd).toFixed(4)}${pricingFallback ? "*" : ""}`].filter(Boolean).join(" · ")
    : "";
  const blocked = blockedReason ? `<p class="cot-ai-blocked" role="status"><span aria-hidden="true">⚠</span> AI unavailable: ${escapeHtml(blockedReason)}</p>` : "";
  let body;
  if (shown) {
    body = `
      ${shown.summary_th ? `<p class="cot-thai" lang="th">${escapeHtml(shown.summary_th)}</p>` : ""}
      <div class="cot-bullbear">
        <div class="cot-bull"><h4><span aria-hidden="true">▲</span> Bull case</h4>${bulletList(shown.bull_points)}</div>
        <div class="cot-bear"><h4><span aria-hidden="true">▼</span> Bear case</h4>${bulletList(shown.bear_points)}</div>
      </div>
      <h4>Key levels</h4>${keyLevelsHtml(shown.key_levels)}
      <h4>Risks</h4>${bulletList(shown.risks)}
      ${shown.change_my_mind ? `<p class="cot-cmm"><strong>What would change its mind:</strong> ${escapeHtml(shown.change_my_mind)}</p>` : ""}`;
  } else if (lastAi) {
    body = `${lastAi.summary_th ? `<p class="cot-thai" lang="th">${escapeHtml(lastAi.summary_th)}</p>` : ""}
      <p class="muted small">Summary from ${escapeHtml(lastAi.trigger || "a review")} ${escapeHtml(relativeTime(lastAi.as_of))}.</p>`;
  } else {
    body = `<p class="muted">No AI analysis for ${escapeHtml(base || "this coin")} yet. Luna reviews a coin automatically when the rule changes state; you can also ask now.</p>`;
  }
  return `<div class="cot-ai" data-cot-ai>
    <div class="cot-sub-head"><h3>AI says <small class="muted">(Luna)</small></h3>${head}</div>
    ${meta ? `<p class="muted small">${escapeHtml(meta)}</p>` : ""}
    ${blocked}
    ${body}
    <div class="paper-inline-actions cot-ask">
      <button type="button" class="btn btn--primary btn--small" data-cot-ask>${escapeHtml(ASK_AI_LABEL)}</button>
      <span class="small muted" data-cot-ask-feedback role="status" aria-live="polite"></span>
    </div>
    <p class="muted small">Advisory text only — the AI never changes the rule's action or sizing.${pricingFallback ? ` * ${escapeHtml(PRICING_FALLBACK_NOTE)}` : ""}</p>
  </div>`;
}

// ---------------------------------------------------------------- Jev

export function jevScore(entry, key) {
  const value = num(entry?.answers?.[key] ?? entry?.[key]);
  if (value === null) return null;
  return Math.max(0, Math.min(1, value));
}

export function miniBar(label, value, tone) {
  if (value === null) return `<span class="cot-mini cot-mini--none"><span class="cot-mini-label">${escapeHtml(label)}</span> <span class="cot-mini-val">—</span></span>`;
  const pctValue = Math.round(value * 100);
  return `<span class="cot-mini cot-mini--${tone}" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pctValue}" aria-label="${escapeHtml(label)} ${pctValue} of 100">
    <span class="cot-mini-label" aria-hidden="true">${escapeHtml(label)}</span>
    <span class="cot-mini-track" aria-hidden="true"><span class="cot-mini-fill" style="width:${pctValue}%"></span></span>
    <span class="cot-mini-val" aria-hidden="true">${escapeHtml(value.toFixed(2))}</span></span>`;
}

export function agreementBadge(value) {
  const a = AGREEMENT[value];
  if (!a) return '<span class="cot-stance cot-stance--none">Jev: —</span>';
  return `<span class="cot-stance cot-stance--${a.tone}"><span aria-hidden="true">${a.glyph}</span> ${escapeHtml(a.label)}</span>`;
}

export function timingTag(value) {
  if (!value) return "";
  return `<span class="cot-timing cot-timing--${escapeHtml(String(value).replace(/[^a-z_]/gi, ""))}">${escapeHtml(TIMING[value] || `Entry: ${value}`)}</span>`;
}

/** Compact Jev chip row for a coin card; empty string when `jev` is null. */
export function renderJevRow(jev) {
  if (!jev) return "";
  return `<div class="cot-jev" aria-label="Jev daily scores">
    <span class="cot-source cot-source--jev">Jev</span>
    ${miniBar("Trend", jevScore(jev, "trend_strength"), "trend")}
    ${miniBar("Reversal", jevScore(jev, "reversal_risk"), "risk")}
    ${agreementBadge(jev.rule_agreement)}
    ${timingTag(jev.entry_timing)}
  </div>`;
}

/** Jev score history (oldest first) from the detail's analyses. */
export function jevHistory(analyses) {
  return (analyses || [])
    .filter((a) => sourceOf(a) === "jev")
    .map((a) => ({ at: a.as_of, t: asTime(a.as_of), trend: jevScore(a, "trend_strength"), risk: jevScore(a, "reversal_risk") }))
    .filter((p) => Number.isFinite(p.t))
    .sort((a, b) => a.t - b.t);
}

function historySpark(history) {
  if (history.length < 2) return '<p class="muted small">The score history appears after two daily scores.</p>';
  const width = 300;
  const height = 70;
  const pad = 4;
  const x = (i) => pad + (i / (history.length - 1)) * (width - pad * 2);
  const y = (v) => pad + (1 - v) * (height - pad * 2);
  const path = (key) => {
    let d = "";
    let pen = false;
    history.forEach((p, i) => {
      const v = p[key];
      if (v === null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
      pen = true;
    });
    return d.trim();
  };
  const last = history[history.length - 1];
  const aria = `Jev score history, ${history.length} days: trend strength now ${last.trend === null ? "unknown" : last.trend.toFixed(2)}, reversal risk now ${last.risk === null ? "unknown" : last.risk.toFixed(2)}`;
  return `<figure class="cot-jev-history">
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(aria)}">
      <line class="cot-jev-mid" x1="${pad}" x2="${width - pad}" y1="${y(0.5).toFixed(1)}" y2="${y(0.5).toFixed(1)}" vector-effect="non-scaling-stroke" />
      <path class="cot-jev-line cot-jev-line--trend" d="${path("trend")}" vector-effect="non-scaling-stroke" />
      <path class="cot-jev-line cot-jev-line--risk" d="${path("risk")}" vector-effect="non-scaling-stroke" />
    </svg>
    <figcaption class="small muted"><span class="cot-legend cot-legend--trend">── trend strength</span> <span class="cot-legend cot-legend--risk">- - reversal risk</span> · 0..1 · ${escapeHtml(isoDate(history[0].at))} → ${escapeHtml(isoDate(last.at))}</figcaption>
  </figure>`;
}

/** Detail "Jev scores" panel: the latest structured answers and the score history. */
export function renderJevPanel(jev, analyses = []) {
  const history = jevHistory(analyses);
  if (!jev) {
    return `<div class="cot-jev-panel"><div class="cot-sub-head"><h3>Jev scores</h3>${sourceChip("jev")}</div>
      <p class="muted">No Jev score yet. Jev scores every coin once a day at about 00:05 UTC, after the daily close.</p>
      ${history.length ? historySpark(history) : ""}</div>`;
  }
  const regime = jev.trend_regime ? String(jev.trend_regime) : "—";
  const risk = jev.key_risk ? KEY_RISK[jev.key_risk] || String(jev.key_risk) : "—";
  return `<div class="cot-jev-panel">
    <div class="cot-sub-head"><h3>Jev scores</h3>${sourceChip("jev")}</div>
    <p class="muted small">${escapeHtml(jev.as_of ? `scored ${relativeTime(jev.as_of)}` : "")}</p>
    <div class="cot-jev cot-jev--large">
      ${miniBar("Trend strength", jevScore(jev, "trend_strength"), "trend")}
      ${miniBar("Reversal risk", jevScore(jev, "reversal_risk"), "risk")}
    </div>
    <div class="cot-chiprow">${agreementBadge(jev.rule_agreement)} ${timingTag(jev.entry_timing)}</div>
    <dl class="kv kv--compact">
      <div><dt>Trend regime</dt><dd>${escapeHtml(regime)}</dd></div>
      <div><dt>Key risk</dt><dd>${escapeHtml(risk)}</dd></div>
    </dl>
    ${historySpark(history)}
  </div>`;
}

/** Analyses history, newest first, each labelled by source (Jev / Luna). */
export function renderAnalysesList(analyses, { limit = 12 } = {}) {
  const list = [...(analyses || [])].sort((a, b) => asTime(b.as_of) - asTime(a.as_of)).slice(0, limit);
  if (!list.length) return '<p class="muted small">No AI analyses recorded yet.</p>';
  return `<ul class="cot-analyses">${list.map((a) => {
    const source = sourceOf(a);
    const stance = source === "jev" ? agreementBadge(a.answers?.rule_agreement ?? a.rule_agreement) : stanceChip(a.stance, a.conviction);
    const cost = num(a.cost_usd);
    const text = source === "jev"
      ? [a.answers?.trend_regime && `regime ${a.answers.trend_regime}`, jevScore(a, "trend_strength") !== null && `trend ${jevScore(a, "trend_strength").toFixed(2)}`, jevScore(a, "reversal_risk") !== null && `reversal ${jevScore(a, "reversal_risk").toFixed(2)}`].filter(Boolean).join(" · ")
      : a.summary_th || "";
    return `<li class="cot-analysis">
      <div class="cot-analysis-head">${sourceChip(source)} ${stance}
        <span class="muted small">${escapeHtml(isoDate(a.as_of))}${a.trigger ? ` · ${escapeHtml(a.trigger)}` : ""}${cost === null ? "" : ` · $${escapeHtml(cost.toFixed(4))}`}</span></div>
      ${text ? `<p class="small"${source === "luna" ? ' lang="th"' : ""}>${escapeHtml(text)}</p>` : ""}
    </li>`;
  }).join("")}</ul>`;
}

// ---------------------------------------------------------------- scorecard

function enough(n) {
  const value = num(n);
  return value !== null && value >= MIN_SAMPLES;
}

function bucketCells(n, avg, hit) {
  const count = num(n);
  const nCell = `<td class="num">${escapeHtml(count === null ? "0" : String(count))}</td>`;
  if (!enough(count)) return `${nCell}<td colspan="2" class="cot-few">too few samples</td>`;
  const a = num(avg);
  const tone = a === null ? "none" : a > 0 ? "pos" : a < 0 ? "neg" : "flat";
  const arrow = a === null ? "" : a > 0 ? "▲ " : a < 0 ? "▼ " : "• ";
  return `${nCell}<td class="num"><span class="pnl pnl--${tone}"><span aria-hidden="true">${arrow}</span>${escapeHtml(fmtPct(a, 1))}</span></td>
    <td class="num">${escapeHtml(num(hit) === null ? "—" : `${Math.round(num(hit) * 100)}%`)}</td>`;
}

/**
 * The AI's value test for one source and horizon: avg(agree) − avg(disagree), or null (with the
 * reason) when either bucket has fewer than MIN_SAMPLES.
 */
export function agreeMinusDisagree(rows, source, horizon = 30) {
  const pick = (stance) => (rows || []).find((r) => r.source === source && r.stance === stance);
  const agree = pick("agree");
  const disagree = pick("disagree");
  const nA = num(agree?.[`n_${horizon}d`]) ?? 0;
  const nD = num(disagree?.[`n_${horizon}d`]) ?? 0;
  if (nA < MIN_SAMPLES || nD < MIN_SAMPLES) return { value: null, text: `too few samples for a verdict (agree n=${nA}, disagree n=${nD})` };
  const a = num(agree[`avg_ret_${horizon}d`]);
  const d = num(disagree[`avg_ret_${horizon}d`]);
  if (a === null || d === null) return { value: null, text: `forward returns unavailable (${horizon}d)` };
  const value = a - d;
  return { value, text: `agree − disagree (${horizon}d): ${fmtPct(value, 1)}` };
}

export function renderScorecard(card, { pricingFallback = false } = {}) {
  if (!card) return '<p class="muted">Scorecard unavailable.</p>';
  const rows = [...(card.rows || [])].sort((a, b) => String(a.source).localeCompare(String(b.source)) || String(a.stance).localeCompare(String(b.stance)));
  const fallback = Boolean(card.note_pricing_fallback || card.pricing_is_fallback || pricingFallback);
  const cost = card.cost || {};
  const calls = cost.calls || {};
  const usd = (v) => (num(v) === null ? "—" : `$${num(v).toFixed(4)}${fallback ? "*" : ""}`);
  const sources = [...new Set(rows.map((r) => r.source))];
  const table = rows.length
    ? `<div class="table-scroll"><table class="data-table cot-scorecard-table">
        <thead>
          <tr><th scope="col" rowspan="2">Source</th><th scope="col" rowspan="2">Stance</th><th scope="colgroup" colspan="3">+7 days</th><th scope="colgroup" colspan="3">+30 days</th></tr>
          <tr><th scope="col">n</th><th scope="col">avg return</th><th scope="col">hit rate</th><th scope="col">n</th><th scope="col">avg return</th><th scope="col">hit rate</th></tr>
        </thead>
        <tbody>${rows.map((r) => `<tr>
          <td>${sourceChip(String(r.source).toLowerCase() === "jev" ? "jev" : "luna")}</td>
          <td>${escapeHtml(r.stance)}</td>
          ${bucketCells(r.n_7d, r.avg_ret_7d, r.hit_7d)}
          ${bucketCells(r.n_30d, r.avg_ret_30d, r.hit_30d)}
        </tr>`).join("")}</tbody>
      </table></div>`
    : '<p class="muted">No scored stances yet. Forward returns fill in 7 and 30 days after each stance.</p>';
  const verdicts = sources.map((source) => {
    const v = agreeMinusDisagree(rows, source, 30);
    return `<li>${sourceChip(String(source).toLowerCase() === "jev" ? "jev" : "luna")} ${escapeHtml(v.text)}</li>`;
  }).join("");
  return `
    <p class="muted small">Forward returns after each recorded stance. The AI adds value only if "caution" and "disagree" are followed by worse returns than "agree". Buckets with n &lt; ${MIN_SAMPLES} show no verdict.</p>
    ${table}
    ${verdicts ? `<ul class="cot-verdicts small">${verdicts}</ul>` : ""}
    <p class="small cot-cost">Cost · ${sourceChip("jev")} ${escapeHtml(usd(cost.jev_usd))} · ${escapeHtml(num(calls.jev) ?? 0)} calls
      · ${sourceChip("luna")} ${escapeHtml(usd(cost.luna_usd))} · ${escapeHtml(num(calls.luna) ?? 0)} calls</p>
    ${fallback ? `<p class="cot-note small">* ${escapeHtml(PRICING_FALLBACK_NOTE)}</p>` : ""}`;
}
