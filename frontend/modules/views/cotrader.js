// Spot AI Co-Trader: summary first, detail on demand. The deterministic trend rule says what to do
// (BUY / SELL / HOLD / WAIT / IN / OUT, with the trigger price and the size); Jev scores every coin
// daily and Luna explains on events or on demand, as advisory text only. Routes:
//   #/cotrader            regime strip, daily briefing, coin grid, AI scorecard
//   #/cotrader/watchlist  the coins the rule follows (add / remove), spot capital and sizing
//   #/cotrader/<BASE>     coin detail: chart, decision card, AI, journal, the rule's trade history
// PAPER decision support only: nothing here places, amends or cancels any order.
import { escapeHtml, relativeTime } from "../format.js";
import { paperApi } from "../paperApi.js";
import { autoRefreshBar, startAutoRefresh } from "../components/autoRefresh.js";
import { coinIcon } from "../components/coinBook.js";
import { confirmAction } from "../components/confirmDialog.js";
import { applyMotion, skeleton, startCountdowns } from "../components/motion.js";
import { feedback, num, pnl } from "../components/ui.js";
import { gauge } from "../components/charts.js";
import {
  DISCLAIMER, METHOD_LABELS, actionBadge, actionFor, cotSpark, fmtPct, fmtPrice, fmtQty, fmtUsdt, isFresh, isoDate,
  marketStanceChip, pctChange, sizingLine, stanceChip, stateChip, triggerText, unavailableHtml,
} from "../components/cotraderBits.js";
import {
  PRICING_FALLBACK_NOTE, latestAnalysis, renderAiCard, renderAnalysesList, renderJevPanel, renderJevRow, renderScorecard,
} from "../components/cotraderAi.js";
import { chartLevels, equitySummary, mountCotraderChart, mountEquityChart, priceChartSummary, toUnix } from "../components/cotraderChart.js";
import { cdcChip, ladderLevels, renderEvidence, renderLadderDetail, renderLadderRow } from "../components/cotraderLadder.js";
import { mountWatchlist, renderWatchlistShell } from "./cotraderWatchlist.js";
import { renderNextStepsTh } from "../components/cotraderSummary.js";

const REFRESH_MS = 60_000;
const THIN_COINS = new Set(["SEI", "ENA"]);
const REGIME_GLYPH = { RISK_ON: "▲", MIXED: "◆", RISK_OFF: "▼" };

// ---------------------------------------------------------------- overview (pure)

function usd(value, digits = 2) {
  const n = num(value);
  return n === null ? "—" : `$${n.toFixed(digits)}`;
}

