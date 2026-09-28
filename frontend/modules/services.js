import { MARKET_SNAPSHOT } from "./demoData.js";
import { createAnalysis, toDecisionRecord } from "./generator.js";

let runtime = {
  mode: "fixture",
  status: null,
  fetch: null,
};

function fallbackToFixture() {
  runtime = { mode: "fixture", status: null, fetch: null };
  return runtime;
}

async function readError(response) {
  try {
    const payload = await response.json();
    const message = payload?.error?.message;
    return typeof message === "string" && message.trim()
      ? message
      : `Runtime API returned HTTP ${response.status}`;
  } catch {
    return `Runtime API returned HTTP ${response.status}`;
  }
}

async function requestJson(path, options = {}) {
  if (typeof runtime.fetch !== "function") {
    throw new Error("Local runtime API is unavailable.");
  }
  let response;
  try {
    response = await runtime.fetch.call(globalThis, path, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new Error("Could not reach the local runtime API.");
  }
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  try {
    return await response.json();
  } catch {
    throw new Error("Local runtime API returned invalid JSON.");
  }
}

export const runtimeService = Object.freeze({
  async initialize(fetchImpl = globalThis.fetch) {
    if (typeof fetchImpl !== "function") return fallbackToFixture();
    let response;
    try {
      response = await fetchImpl("/api/status", {
        headers: { Accept: "application/json" },
      });
    } catch {
      runtime = {
        mode: "unavailable",
        status: null,
        fetch: fetchImpl,
        error: "Could not reach the local runtime API; analysis is disabled.",
      };
      return runtime;
    }
    if (response.status === 404) return fallbackToFixture();
    if (!response.ok) {
      runtime = {
        mode: "unavailable",
        status: null,
        fetch: fetchImpl,
        error: await readError(response),
      };
      return runtime;
    }
    let status;
    try {
      status = await response.json();
    } catch {
      runtime = {
        mode: "unavailable",
        status: null,
        fetch: fetchImpl,
        error: "Local runtime returned invalid status JSON.",
      };
      return runtime;
    }
    if (status?.mode !== "live" || status?.schema_version !== "crypto-live-runtime.status.v1") {
      runtime = {
        mode: "unavailable",
        status: null,
        fetch: fetchImpl,
        error: "Local runtime returned an unsupported status response.",
      };
      return runtime;
    }
    runtime = { mode: "live", status, fetch: fetchImpl };
    return runtime;
  },
  get mode() {
    return runtime.mode;
  },
  get status() {
    return runtime.status;
  },
  async listEvaluations() {
    if (runtime.mode !== "live") return [];
    const payload = await requestJson("/api/evaluations");
    if (!Array.isArray(payload.runs)) {
      throw new Error("Local runtime returned an invalid forward-evaluation list.");
    }
    return payload.runs;
  },
  async scoreForward(runId) {
    if (runtime.mode !== "live") {
      throw new Error("Forward scoring is available only in local runtime mode.");
    }
    return requestJson(`/api/forward/${encodeURIComponent(runId)}/score`, {
      method: "POST",
    });
  },
  useFixtureMode() {
    fallbackToFixture();
  },
});

export const analysisService = Object.freeze({
  get mode() {
    return runtime.mode;
  },
  async create(input) {
    if (runtime.mode === "unavailable") {
      throw new Error(runtime.error || "Local runtime API is unavailable.");
    }
    if (runtime.mode === "live") {
      const asset = String(input.asset || "").trim().toUpperCase();
      const symbol = asset.endsWith("USDT") ? asset : `${asset}USDT`;
      const response = await requestJson("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol,
          instrument: "spot",
          horizon: input.horizon,
          question: input.question || "",
          risk_style: input.riskStyle || "neutral",
        }),
      });
      if (!response?.run || response.run.runtimeMode !== "live") {
        throw new Error("Local runtime returned an invalid analysis result.");
      }
      return response.run;
    }
    return createAnalysis(input);
  },
});

export const marketDataService = Object.freeze({
  mode: "fixture",
  getSnapshot() {
    return MARKET_SNAPSHOT.map((item) => ({ ...item, trend: [...item.trend] }));
  },
});

export const runService = Object.freeze({
  list(store) {
    return store.getState().runs;
  },
  get(store, runId) {
    return store.getState().runs.find((run) => run.id === runId);
  },
  add(store, run) {
    store.addRun(run);
    return run;
  },
});

export const decisionService = Object.freeze({
  list(store) {
    return store.getState().decisions;
  },
  fromRun(run) {
    return toDecisionRecord(run);
  },
  add(store, decision) {
    store.addDecision(decision);
    return decision;
  },
});

export const evaluationService = Object.freeze({
  snapshot(store) {
    const remoteRuns = store.getState().forwardEvaluations || [];
    const forwardById = new Map(remoteRuns.map((run) => [run.run_id, { ...run }]));
    runService.list(store).filter((run) => run.runtimeMode === "live").forEach((run) => {
      forwardById.set(run.id, {
        ...(forwardById.get(run.id) || {}),
        run_id: run.id,
        asset: run.asset,
        symbol: run.symbol,
        horizon: run.horizon,
        status: run.evaluationStatus || "waiting_for_outcome",
        data_cutoff: run.requestSettings?.dataAsOf,
        horizon_closes_at: run.horizonClosesAt,
        dataset_hash: run.datasetHash,
        prediction_id: run.predictionId,
        run,
      });
    });
    return {
      decisions: decisionService.list(store),
      evaluationDemo: store.getState().evaluationDemo,
      forwardRuns: [...forwardById.values()].sort(
        (left, right) => String(right.created_at || right.run?.createdAt || "")
          .localeCompare(String(left.created_at || left.run?.createdAt || "")),
      ),
    };
  },
  listForward() {
    return runtimeService.listEvaluations();
  },
});
