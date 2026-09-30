// Co-Trader position ladder (Addendum D): OUT / STARTER (½ slot) / FULL (2 slots) with the actions
// BUY_STARTER / ADD / TRIM / SELL_ALL / HOLD, the trigger levels for the next change (add above, trim
// below, exit below), past ladder transitions (B/A/T/S), the CDC Action Zone reference ribbon and the
// per-coin evidence table. Pure renderers and data shapers; the ladder is deterministic and AI never
// changes it. Field names are normalised in `ladderOf` so small backend shape differences degrade to
// "—" instead of breaking the page.
import { escapeHtml } from "../format.js";
import { num } from "./ui.js";
import { fmtCompactUsd, fmtPct, fmtPrice, fmtQty, fmtUsdt, isoDate } from "./cotraderBits.js";

export const LADDER_STATES = {
  OUT: { label: "OUT", size: "", glyph: "○", tone: "out" },
  STARTER: { label: "STARTER", size: "½", glyph: "◐", tone: "starter" },
  FULL: { label: "FULL", size: "2×", glyph: "●", tone: "full" },
};

export const LADDER_ACTIONS = {
  BUY_STARTER: { label: "BUY STARTER", glyph: "▲", tone: "buy", letter: "B" },
  ADD: { label: "ADD", glyph: "▲▲", tone: "add", letter: "A" },
  TRIM: { label: "TRIM", glyph: "▽", tone: "trim", letter: "T" },
  SELL_ALL: { label: "SELL ALL", glyph: "▼", tone: "sell", letter: "S" },
  HOLD: { label: "HOLD", glyph: "■", tone: "hold", letter: "" },
};

export const LADDER_HINT_TH = {
  STARTER: "ถือครึ่งไม้ รอเบรค",
  FULL: "เทรนด์แรง เติมเต็มไม้",
  TRIM: "ลดกลับครึ่งไม้ เทรนด์ยังขึ้น",
  OUT: "ถือเงินสด",
};

export const CDC_COLORS = { green: "#1f7a4d", blue: "#2f6fed", yellow: "#d4a017", red: "#a3352a" };
const CDC_TEXT = { green: "green (bull)", blue: "blue (bull, pulling back)", yellow: "yellow (bear, bouncing)", red: "red (bear)" };

export const EVIDENCE_TITLE = "What would have happened on this coin (past, not a promise)";
const EVIDENCE_ROWS = [["ladder", "Ladder"], ["rule", "Rule (hold/cash)"], ["cdc_1d", "CDC 1D"], ["buy_hold", "Buy & hold"]];

const pick = (...values) => values.find((v) => v !== undefined && v !== null);

/**
 * Normalise the coin's ladder block. Accepts `coin.ladder = {state, action: "ADD" | {type, ...},
 * target_weight, delta_usdt, delta_qty, add_above, trim_below, exit_below}` with the numbers either on
 * the ladder, on the action object, or under `levels`/`triggers`. Returns null when there is no ladder.
 */
export function ladderOf(coin) {
  const l = coin?.ladder;
  if (!l || typeof l !== "object") return null;
  const a = l.action && typeof l.action === "object" ? l.action : {};
  const levels = l.levels || l.triggers || a.levels || a.triggers || {};
  const state = String(pick(l.state, a.state, "") || "").toUpperCase();
  const action = String(pick(typeof l.action === "string" ? l.action : null, a.type, a.action, "HOLD") || "HOLD").toUpperCase();
  return {
    state: LADDER_STATES[state] ? state : null,
    action: LADDER_ACTIONS[action] ? action : "HOLD",
    fresh: Boolean(pick(a.fresh, l.fresh, false)),
    text: pick(a.text, l.text, null),
    targetWeight: num(pick(a.target_weight, l.target_weight)),
    deltaUsdt: num(pick(a.delta_usdt, l.delta_usdt)),
    deltaQty: num(pick(a.delta_qty, l.delta_qty)),
    addAbove: num(pick(levels.add_above, a.add_above, l.add_above)),
    addVolumeMin: num(pick(levels.add_volume_min_usdt, a.add_volume_min_usdt, l.add_volume_min_usdt)),
    trimBelow: num(pick(levels.trim_below, a.trim_below, l.trim_below)),
    exitBelow: num(pick(levels.exit_below, a.exit_below, l.exit_below, coin?.trend_line_next)),
  };
}

export function ladderHint(ladder) {
  if (!ladder) return "";
  if (ladder.action === "TRIM") return LADDER_HINT_TH.TRIM;
  return LADDER_HINT_TH[ladder.state] || "";
}

