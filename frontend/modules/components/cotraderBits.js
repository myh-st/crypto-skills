// Shared, pure building blocks for the Spot AI Co-Trader views: action badges, rule-state chips, AI
// stance chips, trigger wording, sizing wording and number formatting. The action always comes from
// the deterministic rule; AI text is context only. Nothing is shown by colour alone: every badge
// carries a text label and an arrow/glyph, every P&L a sign.
import { escapeHtml } from "../format.js";
import { fmtNumber, num, pct } from "./ui.js";

export const DISCLAIMER = "PAPER decision support — not investment advice; you decide and trade manually.";

export const STATE_TEXT = { HOLD: "อยู่ในเทรนด์", CASH: "ถือเงินสด", WATCH: "รอยืนยัน" };
const STATE_GLYPH = { HOLD: "▲", CASH: "▼", WATCH: "◆" };

// Action → tone (colour family) and glyph. BUY/IN green, SELL/OUT red, HOLD blue, WAIT grey.
export const ACTIONS = {
  BUY: { tone: "buy", glyph: "▲", label: "BUY" },
  IN: { tone: "buy", glyph: "●", label: "IN" },
  HOLD: { tone: "hold", glyph: "■", label: "HOLD" },
  SELL: { tone: "sell", glyph: "▼", label: "SELL" },
  OUT: { tone: "sell", glyph: "○", label: "OUT" },
  WAIT: { tone: "wait", glyph: "◷", label: "WAIT" },
};

const STANCES = {
  agree: { label: "AI agrees", glyph: "✓", tone: "agree" },
  caution: { label: "AI: caution", glyph: "!", tone: "caution" },
  disagree: { label: "AI disagrees", glyph: "✕", tone: "disagree" },
};
const MARKET_STANCES = {
  risk_on: { label: "Market: risk-on", glyph: "▲", tone: "agree" },
  mixed: { label: "Market: mixed", glyph: "◆", tone: "caution" },
  risk_off: { label: "Market: risk-off", glyph: "▼", tone: "disagree" },
};

// ---------------------------------------------------------------- numbers

export function fmtPrice(value) {
  return fmtNumber(value);
}

export function fmtUsdt(value, { digits = 2 } = {}) {
  const n = num(value);
  if (n === null) return "—";
  return `${n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })} USDT`;
}

/** Quantity with up to 8 significant decimals, trailing zeros trimmed. */
export function fmtQty(value) {
  const n = num(value);
  if (n === null) return "—";
  const abs = Math.abs(n);
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : abs >= 0.01 ? 6 : 8;
  return n.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function fmtPct(value, digits = 1) {
  return pct(value, { digits, signed: true });
}

/** Signed percentage with an arrow (never colour alone). `value` is a fraction (0.012 = +1.2%). */
export function pctChange(value, { digits = 2 } = {}) {
  const n = num(value);
  if (n === null) return '<span class="pnl pnl--none">—</span>';
  const tone = n > 0 ? "pos" : n < 0 ? "neg" : "flat";
  const arrow = n > 0 ? "▲" : n < 0 ? "▼" : "•";
  return `<span class="pnl pnl--${tone}"><span aria-hidden="true">${arrow}</span> ${escapeHtml(pct(n, { digits, signed: true }))}</span>`;
}

export function isoDate(value) {
  if (value === null || value === undefined || value === "") return "—";
  const ms = typeof value === "number" ? value * 1000 : Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : value);
  if (!Number.isFinite(ms)) return String(value);
  return new Date(ms).toISOString().slice(0, 10);
}

// ---------------------------------------------------------------- action

/**
 * The coin's action. The backend's `action` wins; for an older payload without it, the same
 * deterministic table (Addendum B) is applied to `state` and `held`.
 */
