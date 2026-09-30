// Coin list for the Signals page: one scannable row per coin instead of a wall of cards.
// Columns: coin · price/24h · ladder state (+ today's action) · the next level that changes it ·
// Jev scores. Each row is one link to the coin detail. On narrow screens the row stacks (CSS).
import { escapeHtml } from "../format.js";
import { num } from "./ui.js";
import { coinIcon } from "./coinBook.js";
import { fmtPrice, pctChange } from "./cotraderBits.js";
import { ladderHint, ladderOf } from "./cotraderLadder.js";
import { agreementBadge, jevScore, miniBar } from "./cotraderAi.js";
import { icon } from "./icons.js";

const NEAR_MOVE = 0.03; // within 3% of the level that would change the ladder

// One plain-Thai verdict per coin: what to do now. Deterministic from the ladder (never from AI text).
export const VERDICTS = {
  buy: { th: "เริ่มซื้อ", tone: "buy", icon: "▲" },
  add: { th: "ซื้อเพิ่ม", tone: "buy", icon: "▲" },
  trim: { th: "ลดครึ่ง", tone: "trim", icon: "▼" },
  sell: { th: "ขายออก", tone: "sell", icon: "▼" },
  hold: { th: "ถือต่อ", tone: "hold", icon: "■" },
  wait: { th: "รอก่อน", tone: "wait", icon: "◷" },
};

function pctText(move) {
  return `${move >= 0 ? "+" : ""}${(move * 100).toFixed(1)}%`;
}

/** {key, th, tone, icon, detail, near} or null when the coin has no ladder yet. */
export function verdictOf(coin) {
  const l = ladderOf(coin);
  if (!l || !l.state) return null;
  const price = num(coin?.price);
  const moveTo = (level) => (price && level ? level / price - 1 : null);
  const make = (key, detail, near = false) => ({ key, ...VERDICTS[key], detail, near });
  if (l.fresh) {
    if (l.action === "BUY_STARTER") return make("buy", "ซื้อครึ่งไม้ (เทรนด์เพิ่งเริ่ม)");
    if (l.action === "ADD") return make("add", "เติมเป็นเต็มไม้ (เบรคพร้อมวอลุ่มหนุน)");
    if (l.action === "TRIM") return make("trim", "ขายครึ่งหนึ่ง เหลือครึ่งไม้");
    if (l.action === "SELL_ALL") return make("sell", "ขายทั้งหมด ถือเงินสด");
  }
  if (l.state === "FULL") {
    const move = moveTo(l.trimBelow);
    return move !== null && Math.abs(move) <= NEAR_MOVE
      ? make("hold", `เต็มไม้ · ใกล้จุดลด (${pctText(move)})`, true) : make("hold", "เต็มไม้");
  }
  if (l.state === "STARTER") {
    const move = moveTo(l.addAbove);
    return move !== null && Math.abs(move) <= NEAR_MOVE
      ? make("hold", `ครึ่งไม้ · ใกล้จุดซื้อเพิ่ม (${pctText(move)}, ต้องมีวอลุ่มหนุน)`, true) : make("hold", "ครึ่งไม้ · รอเบรคพร้อมวอลุ่มค่อยซื้อเพิ่ม");
  }
  const move = moveTo(num(coin?.trend_line_next));
  return move !== null && Math.abs(move) <= NEAR_MOVE
    ? make("wait", `ยังไม่ซื้อ · ใกล้สัญญาณซื้อ (${pctText(move)})`, true) : make("wait", "ยังไม่ซื้อ ถือเงินสด");
}

export function verdictPill(verdict, { size = "md" } = {}) {
  if (!verdict) return '<span class="cot-verdict cot-verdict--none">—</span>';
  return `<span class="cot-verdict cot-verdict--${escapeHtml(verdict.tone)} cot-verdict--${size}" lang="th"><span aria-hidden="true">${escapeHtml(verdict.icon)}</span> ${escapeHtml(verdict.th)}</span>`;
}

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

/** Rank: exits first, then buys, then coins near a level, then holds and waits; stable within a group. */
export function sortCoins(coins) {
  const rank = (c) => {
    const v = verdictOf(c);
    if (!v) return 9;
    if (v.key === "sell" || v.key === "trim") return 0; // exits first: they protect capital
    if (v.key === "buy" || v.key === "add") return 1;
    if (v.near) return 2;
    return { hold: 3, wait: 4 }[v.key] ?? 5;
  };
  return (coins || []).map((c, i) => ({ c, i })).sort((a, b) => rank(a.c) - rank(b.c) || a.i - b.i).map(({ c }) => c);
}

