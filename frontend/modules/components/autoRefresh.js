// Auto-refresh for a view: polls `run` every `intervalMs`, pauses while the tab is hidden, refreshes on
// return, and shows "updated Ns ago" plus a manual refresh button in `host`. Returns a dispose function.
import { escapeHtml } from "../format.js";

export function autoRefreshBar(intervalMs) {
  return `<div class="auto-refresh small muted" data-auto-refresh>
    <span class="auto-refresh-dot" aria-hidden="true"></span>
    <span data-auto-refresh-text>Loading…</span>
    <span class="auto-refresh-every">· next in <span data-auto-refresh-next>${escapeHtml(Math.round(intervalMs / 1000))}</span>s</span>
    <button type="button" class="btn btn--ghost btn--small" data-auto-refresh-now>Refresh now</button>
  </div>`;
}

export function startAutoRefresh(host, run, { intervalMs = 15000, doc = globalThis.document } = {}) {
  let lastOk = null;
  let lastError = null;
  let timer = null;
  let ticker = null;
  let inFlight = false;
  let disposed = false;
  const text = host?.querySelector("[data-auto-refresh-text]");
  const nextEl = host?.querySelector("[data-auto-refresh-next]");
  let lastRun = Date.now();
  const bar = host?.querySelector("[data-auto-refresh]") || host;

  function paint() {
    if (nextEl) nextEl.textContent = String(Math.max(0, Math.ceil((lastRun + intervalMs - Date.now()) / 1000)));
    if (!text) return;
    if (lastError) {
      text.textContent = `update failed: ${lastError}`;
      bar?.classList.add("auto-refresh--error");
      return;
    }
    bar?.classList.remove("auto-refresh--error");
    if (!lastOk) return;
    const s = Math.max(0, Math.round((Date.now() - lastOk) / 1000));
    text.textContent = s < 5 ? "updated just now" : `updated ${s}s ago`;
    bar?.classList.toggle("auto-refresh--stale", s * 1000 > intervalMs * 3);
  }

  async function tick() {
    if (disposed || inFlight || doc?.hidden) return;
    inFlight = true;
    lastRun = Date.now();
    bar?.classList.add("auto-refresh--busy");
    try {
      await run();
      lastOk = Date.now();
      lastError = null;
    } catch (error) {
      lastError = error?.message || String(error);
    } finally {
      inFlight = false;
      bar?.classList.remove("auto-refresh--busy");
      paint();
    }
  }

  const onVisible = () => {
    if (!doc?.hidden) tick();
  };
  const onClick = (event) => {
    if (event.target.closest("[data-auto-refresh-now]")) tick();
  };
  doc?.addEventListener?.("visibilitychange", onVisible);
  host?.addEventListener?.("click", onClick);
  tick();
  timer = setInterval(tick, intervalMs);
  ticker = setInterval(paint, 1000);
  return () => {
    disposed = true;
    clearInterval(timer);
    clearInterval(ticker);
    doc?.removeEventListener?.("visibilitychange", onVisible);
    host?.removeEventListener?.("click", onClick);
  };
}