/** Top strip: regime, breadth, BTC state, countdown to the next daily close, AI budget (Jev and Luna). */
export function renderRegimeStrip(d) {
  const r = d?.regime || {};
  const label = ["RISK_ON", "MIXED", "RISK_OFF"].includes(r.label) ? r.label : null;
  const hold = num(r.hold);
  const total = num(r.total);
  const breadth = hold !== null && total ? `${hold}/${total} in trend` : "—";
  const ai = d?.ai || {};
  const cap = num(ai.daily_cap_usd);
  const spent = num(ai.spent_today_usd);
  const left = cap !== null && spent !== null ? Math.max(0, cap - spent) : null;
  const totalLeft = num(ai.total_cap_usd) !== null && num(ai.spent_total_usd) !== null ? Math.max(0, num(ai.total_cap_usd) - num(ai.spent_total_usd)) : null;
  const star = ai.pricing_is_fallback ? "*" : "";
  let aiHtml;
  if (ai.enabled === false) aiHtml = "<strong>AI off</strong>";
  else if (ai.blocked_reason) aiHtml = `<strong class="warn-text"><span aria-hidden="true">⚠</span> AI unavailable</strong><small>${escapeHtml(ai.blocked_reason)}</small>`;
  else aiHtml = `<strong data-motion-key="cot:ai:left">${escapeHtml(usd(left))}${star} left today</strong><small>of ${escapeHtml(usd(cap))} · ${escapeHtml(usd(totalLeft))} of ${escapeHtml(usd(ai.total_cap_usd))} total left</small>`;
  const calls = (n) => (num(n) === null ? "—" : `${num(n)} ${num(n) === 1 ? "call" : "calls"}`);
  const split = [ai.jev_calls_today, ai.luna_calls_today, ai.jev_spent_usd, ai.luna_spent_usd].some((v) => v !== undefined && v !== null)
    ? `<small class="cot-ai-split"><span class="cot-source cot-source--jev">Jev</span> ${escapeHtml(calls(ai.jev_calls_today))} · ${escapeHtml(usd(ai.jev_spent_usd, 4))}${star}
        <span class="cot-source cot-source--luna">Luna</span> ${escapeHtml(calls(ai.luna_calls_today))} · ${escapeHtml(usd(ai.luna_spent_usd, 4))}${star}</small>`
    : "";
  return `<section class="cot-regime cot-regime--${label ? label.toLowerCase().replace("_", "-") : "none"}" aria-label="Market regime">
    <div class="cot-regime-cell cot-regime-main"><span class="cot-kicker">Regime</span>
      <strong class="cot-regime-label"><span aria-hidden="true">${REGIME_GLYPH[label] || "?"}</span> ${escapeHtml(label ? label.replace("_", "-") : "unknown")}</strong></div>
    <div class="cot-regime-cell"><span class="cot-kicker">Breadth</span><strong>${escapeHtml(breadth)}</strong>
      ${num(r.breadth) === null ? "" : gauge(num(r.breadth), { label: `breadth ${breadth}`, danger: 2, motionKey: "cot:breadth" })}</div>
    <div class="cot-regime-cell"><span class="cot-kicker">BTC</span>${stateChip(r.btc_state || null)}</div>
    <div class="cot-regime-cell"><span class="cot-kicker">Next daily close</span>
      <strong>${d?.next_close ? `<span data-countdown="${escapeHtml(d.next_close)}">${escapeHtml(relativeTime(d.next_close))}</span>` : "—"}</strong>
      <small>00:00 UTC = 07:00 Bangkok</small></div>
    <div class="cot-regime-cell"><span class="cot-kicker">AI budget</span>${aiHtml}${split}</div>
  </section>
  ${ai.pricing_is_fallback ? `<p class="cot-note small">* ${escapeHtml(PRICING_FALLBACK_NOTE)}</p>` : ""}`;
}

export function renderBriefing(briefing) {
  if (!briefing) {
    return `<section class="panel cot-briefing"><div class="section-heading"><h2>Daily AI briefing</h2>${marketStanceChip(null)}</div>
      <p class="muted">No briefing yet today. It runs once a day at about 00:10 UTC, after the daily close.</p></section>`;
  }
  const highlights = (briefing.highlights || []).slice(0, 5);
  return `<section class="panel cot-briefing">
    <div class="section-heading"><h2>Daily AI briefing</h2><span class="cot-chiprow">${marketStanceChip(briefing.stance_market)}
      <span class="muted small">${escapeHtml(briefing.as_of ? relativeTime(briefing.as_of) : "")}</span></span></div>
    ${briefing.summary_th ? `<p class="cot-thai" lang="th">${escapeHtml(briefing.summary_th)}</p>` : ""}
    ${highlights.length ? `<div class="cot-highlights">${highlights.map((h) => `<a class="cot-hl" href="#/cotrader/${encodeURIComponent(String(h.coin || ""))}">
      ${coinIcon(String(h.coin || "?"), { size: 18 })}<strong>${escapeHtml(h.coin)}</strong> <span lang="th">${escapeHtml(h.note)}</span></a>`).join("")}</div>` : ""}
  </section>`;
}

export function renderCoinCard(coin) {
  const base = String(coin.base || String(coin.symbol || "").replace(/_USDT$/, ""));
  const action = actionFor(coin);
  const fresh = isFresh(coin);
  const days = num(coin.days_in_state);
  const tone = { BUY: "buy", IN: "buy", SELL: "sell", OUT: "sell", HOLD: "hold", WAIT: "wait" }[action.type] || "wait";
  return `<a class="cot-card cot-card--${tone}${fresh ? " cot-card--fresh" : ""}" href="#/cotrader/${encodeURIComponent(base)}" data-cot-coin="${escapeHtml(base)}">
    <div class="cot-card-head">
      ${coinIcon(base, { size: 34 })}
      <div class="cot-card-name"><strong>${escapeHtml(base)}</strong><span class="muted small">${escapeHtml(coin.symbol || "")}</span></div>
      <div class="cot-card-price"><strong data-motion-key="cot:${escapeHtml(base)}:price">${escapeHtml(fmtPrice(coin.price))}</strong>
        <span class="small">${pctChange(coin.change_24h)} <span class="muted">24h</span></span></div>
    </div>
    <div class="cot-card-action">
      ${actionBadge(action.type)}
      <div class="cot-card-state">${stateChip(coin.state)}
        ${fresh ? '<span class="cot-fresh"><span class="cot-fresh-dot" aria-hidden="true"></span> NEW at today\'s close</span>' : ""}</div>
    </div>
    <p class="cot-card-text">${escapeHtml(action.text || "")}</p>
    <p class="cot-trigger"><span aria-hidden="true">⌖</span> ${escapeHtml(triggerText(action.trigger, coin.price))}</p>
    ${renderLadderRow(coin)}
    ${cotSpark(coin.spark, coin.trend_line_next, { label: `${base} 90-day closes` })}
    <div class="cot-card-foot small">
      <span>${escapeHtml(days === null ? "—" : `${days} ${days === 1 ? "day" : "days"}`)} in ${escapeHtml(coin.state || "—")}</span>
      <span>60d ${escapeHtml(fmtPct(coin.ret_60d))}</span>
      <span>vs SMA100 ${escapeHtml(fmtPct(coin.dist_sma100))}</span>
      ${stanceChip(coin.last_ai?.stance, coin.last_ai?.conviction)}
    </div>
    ${renderJevRow(coin.jev)}
  </a>`;
}

