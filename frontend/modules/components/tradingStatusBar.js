// Compact runtime health / automation status strip.
import { escapeHtml, titleCase } from "../format.js";
import { usdCost } from "../numbers.js";

export function providerHealth(providers, providerId) {
  const provider = (providers || []).find((item) => item.provider_id === providerId);
  if (!provider) return { label: "missing", tone: "bad" };
  if (provider.kind.startsWith("fixture_")) return { label: "FIXTURE", tone: "idle" };
  if (provider.last_validation_status === "passed") return { label: "connected", tone: "ok" };
  if (provider.last_validation_status === "failed") return { label: "failed", tone: "bad" };
  return { label: "not tested", tone: "idle" };
}

export function renderHealthStrip(dashboard) {
  const config = dashboard.experiment.config;
  const stream = dashboard.market_stream;
  const streamState = config.market_data_mode === "gate_usdt"
    ? (stream ? stream.state : "OFFLINE")
    : config.market_data_mode === "fixture" ? "FIXTURE" : "REST ONLY";
  const streamTone = { LIVE: "ok", STALE: "warn", RECONNECTING: "warn", CONNECTING: "warn", OFFLINE: "bad" }[streamState] || "idle";
  const jev = providerHealth(dashboard.providers, config.jev_provider_id);
  const gpt = providerHealth(dashboard.providers, config.gpt_provider_id);
  const budget = dashboard.ai_cost?.budget_status;
  const remaining = budget?.remaining_today_usd;
  const budgetTone = budget?.exhausted ? "bad" : (budget?.utilization_today ?? 0) >= 0.8 ? "warn" : "ok";
  const pill = (label, value, tone) => `<span class="status-pill status-pill--${tone}"><small>${escapeHtml(label)}</small> ${escapeHtml(value)}</span>`;
  return `
    <div class="health-strip" role="status" aria-label="Runtime health">
      ${pill("Gate", streamState, streamTone)}
      ${pill("Scheduler", titleCase(dashboard.experiment.status), dashboard.experiment.status === "running" ? "ok" : "idle")}
      ${pill("Mode", "PAPER", "ok")}
      ${pill("Jev", jev.label, jev.tone)}
      ${pill("GPT", gpt.label, gpt.tone)}
      ${pill("AI budget left today", remaining === null || remaining === undefined ? "no limit" : usdCost(remaining, 2), budgetTone)}
      ${pill("Gate live orders", "BLOCKED BY DESIGN", "blocked")}
    </div>`;
}
