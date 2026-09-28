// Persistent sidebar/top navigation shared by every view. Highlights the
// active destination across the research and PAPER workflows.

export const NAV_ITEMS = [
  { route: "paper-trading", label: "Paper Trading", icon: "▰" },
  { route: "overview", label: "Overview", icon: "◧" },
  { route: "new-analysis", label: "New Analysis", icon: "＋" },
  { route: "runs", label: "Runs", icon: "▤" },
  { route: "decisions", label: "Decisions", icon: "✓" },
  { route: "watchlist", label: "Watchlist", icon: "★" },
  { route: "evaluations", label: "Evaluations", icon: "▦" },
  { route: "data-sources", label: "Data Sources", icon: "⛁" },
  { route: "settings", label: "Settings", icon: "⚙" },
];

export function renderNav(activeRoute) {
  const links = NAV_ITEMS
    .map(({ route, label, icon }) => {
      const isActive = activeRoute === route;
      return `
        <a
          href="#/${route}"
          class="nav-link${isActive ? " nav-link--active" : ""}"
          aria-current="${isActive ? "page" : "false"}"
          data-route="${route}"
        >
          <span class="nav-icon" aria-hidden="true">${icon}</span>
          <span class="nav-label">${label}</span>
        </a>
      `;
    })
    .join("");

  return `
    <div class="sidebar-brand">
      <span class="brand-mark" aria-hidden="true">◆</span>
      <span class="brand-name">CRYPTO RESEARCH</span>
    </div>
    <nav class="sidebar-nav" aria-label="Primary destinations">${links}</nav>
    <div class="sidebar-footer">
      <span class="demo-tag demo-tag--sidebar">${activeRoute === "paper-trading" ? "PAPER only" : "Fixture mode"}</span>
    </div>
  `;
}
