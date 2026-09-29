// Minimal hash-based router. No history API dependency, works from a static
// file server with no build step.

export function createRouter({ routes, defaultRoute, onRouteChange }) {
  function parseHash() {
    // "#/cotrader/NEAR?view=full": the query part is view state (filters) read by the view itself
    // via hashQuery(); it never changes which route renders.
    const raw = window.location.hash.replace(/^#\/?/, "").split("?")[0];
    const [route, ...rest] = raw.split("/").filter(Boolean);
    return { route: route || defaultRoute, params: rest };
  }

  function resolve() {
    const { route, params } = parseHash();
    const handler = routes[route] || routes[defaultRoute];
    const resolvedRoute = routes[route] ? route : defaultRoute;
    onRouteChange(resolvedRoute, params, handler);
  }

  window.addEventListener("hashchange", resolve);

  return {
    start() {
      if (!window.location.hash) {
        window.location.hash = `#/${defaultRoute}`;
      }
      resolve();
    },
    navigate(path) {
      window.location.hash = `#/${path}`;
    },
  };
}

/** URL-shareable view state from the hash query ("#/route?view=full" -> URLSearchParams). */
export function hashQuery(hash = globalThis.location?.hash || "") {
  const index = hash.indexOf("?");
  return new URLSearchParams(index === -1 ? "" : hash.slice(index + 1));
}