export function renderCoinGrid(coins) {
  if (!coins?.length) return '<p class="muted">No coins in the co-trader universe yet.</p>';
  // Fresh signals first, then actions that ask for a trade, then the rest in universe order.
  const rank = (c) => (isFresh(c) ? 0 : ["BUY", "SELL"].includes(actionFor(c).type) ? 1 : 2);
  const sorted = coins.map((c, i) => ({ c, i })).sort((a, b) => rank(a.c) - rank(b.c) || a.i - b.i).map(({ c }) => c);
  return `<div class="cot-grid">${sorted.map(renderCoinCard).join("")}</div>`;
}

export function renderCotraderOverview(d, { scorecard = null, scorecardError = null } = {}) {
  if (!d) return skeleton(5);
  return `
    ${renderNextStepsTh(d)}
    ${renderRegimeStrip(d)}
    ${renderBriefing(d.briefing)}
    <section class="cot-section">
      <div class="section-heading"><h2>Coins</h2><span class="muted small">rule signal on the last closed daily bar (${escapeHtml(isoDate(d.last_close))}); live price for display only</span></div>
      ${renderCoinGrid(d.coins)}
    </section>
    <section class="panel cot-scorecard">
      <div class="section-heading"><h2>AI Scorecard</h2><span class="muted small">does the AI add value?</span></div>
      ${scorecardError ? `<p class="muted">Scorecard unavailable: ${escapeHtml(scorecardError)}</p>` : renderScorecard(scorecard, { pricingFallback: Boolean(d.ai?.pricing_is_fallback) })}
    </section>`;
}

// ---------------------------------------------------------------- detail (pure)

/** Why the rule is in its state, in words. */
export function ruleReason(coin) {
  const ret = num(coin?.ret_60d);
  const dist = num(coin?.dist_sma100);
  if (ret === null || dist === null) return "Not enough closed daily bars to explain the state.";
  const retText = `60-day return ${fmtPct(ret)}`;
  const smaText = `close ${fmtPct(Math.abs(dist)).replace(/^[+−]/, "")} ${dist >= 0 ? "above" : "below"} the 100-day average`;
  if (coin.state === "HOLD") return `Both conditions hold: ${retText} and ${smaText}.`;
  if (coin.state === "CASH") return `Both conditions fail: ${retText} and ${smaText}.`;
  return `The conditions disagree (${retText}; ${smaText}), so the rule holds nothing until they confirm.`;
}

