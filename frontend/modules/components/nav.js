// Persistent sidebar/top navigation. Daily trading destinations first; research tools
// (analysis runs, decisions, watchlist, data sources, the PAPER experiment lab) are grouped
// under Research so they support trading without dominating it.

export const PRIMARY_NAV = [
  { route: "overview", label: "Overview", icon: "◧" },
  { route: "cotrader", label: "Co-Trader", icon: "◎" },
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

// Spot Co-Trader server (`paper-server --cotrader`): only what a manual spot trader needs.
// The futures PAPER lab stays reachable under a collapsed "Futures lab" group.
export const COTRADER_NAV = [
  { route: "cotrader", sub: "", label: "Signals", icon: "◎" },
  { route: "cotrader", sub: "watchlist", label: "Watchlist", icon: "★" },
  { route: "settings", sub: "", label: "AI · Settings", icon: "⚙" },
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

function cotraderLink({ route, sub, label, icon }, activeRoute, params) {
  const activeSub = activeRoute === "cotrader" && params?.[0] === "watchlist" ? "watchlist" : "";
  const isActive = activeRoute === route && (route !== "cotrader" || activeSub === sub);
  const href = sub ? `#/${route}/${sub}` : `#/${route}`;
  return `<a href="${href}" class="nav-link${isActive ? " nav-link--active" : ""}" aria-current="${isActive ? "page" : "false"}" data-route="${route}">
      <span class="nav-icon" aria-hidden="true">${icon}</span><span class="nav-label">${label}</span></a>`;
}

export function renderCotraderNav(activeRoute, params = []) {
  const inLab = activeRoute !== "cotrader" && activeRoute !== "settings";
  return `
    <div class="sidebar-brand">
      <span class="brand-mark" aria-hidden="true">◎</span>
      <span class="brand-name">SPOT CO-TRADER</span>
    </div>
    <nav class="sidebar-nav" aria-label="Primary destinations">
      ${COTRADER_NAV.map((item) => cotraderLink(item, activeRoute, params)).join("")}
    </nav>
    <details class="sidebar-group sidebar-group--lab"${inLab ? " open" : ""}>
      <summary>Futures lab (old)</summary>
      <nav class="sidebar-nav sidebar-nav--secondary" aria-label="Futures lab">
        ${[...PRIMARY_NAV.filter(({ route }) => !["cotrader", "settings"].includes(route)), ...RESEARCH_NAV]
          .map((item) => link(item, activeRoute, { secondary: true })).join("")}
      </nav>
    </details>
    <div class="sidebar-footer">
      <span class="demo-tag demo-tag--sidebar">Decision support only · you trade manually</span>
    </div>
  `;
}

export function renderNav(activeRoute, { mode = "lab", params = [] } = {}) {
  if (mode === "cotrader") return renderCotraderNav(activeRoute, params);
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
