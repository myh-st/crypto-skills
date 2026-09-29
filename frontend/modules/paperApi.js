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
      let confirmation = null;
      try {
        const payload = await response.json();
        if (typeof payload.error === "string") message = payload.error;
        if (payload.confirmation_required === true && typeof payload.code === "string") {
          confirmation = { code: payload.code, details: payload.details || {} };
        }
      } catch {
        // Keep network failure details bounded and credential-free.
      }
      const error = new Error(message);
      error.status = response.status;
      if (confirmation) error.confirmation = confirmation;
      throw error;
    }
    return response;
  }

  return Object.freeze({
    async runtimeHealth() {
      return (await request("/runtime-health")).json();
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
    async reducePosition(positionId, fraction = 1, options = undefined) {
      return (
        await request(`/positions/${encodeURIComponent(positionId)}/reduce`, {
          method: "POST",
          body: options ? { fraction, ...options } : { fraction },
        })
      ).json();
    },
    // ---- Portfolio OS (PAPER-local; the server derives every authoritative size) ----
    async markets({ marketType = "spot", q = "", quote = null, tradable = false, limit = 60, sort = "volume" } = {}) {
      const query = new URLSearchParams({ market_type: marketType, q, limit: String(limit), sort });
      if (quote) query.set("quote", quote);
      if (tradable) query.set("tradable", "true");
      return (await request(`/markets?${query}`)).json();
    },
    async instrument(instrumentId) {
      return (await request(`/markets/${encodeURIComponent(instrumentId)}`)).json();
    },
    async quote(instrumentId) {
      return (await request(`/markets/${encodeURIComponent(instrumentId)}/quote`)).json();
    },
    async instrumentCandles(instrumentId, interval, limit = 300) {
      const query = new URLSearchParams({ instrument_id: instrumentId, interval, limit: String(limit) });
      return (await request(`/market/candles?${query}`)).json();
    },
    async portfolio() {
      return (await request("/portfolio")).json();
    },
    async positions(status = "open") {
      return (await request(`/positions?${new URLSearchParams({ status })}`)).json();
    },
    async position(ref) {
      return (await request(`/positions/${encodeURIComponent(ref)}`)).json();
    },
    async orders(status = null) {
      return (await request(status ? `/orders?${new URLSearchParams({ status })}` : "/orders")).json();
    },
    async previewOrder(order) {
      return (await request("/orders/preview", { method: "POST", body: order })).json();
    },
    async createOrder(order) {
      return (await request("/orders", { method: "POST", body: order })).json();
    },
    async amendOrder(orderRef, patch) {
      return (await request(`/orders/${encodeURIComponent(orderRef)}`, { method: "PATCH", body: patch })).json();
    },
    async cancelOrder(orderRef) {
      return (await request(`/orders/${encodeURIComponent(orderRef)}`, { method: "DELETE" })).json();
    },
    async updateProtection(ref, body) {
      return (await request(`/positions/${encodeURIComponent(ref)}/protection`, { method: "PATCH", body })).json();
    },
    async closePosition(ref, options = {}) {
      return (await request(`/positions/${encodeURIComponent(ref)}/close`, { method: "POST", body: options })).json();
    },
    async setManagementMode(ref, mode, options = {}) {
      return (
        await request(`/positions/${encodeURIComponent(ref)}/management-mode`, { method: "POST", body: { mode, ...options } })
      ).json();
    },
    async requestReplan(ref, body = {}) {
      return (await request(`/positions/${encodeURIComponent(ref)}/replan`, { method: "POST", body })).json();
    },
    async replans({ status = null, positionRef = null } = {}) {
      const query = new URLSearchParams();
      if (status) query.set("status", status);
      if (positionRef) query.set("position_ref", positionRef);
      return (await request(`/replans${query.toString() ? `?${query}` : ""}`)).json();
    },
    async applyReplan(proposalId, body = {}) {
      return (await request(`/replans/${encodeURIComponent(proposalId)}/apply`, { method: "POST", body })).json();
    },
    async rejectReplan(proposalId, body = {}) {
      return (await request(`/replans/${encodeURIComponent(proposalId)}/reject`, { method: "POST", body })).json();
    },
    async attention({ includeResolved = false } = {}) {
      return (await request(includeResolved ? "/attention?include_resolved=true" : "/attention")).json();
    },
    async acknowledgeAttention(attentionId) {
      return (await request(`/attention/${encodeURIComponent(attentionId)}/ack`, { method: "POST", body: {} })).json();
    },
    async activity(filters = {}) {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(filters)) {
        if (value !== null && value !== undefined && value !== "") query.set(key, String(value));
      }
      return (await request(`/activity${query.toString() ? `?${query}` : ""}`)).json();
    },
    async tournament() {
      return (await request("/tournament")).json();
    },
    async reviews() {
      return (await request("/reviews")).json();
    },
    async portfolioReview() {
      return (await request("/portfolio/review", { method: "POST", body: {} })).json();
    },
    async portfolioSettings() {
      return (await request("/portfolio/settings")).json();
    },
    async savePortfolioSettings(patch) {
      return (await request("/portfolio/settings", { method: "POST", body: patch })).json();
    },
    async automation(patch, { confirm = false } = {}) {
      return (await request("/automation", { method: "POST", body: confirm ? { ...patch, confirm: true } : patch })).json();
    },
    async validateExperiment(config) {
      return (await request("/experiment/validate", { method: "POST", body: config })).json();
    },
    async runtimeSummary(symbol = null) {
      return (await request(symbol ? `/runtime/summary?${new URLSearchParams({ symbol })}` : "/runtime/summary")).json();
    },
    async safety() {
      return (await request("/safety")).json();
    },
    async setKillSwitch(level, { reason = "", confirm = false } = {}) {
      return (await request("/safety/kill-switch", { method: "POST", body: confirm ? { level, reason, confirm: true } : { level, reason } })).json();
    },
    async assessInstrument(instrumentId) {
      return (await request("/safety/assess", { method: "POST", body: { instrument_id: instrumentId } })).json();
    },
    async executionPlans() {
      return (await request("/execution-plans")).json();
    },
    async health() {
      return (await request("/health")).json();
    },
    async incidents(status = "") {
      return (await request(`/incidents${status ? `?status=${encodeURIComponent(status)}` : ""}`)).json();
    },
    async backup() {
      return (await request("/backup", { method: "POST", body: {} })).json();
    },
    async lifecycleReview(ref, recommendation = undefined) {
      return (await request(`/lifecycle/${encodeURIComponent(ref)}/review`, { method: "POST", body: recommendation ? { recommendation } : {} })).json();
    },
    async lifecycleApply(ref, options = {}) {
      return (await request(`/lifecycle/${encodeURIComponent(ref)}/apply`, { method: "POST", body: options })).json();
    },
    async lifecycleDismiss(ref) {
      return (await request(`/lifecycle/${encodeURIComponent(ref)}/dismiss`, { method: "POST", body: {} })).json();
    },
    async lifecycleBenchmark(instrumentId, bars = 360) {
      return (await request("/lifecycle/benchmark", { method: "POST", body: { instrument_id: instrumentId, bars } })).json();
    },
    async lifecycleBenchmarks() {
      return (await request("/lifecycle/benchmarks")).json();
    },
    async setCoreFraction(ref, coreFraction) {
      return (await request(`/positions/${encodeURIComponent(ref)}/core`, { method: "POST", body: { core_fraction: coreFraction } })).json();
    },
    runtimeStreamUrl() {
      return `${API_PREFIX}/runtime/stream`;
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
