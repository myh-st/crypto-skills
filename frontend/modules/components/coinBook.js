// Book by coin: one row per coin instead of one row per position. Shows the coin's symbol, the net
// long/short exposure across every sleeve, which sleeves hold it, the combined P&L, and the live
// price. Values update from the runtime stream through the motion keys (coin:<SYMBOL>:...).
import { escapeHtml } from "../format.js";
import { pnl, price } from "./ui.js";

// Brand-inspired colours and glyphs for the watchlist; any other coin gets a stable colour from its name.
const COINS = {
  BTC: { color: "#F7931A", glyph: "₿", name: "Bitcoin" },
  ETH: { color: "#627EEA", glyph: "Ξ", name: "Ethereum" },
  SOL: { color: "#9945FF", glyph: "◎", name: "Solana" },
  NEAR: { color: "#00C08B", glyph: "Ⓝ", name: "NEAR" },
  SUI: { color: "#4DA2FF", glyph: "SUI", name: "Sui" },
  SEI: { color: "#9B1C2E", glyph: "SEI", name: "Sei" },
  AVAX: { color: "#E84142", glyph: "AV", name: "Avalanche" },
  ENA: { color: "#1F2937", glyph: "EN", name: "Ethena" },
  XRP: { color: "#23292F", glyph: "✕", name: "XRP" },
  DOGE: { color: "#C2A633", glyph: "Ð", name: "Dogecoin" },
  BNB: { color: "#F0B90B", glyph: "B", name: "BNB" },
};
const SLEEVES = { "sleeve-don": "DON", "sleeve-ts": "TS", "sleeve-xs": "XS", primary: "BRK" };

function hue(text) {
  let h = 0;
  for (const ch of text) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

export function coinBase(position) {
  return String(position.base || position.symbol || "").replace(/USDT$/, "").replace(/_USDT$/, "").toUpperCase();
}

export function coinIcon(base, { size = 28 } = {}) {
  const info = COINS[base] || { color: `hsl(${hue(base)} 55% 42%)`, glyph: base.slice(0, 3), name: base };
  const glyph = info.glyph.slice(0, 3);
  return `<span class="coin-icon" style="--coin:${escapeHtml(info.color)};--size:${size}px" role="img" aria-label="${escapeHtml(info.name)}">`
    + `<span class="coin-glyph${glyph.length > 1 ? " coin-glyph--text" : ""}" aria-hidden="true">${escapeHtml(glyph)}</span></span>`;
}

/** Aggregate open perpetual positions per coin (pure; exported for tests). */
export function aggregateByCoin(positions) {
  const coins = new Map();
  for (const p of positions || []) {
    if (p.status !== "open" || p.market_type !== "perpetual") continue;
    const base = coinBase(p);
    const sign = p.side === "long" ? 1 : -1;
    const notional = Math.abs(Number(p.notional_usdt) || 0);
    const row = coins.get(base) || { base, symbol: p.symbol, net: 0, gross: 0, pnl: 0, price: null, legs: [] };
    row.net += sign * notional;
    row.gross += notional;
    row.pnl += Number(p.unrealized_pnl_usdt) || 0;
    row.price = Number(p.live_price ?? p.mark_price) || row.price;
    row.legs.push({ sleeve: SLEEVES[p.cohort] || String(p.cohort || "").toUpperCase(), side: p.side, notional, leverage: p.leverage });
    coins.set(base, row);
  }
  return [...coins.values()].sort((a, b) => b.gross - a.gross);
}

export function renderCoinBook(positions) {
  const rows = aggregateByCoin(positions);
  if (!rows.length) return '<p class="muted small">No open perpetual positions.</p>';
  const netLong = rows.reduce((s, r) => s + Math.max(0, r.net), 0);
  const netShort = rows.reduce((s, r) => s + Math.min(0, r.net), 0);
  return `
    <p class="coin-book-summary small muted">${rows.length} coins · net long <strong class="pos-text">${escapeHtml(netLong.toFixed(0))}</strong>
      · net short <strong class="neg-text">${escapeHtml(Math.abs(netShort).toFixed(0))}</strong> USDT</p>
    <ul class="coin-book">${rows.map((r) => {
      const dir = r.net > 0.5 ? "long" : r.net < -0.5 ? "short" : "flat";
      return `<li class="coin-row coin-row--${dir}">
        ${coinIcon(r.base)}
        <div class="coin-main">
          <div class="coin-title"><strong>${escapeHtml(r.base)}</strong>
            <span class="coin-price" data-motion-key="coin:${escapeHtml(r.symbol)}:price">${escapeHtml(price(r.price))}</span></div>
          <div class="coin-legs">${r.legs.map((l) => `<span class="coin-leg coin-leg--${escapeHtml(l.side)}" title="${escapeHtml(l.sleeve)} sleeve · ${escapeHtml(l.side)} ${escapeHtml(l.leverage ?? "")}x · ${escapeHtml(l.notional.toFixed(2))} USDT">${escapeHtml(l.sleeve)} ${l.side === "long" ? "▲" : "▼"} ${escapeHtml(l.notional.toFixed(0))}</span>`).join("")}</div>
        </div>
        <div class="coin-net">
          <span class="coin-dir">${dir === "long" ? "NET LONG" : dir === "short" ? "NET SHORT" : "HEDGED"}</span>
          <strong>${escapeHtml(Math.abs(r.net).toFixed(0))} USDT</strong>
          <span data-motion-key="coin:${escapeHtml(r.symbol)}:pnl">${pnl(r.pnl)}</span>
        </div>
      </li>`;
    }).join("")}</ul>`;
}
