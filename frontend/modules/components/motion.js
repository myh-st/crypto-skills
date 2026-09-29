// Motion for the trading console: numbers glide to their new value and flash up/down like a trading
// terminal, charts draw in on first sight, bars and gauges grow from their previous width, calendars fill
// in with a stagger, new attention items slide in, and countdowns tick. Views re-render their markup on
// every refresh, so motion is keyed: an element opts in with `data-motion-key="stable-id"` and the module
// remembers its last value between renders. Everything is skipped under prefers-reduced-motion.

const lastNumber = new Map();   // motion key -> last numeric value
const lastWidths = new Map();   // motion key -> [width%...]
const seenOnce = new Set();     // keys that already played an entrance
const NUM = /([+\-−]?)(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)/;
const FLASH_MS = 900;
const TWEEN_MS = 650;

export function reducedMotion(win = globalThis) {
  try {
    return Boolean(win.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
  } catch {
    return false;
  }
}

const easeOut = (t) => 1 - Math.pow(1 - t, 3);

// Parse the first number in a piece of display text, keeping how it was written.
export function parseDisplayNumber(text) {
  const m = NUM.exec(String(text ?? ""));
  if (!m) return null;
  const negative = m[1] === "-" || m[1] === "−";
  const digits = m[2];
  return {
    value: (negative ? -1 : 1) * Number(digits.replace(/,/g, "")),
    decimals: (digits.split(".")[1] || "").length,
    grouped: digits.includes(","),
    explicitSign: Boolean(m[1]),
    unicodeMinus: m[1] === "−",
    index: m.index,
    length: m[0].length,
  };
}

// Write a number back in the same style (sign, grouping, decimals) as the original text.
export function formatLike(value, style) {
  const abs = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: style.decimals, maximumFractionDigits: style.decimals, useGrouping: style.grouped,
  });
  if (value < 0) return `${style.unicodeMinus ? "−" : "-"}${abs}`;
  return `${style.explicitSign ? "+" : ""}${abs}`;
}

function numberTextNode(el) {
  const walker = el.ownerDocument.createTreeWalker(el, 4 /* NodeFilter.SHOW_TEXT */);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (NUM.test(node.nodeValue)) return node;
  }
  return null;
}

function tween(node, style, from, to, now, raf) {
  const text = node.nodeValue;
  const before = text.slice(0, style.index);
  const after = text.slice(style.index + style.length);
  const start = now();
  const step = () => {
    if (!node.isConnected) return;
    const k = Math.min(1, (now() - start) / TWEEN_MS);
    node.nodeValue = `${before}${formatLike(from + (to - from) * easeOut(k), style)}${after}`;
    if (k < 1) raf(step);
  };
  raf(step);
}

function flash(el, direction) {
  el.classList.remove("motion-up", "motion-down");
  void el.offsetWidth; // restart the animation
  el.classList.add(direction > 0 ? "motion-up" : "motion-down");
  setTimeout(() => el.classList.remove("motion-up", "motion-down"), FLASH_MS);
}

/**
 * Apply keyed motion inside `root` after it was (re-)rendered. Safe to call on every refresh.
 * Options exist for tests (injectable clock / frame scheduler / reduced-motion flag).
 */
export function applyMotion(root, { reduce = reducedMotion(), now = () => performance.now(), raf = (f) => requestAnimationFrame(f) } = {}) {
  if (!root?.querySelectorAll) return;
  // 1. numbers: glide + flash on change
  for (const el of root.querySelectorAll("[data-motion-key]")) {
    const key = el.dataset.motionKey;
    const node = numberTextNode(el);
    if (!node) continue;
    const style = parseDisplayNumber(node.nodeValue);
    if (!style) continue;
    const previous = lastNumber.get(key);
    lastNumber.set(key, style.value);
    if (previous === undefined || previous === style.value || reduce) continue;
    flash(el, style.value > previous ? 1 : -1);
    tween(node, style, previous, style.value, now, raf);
  }
  if (reduce) return;
  // 2. growing fills (bars, gauges, progress): animate from the previous width (or 0 the first time)
  for (const group of root.querySelectorAll("[data-motion-fill]")) {
    const key = group.dataset.motionFill;
    const fills = [...group.querySelectorAll(".bar-fill, .gauge-fill, .campaign-bar > span")];
    const targets = fills.map((f) => f.style.width);
    const previous = lastWidths.get(key) || targets.map(() => "0%");
    lastWidths.set(key, targets);
    fills.forEach((f, i) => {
      if (previous[i] === targets[i]) return;
      f.style.transition = "none";
      f.style.width = previous[i] ?? "0%";
      void f.offsetWidth;
      f.style.transition = "";
      f.style.width = targets[i];
    });
  }
  // 3. one-time entrances: chart lines draw in, calendars stagger, new list items slide in
  for (const el of root.querySelectorAll("[data-motion-enter]")) {
    const key = el.dataset.motionEnter;
    if (seenOnce.has(key)) continue;
    seenOnce.add(key);
    const line = el.querySelector?.(".chart-line");
    if (line?.getTotalLength) {
      const length = line.getTotalLength();
      line.style.strokeDasharray = `${length}`;
      line.style.strokeDashoffset = `${length}`;
      void line.getBoundingClientRect();
      line.style.transition = "stroke-dashoffset 1.1s cubic-bezier(.2,.7,.2,1)";
      line.style.strokeDashoffset = "0";
      setTimeout(() => { line.style.strokeDasharray = ""; line.style.strokeDashoffset = ""; line.style.transition = ""; }, 1300);
    }
    el.classList.add("motion-enter");
  }
}

/** Forget remembered values (tests, or a view that wants a fresh entrance). */
export function resetMotion() {
  lastNumber.clear(); lastWidths.clear(); seenOnce.clear();
}

// Live countdowns: `<span data-countdown="ISO">` shows "in 1h 02m 07s" and ticks every second.
export function countdownText(iso, nowMs = Date.now()) {
  const ms = new Date(iso).getTime() - nowMs;
  if (!Number.isFinite(ms)) return "—";
  if (ms <= 0) return "now";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h >= 48) return `in ${Math.floor(h / 24)}d ${h % 24}h ${String(m).padStart(2, "0")}m`;
  return h ? `in ${h}h ${String(m).padStart(2, "0")}m ${String(sec).padStart(2, "0")}s` : `in ${m}m ${String(sec).padStart(2, "0")}s`;
}

let ticker = null;
export function startCountdowns(doc = globalThis.document) {
  if (ticker || !doc) return;
  const tick = () => doc.querySelectorAll("[data-countdown]").forEach((el) => { el.textContent = countdownText(el.dataset.countdown); });
  tick();
  ticker = setInterval(tick, 1000);
}

// Shimmering placeholder while a view loads.
export function skeleton(rows = 3) {
  return `<div class="skeleton-block" aria-busy="true" aria-label="Loading">${Array.from({ length: rows }, (_, i) =>
    `<span class="skeleton" style="width:${[92, 76, 84, 64, 88][i % 5]}%"></span>`).join("")}</div>`;
}