export function renderSizing(coin) {
  const s = coin?.sizing;
  const base = String(coin?.base || "");
  const line = sizingLine(s, base);
  if (line.kind === "unset") {
    return `<div class="cot-sizing cot-sizing--unset"><p><strong>Set your spot capital</strong> to see how much to buy or sell.</p>
      <p class="small"><a href="#/cotrader/watchlist">Watchlist › Spot capital &amp; sizing</a></p></div>`;
  }
  const target = num(s.target_usdt);
  const weight = num(s.weight);
  const risk = num(s.risk_to_trend_line_usdt);
  return `<div class="cot-sizing cot-sizing--${line.kind}">
    <p class="cot-sizing-line"><strong>${escapeHtml(line.text)}</strong></p>
    <dl class="kv kv--compact">
      <div><dt>Method</dt><dd>${escapeHtml(METHOD_LABELS[s.method] || s.method || "—")}${weight === null ? "" : ` · ${escapeHtml((weight * 100).toFixed(1))}%`}</dd></div>
      <div><dt>Target</dt><dd>${escapeHtml(fmtUsdt(target))}${target === 0 ? ' <span class="muted">(rule holds cash)</span>' : ""}</dd></div>
      <div><dt>You hold now</dt><dd>${escapeHtml(num(s.current_usdt) === null ? "unknown" : fmtUsdt(s.current_usdt))}</dd></div>
      <div><dt>Change</dt><dd>${pnl(s.delta_usdt)}${num(s.delta_qty) === null ? "" : ` <span class="muted">(${escapeHtml(num(s.delta_qty) > 0 ? "+" : num(s.delta_qty) < 0 ? "−" : "")}${escapeHtml(fmtQty(Math.abs(num(s.delta_qty))))} ${escapeHtml(base)})</span>`}</dd></div>
      ${risk !== null && target ? `<div><dt>Risk to trend line</dt><dd>${pnl(-Math.abs(risk))} <span class="muted">if it exits at ${escapeHtml(fmtPrice(coin.trend_line_next))}</span></dd></div>` : ""}
    </dl>
  </div>`;
}

export function renderHowNotes(coin) {
  const base = String(coin?.base || "");
  const scale = coin?.action?.scale_in ?? coin?.scale_in;
  const scaleText = typeof scale === "string" ? scale : num(scale) !== null ? `Scale in over ${num(scale)} days.` : null;
  return `<ul class="cot-how">
    <li>Decide on the <strong>daily close</strong> (00:00 UTC = 07:00 Bangkok), not on intraday wicks.</li>
    <li>Use <strong>limit orders</strong> near the price. Optionally scale in over 2–3 days.${scaleText ? ` <em>${escapeHtml(scaleText)}</em>` : ""}</li>
    <li>The exit is a <strong>daily close below the trend line</strong> — it is NOT an intraday stop.</li>
    <li>Costs: about 0.1% fee + 5 bps slippage per trade.</li>
    <li${THIN_COINS.has(base) ? ' class="cot-how-warn"' : ""}>SEI and ENA are thin on Gate — use limits${THIN_COINS.has(base) ? `, especially for ${escapeHtml(base)}` : ""}.</li>
  </ul>`;
}

/** The decision card: rule says / how much / how / AI says. */
export function renderDecisionCard(coin, { analysis = null, blockedReason = null, pricingFallback = false } = {}) {
  const action = actionFor(coin);
  return `<section class="panel cot-decision">
    <div class="cot-sub"><div class="cot-sub-head"><h3>Rule says</h3>${stateChip(coin.state)}</div>
      <div class="cot-rule">${actionBadge(action.type)}${isFresh(coin) ? '<span class="cot-fresh"><span class="cot-fresh-dot" aria-hidden="true"></span> NEW at today\'s close</span>' : ""}</div>
      <p class="cot-rule-text"><strong>${escapeHtml(action.text || "")}</strong></p>
      <p class="cot-trigger"><span aria-hidden="true">⌖</span> ${escapeHtml(triggerText(action.trigger, coin.price))}</p>
      <p class="muted small">${escapeHtml(ruleReason(coin))} ${num(coin.days_in_state) === null ? "" : `In ${escapeHtml(coin.state)} for ${escapeHtml(coin.days_in_state)} days (since ${escapeHtml(isoDate(coin.state_since))}).`}</p>
      ${renderLadderDetail(coin)}
      ${cdcChip(coin.cdc)}
    </div>
    <div class="cot-sub"><div class="cot-sub-head"><h3>How much</h3></div>${renderSizing(coin)}</div>
    <div class="cot-sub"><div class="cot-sub-head"><h3>How</h3></div>${renderHowNotes(coin)}</div>
    <div class="cot-sub">${renderAiCard(analysis, { blockedReason, lastAi: coin.last_ai, base: coin.base, pricingFallback })}</div>
    <p class="cot-disclaimer small">${escapeHtml(DISCLAIMER)}</p>
  </section>`;
}

/** Whether the rule agreed with a journal decision at the time (backend value wins). */
export function ruleAgreed(entry) {
  if (typeof entry?.rule_agreed === "boolean") return entry.rule_agreed;
  if (!entry?.rule_state) return null;
  const inTrend = entry.rule_state === "HOLD";
  if (["buy", "hold"].includes(entry.action)) return inTrend;
  if (["sell", "skip"].includes(entry.action)) return !inTrend;
  return null;
}