function pctShort(move) {
  return move === null || move === undefined ? "" : `${move >= 0 ? "+" : ""}${(move * 100).toFixed(1)}%`;
}

/** Nearest support / resistance zones (from swing highs/lows) as two compact lines. */
export function renderSrCell(sr) {
  if (!sr) return '<span class="cot-cell-sub">—</span>';
  const zone = (z) => `${escapeHtml(fmtPrice(z.price))} <span class="cot-cell-sub">${escapeHtml(pctShort(z.distance))}${z.touches > 1 ? ` · ${escapeHtml(z.touches)}×` : ""}</span>`;
  const r = sr.resistance?.[0];
  const sp = sr.support?.[0];
  const res = r ? `${zone(r)}${r.kind === "recent_high" ? ' <span class="cot-cell-sub">(จุดสูงล่าสุด)</span>' : ""}` : sr.at_high ? '<span class="cot-cell-sub">ทำจุดสูงสุดใหม่</span>' : '<span class="cot-cell-sub">—</span>';
  const sup = sp ? zone(sp) : '<span class="cot-cell-sub">—</span>';
  return `<span class="cot-sr-line"><span class="cot-sr-tag cot-sr-tag--r">ต้าน</span> ${res}</span>
    <span class="cot-sr-line"><span class="cot-sr-tag cot-sr-tag--s">รับ</span> ${sup}</span>`;
}

/** The system's own decision prices for the current state: where to buy / sell half / cut the loss. */
export function planLevels(coin) {
  const l = ladderOf(coin);
  const price = num(coin?.price);
  if (!l || !l.state) return [];
  const move = (level) => (price && level ? level / price - 1 : null);
  const out = [];
  if (l.state === "OUT" && num(coin?.trend_line_next) !== null) out.push({ kind: "buy", label: "เริ่มซื้อ >", price: num(coin.trend_line_next) });
  if (l.state === "STARTER" && l.addAbove !== null) out.push({ kind: "add", label: "ซื้อเพิ่ม >", price: l.addAbove });
  if (l.state === "FULL" && l.trimBelow !== null) out.push({ kind: "trim", label: "ขายครึ่ง <", price: l.trimBelow });
  if (l.state !== "OUT" && l.exitBelow !== null) out.push({ kind: "exit", label: "ตัดขาดทุน <", price: l.exitBelow });
  return out.map((x) => ({ ...x, move: move(x.price) }));
}

export function renderPlanCell(coin) {
  const levels = planLevels(coin);
  if (!levels.length) return '<span class="cot-cell-sub">—</span>';
  return levels.map((x) => `<span class="cot-plan-line cot-plan-line--${x.kind}" title="ราคาปิดรายวัน"><span class="cot-cell-label">${escapeHtml(x.label)}</span>
    <strong>${escapeHtml(fmtPrice(x.price))}</strong> <span class="cot-cell-sub">${escapeHtml(pctShort(x.move))}</span></span>`).join("");
}

export function renderCoinRow(coin) {
  const base = baseOf(coin);
  const l = ladderOf(coin);
  const verdict = verdictOf(coin);
  const hint = ladderHint(l);
  const jev = coin?.jev;
  const unavailable = coin?.unavailable_reason;
  const act = verdict && ["buy", "add", "trim", "sell"].includes(verdict.key) ? ` cot-row--act cot-row--${verdict.tone}` : "";
  return `<li><a class="cot-row${act}" href="#/cotrader/${encodeURIComponent(base)}" data-cot-coin="${escapeHtml(base)}">
    <span class="cot-cell cot-cell--coin">${coinIcon(base, { size: 28 })}
      <span><strong>${escapeHtml(base)}</strong><span class="cot-cell-sub">${escapeHtml(coin?.symbol || `${base}_USDT`)}</span></span></span>
    <span class="cot-cell cot-cell--price"><strong data-motion-key="cot:${escapeHtml(base)}:price">${escapeHtml(fmtPrice(coin?.price))}</strong>
      <span class="cot-cell-sub">${pctChange(coin?.change_24h)} 24h</span></span>
    <span class="cot-cell cot-cell--ladder">${unavailable ? `<span class="cot-cell-sub">${escapeHtml(unavailable)}</span>` : `${verdictPill(verdict)}
      <span class="cot-cell-sub${verdict?.near ? " cot-near" : ""}" lang="th">${escapeHtml(verdict?.detail || hint || "")}</span>`}</span>
    <span class="cot-cell cot-cell--sr">${renderSrCell(coin?.sr)}</span>
    <span class="cot-cell cot-cell--next">${renderPlanCell(coin)}</span>
    <span class="cot-cell cot-cell--ai">${jev ? `${miniBar("Trend", jevScore(jev, "trend_strength"), "trend")}${miniBar("Risk", jevScore(jev, "reversal_risk"), "risk")}${agreementBadge(jev.rule_agreement)}` : '<span class="cot-cell-sub">No Jev score yet</span>'}</span>
    <span class="cot-cell cot-cell--go">${icon("chevron")}</span>
  </a></li>`;
}

