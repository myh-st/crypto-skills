import { createStore } from "./modules/state.js";
import { buildSeedData } from "./modules/demoData.js";
import { createRouter } from "./modules/router.js";
import { renderNav, NAV_ITEMS } from "./modules/components/nav.js";
import { runtimeService } from "./modules/services.js";

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

async function main() {
  const runtime = await runtimeService.initialize();
  const runtimeBadge = document.getElementById("runtime-mode");
  const runtimeMessage = document.getElementById("runtime-message");
  if (runtime.mode === "live") {
    runtimeBadge.textContent = runtime.status?.model_configured ? "LIVE PAPER" : "SETUP";
    runtimeMessage.textContent = runtime.status?.model_configured
      ? "Binance Spot + server-side Luna · overview fixture cards remain synthetic · no trading"
      : runtime.status?.configuration_error
        || "Local runtime active · configure OPENAI_API_KEY on the server · no model request will be made";
    try {
      store.setForwardEvaluations(await runtimeService.listEvaluations());
    } catch {
      store.setForwardEvaluations([]);
    }
  } else if (runtime.mode === "unavailable") {
    runtimeBadge.textContent = "RUNTIME OFFLINE";
    runtimeMessage.textContent = runtime.error || "Local API unavailable · analysis is disabled";
  }

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
  document.body.classList.toggle("runtime-live", runtime.mode === "live");

  router.start();
}

document.addEventListener("DOMContentLoaded", main);
