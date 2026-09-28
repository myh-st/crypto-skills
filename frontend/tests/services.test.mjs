import test from "node:test";
import assert from "node:assert/strict";

import { analysisService, decisionService, evaluationService, marketDataService, runService, runtimeService } from "../modules/services.js";

function createMemoryStore() {
  const state = {
    runs: [],
    decisions: [],
    evaluationDemo: { summary: "fixture" },
  };
  return {
    getState: () => state,
    addRun: (run) => { state.runs.unshift(run); },
    addDecision: (decision) => { state.decisions.unshift(decision); },
  };
}

test("service boundary is explicitly fixture-only and market data is synthetic", () => {
  assert.equal(analysisService.mode, "fixture");
  assert.equal(marketDataService.mode, "fixture");
  const snapshot = marketDataService.getSnapshot();
  assert.ok(snapshot.length >= 6);
  assert.ok(snapshot.every((item) => Array.isArray(item.trend)));
});

test("fixture run and decision services preserve the local workflow", async () => {
  const store = createMemoryStore();
  const run = await analysisService.create({
    asset: "SEI",
    analysisType: "spot",
    horizon: "swing",
    question: "Test fixture request",
    basePrice: 0.071,
    timestamp: "2026-09-27T00:00:00.000Z",
    requestSettings: { venue: "Binance", instrument: "spot", model: "fixture", dataProviders: [] },
  });

  runService.add(store, run);
  const savedDecision = decisionService.fromRun(run);
  decisionService.add(store, savedDecision);

  assert.equal(runService.get(store, run.id).asset, "SEI");
  assert.equal(decisionService.list(store)[0].runId, run.id);
  assert.equal(evaluationService.snapshot(store).decisions.length, 1);
});

test("runtime handshake posts a same-origin Spot request without client credentials", async () => {
  const calls = [];
  await runtimeService.initialize(async function (path, options = {}) {
    calls.push({ path, options, thisValue: this });
    if (path === "/api/status") {
      return {
        status: 200,
        ok: true,
        async json() {
          return {
            schema_version: "crypto-live-runtime.status.v1",
            mode: "live",
            model_configured: true,
            model_id: "gpt-6-luna",
          };
        },
      };
    }
    return {
      status: 201,
      ok: true,
      async json() {
        return { run: { id: "fwd-test", runtimeMode: "live" } };
      },
    };
  });

  const run = await analysisService.create({
    asset: "BTC",
    analysisType: "spot",
    horizon: "intraday",
    question: "Assess the closed-candle setup.",
    riskStyle: "neutral",
  });

  assert.equal(runtimeService.mode, "live");
  assert.equal(run.id, "fwd-test");
  assert.equal(calls[1].path, "/api/analyze");
  assert.equal(calls[1].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    symbol: "BTCUSDT",
    instrument: "spot",
    horizon: "intraday",
    question: "Assess the closed-candle setup.",
    risk_style: "neutral",
  });
  assert.equal(calls[1].thisValue, globalThis);
  assert.equal("Authorization" in calls[1].options.headers, false);
  runtimeService.useFixtureMode();
});

test("live API errors are explicit and never fall back to synthetic analysis", async () => {
  await runtimeService.initialize(async (path) => ({
    status: path === "/api/status" ? 200 : 503,
    ok: path === "/api/status",
    async json() {
      return path === "/api/status"
        ? {
            schema_version: "crypto-live-runtime.status.v1",
            mode: "live",
            model_configured: false,
            model_id: "gpt-6-luna",
          }
        : {
            error: {
              code: "model_credentials_missing",
              message: "OPENAI_API_KEY is not configured in the server environment.",
            },
          };
    },
  }));

  await assert.rejects(
    analysisService.create({
      asset: "BTC",
      analysisType: "spot",
      horizon: "intraday",
      question: "",
    }),
    /OPENAI_API_KEY is not configured/,
  );
  assert.equal(analysisService.mode, "live");
  runtimeService.useFixtureMode();
});

test("a fixture-only static server keeps the original demo workflow", async () => {
  await runtimeService.initialize(async () => ({
    status: 404,
    ok: false,
    async json() {
      return {};
    },
  }));

  assert.equal(runtimeService.mode, "fixture");
  assert.equal(analysisService.mode, "fixture");
});

test("an unreachable runtime does not silently fall back to fixture analysis", async () => {
  await runtimeService.initialize(async () => {
    throw new Error("browser network failure");
  });

  assert.equal(runtimeService.mode, "unavailable");
  await assert.rejects(
    analysisService.create({
      asset: "BTC",
      analysisType: "spot",
      horizon: "intraday",
      question: "Try analysis",
    }),
    /Could not reach the local runtime API/,
  );
});