// Filter views for the coin list; the active one lives in the URL (#/cotrader?view=full) so
// refresh, back/forward and shared links keep it. Summary cards drill down into these views.
export const COIN_VIEWS = [
  { id: "all", label: "ทั้งหมด", test: () => true },
  { id: "buy", label: "ซื้อ / ซื้อเพิ่ม", tone: "buy", test: (v) => v?.key === "buy" || v?.key === "add" },
  { id: "sell", label: "ลด / ขายออก", tone: "sell", test: (v) => v?.key === "trim" || v?.key === "sell" },
  { id: "hold", label: "ถือต่อ", tone: "hold", test: (v) => v?.key === "hold" },
  { id: "wait", label: "รอก่อน", tone: "wait", test: (v) => v?.key === "wait" },
  { id: "near", label: "ใกล้จุดเปลี่ยน", tone: "near", test: (v) => Boolean(v?.near) },
];
const VIEW_BY_ID = Object.fromEntries(COIN_VIEWS.map((v) => [v.id, v]));

export function normaliseView(view) {
  return VIEW_BY_ID[view] ? view : "all";
}

export function filterCoins(coins, view = "all") {
  const v = VIEW_BY_ID[normaliseView(view)];
  return (coins || []).filter((c) => v.test(verdictOf(c)));
}

/** Filter chips with counts; the active chip is marked and a clear link appears when filtered. */
export function renderCoinFilters(coins, view = "all") {
  const active = normaliseView(view);
  const chips = COIN_VIEWS.map((v) => {
    const n = filterCoins(coins, v.id).length;
    const on = v.id === active;
    const href = v.id === "all" ? "#/cotrader" : `#/cotrader?view=${v.id}`;
    const tone = v.tone && n > 0 ? ` cot-chip--${v.tone}` : "";
    return `<a class="cot-chip${tone}${on ? " is-active" : ""}" href="${href}" lang="th"${on ? ' aria-current="true"' : ""}>${escapeHtml(v.label)} <span class="cot-chip-n">${n}</span></a>`;
  }).join("");
  return `<nav class="cot-filters" aria-label="Filter coins">${chips}</nav>`;
}

export function renderCoinTable(coins, { view = "all" } = {}) {
  if (!coins?.length) return '<p class="muted">No coins yet. Add some in the Watchlist.</p>';
  const active = normaliseView(view);
  const shown = filterCoins(coins, active);
  const status = active === "all" ? "" : `<p class="cot-filter-status small" role="status" lang="th">แสดง ${shown.length} จาก ${coins.length} เหรียญ · ${escapeHtml(VIEW_BY_ID[active].label)} <a href="#/cotrader">ล้างตัวกรอง</a></p>`;
  if (!shown.length) {
    return `${renderCoinFilters(coins, active)}${status}<div class="cot-empty" lang="th">ตอนนี้ไม่มีเหรียญในกลุ่ม “${escapeHtml(VIEW_BY_ID[active].label)}” <a href="#/cotrader">ดูทั้งหมด</a></div>`;
  }
  return `${renderCoinFilters(coins, active)}${status}<div class="cot-table" role="region" aria-label="Coins">
    <div class="cot-row cot-row--head" aria-hidden="true">
      <span lang="th">เหรียญ</span><span lang="th">ราคา</span><span lang="th">ทำอะไร</span><span lang="th">แนวต้าน / แนวรับ</span><span lang="th" title="เทียบกับราคาปิดรายวัน (07:00 น.)">จุดซื้อ / ขาย / ตัดขาดทุน (ราคาปิดวัน)</span><span>Jev (AI)</span><span></span>
    </div>
    <ul class="cot-list">${sortCoins(shown).map(renderCoinRow).join("")}</ul>
  </div>`;
}
