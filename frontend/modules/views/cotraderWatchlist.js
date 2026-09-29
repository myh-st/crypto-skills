// Co-Trader Watchlist: the coins the rule follows (add / remove), plus spot capital and sizing.
// Adding a coin is checked server-side against Gate spot listings (public data only); its daily
// history is fetched right away. No exchange account or API key is needed.
import { escapeHtml } from "../format.js";
import { coinIcon } from "../components/coinBook.js";
import { feedback, num } from "../components/ui.js";
import { skeleton } from "../components/motion.js";
import { renderCotraderSettingsForm } from "./cotraderHoldings.js";

export const MAX_COINS = 20;
const SYMBOL_RE = /^[A-Z0-9]{1,20}$/;

/** Normalise user input ("near", "NEAR_USDT", " sol ") to a base symbol, or null. */
export function normaliseSymbol(raw) {
  let base = String(raw || "").trim().toUpperCase().replace(/[\s/-]+/g, "");
  for (const suffix of ["_USDT", "USDT"]) {
    if (base.endsWith(suffix) && base.length > suffix.length) {
      base = base.slice(0, -suffix.length);
      break;
    }
  }
  return SYMBOL_RE.test(base) ? base : null;
}

export function renderWatchlist(universe, coinsByBase = {}) {
  const list = universe || [];
  if (!list.length) return '<p class="muted">No coins yet. Add one below.</p>';
  return `<ul class="cot-watch">${list.map((base) => {
    const coin = coinsByBase[base] || {};
    const state = coin.ladder?.state || coin.state || "—";
    return `<li class="cot-watch-row">
      ${coinIcon(base, { size: 28 })}
      <a class="cot-watch-name" href="#/cotrader/${encodeURIComponent(base)}"><strong>${escapeHtml(base)}</strong><span class="muted small">${escapeHtml(base)}_USDT</span></a>
      <span class="cot-watch-state small">${escapeHtml(state)}</span>
      <button type="button" class="btn btn--ghost btn--small" data-watch-remove="${escapeHtml(base)}" ${list.length <= 1 ? "disabled" : ""} aria-label="Remove ${escapeHtml(base)}">Remove</button>
    </li>`;
  }).join("")}</ul>`;
}

export function addedMessage(added) {
  if (!added) return "";
  const parts = [`เพิ่ม ${added.base} แล้ว`];
  if (added.thin) parts.push(`สภาพคล่องบน Gate ต่ำ (${num(added.quote_volume_24h_usdt) === null ? "ไม่ทราบ" : `$${Math.round(num(added.quote_volume_24h_usdt)).toLocaleString("en-US")}`}/วัน) ควรใช้ limit order`);
  if (added.history_ok === false) parts.push(`ประวัติราคามี ${added.daily_bars ?? 0} วัน (น้อยกว่า 1 ปี) หลักฐานย้อนหลังจะยังไม่แสดงผลสรุป`);
  if (added.fetch_error) parts.push(`ดึงราคาไม่สำเร็จ: ${added.fetch_error} (จะลองใหม่รอบถัดไป)`);
  return parts.join(" · ");
}

export function renderWatchlistShell() {
  return `<div class="cot-two">
    <section class="panel"><div class="section-heading"><h2>Watchlist</h2><span class="muted small" data-watch-count></span></div>
      <div data-watch-list>${skeleton(4)}</div>
      <form class="paper-form cot-watch-form" data-watch-add>
        <label class="ticket-field">Add a coin (Gate spot, USDT pair)
          <input name="coin" autocomplete="off" autocapitalize="characters" placeholder="e.g. SOL, LINK, PEPE" maxlength="24" required /></label>
        <div class="paper-inline-actions"><button type="submit" class="btn btn--primary btn--small">Add</button></div>
        <div data-watch-feedback role="status" aria-live="polite"></div>
      </form>
      <p class="small muted">ระบบเช็กว่าเหรียญมีคู่ USDT บน Gate Spot แล้วดึงกราฟรายวันให้ทันที · ยิ่งเหรียญเยอะ AI (Jev) ยิ่งใช้งบต่อวันมากขึ้นเล็กน้อย · สูงสุด ${MAX_COINS} เหรียญ</p>
    </section>
    <section class="panel"><div class="section-heading"><h2>Spot capital &amp; sizing</h2></div><div data-cot-settings>${skeleton(2)}</div></section>
  </div>`;
}

export function mountWatchlist(host, { api }) {
  let busy = false;
  const q = (sel) => host.querySelector(sel);

  async function refresh() {
    const [cot, settings] = await Promise.all([api.cotrader(), api.cotraderSettings()]);
    const s = settings.settings || settings;
    const coins = Object.fromEntries((cot.coins || []).map((c) => [String(c.base), c]));
    q("[data-watch-list]").innerHTML = renderWatchlist(s.universe, coins);
    q("[data-watch-count]").textContent = `${(s.universe || []).length} / ${MAX_COINS} coins`;
    const settingsHost = q("[data-cot-settings]");
    if (!settingsHost.querySelector("form")) settingsHost.innerHTML = renderCotraderSettingsForm(s);
  }

  async function onSubmit(event) {
    const form = event.target;
    if (form.matches("[data-watch-add]")) {
      event.preventDefault();
      if (busy) return;
      const fb = q("[data-watch-feedback]");
      const base = normaliseSymbol(new FormData(form).get("coin"));
      if (!base) return feedback(fb, "Type a coin symbol such as SOL or LINK.", "error");
      busy = true;
      feedback(fb, `Checking ${base}_USDT on Gate and loading its daily history…`, "info");
      try {
        const result = await api.cotraderWatchlist({ add: base });
        form.reset();
        feedback(fb, addedMessage(result.added), result.added?.thin || result.added?.history_ok === false ? "warning" : "success");
        await refresh();
      } catch (error) {
        feedback(fb, error.message, "error");
      } finally {
        busy = false;
      }
      return;
    }
    if (form.matches("[data-cot-settings-form]")) {
      event.preventDefault();
      const fb = form.querySelector("[data-cot-settings-feedback]");
      const data = new FormData(form);
      const raw = String(data.get("cotrader_capital_usdt") || "").trim();
      const capital = raw === "" ? null : num(raw);
      if (raw !== "" && (capital === null || capital <= 0)) return feedback(fb, "Capital must be a positive number or blank.", "error");
      try {
        await api.saveCotraderSettings({ cotrader_capital_usdt: capital, sizing_method: data.get("sizing_method") });
        feedback(fb, "Saved. Signals now show how much to buy or sell.", "success");
      } catch (error) {
        feedback(fb, error.message, "error");
      }
    }
  }

  async function onClick(event) {
    const button = event.target.closest("[data-watch-remove]");
    if (!button || busy) return;
    busy = true;
    button.disabled = true;
    try {
      await api.cotraderWatchlist({ remove: button.dataset.watchRemove });
      await refresh();
    } catch (error) {
      feedback(q("[data-watch-feedback]"), error.message, "error");
      button.disabled = false;
    } finally {
      busy = false;
    }
  }

  host.addEventListener("submit", onSubmit);
  host.addEventListener("click", onClick);
  return {
    refresh,
    dispose() {
      host.removeEventListener("submit", onSubmit);
      host.removeEventListener("click", onClick);
    },
  };
}
