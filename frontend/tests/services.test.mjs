import test from "node:test";
import assert from "node:assert/strict";

import { analysisService, decisionService, evaluationService, marketDataService, runService } from "../modules/services.js";

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

test("fixture run and decision services preserve the local workflow", () => {
  const store = createMemoryStore();
  const run = analysisService.create({
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
