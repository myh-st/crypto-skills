// "ทำอะไรต่อ" — a short Thai summary of what the ladder says to do after the last daily close.
// Pure and deterministic: built only from the coin ladder states/actions and trigger levels the
// backend already computed. No AI text is used here, so it is always available and never billed.
import { escapeHtml } from "../format.js";
import { num } from "./ui.js";
import { fmtPrice, fmtUsdt } from "./cotraderBits.js";
import { ladderOf } from "./cotraderLadder.js";

const ACTION_TH = {
  BUY_STARTER: (b) => `เริ่มซื้อ ${b} ครึ่งไม้`,
  ADD: (b) => `เติม ${b} เป็นเต็มไม้ (เทรนด์แรง เบรคจุดสูงสุด 20 วัน)`,
  TRIM: (b) => `ลด ${b} กลับเหลือครึ่งไม้ (หลุด EMA20 แต่เทรนด์ยังขึ้น)`,
  SELL_ALL: (b) => `ขาย ${b} ทั้งหมด ถือเงินสด (หลุดเทรนด์)`,
};
const SIZE_TH = { FULL: "เต็มไม้", STARTER: "ครึ่งไม้" };

function baseOf(coin) {
  return String(coin?.base || String(coin?.symbol || "").replace(/_USDT$/, ""));
}

function amountText(ladder, capital) {
  const cap = num(capital);
  const fraction = num(ladder?.raw?.capital_fraction);
  if (cap === null || fraction === null) return "";
  return ` ≈ ${fmtUsdt(cap * fraction)}`;
}

/** Nearest next triggers across coins: [{base, kind, price, move}] sorted by distance. */
export function nearestTriggers(coins, limit = 3) {
  const out = [];
  for (const coin of coins || []) {
    const l = ladderOf(coin);
    const price = num(coin?.price);
    if (!l || price === null || price <= 0) continue;
    const candidates = [];
    if (l.state === "FULL" && l.trimBelow !== null) candidates.push({ kind: "trim", price: l.trimBelow });
    if ((l.state === "STARTER" || l.state === "OUT") && l.addAbove !== null) candidates.push({ kind: "add", price: l.addAbove });
    if (l.state !== "OUT" && l.exitBelow !== null) candidates.push({ kind: "exit", price: l.exitBelow });
    for (const c of candidates) out.push({ base: baseOf(coin), ...c, move: c.price / price - 1 });
  }
  return out.sort((a, b) => Math.abs(a.move) - Math.abs(b.move)).slice(0, limit);
}

const TRIGGER_TH = {
  trim: (t) => `${t.base}: ถ้าปิดวันต่ำกว่า ${fmtPrice(t.price)} ให้ลดเหลือครึ่งไม้`,
  add: (t) => `${t.base}: ถ้าปิดวันเหนือ ${fmtPrice(t.price)} ให้เติมเป็นเต็มไม้`,
  exit: (t) => `${t.base}: ถ้าปิดวันต่ำกว่า ${fmtPrice(t.price)} ให้ขายทั้งหมด`,
};

/** {todo: [..], hold: {FULL: [..], STARTER: [..]}, out: [..], watch: [..]} (pure; exported for tests). */
export function nextSteps(d) {
  const capital = d?.settings?.cotrader_capital_usdt;
  const todo = [];
  const todoBases = [];
  const hold = { FULL: [], STARTER: [] };
  const out = [];
  for (const coin of d?.coins || []) {
    const base = baseOf(coin);
    const l = ladderOf(coin);
    if (!l || !l.state) continue;
    const withRaw = { ...l, raw: coin.ladder?.action && typeof coin.ladder.action === "object" ? coin.ladder.action : {} };
    if (l.fresh && ACTION_TH[l.action]) {
      todo.push(ACTION_TH[l.action](base) + amountText(withRaw, capital));
      todoBases.push(base);
    }
    if (l.state === "OUT") out.push(base);
    else hold[l.state].push(base + amountText(withRaw, capital));
  }
  const triggers = nearestTriggers(d?.coins);
  const watch = triggers.map((t) => `${TRIGGER_TH[t.kind](t)} (ห่าง ${(t.move * 100).toFixed(1)}%)`);
  return { todo, todoBases, hold, out, watch, watchBases: triggers.map((t) => t.base) };
}

export function renderNextStepsTh(d) {
  if (!d?.coins?.length) return "";
  const s = nextSteps(d);
  // Each item links to its coin (drill-down to the evidence behind the summary).
  const list = (items, bases = []) => items.map((t, i) => (bases[i]
    ? `<li><a href="#/cotrader/${encodeURIComponent(bases[i])}">${escapeHtml(t)}</a></li>`
    : `<li>${escapeHtml(t)}</li>`)).join("");
  const holdLine = [
    s.hold.FULL.length ? `เต็มไม้: ${s.hold.FULL.join(", ")}` : "",
    s.hold.STARTER.length ? `ครึ่งไม้: ${s.hold.STARTER.join(", ")}` : "",
  ].filter(Boolean).join(" · ");
  const noCapital = num(d?.settings?.cotrader_capital_usdt) === null;
  return `<section class="panel cot-next" lang="th" aria-label="สรุปว่าต้องทำอะไรต่อ">
    <div class="section-heading"><h2>ทำอะไรต่อ</h2><span class="muted small">อัปเดตทุกวัน 07:00 น.</span></div>
    <p class="cot-next-lead"><strong>${s.todo.length ? "วันนี้มีสิ่งที่ต้องทำ:" : "วันนี้ไม่ต้องซื้อขายเพิ่ม — ถือตามเดิม"}</strong></p>
    ${s.todo.length ? `<ul class="cot-next-todo">${list(s.todo, s.todoBases)}</ul>` : ""}
    <dl class="cot-next-kv">
      ${holdLine ? `<div><dt>ควรถืออยู่</dt><dd>${escapeHtml(holdLine)}</dd></div>` : ""}
      ${s.out.length ? `<div><dt>ยังไม่ต้องซื้อ</dt><dd>${escapeHtml(s.out.join(", "))}</dd></div>` : ""}
    </dl>
    ${s.watch.length ? `<p class="small"><strong>จับตา (ใกล้จุดเปลี่ยนที่สุด)</strong></p><ul class="cot-next-watch small">${list(s.watch, s.watchBases)}</ul>` : ""}
    ${noCapital ? '<p class="small muted">ใส่เงินทุน Spot ในหน้า <a href="#/cotrader/watchlist">Watchlist</a> เพื่อให้บอกจำนวน USDT ที่ควรซื้อ/ขาย</p>' : ""}
  </section>`;
}
