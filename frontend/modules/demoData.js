// Demo-only seed data for the Crypto Research console. Every value here is
// synthetic and exists purely to make the UI feel populated on first load;
// none of it comes from a live market data provider.

import { createAnalysis, toDecisionRecord } from "./generator.js";

export const ASSET_CATALOG = [
  { symbol: "BTC", name: "Bitcoin", basePrice: 65430 },
  { symbol: "ETH", name: "Ethereum", basePrice: 2654 },
  { symbol: "SOL", name: "Solana", basePrice: 156.28 },
  { symbol: "SUI", name: "Sui", basePrice: 0.94 },
  { symbol: "SEI", name: "Sei", basePrice: 0.071 },
  { symbol: "AVAX", name: "Avalanche", basePrice: 29.2 },
  { symbol: "PYTH", name: "Pyth Network", basePrice: 0.118 },
  { symbol: "ARB", name: "Arbitrum", basePrice: 0.82 },
  { symbol: "LINK", name: "Chainlink", basePrice: 14.6 },
];

export const MARKET_SNAPSHOT = [
  { asset: "BTC", price: 65430, change24h: 0.4, trend: [1, 3, 2, 5, 4, 6, 5, 9, 8, 10, 8, 12] },
  { asset: "ETH", price: 2654, change24h: -1.2, trend: [11, 9, 10, 7, 8, 5, 6, 4, 3, 5, 2, 1] },
  { asset: "SOL", price: 156.28, change24h: 2.1, trend: [2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 15] },
  { asset: "SUI", price: 0.94, change24h: -0.8, trend: [10, 12, 9, 8, 10, 6, 7, 5, 6, 3, 4, 2] },
  { asset: "SEI", price: 0.071, change24h: 2.8, trend: [1, 3, 2, 4, 7, 5, 9, 8, 10, 12, 11, 15] },
  { asset: "AVAX", price: 29.2, change24h: 0.4, trend: [4, 2, 5, 4, 7, 6, 5, 8, 7, 9, 8, 10] },
  { asset: "PYTH", price: 0.118, change24h: -2.2, trend: [13, 11, 12, 9, 10, 8, 9, 6, 7, 4, 5, 2] },
];

export const ANALYSIS_TYPES = [
  { value: "spot", label: "Spot" },
  { value: "futures", label: "Futures" },
  { value: "investment", label: "Investment" },
];

export const QUICK_START_PROMPTS = [
  {
    id: "qs-sei-swing",
    label: "SEI Swing Analysis",
    asset: "SEI",
    analysisType: "spot",
    horizon: "swing",
    question: "Should I accumulate SEI around the current demo price?",
  },
  {
    id: "qs-sui-long-term",
    label: "SUI Long-term Outlook",
    asset: "SUI",
    analysisType: "investment",
    horizon: "long_term",
    question: "What are the main long-term SUI risks and valuation scenarios?",
  },
  {
    id: "qs-btc-market",
    label: "BTC Market Outlook",
    asset: "BTC",
    analysisType: "spot",
    horizon: "swing",
    question: "What are the key BTC market levels and risks in this demo snapshot?",
  },
];

export const DATA_SOURCES = [
  {
    name: "Demo Chart Feed",
    category: "Price structure & technicals",
    status: "demo",
    description: "Synthetic OHLC and structure snapshots. Not connected to a live exchange.",
  },
  {
    name: "Demo Spot Feed",
    category: "Spot volume & taker flow",
    status: "demo",
    description: "Synthetic spot flow figures for illustration only.",
  },
  {
    name: "Demo Derivatives Feed",
    category: "Futures OI, funding, liquidations",
    status: "demo",
    description: "Synthetic derivatives snapshot; no real leverage data is fetched.",
  },
  {
    name: "Demo On-chain Feed",
    category: "On-chain flows & holder behavior",
    status: "demo",
    description: "Synthetic on-chain figures for illustration only.",
  },
  {
    name: "Demo Tokenomics Feed",
    category: "Supply, unlocks, valuation",
    status: "demo",
    description: "Synthetic tokenomics figures for illustration only.",
  },
  {
    name: "Demo Volatility Feed",
    category: "Realized/implied volatility",
    status: "demo",
    description: "Synthetic volatility figures for illustration only.",
  },
];

export const DEFAULT_SETTINGS = {
  defaultHorizon: "swing",
  defaultRiskStyle: "neutral",
  compactDensity: false,
  showAdvancedByDefault: false,
};

function buildSeedRun(asset, horizon, riskStyle, question, minutesAgo, seedSuffix) {
  const catalogEntry = ASSET_CATALOG.find((entry) => entry.symbol === asset);
  return createAnalysis({
    asset,
    analysisType: "full_research",
    horizon,
    question,
    riskStyle,
    capital: null,
    basePrice: catalogEntry?.basePrice,
    timestamp: new Date(Date.now() - minutesAgo * 60 * 1000).toISOString(),
    seedSuffix,
  });
}

export function buildSeedData() {
  const seedRuns = [
    buildSeedRun("BTC", "swing", "neutral", "Is the higher-timeframe structure still bullish?", 40, "seed-1"),
    buildSeedRun("ETH", "intraday", "aggressive", "How stretched is perpetual leverage right now?", 130, "seed-2"),
    buildSeedRun("SOL", "swing", "neutral", "Has the recent breakout been confirmed?", 260, "seed-3"),
    buildSeedRun("SEI", "position", "conservative", "What is the invalidation for a starter position?", 500, "seed-4"),
    buildSeedRun("ARB", "swing", "neutral", "Is relative strength turning versus ETH?", 900, "seed-5"),
  ];

  const seedDecisions = [seedRuns[0], seedRuns[2], seedRuns[3]].map((run) => toDecisionRecord(run));
  // Vary the demo thesis_result so Evaluations has something illustrative to show.
  seedDecisions[0].thesis_result = "pending";
  seedDecisions[1].thesis_result = "confirmed";
  seedDecisions[1].outcome_known_at = new Date(Date.now() - 60 * 60 * 1000).toISOString();
  seedDecisions[1].outcome.target_hit_first = true;
  seedDecisions[1].raw_return = 0.086;
  seedDecisions[2].thesis_result = "invalidated";
  seedDecisions[2].outcome_known_at = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString();
  seedDecisions[2].outcome.invalidation_hit_first = true;
  seedDecisions[2].raw_return = -0.041;

  const watchlist = [
    {
      asset: "BTC",
      agent: "Research Agent",
      note: "Watching for a daily close reclaim above the swing high.",
      linkedRunId: seedRuns[0].id,
    },
    {
      asset: "ETH",
      agent: "Risk Agent",
      note: "Flagged for elevated leverage; reassess before adding risk.",
      linkedRunId: seedRuns[1].id,
    },
    {
      asset: "SOL",
      agent: "Research Agent",
      note: "Confirmed breakout; monitoring for a retest of the prior range high.",
      linkedRunId: seedRuns[2].id,
    },
    {
      asset: "SEI",
      agent: "Portfolio Agent",
      note: "Starter position sizing only; unlock supply risk into next quarter.",
      linkedRunId: seedRuns[3].id,
    },
  ];

  const evaluationDemo = {
    summary: "Fixture outcome counts from local sample decisions. No live benchmark or skill-vs-control comparison is connected; see crypto_eval for harness results.",
  };

  return {
    runs: seedRuns,
    decisions: seedDecisions,
    watchlist,
    dataSources: DATA_SOURCES,
    evaluationDemo,
    settings: DEFAULT_SETTINGS,
  };
}
