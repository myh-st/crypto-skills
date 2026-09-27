// Minimal hash-based router. No history API dependency, works from a static
// file server with no build step.

export function createRouter({ routes, defaultRoute, onRouteChange }) {
  function parseHash() {
    const raw = window.location.hash.replace(/^#\/?/, "");
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
