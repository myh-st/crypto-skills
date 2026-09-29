// Research supports trading without dominating it: runs, decisions, watchlist, data sources,
// the PAPER experiment lab (engineering-level runtime detail), and the analysis workspace.
import { renderResearchOverview } from "./overview.js";

const LINKS = [
  ["strategy-search", "Strategy Search", "Day-trade futures: which coins, strategies, timeframes and leverage work"],
  ["new-analysis", "New Analysis", "Frozen, point-in-time analysis with the crypto skill"],
  ["runs", "Runs", "Every analysis run and its evidence"],
  ["decisions", "Decisions", "Saved decision records"],
  ["watchlist", "Watchlist", "Assets and trigger conditions"],
  ["data-sources", "Data Sources", "Where research data comes from"],
  ["paper-trading", "Paper Trading Lab", "Experiment configuration, cycles, routing, arms, raw events"],
];

export function render(root, ctx) {
  root.innerHTML = `<div class="view view--research">
    <header class="page-header"><div><h1>Research</h1><p>Evidence and experiments behind the trading decisions</p></div></header>
    <nav class="research-links" aria-label="Research tools">
      ${LINKS.map(([route, label, text]) => `<a class="research-link" href="#/${route}"><strong>${label}</strong><span>${text}</span></a>`).join("")}
    </nav>
    <div data-research-workspace></div></div>`;
  renderResearchOverview(root.querySelector("[data-research-workspace]"), ctx, { embedded: true });
  return undefined;
}
