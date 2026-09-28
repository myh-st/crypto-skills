import { createStore } from "./modules/state.js";
import { buildSeedData } from "./modules/demoData.js";
import { createRouter } from "./modules/router.js";
import { renderNav, NAV_ITEMS } from "./modules/components/nav.js";

import * as overview from "./modules/views/overview.js";
import * as newAnalysis from "./modules/views/newAnalysis.js";
import * as runs from "./modules/views/runs.js";
import * as runDetail from "./modules/views/runDetail.js";
import * as decisions from "./modules/views/decisions.js";
import * as watchlist from "./modules/views/watchlist.js";
import * as evaluations from "./modules/views/evaluations.js";
import * as dataSources from "./modules/views/dataSources.js";
import * as settings from "./modules/views/settings.js";
import * as paperTrading from "./modules/views/paperTrading.js";

const store = createStore(buildSeedData());

const routeRenderers = {
  overview: overview.render,
  "new-analysis": newAnalysis.render,
  runs: (root, ctx) => (ctx.params.length ? runDetail.render(root, ctx) : runs.render(root, ctx)),
  decisions: decisions.render,
  watchlist: watchlist.render,
  evaluations: evaluations.render,
  "data-sources": dataSources.render,
  settings: settings.render,
  "paper-trading": paperTrading.render,
};

const routeTables = Object.fromEntries(NAV_ITEMS.map(({ route }) => [route, true]));
// "runs" also needs to match "runs/:id" for the detail view.
routeTables.runs = true;

function main() {
  const sidebar = document.getElementById("sidebar");
  const mobileNav = document.getElementById("mobile-nav");
  const content = document.getElementById("content");
  const mobileToggle = document.getElementById("mobile-nav-toggle");

  let currentRoute = "overview";
  let currentParams = [];

  function renderCurrent() {
    const renderer = routeRenderers[currentRoute] || overview.render;
    renderer(content, {
      store,
      params: currentParams,
      navigate: (path) => router.navigate(path),
    });
    content.scrollTop = 0;
  }

  function renderChrome() {
    const navHtml = renderNav(currentRoute);
    sidebar.innerHTML = navHtml;
    mobileNav.innerHTML = navHtml;
  }

  const router = createRouter({
    routes: routeTables,
    defaultRoute: "overview",
    onRouteChange(route, params) {
      currentRoute = route;
      currentParams = params;
      renderChrome();
      renderCurrent();
      document.body.classList.remove("mobile-nav-open");
      mobileToggle?.setAttribute("aria-expanded", "false");
    },
  });

  store.subscribe(() => {
    renderCurrent();
  });

  if (mobileToggle) {
    mobileToggle.addEventListener("click", () => {
      const open = document.body.classList.toggle("mobile-nav-open");
      mobileToggle.setAttribute("aria-expanded", String(open));
    });
  }

  document.body.classList.toggle("density-compact", store.getState().settings.compactDensity);

  router.start();
}

document.addEventListener("DOMContentLoaded", main);
