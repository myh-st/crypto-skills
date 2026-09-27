// Dependency-free logic test for the demo analysis generator, using Node's
// built-in test runner (available without installing any package). Run with:
//   node --test frontend/tests/generator.test.mjs
//
// This exercises pure data-generation logic only; it does not require a
// browser or DOM and complements (does not replace) the browser-based manual
// verification described in frontend/README.md.

import test from "node:test";
import assert from "node:assert/strict";

import { createAnalysis, toDecisionRecord } from "../modules/generator.js";
import { HORIZONS } from "../modules/contracts.js";

const CANONICAL_STATES = [
  "ACCUMULATE",
  "ENTER_LONG",
  "ENTER_SHORT",
  "WAIT_FOR_PULLBACK",
  "WAIT_FOR_BREAKOUT_CONFIRMATION",
  "AVOID_CHASING",
  "NO_TRADE",
  "HOLD",
  "REDUCE",
  "TAKE_PARTIAL_PROFIT",
  "HEDGE_DE_RISK",
  "EXIT",
];

function sampleInput(overrides = {}) {
  return {
    asset: "BTC",
    analysisType: "full_research",
    horizon: "swing",
    question: "Is the higher-timeframe structure still bullish?",
    riskStyle: "neutral",
    capital: null,
    basePrice: 64000,
    timestamp: "2026-01-01T00:00:00Z",
    seedSuffix: "test-seed",
    ...overrides,
  };
}

test("createAnalysis produces a report matching analysis-output.schema.json shape", () => {
  const run = createAnalysis(sampleInput());
  const { report } = run;

  assert.ok(CANONICAL_STATES.includes(report.state), `unexpected state ${report.state}`);
  assert.ok(["bullish", "neutral", "bearish"].includes(report.bias));
  assert.ok(["low", "moderate", "high"].includes(report.confidence));
  assert.ok(report.reasons.length <= 5, "reasons must respect the schema maxItems: 5");
  assert.ok(report.reasons.length > 0);
  assert.equal(typeof report.risk, "string");
  assert.ok(report.risk.length > 0);
  assert.ok(Array.isArray(report.targets) && report.targets.length > 0);
  assert.ok(Array.isArray(report.monitoring_conditions));
});

test("createAnalysis produces an evidence ledger matching evidence-ledger.schema.json shape", () => {
  const run = createAnalysis(sampleInput());
  const ledger = run.evidence.evidence_ledger;

  assert.ok(Array.isArray(ledger) && ledger.length > 0);
  for (const item of ledger) {
    assert.equal(typeof item.observed_at, "string");
    assert.ok(["primary", "aggregator", "charting", "derived", "news", "other"].includes(item.source.type));
    assert.equal(typeof item.source.provider, "string");
    assert.ok(["high", "medium", "low"].includes(item.quality));
    assert.ok(["bull", "bear", "neutral"].includes(item.supports));
  }
});

test("priceLevels are numerically consistent (invalidation below entry, targets above entry)", () => {
  const run = createAnalysis(sampleInput());
  const { priceLevels } = run;

  assert.ok(priceLevels.invalidation < priceLevels.entryZone[0]);
  assert.ok(priceLevels.entryZone[0] <= priceLevels.entryZone[1]);
  for (const target of priceLevels.targets) {
    assert.ok(target > priceLevels.entryZone[1], "targets should sit above the entry zone");
  }
});

test("toDecisionRecord produces a shape matching decision-record.schema.json", () => {
  const run = createAnalysis(sampleInput());
  const decision = toDecisionRecord(run);

  assert.ok(HORIZONS.includes(decision.horizon));
  assert.ok(CANONICAL_STATES.includes(decision.decision_state));
  assert.equal(decision.entry_zone.length, 2);
  assert.equal(decision.thesis_result, "pending");
  assert.ok(decision.trigger && decision.trigger.triggered === false);
  assert.ok(decision.outcome && decision.outcome.target_hit_first === null);
});

test("createAnalysis is deterministic for a fixed seed", () => {
  const runA = createAnalysis(sampleInput());
  const runB = createAnalysis(sampleInput());

  assert.equal(runA.report.state, runB.report.state);
  assert.deepEqual(runA.priceLevels.targets, runB.priceLevels.targets);
});

test("createAnalysis varies with a different seed", () => {
  const runA = createAnalysis(sampleInput({ seedSuffix: "seed-a" }));
  const runB = createAnalysis(sampleInput({ seedSuffix: "seed-b", asset: "ETH" }));

  assert.notEqual(runA.id, runB.id);
});

test("leverage summary and evidence use the same funding and open-interest observation", () => {
  const run = createAnalysis(sampleInput());
  const derivativesEvidence = run.evidence.evidence_ledger.find((item) => item.metric === "funding_rate");
  const fundingDirection = run.leverage.fundingRate >= 0 ? "positive" : "negative";

  assert.equal(run.leverage.notes, run.report.reasons.find((reason) => reason === run.leverage.notes));
  assert.match(derivativesEvidence.claim, new RegExp(fundingDirection));
  assert.match(derivativesEvidence.claim, new RegExp(run.leverage.openInterestTrend));
  assert.equal(derivativesEvidence.value, Number((run.leverage.fundingRate * 100).toFixed(3)));
  if (run.leverage.openInterestTrend === "rising") {
    assert.equal(run.leverage.state, "elevated");
  }
});