export function actionFor(coin) {
  if (coin?.action?.type && ACTIONS[coin.action.type]) return coin.action;
  const inTrend = coin?.state === "HOLD";
  const held = coin?.held === true ? true : coin?.held === false ? false : null;
  const level = num(coin?.trend_line_next);
  const lvl = level === null ? "the trend line" : fmtPrice(level);
  const fresh = Boolean(coin?.changed_today);
  const trigger = level === null ? null : { kind: inTrend ? "close_below" : "close_above", price: level, distance_pct: num(coin?.distance_to_trend_line) };
  if (inTrend) {
    if (held === false) return { type: "BUY", text: "In trend. The rule holds this coin.", fresh, trigger };
    if (held === true) return { type: "HOLD", text: `Keep. Exit if a daily close is below ${lvl}.`, fresh, trigger };
    return { type: "IN", text: "Rule: hold.", fresh, trigger };
  }
  if (held === true) return { type: "SELL", text: "Out of trend. The rule holds cash.", fresh, trigger };
  if (held === false) return { type: "WAIT", text: `Stay out. Buy if a daily close is above ${lvl}.`, fresh, trigger };
  return { type: "OUT", text: "Rule: cash.", fresh, trigger };
}

export function isFresh(coin) {
  return Boolean(actionFor(coin)?.fresh || coin?.changed_today);
}

export function actionBadge(type, { size = "big" } = {}) {
  const a = ACTIONS[type] || { tone: "wait", glyph: "?", label: String(type || "—") };
  return `<span class="cot-action cot-action--${a.tone} cot-action--${escapeHtml(size)}"><span aria-hidden="true">${a.glyph}</span> ${escapeHtml(a.label)}</span>`;
}

export function stateChip(state, { withText = true } = {}) {
  if (!state || !STATE_TEXT[state]) return '<span class="cot-state cot-state--none">not in universe</span>';
  return `<span class="cot-state cot-state--${state.toLowerCase()}"><span aria-hidden="true">${STATE_GLYPH[state]}</span> ${escapeHtml(state)}${withText ? ` <span lang="th">— ${escapeHtml(STATE_TEXT[state])}</span>` : ""}</span>`;
}

export function stanceChip(stance, conviction = null) {
  const s = STANCES[stance];
  if (!s) return '<span class="cot-stance cot-stance--none">AI: no view yet</span>';
  return `<span class="cot-stance cot-stance--${s.tone}"><span aria-hidden="true">${s.glyph}</span> ${escapeHtml(s.label)}${conviction ? ` · ${escapeHtml(conviction)}` : ""}</span>`;
}

export function marketStanceChip(stance) {
  const s = MARKET_STANCES[stance];
  if (!s) return '<span class="cot-stance cot-stance--none">Market: —</span>';
  return `<span class="cot-stance cot-stance--${s.tone}"><span aria-hidden="true">${s.glyph}</span> ${escapeHtml(s.label)}</span>`;
}

/** Move needed from the live price to the trigger (trigger / price − 1); falls back to distance_pct. */
export function triggerMove(trigger, livePrice) {
  const level = num(trigger?.price);
  const p = num(livePrice);
  if (level !== null && p !== null && p > 0) return level / p - 1;
  return num(trigger?.distance_pct);
}

/** "Buy if a daily close is above 1.23 (+4.1% away)" — or the exit wording for close_below. */
export function triggerText(trigger, livePrice = null) {
  const level = num(trigger?.price);
  if (!trigger || level === null) return "Trigger unavailable (not enough closed daily bars).";
  const above = trigger.kind === "close_above";
  const head = above ? `Buy if a daily close is above ${fmtPrice(level)}` : `Exit if a daily close is below ${fmtPrice(level)}`;
  const move = triggerMove(trigger, livePrice);
  if (move === null) return head;
  const alreadyPast = above ? move <= 0 : move >= 0;
  if (alreadyPast) return `${head} (the live price is already ${above ? "above" : "below"} it; only the daily close counts)`;
  return `${head} (${fmtPct(move)} away)`;
}

// ---------------------------------------------------------------- sizing

/**
 * One line that says how much to buy or sell. Kinds: "unset" (capital unknown), "buy", "sell",
 * "none" (on target) and "unknown" (holding unknown, so no delta can be computed).
 */