export function ladderBadge(state) {
  const s = LADDER_STATES[state];
  if (!s) return '<span class="cot-ladder-state cot-ladder-state--none">Ladder —</span>';
  return `<span class="cot-ladder-state cot-ladder-state--${s.tone}"><span aria-hidden="true">${s.glyph}</span> ${escapeHtml(s.label)}${s.size ? ` (${escapeHtml(s.size)})` : ""}</span>`;
}

export function ladderActionBadge(action) {
  const a = LADDER_ACTIONS[action] || LADDER_ACTIONS.HOLD;
  return `<span class="cot-ladder-action cot-ladder-action--${a.tone}"><span aria-hidden="true">${a.glyph}</span> ${escapeHtml(a.label)}</span>`;
}

/** "Buy 120.00 USDT ≈ 0.05 NEAR" / "Sell …" / "no change"; "—" when the size is unknown. */
export function ladderDeltaText(ladder, base = "") {
  const delta = ladder?.deltaUsdt;
  if (delta === null || delta === undefined) return "size — set your spot capital";
  if (Math.abs(delta) < 0.005) return "no change";
  const qty = ladder.deltaQty === null ? "" : ` ≈ ${fmtQty(Math.abs(ladder.deltaQty))} ${base}`;
  return `${delta > 0 ? "Buy" : "Sell"} ${fmtUsdt(Math.abs(delta))}${qty}`;
}

/** Compact ladder row for a coin card; empty when the coin has no ladder block. */
export function renderLadderRow(coin) {
  const ladder = ladderOf(coin);
  if (!ladder) return "";
  const hint = ladderHint(ladder);
  return `<div class="cot-ladder" aria-label="Position ladder">
    ${ladderBadge(ladder.state)} ${ladderActionBadge(ladder.action)}
    <span class="cot-ladder-delta small">${escapeHtml(ladderDeltaText(ladder, coin.base))}</span>
    ${hint ? `<span class="cot-ladder-hint small" lang="th">${escapeHtml(hint)}</span>` : ""}
  </div>`;
}

function levelRow(label, value, livePrice, dir) {
  if (value === null) return `<div><dt>${escapeHtml(label)}</dt><dd>—</dd></div>`;
  const p = num(livePrice);
  const move = p ? value / p - 1 : null;
  return `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(fmtPrice(value))}${move === null ? "" : ` <span class="muted">(${escapeHtml(fmtPct(move))} ${dir})</span>`}</dd></div>`;
}

/** The ladder part of the decision card: state, action, size and the levels for the next change. */
export function renderLadderDetail(coin) {
  const ladder = ladderOf(coin);
  if (!ladder) return "";
  const hint = ladderHint(ladder);
  const weight = ladder.targetWeight;
  return `<div class="cot-ladder-detail">
    <div class="cot-ladder">${ladderBadge(ladder.state)} ${ladderActionBadge(ladder.action)}
      ${hint ? `<span class="cot-ladder-hint" lang="th">${escapeHtml(hint)}</span>` : ""}</div>
    ${ladder.text ? `<p class="small">${escapeHtml(ladder.text)}</p>` : ""}
    <dl class="kv kv--compact">
      <div><dt>Target</dt><dd>${escapeHtml(weight === null ? "—" : `${weight} slot${weight === 1 ? "" : "s"}`)}</dd></div>
      <div><dt>Ladder change</dt><dd>${escapeHtml(ladderDeltaText(ladder, coin.base))}</dd></div>
      ${levelRow("Add above", ladder.addAbove, coin.price, "away")}
      ${ladder.addVolumeMin === null ? "" : `<div><dt>…with volume ≥</dt><dd>${escapeHtml(fmtCompactUsd(ladder.addVolumeMin))} <span class="muted">(1.5× the 20-day average)</span></dd></div>`}
      ${levelRow("Trim below", ladder.trimBelow, coin.price, "away")}
      ${levelRow("Exit below", ladder.exitBelow, coin.price, "away")}
    </dl>
    <p class="muted small">Starter = ½ slot; FULL = 2 slots on a 20-day closing high with volume ≥ 1.5× its 20-day average; back to starter on a close below EMA20. Levels act on the daily close only.</p>
  </div>`;
}

/** Dashed chart levels for the next ladder change: add above (green), trim below (amber), exit below (red). */
export function ladderLevels(coin) {
  const ladder = ladderOf(coin);
  if (!ladder) return [];
  const out = [];
  const add = (price, word, kind) => {
    if (price !== null && price > 0) out.push({ price, title: `${word} ${fmtPrice(price)}`, kind, style: "dashed" });
  };
  add(ladder.addAbove, "Add above", "add");
  add(ladder.trimBelow, "Trim below", "trim");
  add(ladder.exitBelow, "Exit below", "exit");
  return out;
}

