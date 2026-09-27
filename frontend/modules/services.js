import { MARKET_SNAPSHOT } from "./demoData.js";
import { createAnalysis, toDecisionRecord } from "./generator.js";

export const analysisService = Object.freeze({
  mode: "fixture",
  create(input) {
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
    return {
      decisions: decisionService.list(store),
      evaluationDemo: store.getState().evaluationDemo,
    };
  },
});
