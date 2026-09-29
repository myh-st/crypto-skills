import { createStore } from "./modules/state.js";
import { buildSeedData } from "./modules/demoData.js";
import { createRouter } from "./modules/router.js";
import { renderNav, NAV_ITEMS } from "./modules/components/nav.js";
import { createPositionDrawer } from "./modules/components/positionDetailDrawer.js";
import { runtimeService } from "./modules/services.js";
import { paperApi } from "./modules/paperApi.js";

import * as overview from "./modules/views/overview.js";
import * as portfolio from "./modules/views/portfolio.js";
import * as trade from "./modules/views/trade.js";
import * as activity from "./modules/views/activity.js";
import * as research from "./modules/views/research.js";
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
  portfolio: portfolio.render,
  trade: trade.render,
  activity: activity.render,
  research: research.render,
  position: overview.render,
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
// "runs" also needs to match "runs/:id"; "trade" matches "trade/:instrument"; "position" opens the drawer.
routeTables.runs = true;
routeTables.position = true;

async function detectPaperRuntime() {
  try {
    const health = await paperApi.health();
    return health?.service === "paper-futures" && health.execution_mode === "PAPER";
  } catch {
    return false;
  }
}

async function main() {
  const [runtime, paperRuntime] = await Promise.all([runtimeService.initialize(), detectPaperRuntime()]);
  const runtimeBadge = document.getElementById("runtime-mode");
  const runtimeMessage = document.getElementById("runtime-message");
  if (paperRuntime) {
    runtimeBadge.textContent = "PAPER";
    runtimeMessage.textContent = "PAPER execution · exchange data from the local backend · Gate live orders BLOCKED BY DESIGN · No real-money execution";
  } else if (runtime.mode === "live") {
    runtimeBadge.textContent = runtime.status?.model_configured ? "LIVE PAPER" : "SETUP";
    runtimeMessage.textContent = runtime.status?.model_configured
      ? "Binance Spot + server-side Luna · overview fixture cards remain synthetic · no trading"
      : runtime.status?.configuration_error
        || "Local runtime active · configure OPENAI_API_KEY on the server · no model request will be made";
  } else if (runtime.mode === "unavailable") {
    runtimeBadge.textContent = "RUNTIME OFFLINE";
    runtimeMessage.textContent = runtime.error || "Local API unavailable · analysis is disabled";
  }
  if (runtime.mode === "live") {
    try {
      store.setForwardEvaluations(await runtimeService.listEvaluations());
    } catch {
      store.setForwardEvaluations([]);
    }
  }

  const sidebar = document.getElementById("sidebar");
  const mobileNav = document.getElementById("mobile-nav");
  const content = document.getElementById("content");
  const mobileToggle = document.getElementById("mobile-nav-toggle");

  let currentRoute = "overview";
  let currentParams = [];
  let cleanup = null;
  let changeListeners = [];
  const notifyPortfolioChange = () => changeListeners.forEach((listener) => listener());
  const drawer = paperRuntime ? createPositionDrawer({ api: paperApi, onChange: notifyPortfolioChange }) : null;

  function renderCurrent() {
    if (typeof cleanup === "function") cleanup();
    cleanup = null;
    changeListeners = [];
    const renderer = routeRenderers[currentRoute] || overview.render;
    cleanup = renderer(content, {
      store,
      params: currentParams,
      paperRuntime,
      navigate: (path) => router.navigate(path),
      onPortfolioChange: (listener) => changeListeners.push(listener),
      notifyPortfolioChange,
    });
    content.scrollTop = 0;
    if (currentRoute === "position" && drawer && currentParams.length) {
      drawer.open(decodeURIComponent(currentParams.join("/")));
    }
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

  // Global delegation: any [data-open-position] opens the position manager drawer;
  // [data-route-to] navigates (used by attention items).
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-open-position]");
    if (opener && drawer && !opener.closest(".drawer")) {
      event.preventDefault();
      drawer.open(opener.dataset.openPosition, { replanIntent: opener.dataset.replanIntent || null });
      return;
    }
    const routeTo = event.target.closest("[data-route-to]");
    if (routeTo) {
      event.preventDefault();
      router.navigate(routeTo.dataset.routeTo);
    }
  });

  if (paperRuntime && typeof EventSource === "function") {
    // Runtime stream: attention counts for the nav badge and a nudge to refresh open views
    // when new journal events arrive. Prices are never announced to assistive technology.
    const stream = new EventSource(paperApi.runtimeStreamUrl());
    stream.addEventListener("runtime", (event) => {
      try {
        const payload = JSON.parse(event.data);
        const counts = payload.attention_counts || {};
        const urgent = (counts.CRITICAL || 0) + (counts.ACTION || 0);
        document.querySelectorAll("[data-nav-attention]").forEach((badge) => {
          badge.hidden = urgent === 0;
          badge.textContent = String(urgent);
          badge.setAttribute("aria-label", `${urgent} items need attention`);
        });
        if ((payload.activity || []).length) notifyPortfolioChange();
      } catch {
        // Ignore malformed frames; the next frame reconciles.
      }
    });
  }

  if (mobileToggle) {
    mobileToggle.addEventListener("click", () => {
      const open = document.body.classList.toggle("mobile-nav-open");
      mobileToggle.setAttribute("aria-expanded", String(open));
    });
  }

  document.body.classList.toggle("density-compact", store.getState().settings.compactDensity);
  document.body.classList.toggle("runtime-live", runtime.mode === "live" || paperRuntime);

  router.start();
}

document.addEventListener("DOMContentLoaded", main);