export function sizingLine(sizing, base) {
  if (!sizing || num(sizing.target_usdt) === null) {
    return { kind: "unset", text: "Set your spot capital to see how much to buy or sell." };
  }
  const delta = num(sizing.delta_usdt);
  if (delta === null) return { kind: "unknown", text: "Target known, but your current holding is unknown — add it in Holdings." };
  const qty = num(sizing.delta_qty);
  const qtyText = qty === null ? "" : ` ≈ ${fmtQty(Math.abs(qty))} ${base}`;
  if (Math.abs(delta) < 0.005) return { kind: "none", text: "On target — nothing to buy or sell." };
  if (delta > 0) return { kind: "buy", text: `Buy ${fmtUsdt(delta)}${qtyText}` };
  return { kind: "sell", text: `Sell ${fmtUsdt(Math.abs(delta))}${qtyText}` };
}

export const METHOD_LABELS = { equal_weight: "Equal weight (1/N)", inverse_vol: "Inverse volatility" };

// ---------------------------------------------------------------- sparkline

/** 90-day close sparkline with the trend line as a dashed level. Pure SVG, colour from CSS. */
export function cotSpark(points, level = null, { label = "90-day closes", width = 240, height = 56 } = {}) {
  const values = (points || []).map((p) => num(p?.[1])).filter((v) => v !== null);
  if (values.length < 2) return '<div class="cot-spark cot-spark--empty muted small">No closed daily bars yet.</div>';
  const lvl = num(level);
  const lo = Math.min(...values, lvl ?? Infinity);
  const hi = Math.max(...values, lvl ?? -Infinity);
  const span = hi - lo || Math.abs(hi) * 0.01 || 1;
  const pad = 3;
  const x = (i) => pad + (i / (values.length - 1)) * (width - pad * 2);
  const y = (v) => pad + (1 - (v - lo) / span) * (height - pad * 2);
  const line = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = values[values.length - 1];
  const up = last >= values[0];
  const aria = `${label}: from ${fmtPrice(values[0])} to ${fmtPrice(last)}${lvl === null ? "" : `; trend line ${fmtPrice(lvl)}`}`;
  return `<svg class="cot-spark ${up ? "cot-spark--up" : "cot-spark--down"}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(aria)}">
    ${lvl === null ? "" : `<line class="cot-spark-level" x1="${pad}" x2="${width - pad}" y1="${y(lvl).toFixed(1)}" y2="${y(lvl).toFixed(1)}" vector-effect="non-scaling-stroke" />`}
    <path class="cot-spark-line" d="${line}" vector-effect="non-scaling-stroke" />
    <circle class="cot-spark-dot" cx="${x(values.length - 1).toFixed(1)}" cy="${y(last).toFixed(1)}" r="2.5" />
  </svg>`;
}

/** Friendly message when this server has no co-trader (404) or the request failed. */
export function unavailableHtml(error) {
  if (error?.status === 404) {
    return `<div class="panel cot-unavailable"><h2>Co-Trader is not enabled on this server</h2>
      <p class="muted">Start the co-trader instance with its own database:
      <code>python3 -m crypto_eval paper-server --database ~/paper-cotrader.sqlite3 --port 8771 --cotrader</code></p></div>`;
  }
  return `<div class="panel cot-unavailable"><h2>Co-Trader data unavailable</h2><p class="muted">${escapeHtml(error?.message || "Request failed")}. Nothing is simulated.</p></div>`;
}

/** Compact USD amount for volumes: 1.25M, 830K. */
export function fmtCompactUsd(value) {
  const n = num(value);
  if (n === null) return "—";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

/**
 * Bounded lists: rows past `visible` get class "cot-extra" (hidden until expanded) and this
 * button toggles the container's `is-expanded`. Returns "" when nothing is hidden.
 */
export const extraClass = (index, visible) => (index >= visible ? " cot-extra" : "");
export function showAllButton(total, visible, key, noun) {
  if (total <= visible) return "";
  const label = `Show all ${total} ${noun}`;
  return `<button type="button" class="btn btn--ghost btn--small cot-more" data-cot-expand="${escapeHtml(key)}" data-label="${escapeHtml(label)}" aria-expanded="false">${escapeHtml(label)}</button>`;
}
