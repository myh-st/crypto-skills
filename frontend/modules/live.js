// Live updates from the runtime stream (server push every ~3 s): the latest equity, each open position's
// live price and P&L, and the next decision time. Whatever page is open is updated in place, and the
// motion layer glides and flashes the numbers that changed. Missing or stale data is left as rendered.
import { price, pnl } from "./components/ui.js";
import { applyMotion } from "./components/motion.js";

const listeners = new Set();
let latest = null;

export function onLive(fn) {
  listeners.add(fn);
  if (latest) fn(latest);
  return () => listeners.delete(fn);
}

const usdt = (v) => `${Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT`;
const signed = (v) => `${v >= 0 ? "+" : "-"}${Math.abs(v).toFixed(2)} USDT`;

/** Apply one runtime frame to the current page. Exported for tests (pass a fake document). */
export function applyLiveFrame(payload, doc = globalThis.document) {
  const live = payload?.live;
  if (!live || !doc) return;
  const set = (key, html) => doc.querySelectorAll(`[data-motion-key="${key}"]`).forEach((el) => { el.innerHTML = html; });
  for (const p of live.positions || []) {
    if (!p.fresh) continue;                                  // never overwrite with a stale price
    set(`pos:${p.position_ref}:mark`, price(p.price));
    set(`pos:${p.position_ref}:pnl`, pnl(p.unrealized_pnl_usdt));
  }
  const byCoin = new Map();
  for (const p of live.positions || []) {
    const c = byCoin.get(p.symbol) || { pnl: 0, fresh: false, price: p.price };
    c.pnl += Number(p.unrealized_pnl_usdt) || 0;
    if (p.fresh) { c.fresh = true; c.price = p.price; }
    byCoin.set(p.symbol, c);
  }
  for (const [symbol, c] of byCoin) {
    if (!c.fresh) continue;
    set(`coin:${symbol}:price`, price(c.price));
    set(`coin:${symbol}:pnl`, pnl(c.pnl));
  }
  if (Number.isFinite(live.equity_usdt)) {
    set("kpi:equity", usdt(live.equity_usdt));
    const total = Number(payload.portfolio?.total_equity_usdt), trading = Number(payload.portfolio?.trading_pnl_usdt);
    if (Number.isFinite(total) && Number.isFinite(trading)) set("kpi:trading", pnl(live.equity_usdt - (total - trading)));
    set("today:equity", usdt(live.equity_usdt));
    const hero = doc.querySelector("[data-day-start]");
    const start = Number(hero?.dataset.dayStart);
    if (Number.isFinite(start) && start > 0) set("today:pnl", `<span class="${live.equity_usdt - start >= 0 ? "pos" : "neg"}-text">${signed(live.equity_usdt - start)}</span>`);
  }
  if (live.next_decision_at) doc.querySelectorAll("[data-countdown-next-decision]").forEach((el) => { el.dataset.countdown = live.next_decision_at; });
  applyMotion(doc.querySelector(".view") || doc.body);
}

export function connectLive(streamUrl, { onFrame = () => {} } = {}) {
  if (typeof EventSource !== "function") return null;
  const source = new EventSource(streamUrl);
  source.addEventListener("runtime", (event) => {
    try {
      latest = JSON.parse(event.data);
    } catch {
      return; // ignore a malformed frame; the next one reconciles
    }
    onFrame(latest);
    applyLiveFrame(latest);
    listeners.forEach((fn) => fn(latest));
  });
  return source;
}
