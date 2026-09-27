import test from "node:test";
import assert from "node:assert/strict";

import { renderMarketOverviewChart } from "../modules/components/marketOverviewChart.js";
import { renderPriceChart } from "../modules/components/priceChart.js";
import { createAnalysis } from "../modules/generator.js";

function sampleRun() {
  return createAnalysis({
    asset: "SEI",
    analysisType: "spot",
    horizon: "swing",
    question: "Review the synthetic chart levels.",
    riskStyle: "neutral",
    capital: null,
    basePrice: 0.071,
    timestamp: "2026-09-27T02:00:00.000Z",
    seedSuffix: "chart-test",
  });
}

test("market overview chart labels metrics, time, axes, and asset series", () => {
  const html = renderMarketOverviewChart();

  assert.match(html, /Market overview/);
  assert.match(html, /Price change \(%\)/);
  assert.match(html, /Time \(1D\)/);
  assert.match(html, /aria-label="Market chart metric"/);
  assert.match(html, /aria-label="Market chart time range"/);
  for (const asset of ["BTC", "ETH", "SOL", "SEI"]) {
    assert.match(html, new RegExp(`data-market-asset="${asset}"`));
  }
  for (const range of ["1D", "7D", "1M", "3M", "1Y", "ALL"]) {
    assert.match(html, new RegExp(`data-market-range="${range}"`));
  }
});

test("market overview controls render the selected metric, range, and visible series", () => {
  const html = renderMarketOverviewChart({
    metric: "volume",
    range: "7D",
    visibleAssets: new Set(["SEI"]),
  });
  const lines = html.match(/class="market-chart-line"/g) || [];

  assert.match(html, /Volume change \(%\)/);
  assert.match(html, /Time \(7D\)/);
  assert.equal(lines.length, 1);
  assert.match(html, /data-market-asset="SEI" aria-pressed="true"/);
  assert.match(html, /data-market-asset="BTC" aria-pressed="false"/);
});

test("research result chart renders labeled candlesticks, volume, and decision levels", () => {
  const run = sampleRun();
  const html = renderPriceChart(run);
  const candles = html.match(/class="chart-candle chart-candle--/g) || [];
  const targets = html.match(/class="chart-target-line"/g) || [];

  assert.equal(candles.length, 48);
  assert.equal(targets.length, run.priceLevels.targets.length);
  assert.match(html, /Price \(USDT\)/);
  assert.match(html, /Volume \(demo units\)/);
  assert.match(html, /Time \(1M\)/);
  assert.match(html, /Candlestick · Volume/);
  assert.match(html, /Primary entry/);
  assert.match(html, /Secondary entry/);
  assert.match(html, /INVALID/);
  assert.match(html, /TP1/);
  assert.match(html, /NOW/);
  assert.match(html, /Synthetic OHLC/);
});

test("price chart time-range controls select the requested interval", () => {
  const html = renderPriceChart(sampleRun(), "1W");
  assert.match(html, /Time \(1W\)/);
  assert.match(html, /data-chart-range="1W" aria-pressed="true"/);
});
