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
  assert.match(html, /<span class="demo-tag">Demo chart<\/span>/);
  assert.match(html, /Values are synthetic and not live prices\./);
  assert.match(html, /Synthetic demo data only\./);
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
  assert.match(html, /ENTRY ZONE/);
  assert.match(html, /Secondary entry/);
  assert.match(html, /INVALID/);
  assert.match(html, /TP1/);
  assert.match(html, /REFERENCE/);
  assert.match(html, /Synthetic OHLC/);
});

test("price chart time-range controls select the requested interval", () => {
  const html = renderPriceChart(sampleRun(), "1W");
  assert.match(html, /Time \(1W\)/);
  assert.match(html, /data-chart-range="1W" aria-pressed="true"/);
});

test("live price chart uses only supplied closed candles and omits absent decision levels", () => {
  const run = sampleRun();
  run.runtimeMode = "live";
  run.requestSettings.dataAsOf = "2026-09-27T17:00:00.000000Z";
  run.marketCandles = [
    {
      open_time: "2026-09-27T14:00:00.000000Z",
      close_time: "2026-09-27T15:00:00.000000Z",
      open: 100,
      high: 103,
      low: 99,
      close: 102,
      volume: 12,
    },
    {
      open_time: "2026-09-27T15:00:00.000000Z",
      close_time: "2026-09-27T16:00:00.000000Z",
      open: 102,
      high: 104,
      low: 101,
      close: 103,
      volume: 15,
    },
  ];
  run.priceLevels = {
    current: 103,
    entryZone: null,
    secondaryEntry: null,
    invalidation: null,
    targets: [],
  };

  const html = renderPriceChart(run);

  assert.equal((html.match(/class="chart-candle chart-candle--/g) || []).length, 2);
  assert.match(html, /Binance Spot · closed OHLCV/);
  assert.match(html, /Volume \(base units\)/);
  assert.match(html, /2026-09-27T15:00:00.000Z/);
  assert.doesNotMatch(html, /Synthetic OHLC|synthetic fixture data|demo units|ENTRY 2|INVALID/);
});

test("live trading chart uses distinct UTC candle times and only supplied overlays", () => {
  const run = sampleRun();
  run.runtimeMode = "live";
  run.entryKind = "pullback";
  run.requestSettings = {
    dataAsOf: "2026-09-28T01:00:00.000000Z",
    interval: "1h",
  };
  run.marketCandles = [
    {
      open_time: "2026-09-27T21:00:00.000000Z",
      close_time: "2026-09-27T22:00:00.000000Z",
      open: 100,
      high: 101.5,
      low: 99.5,
      close: 101,
      volume: 12,
    },
    {
      open_time: "2026-09-27T22:00:00.000000Z",
      close_time: "2026-09-27T23:00:00.000000Z",
      open: 101,
      high: 104,
      low: 100.5,
      close: 103,
      volume: 18,
    },
    {
      open_time: "2026-09-28T00:00:00.000000Z",
      close_time: "2026-09-28T01:00:00.000000Z",
      open: 103,
      high: 104,
      low: 101,
      close: 102,
      volume: 15,
    },
  ];
  run.priceLevels = {
    current: 102,
    entryZone: [98, 99],
    secondaryEntry: null,
    invalidation: 97,
    targets: [106, 110],
  };
  run.marketStructure = {
    keyLevels: [
      { label: "Snapshot low", price: 99.5 },
      { label: "Snapshot high", price: 104 },
    ],
  };

  const chart = renderPriceChart(run, "1D");
  const fittedChart = renderPriceChart(run, "1D", true);
  const triggerRun = {
    ...run,
    entryKind: "breakout",
    priceLevels: { ...run.priceLevels, entryZone: [105, 105] },
  };
  const triggerChart = renderPriceChart(triggerRun, "1D");

  assert.equal((chart.match(/class="chart-candle chart-candle--/g) || []).length, 3);
  assert.ok(/Sep 27, 22:00/.test(chart), "first UTC candle time is shown");
  assert.ok(/Sep 27, 23:00/.test(chart), "second UTC candle time is shown");
  assert.ok(/Sep 28, 01:00/.test(chart), "next-day UTC candle time is shown");
  assert.ok(/3 of 3 closed 1h candles/.test(chart), "actual candle coverage is shown");
  assert.ok(/aria-label="Latest candle OHLCV"/.test(chart), "latest OHLCV is labeled");
  assert.ok(/REFERENCE \$102\.00/.test(chart), "reference price is labeled");
  assert.ok(/ENTRY ZONE ↓ \$98\.000–\$99\.000/.test(chart), "off-chart entry zone shows exact prices");
  assert.ok(/INVALIDATION ↓ \$97\.000/.test(chart), "off-chart invalidation shows exact price");
  assert.ok(/TP1 ↑ \$106\.00/.test(chart), "first target shows exact price");
  assert.ok(/TP2 ↑ \$110\.00/.test(chart), "second target shows exact price");
  assert.ok(/Snapshot low \$99\.500/.test(chart), "provided snapshot low is labeled");
  assert.ok(/Snapshot high \$104\.00/.test(chart), "provided snapshot high is labeled");
  assert.ok(/chart-target-line chart-level-edge/.test(chart), "off-range target is marked at the scale edge");
  assert.ok(!/>NOW /.test(chart), "the stale NOW label is not used");
  assert.ok(!/Support|Resistance/.test(chart), "no unsupported support/resistance claims are added");
  assert.doesNotMatch(chart, /class="chart-entry-band"/);

  assert.match(fittedChart, /class="chart-entry-band"/);
  assert.match(fittedChart, /class="chart-target-line"/);
  assert.match(fittedChart, /data-chart-fit-levels aria-pressed="true">Fit candles/);
  assert.match(triggerChart, /ENTRY TRIGGER ↑ \$105\.00/);
  assert.match(triggerChart, /chart-entry-trigger-line chart-level-edge/);
  assert.doesNotMatch(triggerChart, /class="chart-entry-band"/);
});

test("live chart range buttons filter only the closed candles actually returned", () => {
  const run = sampleRun();
  run.runtimeMode = "live";
  run.entryKind = "none";
  run.requestSettings = { interval: "1h" };
  run.marketCandles = Array.from({ length: 28 }, (_, index) => {
    const openTime = Date.UTC(2026, 8, 26, 21 + index);
    const base = 100 + index * 0.1;
    return {
      open_time: new Date(openTime).toISOString(),
      close_time: new Date(openTime + 60 * 60 * 1000).toISOString(),
      open: base,
      high: base + 0.2,
      low: base - 0.2,
      close: base + 0.1,
      volume: 10 + index,
    };
  });
  run.priceLevels = {
    current: run.marketCandles.at(-1).close,
    entryZone: null,
    secondaryEntry: null,
    invalidation: null,
    targets: [],
  };
  run.marketStructure = { keyLevels: [] };

  const oneDay = renderPriceChart(run, "1D");
  const threeMonths = renderPriceChart(run, "3M");

  assert.equal((oneDay.match(/class="chart-candle chart-candle--/g) || []).length, 24);
  assert.equal((threeMonths.match(/class="chart-candle chart-candle--/g) || []).length, 28);
  assert.match(oneDay, /24 of 28 closed 1h candles/);
  assert.match(threeMonths, /28 of 28 closed 1h candles/);
  assert.match(oneDay, /Sep 27, 02:00/);
  assert.match(oneDay, /Sep 28, 01:00/);
  assert.doesNotMatch(oneDay, /synthetic fixture data|NOW |TP1|INVALIDATION/);
});

test("maximum supported target overlays remain inside the SVG viewBox", () => {
  const run = sampleRun();
  run.runtimeMode = "live";
  run.entryKind = "none";
  run.requestSettings = { interval: "1h" };
  run.marketCandles = [
    {
      open_time: "2026-09-27T14:00:00.000000Z",
      close_time: "2026-09-27T15:00:00.000000Z",
      open: 100,
      high: 103,
      low: 99,
      close: 101,
      volume: 10,
    },
    {
      open_time: "2026-09-27T15:00:00.000000Z",
      close_time: "2026-09-27T16:00:00.000000Z",
      open: 101,
      high: 104,
      low: 100,
      close: 102,
      volume: 11,
    },
  ];
  run.priceLevels = {
    current: 102,
    entryZone: [100.5, 101],
    secondaryEntry: null,
    invalidation: 98,
    targets: [110, 111, 112, 113, 114],
  };
  run.marketStructure = {
    keyLevels: [
      { label: "Snapshot low", price: 99 },
      { label: "Snapshot high", price: 104 },
    ],
  };

  const html = renderPriceChart(run, "1D");
  const labelBaselines = [...html.matchAll(
    /<text x="[^"]+" y="(-?[\d.]+)" class="chart-level-label/g,
  )].map((match) => Number(match[1]));

  assert.equal((html.match(/class="chart-target-line chart-level-edge"/g) || []).length, 5);
  assert.equal(labelBaselines.length, 10);
  assert.ok(labelBaselines.every((value) => value >= 0 && value <= 455));
});