const JOURNAL_LABEL = { buy: "I bought", sell: "I sold", hold: "I held", skip: "I skipped" };

export function renderJournalList(entries) {
  const list = [...(entries || [])].sort((a, b) => (Date.parse(b.at) || 0) - (Date.parse(a.at) || 0));
  if (!list.length) return '<p class="muted small">No decisions journaled yet. Record what you actually did to compare your discretion with the rule.</p>';
  return `<ul class="cot-journal">${list.map((e) => {
    const agreed = ruleAgreed(e);
    const priced = num(e.price) !== null;
    return `<li class="cot-journal-row">
      <div class="cot-journal-head"><strong>${escapeHtml(JOURNAL_LABEL[e.action] || e.action)}</strong>
        <span class="muted small">${escapeHtml(isoDate(e.at))}</span>
        ${e.rule_state ? stateChip(e.rule_state, { withText: false }) : ""}
        ${agreed === null ? "" : agreed ? '<span class="cot-agree cot-agree--yes"><span aria-hidden="true">✓</span> rule agreed</span>' : '<span class="cot-agree cot-agree--no"><span aria-hidden="true">✗</span> against the rule</span>'}
        ${e.ai_stance ? stanceChip(e.ai_stance) : ""}</div>
      <div class="small">${priced ? `at ${escapeHtml(fmtPrice(e.price))}` : "no price recorded"}${num(e.amount_usdt) === null ? "" : ` · ${escapeHtml(fmtUsdt(e.amount_usdt))}`}
        · ${priced && num(e.price_now) !== null ? `now ${escapeHtml(fmtPrice(e.price_now))} ${pctChange(e.change_since)} since` : '<span class="muted">no outcome (no entry price)</span>'}</div>
      ${e.note ? `<p class="small cot-journal-note">${escapeHtml(e.note)}</p>` : ""}
    </li>`;
  }).join("")}</ul>`;
}

export function renderJournalForm(base, price = null) {
  return `<form class="paper-form cot-journal-form" data-cot-journal-form>
    <fieldset class="segmented segmented--full cot-journal-actions"><legend class="sr-only">What did you do?</legend>
      <label class="segmented-option segmented-option--pos"><input type="radio" name="action" value="buy" required /> Bought</label>
      <label class="segmented-option segmented-option--neg"><input type="radio" name="action" value="sell" /> Sold</label>
      <label class="segmented-option"><input type="radio" name="action" value="hold" /> Held</label>
      <label class="segmented-option"><input type="radio" name="action" value="skip" /> Skipped</label>
    </fieldset>
    <div class="paper-form-grid">
      <label class="ticket-field">Price (USDT, optional)<input name="price" type="number" inputmode="decimal" step="any" min="0" placeholder="${escapeHtml(num(price) === null ? "" : String(price))}" /></label>
      <label class="ticket-field">Amount (USDT, optional)<input name="amount_usdt" type="number" inputmode="decimal" step="any" min="0" /></label>
    </div>
    <label class="ticket-field">Note<textarea name="note" maxlength="500" rows="2" placeholder="Why? (max 500 characters)"></textarea></label>
    <div class="paper-inline-actions"><button type="submit" class="btn btn--primary btn--small">Record decision for ${escapeHtml(base)}</button></div>
    <div data-cot-journal-feedback role="status" aria-live="polite"></div>
  </form>`;
}

/** Validate and shape the journal body: {coin, action, price|null, amount_usdt|null, note}. */
export function journalEntry(base, data) {
  const action = String(data.action || "");
  if (!["buy", "sell", "hold", "skip"].includes(action)) return { ok: false, error: "Pick what you did: bought, sold, held or skipped." };
  const parse = (raw, label) => {
    const text = String(raw ?? "").trim();
    if (text === "") return { value: null };
    const value = num(text);
    if (value === null || value <= 0) return { error: `${label} must be a positive number or blank.` };
    return { value };
  };
  const price = parse(data.price, "Price");
  if (price.error) return { ok: false, error: price.error };
  const amount = parse(data.amount_usdt, "Amount");
  if (amount.error) return { ok: false, error: amount.error };
  return { ok: true, entry: { coin: base, action, price: price.value, amount_usdt: amount.value, note: String(data.note || "").slice(0, 500) } };
}

