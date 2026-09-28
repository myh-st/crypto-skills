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
    // Credentials are write-only: the server stores them in the OS credential store and
    // answers with masked metadata. Nothing is cached or persisted in the browser.
    async saveProviderSecret(providerId, value) {
      return (
        await request(`/providers/${encodeURIComponent(providerId)}/secret`, {
          method: "POST",
          body: { value },
        })
      ).json();
    },
    async deleteProviderSecret(providerId) {
      return (
        await request(`/providers/${encodeURIComponent(providerId)}/secret/delete`, {
          method: "POST",
          body: {},
        })
      ).json();
    },
    async secretStore() {
      return (await request("/secret-store")).json();
    },
    async cost() {
      return (await request("/cost")).json();
    },
    async economics() {
      return (await request("/economics")).json();
    },
    async addPrice(entry) {
      return (await request("/price-book", { method: "POST", body: entry })).json();
    },
    async saveCostControls(controls) {
      return (await request("/cost-controls", { method: "POST", body: controls })).json();
    },
    async exchangeAccounts() {
      return (await request("/exchange-accounts")).json();
    },
    async saveExchangeAccount(account) {
      return (await request("/exchange-accounts", { method: "POST", body: account })).json();
    },
    async saveAccountSecrets(accountId, apiKey, apiSecret) {
      return (
        await request(`/exchange-accounts/${encodeURIComponent(accountId)}/secrets`, {
          method: "POST",
          body: { api_key: apiKey, api_secret: apiSecret },
        })
      ).json();
    },
    async syncAccount(accountId) {
      return (
        await request(`/exchange-accounts/${encodeURIComponent(accountId)}/sync`, {
          method: "POST",
          body: {},
        })
      ).json();
    },
    async copyAccountEquity(accountId) {
      return (
        await request(`/exchange-accounts/${encodeURIComponent(accountId)}/copy-equity`, {
          method: "POST",
          body: {},
        })
      ).json();
    },
    async candles(symbol, interval, limit = 300) {
      const query = new URLSearchParams({ symbol, interval, limit: String(limit) });
      return (await request(`/market/candles?${query}`)).json();
    },
    async ticker(symbol) {
      return (await request(`/market/ticker?${new URLSearchParams({ symbol })}`)).json();
    },
    async marketStatus() {
      return (await request("/market/status")).json();
    },
    streamUrl(symbols, interval) {
      const query = new URLSearchParams({ symbols: symbols.join(","), interval });
      return `${API_PREFIX}/market/stream?${query}`;
    },
  });
}

export const paperApi = createPaperApi();
