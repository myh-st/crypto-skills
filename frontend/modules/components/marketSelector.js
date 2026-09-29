// Exchange-backed instrument selector (Gate Spot / Perpetual). Results come from the server's
// normalized catalog; favorites and recent picks are a per-browser convenience only.
import { escapeHtml } from "../format.js";
import { fmtNumber, pct, price } from "./ui.js";

const STORAGE_KEY = "portfolio-os.market-selector.v1";

export function loadPicks() {
  try {
    const value = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
    return { favorites: Array.isArray(value.favorites) ? value.favorites : [], recent: Array.isArray(value.recent) ? value.recent : [] };
  } catch {
    return { favorites: [], recent: [] };
  }
}

function savePicks(picks) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(picks));
  } catch {
    // Storage may be unavailable (private mode); selection still works.
  }
}

export function rememberInstrument(instrument) {
  const picks = loadPicks();
  const entry = { instrument_id: instrument.instrument_id, display_symbol: instrument.display_symbol, market_type: instrument.market_type };
  picks.recent = [entry, ...picks.recent.filter((item) => item.instrument_id !== entry.instrument_id)].slice(0, 8);
  savePicks(picks);
}

export function toggleFavorite(instrument) {
  const picks = loadPicks();
  const exists = picks.favorites.some((item) => item.instrument_id === instrument.instrument_id);
  picks.favorites = exists
    ? picks.favorites.filter((item) => item.instrument_id !== instrument.instrument_id)
    : [...picks.favorites, { instrument_id: instrument.instrument_id, display_symbol: instrument.display_symbol, market_type: instrument.market_type }].slice(0, 20);
  savePicks(picks);
  return !exists;
}

export function renderInstrumentRows(instruments, favorites = []) {
  const favoriteIds = new Set(favorites.map((item) => item.instrument_id));
  if (!instruments.length) return '<li class="market-option market-option--empty" role="option" aria-disabled="true">No matching exchange instruments.</li>';
  return instruments.map((item) => `
    <li class="market-option${item.tradable ? "" : " is-disabled"}" role="option" id="opt-${escapeHtml(item.instrument_id.replaceAll(":", "-"))}"
      data-instrument='${escapeHtml(JSON.stringify({ instrument_id: item.instrument_id, display_symbol: item.display_symbol, market_type: item.market_type }))}'
      aria-disabled="${item.tradable ? "false" : "true"}" aria-selected="false">
      <button type="button" class="fav-toggle" data-fav="${escapeHtml(item.instrument_id)}" aria-label="${favoriteIds.has(item.instrument_id) ? "Remove" : "Add"} ${escapeHtml(item.display_symbol)} ${favoriteIds.has(item.instrument_id) ? "from" : "to"} favorites" aria-pressed="${favoriteIds.has(item.instrument_id)}">${favoriteIds.has(item.instrument_id) ? "★" : "☆"}</button>
      <span class="market-option-symbol"><strong>${escapeHtml(item.display_symbol)}</strong><small>${escapeHtml(item.base)} / ${escapeHtml(item.quote)}</small></span>
      <span>${escapeHtml(price(item.last_price))}</span>
      <span class="${(item.change_24h ?? 0) >= 0 ? "text-pos" : "text-neg"}">${item.change_24h == null ? "—" : `${item.change_24h >= 0 ? "▲" : "▼"} ${escapeHtml(pct(item.change_24h, { signed: true }))}`}</span>
      <span title="24h quote volume">${escapeHtml(fmtNumber(item.volume_24h_quote, { compact: true }))}</span>
      <span title="Spread">${item.spread_bps == null ? "—" : `${escapeHtml(Number(item.spread_bps).toFixed(1))} bps`}</span>
      <span class="status-tag status-tag--${item.tradable ? "ok" : "bad"}">${escapeHtml(item.tradable ? item.liquidity : item.status)}</span>
    </li>`).join("");
}