export function renderTradesTable(trades) {
  const list = [...(trades || [])].sort((a, b) => (toUnix(b.entry_at) ?? 0) - (toUnix(a.entry_at) ?? 0));
  if (!list.length) return '<p class="muted small">The rule has no round trips on this coin in the cached history.</p>';
  const closed = list.filter((t) => num(t.return_pct) !== null);
  const wins = closed.filter((t) => num(t.return_pct) > 0).length;
  return `<p class="small muted">${list.length} trades · ${closed.length ? `${wins}/${closed.length} winners` : "none closed"} · decided on daily closes, fees included by the backend</p>
    <div class="table-scroll"><table class="data-table cot-trades">
      <thead><tr><th scope="col">Entry</th><th scope="col">Entry price</th><th scope="col">Exit</th><th scope="col">Exit price</th><th scope="col">Return</th><th scope="col">Days</th></tr></thead>
      <tbody>${list.map((t) => {
        const open = t.exit_at === null || t.exit_at === undefined;
        return `<tr${open ? ' class="cot-trade--open"' : ""}>
          <td><span aria-hidden="true" class="pos-text">▲</span> ${escapeHtml(isoDate(t.entry_at))}</td>
          <td class="num">${escapeHtml(fmtPrice(t.entry_price))}</td>
          <td>${open ? '<span class="cot-open-tag">open</span>' : `<span aria-hidden="true" class="neg-text">▼</span> ${escapeHtml(isoDate(t.exit_at))}`}</td>
          <td class="num">${open ? "—" : escapeHtml(fmtPrice(t.exit_price))}</td>
          <td class="num">${pctChange(t.return_pct, { digits: 1 })}</td>
          <td class="num">${escapeHtml(num(t.days) ?? "—")}</td>
        </tr>`;
      }).join("")}</tbody>
    </table></div>`;
}

export function renderDetailHead(coin, holding = null) {
  const base = String(coin.base || "");
  const action = actionFor(coin);
  const held = holding && num(holding.qty) > 0
    ? `<span class="small">You hold ${escapeHtml(fmtQty(holding.qty))} ${escapeHtml(base)}${num(holding.avg_price) === null ? " (cost unknown)" : ` @ ${escapeHtml(fmtPrice(holding.avg_price))}`} · ${pnl(holding.unrealized_usdt)}</span>`
    : coin.held === false ? '<span class="small muted">You don\'t hold this coin.</span>' : "";
  return `<div class="cot-detail-head">
    <a class="cot-back small" href="#/cotrader"><span aria-hidden="true">←</span> All coins</a>
    <div class="cot-detail-title">
      ${coinIcon(base, { size: 44 })}
      <div><h1>${escapeHtml(base)} <span class="muted small">${escapeHtml(coin.symbol || "")}</span></h1>
        <div class="cot-detail-price"><strong data-motion-key="cot:${escapeHtml(base)}:price">${escapeHtml(fmtPrice(coin.price))}</strong> ${pctChange(coin.change_24h)} <span class="muted small">24h · live, display only</span></div>
        ${held}</div>
      <div class="cot-detail-action">${actionBadge(action.type)}${stateChip(coin.state)}</div>
    </div>
  </div>`;
}

// ---------------------------------------------------------------- pages

function shell(activeTab, body) {
  const tab = (id, href, label) => `<a href="${href}" class="cot-tab${activeTab === id ? " is-active" : ""}"${activeTab === id ? ' aria-current="page"' : ""}>${label}</a>`;
  return `<div class="view view--cotrader">
    <header class="page-header"><div><h1>Co-Trader</h1><p>Spot trend rule + AI second opinion · decide on the daily close</p></div>${autoRefreshBar(REFRESH_MS)}</header>
    <nav class="cot-tabs" aria-label="Co-Trader sections">${tab("signals", "#/cotrader", "Signals")}${tab("watchlist", "#/cotrader/watchlist", "Watchlist")}</nav>
    <p class="cot-disclaimer" role="note"><span aria-hidden="true">ⓘ</span> ${escapeHtml(DISCLAIMER)}</p>
    ${body}
  </div>`;
}

function renderOverviewPage(root, api) {
  root.innerHTML = shell("signals", `<div data-cot-body>${skeleton(6)}</div>`);
  const view = root.firstElementChild;
  const body = view.querySelector("[data-cot-body]");
  let loaded = false;
  return startAutoRefresh(view, async () => {
    const [cot, card] = await Promise.allSettled([api.cotrader(), api.cotraderScorecard()]);
    if (cot.status === "rejected") {
      if (!loaded) body.innerHTML = unavailableHtml(cot.reason);
      throw cot.reason;
    }
    loaded = true;
    body.innerHTML = renderCotraderOverview(cot.value, {
      scorecard: card.status === "fulfilled" ? card.value : null,
      scorecardError: card.status === "rejected" ? card.reason?.message || "request failed" : null,
    });
    applyMotion(view);
    startCountdowns();
  }, { intervalMs: REFRESH_MS });
}

