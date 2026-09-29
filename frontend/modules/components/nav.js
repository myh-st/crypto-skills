// Persistent sidebar/top navigation. Daily trading destinations first; research tools
// (analysis runs, decisions, watchlist, data sources, the PAPER experiment lab) are grouped
// under Research so they support trading without dominating it.

export const PRIMARY_NAV = [
  { route: "overview", label: "Overview", icon: "◧" },
  { route: "today", label: "Today", icon: "☀" },
  { route: "experiments", label: "Experiments", icon: "⚗" },
  { route: "portfolio", label: "Portfolio", icon: "◔" },
  { route: "trade", label: "Trade", icon: "⇅" },
  { route: "activity", label: "Activity", icon: "≡" },
  { route: "research", label: "Research", icon: "⌕" },
  { route: "evaluations", label: "Evaluations", icon: "▦" },
  { route: "settings", label: "Settings", icon: "⚙" },
];

export const RESEARCH_NAV = [
  { route: "strategy-search", label: "Strategy Search", icon: "⌖" },
  { route: "new-analysis", label: "New Analysis", icon: "＋" },
  { route: "runs", label: "Runs", icon: "▤" },
  { route: "decisions", label: "Decisions", icon: "✓" },
  { route: "watchlist", label: "Watchlist", icon: "★" },
  { route: "data-sources", label: "Data Sources", icon: "⛁" },
  { route: "paper-trading", label: "Paper Trading Lab", icon: "▰" },
];

export const NAV_ITEMS = [...PRIMARY_NAV, ...RESEARCH_NAV];
const RESEARCH_ROUTES = new Set(RESEARCH_NAV.map(({ route }) => route));

function link({ route, label, icon }, activeRoute, { secondary = false } = {}) {
  const isActive = activeRoute === route || (route === "research" && RESEARCH_ROUTES.has(activeRoute));
  return `
    <a
      href="#/${route}"
      class="nav-link${isActive ? " nav-link--active" : ""}${secondary ? " nav-link--secondary" : ""}"
      aria-current="${activeRoute === route ? "page" : "false"}"
      data-route="${route}"
    >
      <span class="nav-icon" aria-hidden="true">${icon}</span>
      <span class="nav-label">${label}</span>
      ${route === "overview" ? '<span class="nav-badge" data-nav-attention hidden></span>' : ""}
    </a>
  `;
}

export function renderNav(activeRoute) {
  const showResearch = activeRoute === "research" || RESEARCH_ROUTES.has(activeRoute);
  return `
    <div class="sidebar-brand">
      <span class="brand-mark" aria-hidden="true">◆</span>
      <span class="brand-name">PORTFOLIO OS</span>
    </div>
    <nav class="sidebar-nav" aria-label="Primary destinations">
      ${PRIMARY_NAV.map((item) => link(item, activeRoute)).join("")}
    </nav>
    <details class="sidebar-group"${showResearch ? " open" : ""}>
      <summary>Research tools</summary>
      <nav class="sidebar-nav sidebar-nav--secondary" aria-label="Research tools">
        ${RESEARCH_NAV.map((item) => link(item, activeRoute, { secondary: true })).join("")}
      </nav>
    </details>
    <div class="sidebar-footer">
      <span class="demo-tag demo-tag--sidebar">PAPER only · Gate live writes blocked</span>
    </div>
  `;
}
