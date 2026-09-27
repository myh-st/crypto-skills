// Deterministic, dependency-free demo analysis generator.
//
// IMPORTANT: this module fabricates plausible-looking numbers from a seeded
// pseudo-random generator. It never fetches market data and must not be
// mistaken for real research. Every consumer of this module must keep the
// "Demo data" labeling visible in the UI.
//
// Output shapes intentionally track the repository contracts so presentation
// data stays structured rather than free-form prose:
//   report          -> schemas/analysis-output.schema.json
//   evidence        -> schemas/evidence-ledger.schema.json
//   toDecisionRecord() -> schemas/decision-record.schema.json

import { HORIZONS } from "./contracts.js";

// mulberry32: tiny, seedable PRNG so a given run is reproducible from its seed
// string without pulling in a dependency.
function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashString(value) {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function pick(rng, list) {
  return list[Math.floor(rng() * list.length) % list.length];
}

const STRUCTURE_NOTES = [
  "Price is holding a series of higher lows on the daily chart.",
  "A lower-high sequence is forming below the prior swing high.",
  "Price is consolidating in a tightening range near the mid-term average.",
  "A recent breakout retest is still being defended by buyers.",
];

const LEVERAGE_SCENARIOS = [
  {
    openInterestTrend: "falling",
    fundingRate: (rng) => Number((0.002 + rng() * 0.008).toFixed(4)),
    notes: "Open interest is falling while funding remains mildly positive.",
  },
  {
    openInterestTrend: "rising",
    fundingRate: (rng) => Number((0.012 + rng() * 0.02).toFixed(4)),
    notes: "Open interest is rising while funding is positive, increasing chase risk.",
  },
  {
    openInterestTrend: "flat",
    fundingRate: (rng) => Number((rng() * 0.01 - 0.005).toFixed(4)),
    notes: "Funding is near flat and open interest is range-bound.",
  },
];

const SCENARIO_TEMPLATES = [
  { scenario: "Bull continuation", trigger: "Close above the swing high with rising spot volume." },
  { scenario: "Range and reload", trigger: "Price holds the demo entry zone without a daily close below invalidation." },
  { scenario: "Invalidation", trigger: "Daily close below the invalidation level on expanding volume." },
];

const EVIDENCE_TEMPLATES = [
  {
    claim: "Daily structure remains higher-high / higher-low",
    metric: "daily_structure",
    evidence_type: "price_structure",
    supports: "bull",
    quality: "high",
    provider: "Demo Chart Feed",
    sourceType: "charting",
  },
  {
    claim: "Perpetual funding is positive while open interest cools",
    metric: "funding_rate",
    evidence_type: "derivatives",
    supports: "bear",
    quality: "medium",
    provider: "Demo Derivatives Feed",
    sourceType: "primary",
  },
  {
    claim: "Spot taker flow favors buyers on the latest retest",
    metric: "taker_buy_ratio",
    evidence_type: "spot_flow",
    supports: "bull",
    quality: "medium",
    provider: "Demo Spot Feed",
    sourceType: "primary",
  },
  {
    claim: "Stablecoin exchange reserves ticked up, a mild risk-off tell",
    metric: "stablecoin_reserve_change",
    evidence_type: "on_chain",
    supports: "bear",
    quality: "low",
    provider: "Demo On-chain Feed",
    sourceType: "aggregator",
  },
  {
    claim: "Realized volatility is compressing ahead of a scheduled catalyst",
    metric: "realized_volatility",
    evidence_type: "volatility",
    supports: "neutral",
    quality: "medium",
    provider: "Demo Volatility Feed",
    sourceType: "derived",
  },
  {
    claim: "Token unlock supply pressure is limited over the stated horizon",
    metric: "unlock_supply_pct",
    evidence_type: "tokenomics",
    supports: "bull",
    quality: "low",
    provider: "Demo Tokenomics Feed",
    sourceType: "aggregator",
  },
];

function roundPrice(value) {
  const decimals = value >= 100 ? 2 : value >= 1 ? 4 : 6;
  return Number(value.toFixed(decimals));
}

function formatPriceString(value) {
  const decimals = value >= 100 ? 2 : value >= 1 ? 3 : 4;
  return `$${value.toFixed(decimals)}`;
}

function decisionFromBias(rng, bias) {
  if (bias === "bullish") {
    return pick(rng, ["ACCUMULATE", "WAIT_FOR_PULLBACK", "ENTER_LONG"]);
  }
  if (bias === "bearish") {
    return pick(rng, ["REDUCE", "AVOID_CHASING", "ENTER_SHORT", "HEDGE_DE_RISK"]);
  }
  return pick(rng, ["HOLD", "NO_TRADE", "WAIT_FOR_BREAKOUT_CONFIRMATION"]);
}

export function createAnalysis(input) {
  const {
    asset,
    analysisType = "full_research",
    horizon = "swing",
    question = "",
    riskStyle = "neutral",
    capital = null,
    basePrice,
    timestamp,
    seedSuffix,
    requestSettings,
  } = input;

  const createdAt = timestamp ? new Date(timestamp) : new Date();
  const normalizedRequestSettings = requestSettings ?? {
    venue: "Binance",
    instrument: analysisType === "futures" ? "perpetual" : "spot",
    model: "fixture",
    dataAsOf: createdAt.toISOString(),
    dataProviders: [],
    referenceCurrency: "USD",
  };
  const seedKey = `${asset}|${analysisType}|${horizon}|${question}|${riskStyle}|${capital}|${JSON.stringify(normalizedRequestSettings)}|${seedSuffix ?? createdAt.getTime()}`;
  const rng = mulberry32(hashString(seedKey));

  const price = basePrice ?? 10 + rng() * 200;
  const biasRoll = rng();
  const bias = biasRoll < 0.42 ? "bullish" : biasRoll < 0.72 ? "neutral" : "bearish";
  const confidence = pick(rng, ["low", "moderate", "high"]);
  const state = decisionFromBias(rng, bias);

  const entryLow = roundPrice(price * (0.94 + rng() * 0.02));
  const entryHigh = roundPrice(entryLow * (1.01 + rng() * 0.015));
  const secondaryLow = roundPrice(entryLow * (0.9 - rng() * 0.03));
  const secondaryHigh = roundPrice(secondaryLow * 1.02);
  const invalidation = roundPrice(secondaryLow * (0.9 - rng() * 0.03));
  const targetCount = 2 + Math.floor(rng() * 2);
  const targets = [];
  let last = entryHigh;
  for (let i = 0; i < targetCount; i += 1) {
    last = roundPrice(last * (1.08 + rng() * 0.09));
    targets.push(last);
  }

  const leverageScenario = pick(rng, LEVERAGE_SCENARIOS);
  const fundingRate = leverageScenario.fundingRate(rng);
  const leverageState = leverageScenario.openInterestTrend === "rising"
    ? "elevated"
    : pick(rng, ["moderate", "low"]);

  const reasonsPool = [
    `Higher-timeframe structure is ${bias === "bearish" ? "deteriorating" : "constructive"} into the ${horizon.replace("_", " ")} horizon.`,
    pick(rng, STRUCTURE_NOTES),
    leverageScenario.notes,
    `${riskStyle === "aggressive" ? "Aggressive" : riskStyle === "conservative" ? "Conservative" : "Neutral"} sizing favors ${bias === "bullish" ? "scaling in near the demo entry zone" : bias === "bearish" ? "waiting for confirmation before adding risk" : "a smaller starter position"}.`,
    `Demo data quality is ${pick(rng, ["good", "partial", "limited"])} for this asset and horizon.`,
  ];
  const reasons = reasonsPool.slice(0, 4 + (rng() > 0.5 ? 1 : 0)).slice(0, 5);

  const scenarioMap = SCENARIO_TEMPLATES.map((template) => ({
    scenario: template.scenario,
    trigger: template.trigger,
    probability: pick(rng, ["low", "moderate", "high"]),
    action: template.scenario === "Invalidation"
      ? "Respect the invalidation level and step aside."
      : template.scenario === "Bull continuation"
        ? "Trail risk under the higher low and scale into strength."
        : "Hold the demo entry zone with reduced size and reassess.",
  }));

  const evidenceValueFor = (metric) => {
    switch (metric) {
      case "funding_rate":
        return Number((fundingRate * 100).toFixed(3));
      case "unlock_supply_pct":
        return Number((rng() * 6).toFixed(2));
      case "taker_buy_ratio":
        return Number((0.45 + rng() * 0.2).toFixed(3));
      case "stablecoin_reserve_change":
        return Number((rng() * 4 - 2).toFixed(2));
      case "realized_volatility":
        return Number((30 + rng() * 40).toFixed(1));
      case "daily_structure":
        return "HH/HL";
      default:
        return roundPrice(price * (0.4 + rng()));
    }
  };

  const evidenceUnitFor = (metric) => {
    if (["funding_rate", "unlock_supply_pct", "stablecoin_reserve_change", "realized_volatility"].includes(metric)) return "%";
    if (metric === "taker_buy_ratio") return "ratio";
    return null;
  };

  const evidenceLedger = EVIDENCE_TEMPLATES.map((template, index) => {
    const observedAt = new Date(createdAt.getTime() - index * 45 * 60 * 1000).toISOString();
    const claim = template.metric === "funding_rate"
      ? `Perpetual funding is ${fundingRate >= 0 ? "positive" : "negative"} while open interest is ${leverageScenario.openInterestTrend}.`
      : template.claim;
    const supports = template.metric === "funding_rate" && leverageScenario.openInterestTrend === "flat"
      ? "neutral"
      : template.supports;
    return {
      claim,
      metric: template.metric,
      value: evidenceValueFor(template.metric),
      unit: evidenceUnitFor(template.metric),
      venue: "Demo Exchange",
      instrument: template.evidence_type === "derivatives" ? "perpetual" : "spot",
      observed_at: observedAt,
      retrieved_at: observedAt,
      freshness_seconds: 60 + Math.floor(rng() * 600),
      source: {
        provider: template.provider,
        type: template.sourceType,
        uri: null,
      },
      evidence_type: template.evidence_type,
      quality: template.quality,
      supports,
      interpretation: {
        supports,
        confidence: template.quality === "high" ? "high" : template.quality === "medium" ? "medium" : "low",
        notes: "Demo evidence for illustration only; not connected to a live data provider.",
      },
      notes: "Demo evidence for illustration only; not connected to a live data provider.",
    };
  });

  const report = {
    asset,
    state,
    bias,
    confidence,
    preferred_entry: `${formatPriceString(entryLow)}-${formatPriceString(entryHigh)}`,
    secondary_entry: `${formatPriceString(secondaryLow)}-${formatPriceString(secondaryHigh)}`,
    confirmation: bias === "bearish"
      ? "Daily close below the invalidation level with expanding volume."
      : "Spot volume holds the retest while leverage cools.",
    invalidation: `Daily close below ${formatPriceString(invalidation)}.`,
    targets: targets.map((value) => formatPriceString(value)),
    horizon: `${titleCaseHorizon(horizon)}${question ? `; question: ${question.slice(0, 80)}` : ""}`,
    reasons,
    risk: pick(rng, [
      "Broad market reversal, loss of the higher-timeframe structure, or a supply-driven liquidity shock.",
      "Leverage unwind, thin weekend liquidity, or a macro liquidity shock.",
      "Unlock-driven supply pressure or a sudden funding reset.",
    ]),
    data_quality: `Demo snapshot only; ${pick(rng, ["derivatives", "on-chain", "options"])} lane unavailable in this prototype.`,
    scenario_map: scenarioMap,
    monitoring_conditions: [
      "Daily close beyond the stated invalidation or target zone.",
      "A material change in funding or open interest trend.",
    ],
  };

  const marketStructure = {
    trend: bias === "bullish" ? "Higher-high / higher-low" : bias === "bearish" ? "Lower-high / lower-low" : "Range-bound",
    notes: pick(rng, STRUCTURE_NOTES),
    keyLevels: [
      { label: "Demand", price: secondaryLow },
      { label: "Entry", price: entryLow },
      { label: "Supply", price: targets[0] },
    ],
  };

  const leverage = {
    state: leverageState,
    fundingRate,
    openInterestTrend: leverageScenario.openInterestTrend,
    notes: leverageScenario.notes,
  };

  return {
    id: `run-${createdAt.getTime().toString(36)}-${Math.floor(rng() * 1e6).toString(36)}`,
    createdAt: createdAt.toISOString(),
    asset,
    analysisType,
    horizon,
    question,
    riskStyle,
    capital,
    requestSettings: normalizedRequestSettings,
    status: "completed",
    report,
    marketStructure,
    leverage,
    priceLevels: {
      current: roundPrice(price),
      entryZone: [entryLow, entryHigh],
      secondaryEntry: [secondaryLow, secondaryHigh],
      invalidation,
      targets,
    },
    evidence: { evidence_ledger: evidenceLedger },
  };
}

function titleCaseHorizon(horizon) {
  const known = HORIZONS.includes(horizon) ? horizon : "swing";
  return known.replace("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function toDecisionRecord(run) {
  const { report, priceLevels } = run;
  return {
    analysis_time: run.createdAt,
    asset: run.asset,
    horizon: run.horizon,
    market_regime: run.marketStructure?.trend ?? null,
    decision_state: report.state,
    entry_zone: priceLevels.entryZone,
    invalidation: priceLevels.invalidation,
    targets: priceLevels.targets,
    confidence: report.confidence,
    decisive_evidence: report.reasons.slice(0, 3),
    leverage_state: run.leverage?.state ?? null,
    btc_regime: null,
    trigger: { triggered: false, triggered_at: null, entry_reference: null, time_to_trigger: null },
    outcome: {
      target_hit_first: null,
      invalidation_hit_first: null,
      post_trigger_return: null,
      post_trigger_mfe: null,
      post_trigger_mae: null,
    },
    outcome_window: "90d",
    raw_return: null,
    benchmark_return: null,
    alpha: null,
    max_favorable_excursion: null,
    max_adverse_excursion: null,
    thesis_result: "pending",
    reflection: null,
    outcome_known_at: null,
    // Presentation-only fields kept outside the schema-aligned block above.
    id: `decision-${run.id}`,
    runId: run.id,
    savedAt: new Date().toISOString(),
  };
}

export const _internal = { mulberry32, hashString };
