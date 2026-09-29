// Small shared UI primitives. State is never conveyed by color alone: every PnL carries
// an explicit sign and arrow, every mode/severity a text label.
import { escapeHtml } from "../format.js";

export function metric(label, value, note = "") {
  return `
    <div class="paper-metric">
      <span class="paper-metric-value">${escapeHtml(value)}</span>
      <span class="paper-metric-label">${escapeHtml(label)}</span>
      ${note ? `<small>${escapeHtml(note)}</small>` : ""}
    </div>
  `;
}

export function num(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function fmtNumber(value, { digits = null, compact = false } = {}) {
  const number = num(value);
  if (number === null) return "—";
  const abs = Math.abs(number);
  if (compact && abs >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}M`;
  if (compact && abs >= 10_000) return `${(number / 1_000).toFixed(1)}k`;
  const precision = digits ?? (abs >= 1000 ? 2 : abs >= 1 ? 4 : abs >= 0.01 ? 5 : 8);
  return number.toLocaleString("en-US", { maximumFractionDigits: precision, minimumFractionDigits: Math.min(2, precision) });
}

export function price(value) {
  return fmtNumber(value);
}

export function signedText(value, { digits = 2, unit = " USDT" } = {}) {
  const number = num(value);
  if (number === null) return "—";
  const sign = number > 0 ? "+" : number < 0 ? "−" : "";
  return `${sign}${Math.abs(number).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}${unit}`;
}

export function pnl(value, { digits = 2, unit = " USDT" } = {}) {
  const number = num(value);
  if (number === null) return '<span class="pnl pnl--none">—</span>';
  const tone = number > 0 ? "pos" : number < 0 ? "neg" : "flat";
  const arrow = number > 0 ? "▲" : number < 0 ? "▼" : "•";
  return `<span class="pnl pnl--${tone}"><span aria-hidden="true">${arrow}</span> ${escapeHtml(signedText(number, { digits, unit }))}</span>`;
}

export function pct(value, { digits = 2, signed = false } = {}) {
  const number = num(value);
  if (number === null) return "—";
  const sign = signed && number > 0 ? "+" : number < 0 ? "−" : "";
  return `${sign}${Math.abs(number * 100).toFixed(digits)}%`;
}

export const MODE_LABELS = {
  AUTO_PAPER: "AI managed",
  RECOMMEND_ONLY: "Recommend only",
  MANUAL_OVERRIDE: "Manual override",
  PAUSED: "AI paused",
  UNASSIGNED: "Unassigned",
};

export function modeBadge(mode) {
  const key = MODE_LABELS[mode] ? mode : "UNASSIGNED";
  const icon = { AUTO_PAPER: "◆", RECOMMEND_ONLY: "◇", MANUAL_OVERRIDE: "✋", PAUSED: "Ⅱ", UNASSIGNED: "?" }[key];
  return `<span class="mode-badge mode-badge--${key.toLowerCase()}"><span aria-hidden="true">${icon}</span> ${escapeHtml(MODE_LABELS[key])}</span>`;
}

export function severityBadge(severity) {
  const value = String(severity || "INFO").toUpperCase();
  return `<span class="sev-badge sev-badge--${value.toLowerCase()}">${escapeHtml(value)}</span>`;
}

export function sourceBadge(source) {
  const value = String(source || "SYSTEM").toUpperCase();
  return `<span class="src-badge src-badge--${value.toLowerCase()}">${escapeHtml(value)}</span>`;
}

export function marketBadge(marketType) {
  const label = marketType === "spot" ? "SPOT" : "PERP";
  return `<span class="mkt-badge mkt-badge--${label.toLowerCase()}">${label}</span>`;
}

export function sideBadge(side, leverage = null) {
  const value = String(side || "").toLowerCase();
  const label = value === "long" || value === "buy" ? (value === "buy" ? "BUY" : "LONG") : value === "short" || value === "sell" ? (value === "sell" ? "SELL" : "SHORT") : value.toUpperCase();
  const tone = value === "long" || value === "buy" ? "pos" : "neg";
  return `<span class="side-badge side-badge--${tone}">${escapeHtml(label)}${leverage ? ` ${escapeHtml(leverage)}x` : ""}</span>`;
}

export function emptyState(message, action = "") {
  return `<div class="empty-state"><p>${escapeHtml(message)}</p>${action}</div>`;
}

export function feedback(host, message, tone = "neutral") {
  if (!host) return;
  host.className = `paper-feedback paper-feedback--${tone}`;
  host.textContent = message;
}

export function uid(prefix = "req") {
  const random = globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`.slice(0, 80);
}
