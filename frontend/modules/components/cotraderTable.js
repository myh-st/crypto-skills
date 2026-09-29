// Coin list for the Signals page: one scannable row per coin instead of a wall of cards.
// Columns: coin · price/24h · ladder state (+ today's action) · the next level that changes it ·
// Jev scores. Each row is one link to the coin detail. On narrow screens the row stacks (CSS).
import { escapeHtml } from "../format.js";
import { num } from "./ui.js";
import { coinIcon } from "./coinBook.js";
import { fmtPrice, pctChange } from "./cotraderBits.js";
import { LADDER_ACTIONS, ladderBadge, ladderHint, ladderOf } from "./cotraderLadder.js";
import { agreementBadge, jevScore, miniBar } from "./cotraderAi.js";
import { icon } from "./icons.js";

const NEXT_LABEL = { trim: "Trim below", add: "Add above", exit: "Exit below" };

/** The level that would change this coin's ladder next, with the move needed from the live price. */
export function nextLevel(coin) {
  const l = ladderOf(coin);
  const price = num(coin?.price);
  if (!l || !l.state) return null;
  const options = [];
  if (l.state === "FULL" && l.trimBelow !== null) options.push({ kind: "trim", price: l.trimBelow });
  if (l.state !== "FULL" && l.addAbove !== null) options.push({ kind: "add", price: l.addAbove });
  if (l.state !== "OUT" && l.exitBelow !== null) options.push({ kind: "exit", price: l.exitBelow });
  if (!options.length) return null;
  const withMove = options.map((o) => ({ ...o, move: price ? o.price / price - 1 : null }));
  withMove.sort((a, b) => Math.abs(a.move ?? Infinity) - Math.abs(b.move ?? Infinity));
  return withMove[0];
}

function baseOf(coin) {
  return String(coin?.base || String(coin?.symbol || "").replace(/_USDT$/, ""));
}

/** Rank: today's fresh ladder actions first, then FULL, STARTER, OUT; stable within a group. */
export function sortCoins(coins) {
  const rank = (c) => {
    const l = ladderOf(c);
    if (l?.fresh && l.action !== "HOLD") return 0;
    return { FULL: 1, STARTER: 2, OUT: 3 }[l?.state] ?? 4;
  };
  return (coins || []).map((c, i) => ({ c, i })).sort((a, b) => rank(a.c) - rank(b.c) || a.i - b.i).map(({ c }) => c);
}

export function renderCoinRow(coin) {
  const base = baseOf(coin);
  const l = ladderOf(coin);
  const fresh = Boolean(l?.fresh && l.action !== "HOLD");
  const action = fresh ? LADDER_ACTIONS[l.action] : null;
  const next = nextLevel(coin);
  const hint = ladderHint(l);
  const jev = coin?.jev;
  const unavailable = coin?.unavailable_reason;
  return `<li><a class="cot-row${fresh ? " cot-row--fresh" : ""}" href="#/cotrader/${encodeURIComponent(base)}" data-cot-coin="${escapeHtml(base)}">
    <span class="cot-cell cot-cell--coin">${coinIcon(base, { size: 28 })}
      <span><strong>${escapeHtml(base)}</strong><span class="cot-cell-sub">${escapeHtml(coin?.symbol || `${base}_USDT`)}</span></span></span>
    <span class="cot-cell cot-cell--price"><strong data-motion-key="cot:${escapeHtml(base)}:price">${escapeHtml(fmtPrice(coin?.price))}</strong>
      <span class="cot-cell-sub">${pctChange(coin?.change_24h)} 24h</span></span>
    <span class="cot-cell cot-cell--ladder">${unavailable ? `<span class="cot-cell-sub">${escapeHtml(unavailable)}</span>` : `${ladderBadge(l?.state)}
      ${action ? `<span class="cot-today cot-today--${escapeHtml(action.tone)}">Today: ${escapeHtml(action.label)}</span>` : hint ? `<span class="cot-cell-sub" lang="th">${escapeHtml(hint)}</span>` : ""}`}</span>
    <span class="cot-cell cot-cell--next">${next ? `<span class="cot-cell-label">${escapeHtml(NEXT_LABEL[next.kind])}</span>
      <strong>${escapeHtml(fmtPrice(next.price))}</strong>
      <span class="cot-cell-sub">${next.move === null ? "" : `${escapeHtml((next.move * 100).toFixed(1))}% away`}</span>` : '<span class="cot-cell-sub">—</span>'}</span>
    <span class="cot-cell cot-cell--ai">${jev ? `${miniBar("Trend", jevScore(jev, "trend_strength"), "trend")}${miniBar("Risk", jevScore(jev, "reversal_risk"), "risk")}${agreementBadge(jev.rule_agreement)}` : '<span class="cot-cell-sub">No Jev score yet</span>'}</span>
    <span class="cot-cell cot-cell--go">${icon("chevron")}</span>
  </a></li>`;
}