function renderWatchlistPage(root, api) {
  root.innerHTML = shell("watchlist", `<div data-cot-watchlist>${renderWatchlistShell()}</div>`);
  const view = root.firstElementChild;
  const watchlist = mountWatchlist(view.querySelector("[data-cot-watchlist]"), { api });
  const stop = startAutoRefresh(view, watchlist.refresh, { intervalMs: REFRESH_MS });
  return () => {
    stop();
    watchlist.dispose();
  };
}

function renderDetailPage(root, api, base) {
  root.innerHTML = shell("signals", `
    <div data-cot-head>${skeleton(2)}</div>
    <div class="cot-detail">
      <div class="cot-detail-main">
        <section class="panel"><div class="section-heading"><h2>Daily chart</h2><span class="muted small">closed daily bars · trend line · ▲BUY / ▼SELL = the rule's trades</span></div>
          <div class="cot-chart" data-cot-chart role="img" aria-label="${escapeHtml(base)} daily candlestick chart"></div>
          <p class="chart-caption small" data-cot-chart-caption></p>
          <p class="cot-chart-legend small"><span class="cot-key cot-key--trend">── trend line</span> <span class="cot-key cot-key--sma">··· SMA100</span>
            <span class="cot-key cot-key--hold">▮ HOLD period</span> <span class="cot-key cot-key--avg">── your avg entry</span> <span class="cot-key cot-key--ai">- - AI key levels</span>
            <span class="cot-key cot-key--add">- - add above</span> <span class="cot-key cot-key--trim">- - trim below</span> <span class="cot-key cot-key--exit">- - exit below</span>
            <span class="cot-key">B/A/T/S = ladder buy starter / add / trim / sell all</span>
            <span class="cot-key cot-key--cdc"><i aria-hidden="true"></i> CDC (reference)</span></p></section>
        <section class="panel" data-cot-evidence>${skeleton(3)}</section>
        <section class="panel"><div class="section-heading"><h2>Rule vs buy &amp; hold</h2></div>
          <div class="cot-chart cot-chart--equity" data-cot-equity role="img" aria-label="Equity of the rule versus buy and hold"></div>
          <p class="chart-caption small" data-cot-equity-caption></p></section>
        <section class="panel"><div class="section-heading"><h2>The rule's trade history</h2></div><div data-cot-trades>${skeleton(3)}</div></section>
      </div>
      <div class="cot-detail-side">
        <div data-cot-decision>${skeleton(6)}</div>
        <section class="panel" data-cot-jev>${skeleton(2)}</section>
        <section class="panel"><div class="section-heading"><h2>AI analyses</h2><span class="muted small">by source</span></div><div data-cot-analyses>${skeleton(2)}</div></section>
        <section class="panel"><div class="section-heading"><h2>My decision journal</h2></div>
          <div data-cot-journal-form-host></div>
          <div data-cot-journal>${skeleton(2)}</div></section>
      </div>
    </div>`);
  const view = root.firstElementChild;
  const q = (sel) => view.querySelector(sel);
  const state = { detail: null, holding: null, analysis: null, blockedReason: null, chart: null, equity: null, formShown: false, asking: false, disposed: false };

  function paintDecision() {
    const d = state.detail;
    if (!d) return;
    const stored = latestAnalysis(d.analyses, "luna");
    const pick = state.analysis && (!stored || Date.parse(state.analysis.as_of) >= Date.parse(stored.as_of)) ? state.analysis : stored;
    q("[data-cot-decision]").innerHTML = renderDecisionCard(d, { analysis: pick, blockedReason: state.blockedReason, pricingFallback: Boolean(d.ai?.pricing_is_fallback) });
    return pick;
  }

  async function run() {
    // Holdings are tracked in the user's own exchange app; the co-trader only follows the watchlist.
    const [detail] = await Promise.allSettled([api.cotraderCoin(base)]);
    if (detail.status === "rejected") {
      if (!state.detail) q("[data-cot-head]").innerHTML = unavailableHtml(detail.reason);
      throw detail.reason;
    }
    const d = detail.value;
    state.detail = d;
    state.holding = null;
    q("[data-cot-head]").innerHTML = renderDetailHead(d, state.holding);
    const analysis = paintDecision();
    q("[data-cot-jev]").innerHTML = renderJevPanel(d.jev, d.analyses);
    q("[data-cot-analyses]").innerHTML = renderAnalysesList(d.analyses);
    q("[data-cot-trades]").innerHTML = renderTradesTable(d.trades);
    q("[data-cot-evidence]").innerHTML = renderEvidence(d.evidence);
    q("[data-cot-journal]").innerHTML = renderJournalList(d.journal);
    if (!state.formShown) {
      q("[data-cot-journal-form-host]").innerHTML = renderJournalForm(base, d.price);
      state.formShown = true;
    }
    const avgPrice = state.holding && num(state.holding.qty) > 0 ? state.holding.avg_price : null;
    const chartOpts = { avgPrice, keyLevels: analysis?.key_levels || null };
    q("[data-cot-chart-caption]").textContent = priceChartSummary(d, chartLevels({ trendLineNext: d.trend_line_next, ...chartOpts, ladder: ladderLevels(d) }));
    q("[data-cot-equity-caption]").textContent = equitySummary(d.equity, d.rule);
    if (!state.chart) {
      state.chart = mountCotraderChart(q("[data-cot-chart]"), d, chartOpts);
      state.equity = mountEquityChart(q("[data-cot-equity]"), d.equity);
    } else {
      (await state.chart).update(d, chartOpts);
      (await state.equity).update(d.equity);
    }
    applyMotion(view);
  }

  async function ask(button) {
    if (state.asking) return;
    state.asking = true;
    button.disabled = true;
    const say = (text) => {
      const fb = q("[data-cot-ask-feedback]");
      if (fb) fb.textContent = text;
    };
    say("Asking Luna…");
    try {
      let result;
      try {
        result = await api.cotraderAnalyze(base);
      } catch (error) {
        if (!error?.confirmation) throw error;
        const ok = await confirmAction({
          title: "Extra paid AI review",
          message: `You have used today's manual AI reviews for ${base} (3 per day). Another review is billed to the AI budget, about $0.20–0.30 at the current placeholder prices.`,
          confirmLabel: "Ask AI anyway",
          tone: "primary",
        });
        if (!ok) {
          say("Cancelled — nothing was billed.");
          return;
        }
        result = await api.cotraderAnalyze(base, { confirm: true });
      }
      if (result?.analysis) {
        state.analysis = result.analysis;
        state.blockedReason = null;
      } else {
        state.blockedReason = result?.blocked_reason || "the AI returned no analysis";
      }
      paintDecision();
      q("[data-cot-analyses]").innerHTML = renderAnalysesList([...(state.detail?.analyses || []), ...(result?.analysis ? [result.analysis] : [])]);
    } catch (error) {
      say(`AI request failed: ${error.message}`);
    } finally {
      state.asking = false;
      const again = q("[data-cot-ask]");
      if (again) again.disabled = false;
    }
  }

  async function onClick(event) {
    const button = event.target.closest("[data-cot-ask]");
    if (button) await ask(button);
  }

  async function onSubmit(event) {
    if (!event.target.matches("[data-cot-journal-form]")) return;
    event.preventDefault();
    const form = event.target;
    const fb = form.querySelector("[data-cot-journal-feedback]");
    const result = journalEntry(base, Object.fromEntries(new FormData(form).entries()));
    if (!result.ok) return feedback(fb, result.error, "error");
    try {
      await api.cotraderJournal(result.entry);
      form.reset();
      feedback(fb, "Recorded with the rule state and AI stance of this moment.", "success");
      await run();
    } catch (error) {
      feedback(fb, error.message, "error");
    }
  }

  view.addEventListener("click", onClick);
  view.addEventListener("submit", onSubmit);
  const stop = startAutoRefresh(view, run, { intervalMs: REFRESH_MS });
  return () => {
    state.disposed = true;
    stop();
    view.removeEventListener("click", onClick);
    view.removeEventListener("submit", onSubmit);
    Promise.resolve(state.chart).then((c) => c?.destroy());
    Promise.resolve(state.equity).then((c) => c?.destroy());
  };
}

export function render(root, ctx = {}, api = paperApi) {
  const param = ctx.params?.[0] ? decodeURIComponent(ctx.params[0]) : "";
  if (param === "watchlist" || param === "holdings") return renderWatchlistPage(root, api);
  if (param && /^[A-Za-z0-9]{1,20}$/.test(param)) return renderDetailPage(root, api, param.toUpperCase());
  return renderOverviewPage(root, api);
}
