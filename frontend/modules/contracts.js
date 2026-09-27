// Loads the canonical decision-state vocabulary directly from the repository's
// JSON Schema contracts so the frontend cannot silently drift from
// schemas/decision-state.schema.json. Falls back to an embedded copy only if
// the fetch fails (e.g. the page was opened without an HTTP server).
//
// The frontend must be served from the repository root, e.g.:
//   python3 -m http.server 8000
// then open http://localhost:8000/frontend/  (see frontend/README.md)

const FALLBACK_STATES = [
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

// Restrained decision coloring: bullish states get muted green, bearish states
// muted red, wait/hold/neutral states slate, risk-off states amber. No
// gradients, matching the visual direction requested in the product brief.
export const STATE_TONE = {
  ACCUMULATE: "positive",
  ENTER_LONG: "positive",
  ENTER_SHORT: "negative",
  WAIT_FOR_PULLBACK: "neutral",
  WAIT_FOR_BREAKOUT_CONFIRMATION: "neutral",
  AVOID_CHASING: "caution",
  NO_TRADE: "neutral",
  HOLD: "neutral",
  REDUCE: "caution",
  TAKE_PARTIAL_PROFIT: "caution",
  HEDGE_DE_RISK: "caution",
  EXIT: "negative",
};

export const HORIZONS = ["scalp", "intraday", "swing", "position", "long_term"];
export const CONFIDENCE_LEVELS = ["low", "moderate", "high"];
export const RISK_STYLES = ["aggressive", "neutral", "conservative"];
export const EVIDENCE_TYPES = [
  "price_structure",
  "spot_flow",
  "derivatives",
  "volatility",
  "options",
  "on_chain",
  "tokenomics",
  "macro",
  "catalyst",
  "relative_strength",
  "other",
];

let cachedStates = null;

export async function loadDecisionStates() {
  if (cachedStates) return cachedStates;
  try {
    const response = await fetch("../schemas/decision-state.schema.json");
    if (!response.ok) throw new Error(`status ${response.status}`);
    const schema = await response.json();
    if (Array.isArray(schema.enum) && schema.enum.length > 0) {
      cachedStates = schema.enum;
      return cachedStates;
    }
    throw new Error("schema missing enum");
  } catch (error) {
    console.warn(
      "Falling back to embedded decision-state list (schema fetch failed):",
      error,
    );
    cachedStates = FALLBACK_STATES;
    return cachedStates;
  }
}
