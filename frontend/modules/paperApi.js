const API_PREFIX = "/api";

export function createPaperApi(fetcher = globalThis.fetch) {
  async function request(path, options = {}) {
    if (typeof fetcher !== "function") {
      throw new Error("Paper runtime fetch is unavailable");
    }
    const headers = new Headers(options.headers || {});
    const init = {
      ...options,
      credentials: "same-origin",
      cache: "no-store",
      headers,
    };
    if (options.body !== undefined && !(options.body instanceof Blob)) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(options.body);
    }
    const response = await fetcher(`${API_PREFIX}${path}`, init);
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        const payload = await response.json();
        if (typeof payload.error === "string") message = payload.error;
      } catch {
        // Keep network failure details bounded and credential-free.
      }
      throw new Error(message);
    }
    return response;
  }

  return Object.freeze({
    async health() {
      return (await request("/health")).json();
    },
    async experiment() {
      return (await request("/experiment")).json();
    },
    async saveExperiment(config) {
      return (await request("/experiment", { method: "POST", body: config })).json();
    },
    async providers() {
      return (await request("/providers")).json();
    },
    async saveProvider(provider) {
      return (await request("/providers", { method: "POST", body: provider })).json();
    },
    async testProvider(providerId) {
      return (
        await request(`/providers/${encodeURIComponent(providerId)}/test`, {
          method: "POST",
          body: {},
        })
      ).json();
    },
    async dashboard() {
      return (await request("/dashboard")).json();
    },
    async evaluation() {
      return (await request("/evaluation")).json();
    },
    async runtime(action) {
      if (!["start", "pause", "resume", "stop"].includes(action)) {
        throw new Error("Unsupported runtime action");
      }
      return (await request(`/runtime/${action}`, { method: "POST", body: {} })).json();
    },
    async runCycle(symbol = null) {
      return (
        await request("/runtime/cycle", {
          method: "POST",
          body: symbol ? { symbol, manual: true } : { manual: true },
        })
      ).json();
    },
    async testMarketData(mode) {
      return (
        await request("/market-data/test", {
          method: "POST",
          body: { mode },
        })
      ).json();
    },
    async reducePosition(positionId, fraction = 1) {
      return (
        await request(`/positions/${encodeURIComponent(positionId)}/reduce`, {
          method: "POST",
          body: { fraction },
        })
      ).json();
    },
    async downloadExport() {
      return (await request("/export")).blob();
    },
  });
}

export const paperApi = createPaperApi();