/** Past ladder transitions from the detail payload, oldest first. */
export function ladderTransitions(detail) {
  const list = pick(detail?.ladder_transitions, detail?.ladder?.transitions, detail?.ladder_events, detail?.ladder_history, []) || [];
  return list.map((t) => {
    const action = String(pick(t.action, t.type, "") || "").toUpperCase();
    return { at: pick(t.at, t.date, t.time), action: LADDER_ACTIONS[action] ? action : null, price: num(t.price ?? t.close) };
  }).filter((t) => t.action && t.action !== "HOLD" && t.at !== undefined && t.at !== null);
}

/** Ribbon colours per bar from `cdc_series` ([[t, zone]]) — reference only. */
export function cdcRibbon(detail) {
  const series = pick(detail?.cdc_series, detail?.cdc_zones, []) || [];
  return series.map((row) => {
    const t = Array.isArray(row) ? row[0] : row?.time ?? row?.t;
    const zone = String(Array.isArray(row) ? row[1] : row?.zone || "").toLowerCase();
    const time = num(t);
    if (time === null || !CDC_COLORS[zone]) return null;
    return { time: Math.floor(time), value: 1, color: CDC_COLORS[zone], zone };
  }).filter(Boolean).sort((a, b) => a.time - b.time);
}

export function cdcChip(cdc) {
  const zone = String(cdc?.zone || "").toLowerCase();
  if (!CDC_COLORS[zone]) return "";
  return `<span class="cot-cdc cot-cdc--${zone}"><i aria-hidden="true"></i> CDC 1D: ${escapeHtml(CDC_TEXT[zone])}${cdc.since ? ` since ${escapeHtml(isoDate(cdc.since))}` : ""} <span class="muted">(reference)</span></span>`;
}

function evidenceStrategy(evidence, key) {
  return pick(evidence?.strategies?.[key], evidence?.results?.[key], evidence?.[key], null);
}

/** Evidence table: ladder / rule / CDC 1D / buy & hold × trades, win rate, total return, max DD, Sharpe. */
export function renderEvidence(evidence) {
  const head = `<div class="section-heading"><h2>${escapeHtml(EVIDENCE_TITLE)}</h2></div>`;
  if (!evidence) return `${head}<p class="muted">No evidence computed yet for this coin.</p>`;
  const bars = num(evidence.bars);
  const span = evidence.from || evidence.to ? `${isoDate(evidence.from)} → ${isoDate(evidence.to)}` : "";
  const meta = `<p class="muted small">${escapeHtml([bars === null ? null : `${bars} daily bars`, span, "0.25% cost per side"].filter(Boolean).join(" · "))}</p>`;
  if (evidence.insufficient_history) {
    return `${head}${meta}<p class="cot-align cot-align--info"><span aria-hidden="true">ℹ</span> Not enough history (${escapeHtml(bars ?? 0)} of 365 daily bars needed) — no verdict.</p>`;
  }
  const pctCell = (v, { signed = true } = {}) => {
    const n = num(v);
    if (n === null) return "—";
    return signed ? fmtPct(n, 1) : `${(n * 100).toFixed(0)}%`;
  };
  const rows = EVIDENCE_ROWS.map(([key, label]) => {
    const s = evidenceStrategy(evidence, key);
    if (!s) return `<tr><th scope="row">${escapeHtml(label)}</th><td colspan="5" class="muted">—</td></tr>`;
    const total = num(s.total_return_pct);
    const tone = total === null ? "none" : total > 0 ? "pos" : total < 0 ? "neg" : "flat";
    const arrow = total === null ? "" : total > 0 ? "▲ " : total < 0 ? "▼ " : "• ";
    const dd = num(s.max_dd_pct);
    return `<tr${key === "ladder" ? ' class="cot-evidence-main"' : ""}><th scope="row">${escapeHtml(label)}</th>
      <td class="num">${escapeHtml(num(s.trades) ?? "—")}</td>
      <td class="num">${escapeHtml(pctCell(s.win_rate, { signed: false }))}</td>
      <td class="num"><span class="pnl pnl--${tone}"><span aria-hidden="true">${arrow}</span>${escapeHtml(pctCell(total))}</span></td>
      <td class="num">${escapeHtml(dd === null ? "—" : fmtPct(-Math.abs(dd), 1))}</td>
      <td class="num">${escapeHtml(num(s.sharpe) === null ? "—" : num(s.sharpe).toFixed(2))}</td></tr>`;
  }).join("");
  return `${head}${meta}
    <div class="table-scroll"><table class="data-table cot-evidence">
      <thead><tr><th scope="col">Strategy</th><th scope="col">Trades</th><th scope="col">Win rate</th><th scope="col">Total return</th><th scope="col">Max DD</th><th scope="col">Sharpe</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <p class="muted small">Backtests on this coin's own past. About 20 variants were examined, so expect mild selection bias.</p>`;
}