// Filter views for the coin list; the active one lives in the URL (#/cotrader?view=full) so
// refresh, back/forward and shared links keep it. Summary cards drill down into these views.
export const COIN_VIEWS = [
  { id: "all", label: "All", test: () => true },
  { id: "actions", label: "Today's actions", test: (l) => Boolean(l?.fresh && l.action !== "HOLD") },
  { id: "in", label: "In trend", test: (l) => l?.state === "FULL" || l?.state === "STARTER" },
  { id: "full", label: "Full", test: (l) => l?.state === "FULL" },
  { id: "starter", label: "Half", test: (l) => l?.state === "STARTER" },
  { id: "out", label: "Cash", test: (l) => l?.state === "OUT" },
];
const VIEW_BY_ID = Object.fromEntries(COIN_VIEWS.map((v) => [v.id, v]));

export function normaliseView(view) {
  return VIEW_BY_ID[view] ? view : "all";
}

export function filterCoins(coins, view = "all") {
  const v = VIEW_BY_ID[normaliseView(view)];
  return (coins || []).filter((c) => v.test(ladderOf(c)));
}

/** Filter chips with counts; the active chip is marked and a clear link appears when filtered. */
export function renderCoinFilters(coins, view = "all") {
  const active = normaliseView(view);
  const chips = COIN_VIEWS.map((v) => {
    const n = filterCoins(coins, v.id).length;
    const on = v.id === active;
    const href = v.id === "all" ? "#/cotrader" : `#/cotrader?view=${v.id}`;
    return `<a class="cot-chip${on ? " is-active" : ""}" href="${href}"${on ? ' aria-current="true"' : ""}>${escapeHtml(v.label)} <span class="cot-chip-n">${n}</span></a>`;
  }).join("");
  return `<nav class="cot-filters" aria-label="Filter coins">${chips}</nav>`;
}

export function renderCoinTable(coins, { view = "all" } = {}) {
  if (!coins?.length) return '<p class="muted">No coins yet. Add some in the Watchlist.</p>';
  const active = normaliseView(view);
  const shown = filterCoins(coins, active);
  const status = active === "all" ? "" : `<p class="cot-filter-status small" role="status">Showing ${shown.length} of ${coins.length} coins · ${escapeHtml(VIEW_BY_ID[active].label)} <a href="#/cotrader">Clear filter</a></p>`;
  if (!shown.length) {
    return `${renderCoinFilters(coins, active)}${status}<div class="cot-empty">No coins match “${escapeHtml(VIEW_BY_ID[active].label)}” right now. <a href="#/cotrader">Show all coins</a></div>`;
  }
  return `${renderCoinFilters(coins, active)}${status}<div class="cot-table" role="region" aria-label="Coins">
    <div class="cot-row cot-row--head" aria-hidden="true">
      <span>Coin</span><span>Price</span><span>Position</span><span>Next level</span><span>Jev (AI)</span><span></span>
    </div>
    <ul class="cot-list">${sortCoins(shown).map(renderCoinRow).join("")}</ul>
  </div>`;
}