export function mountMarketSelector(host, { api, marketType = "perpetual", selected = null, onSelect }) {
  const state = { marketType, query: "", results: [], timer: null, active: -1, freshness: null };
  host.innerHTML = `
    <div class="market-selector">
      <div class="segmented" role="group" aria-label="Market type">
        <button type="button" data-market-type="spot" aria-pressed="${marketType === "spot"}">Spot</button>
        <button type="button" data-market-type="perpetual" aria-pressed="${marketType === "perpetual"}">Perpetual</button>
      </div>
      <div class="market-combobox">
        <label class="sr-only" for="market-search">Search exchange instruments</label>
        <input id="market-search" type="search" autocomplete="off" role="combobox" aria-expanded="false" aria-controls="market-results"
          aria-autocomplete="list" placeholder="${escapeHtml(selected?.display_symbol || "Search Gate instruments…")}" />
        <ul id="market-results" class="market-results" role="listbox" hidden></ul>
      </div>
      <div class="market-picks" data-market-picks></div>
    </div>`;
  const input = host.querySelector("#market-search");
  const list = host.querySelector("#market-results");
  const picksHost = host.querySelector("[data-market-picks]");

  function renderPicks() {
    const picks = loadPicks();
    const favorites = picks.favorites.filter((item) => item.market_type === state.marketType);
    const recent = picks.recent.filter((item) => item.market_type === state.marketType && !favorites.some((f) => f.instrument_id === item.instrument_id));
    const chip = (item, kind) => `<button type="button" class="chip" data-pick='${escapeHtml(JSON.stringify(item))}' aria-label="${kind} ${escapeHtml(item.display_symbol)}">${kind === "Favorite" ? "★ " : ""}${escapeHtml(item.display_symbol)}</button>`;
    picksHost.innerHTML = [...favorites.map((item) => chip(item, "Favorite")), ...recent.slice(0, 5).map((item) => chip(item, "Recent"))].join("");
  }

  async function search() {
    try {
      const payload = await api.markets({ marketType: state.marketType, q: state.query, limit: 40 });
      state.results = payload.instruments || [];
      state.freshness = payload.freshness;
      list.innerHTML = renderInstrumentRows(state.results, loadPicks().favorites) +
        `<li class="market-option market-option--meta" role="presentation">${escapeHtml(payload.data_origin)} · ${escapeHtml(payload.total)} instruments${payload.freshness?.stale ? " · STALE metadata" : ""}</li>`;
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      state.active = -1;
    } catch (error) {
      list.innerHTML = `<li class="market-option market-option--empty" role="option" aria-disabled="true">${escapeHtml(error.message)}</li>`;
      list.hidden = false;
    }
  }

  function choose(item) {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.value = "";
    input.placeholder = item.display_symbol;
    rememberInstrument(item);
    renderPicks();
    onSelect(item);
  }

  function highlight(index) {
    const options = [...list.querySelectorAll(".market-option[data-instrument]")];
    if (!options.length) return;
    state.active = (index + options.length) % options.length;
    options.forEach((option, i) => option.setAttribute("aria-selected", String(i === state.active)));
    input.setAttribute("aria-activedescendant", options[state.active].id);
    options[state.active].scrollIntoView({ block: "nearest" });
  }

  input.addEventListener("input", () => {
    state.query = input.value.trim();
    clearTimeout(state.timer);
    state.timer = setTimeout(search, 220);
  });
  input.addEventListener("focus", () => { if (list.hidden) search(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") { event.preventDefault(); highlight(state.active + 1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); highlight(state.active - 1); }
    else if (event.key === "Enter") {
      const option = list.querySelectorAll(".market-option[data-instrument]")[state.active];
      if (option && option.getAttribute("aria-disabled") !== "true") { event.preventDefault(); choose(JSON.parse(option.dataset.instrument)); }
    } else if (event.key === "Escape") { list.hidden = true; input.setAttribute("aria-expanded", "false"); }
  });
  host.addEventListener("click", (event) => {
    const fav = event.target.closest("[data-fav]");
    if (fav) {
      const option = fav.closest("[data-instrument]");
      const on = toggleFavorite(JSON.parse(option.dataset.instrument));
      fav.textContent = on ? "★" : "☆";
      fav.setAttribute("aria-pressed", String(on));
      renderPicks();
      return;
    }
    const option = event.target.closest(".market-option[data-instrument]");
    if (option && option.getAttribute("aria-disabled") !== "true") return choose(JSON.parse(option.dataset.instrument));
    const pick = event.target.closest("[data-pick]");
    if (pick) return choose(JSON.parse(pick.dataset.pick));
    const toggle = event.target.closest("[data-market-type]");
    if (toggle && toggle.dataset.marketType !== state.marketType) {
      state.marketType = toggle.dataset.marketType;
      host.querySelectorAll("[data-market-type]").forEach((button) => button.setAttribute("aria-pressed", String(button === toggle)));
      renderPicks();
      search();
    }
    return undefined;
  });
  document.addEventListener("click", (event) => {
    if (!host.contains(event.target)) { list.hidden = true; input.setAttribute("aria-expanded", "false"); }
  });
  renderPicks();
  return { get marketType() { return state.marketType; } };
}
