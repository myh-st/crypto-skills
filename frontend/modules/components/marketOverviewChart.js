import { escapeHtml } from "../format.js";

export const MARKET_SERIES = [
  { asset: "BTC", color: "#E69A16" },
  { asset: "ETH", color: "#4B83E5" },
  { asset: "SOL", color: "#8B5CF6" },
  { asset: "SEI", color: "#D95252" },
];

const METRICS = {
  price: {
    label: "Price",
    axisLabel: "Price change (%)",
    range: 3.5,
    endValues: { BTC: 0.4, ETH: -1.2, SOL: 2.1, SEI: 2.8 },
  },
  market_cap: {
    label: "Market Cap",
    axisLabel: "Market cap change (%)",
    range: 4,
    endValues: { BTC: 0.2, ETH: -0.7, SOL: 1.8, SEI: 1.2 },
  },
  volume: {
    label: "Volume",
    axisLabel: "Volume change (%)",
    range: 20,
    endValues: { BTC: 8.2, ETH: -4.5, SOL: 12.6, SEI: 5.3 },
  },
};

const RANGES = {
  "1D": { scale: 1, labels: ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"] },
  "7D": { scale: 1.6, labels: ["Sep 21", "Sep 22", "Sep 23", "Sep 24", "Sep 25", "Sep 27"] },
  "1M": { scale: 2.5, labels: ["Aug 27", "Sep 02", "Sep 08", "Sep 14", "Sep 20", "Sep 27"] },
  "3M": { scale: 3.5, labels: ["Jul 01", "Jul 15", "Aug 01", "Aug 15", "Sep 01", "Sep 27"] },
  "1Y": { scale: 5, labels: ["Oct 25", "Dec 25", "Feb 26", "Apr 26", "Jun 26", "Sep 26"] },
  ALL: { scale: 6.5, labels: ["2021", "2022", "2023", "2024", "2025", "2026"] },
};

function seededRng(seedText) {
  let seed = 0;
  for (let index = 0; index < seedText.length; index += 1) {
    seed = (seed * 31 + seedText.charCodeAt(index)) >>> 0;
  }
  return () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  };
}

function buildSeries(asset, metric, range, points = 31) {
  const rng = seededRng(`${asset}|${metric}|${range}`);
  const metricConfig = METRICS[metric];
  const rangeConfig = RANGES[range];
  const endValue = metricConfig.endValues[asset] * rangeConfig.scale;
  const volatility = metricConfig.range * rangeConfig.scale * 0.8;

  return Array.from({ length: points }, (_, index) => {
    if (index === 0) return 0;
    if (index === points - 1) return endValue;
    const progress = index / (points - 1);
    const bridge = Math.sin(Math.PI * progress);
    const drift = endValue * progress;
    return drift + (rng() - 0.5) * volatility * bridge;
  });
}

function formatChange(value) {
  const rounded = Number(value.toFixed(1));
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(1)}%`;
}

function chartMarkup(metric, range, visibleAssets) {
  const metricConfig = METRICS[metric];
  const rangeConfig = RANGES[range];
  const width = 960;
  const height = 330;
  const plot = { left: 82, right: 938, top: 28, bottom: 270 };
  const yLimit = metricConfig.range * rangeConfig.scale;
  const y = (value) => plot.top + ((yLimit - value) / (2 * yLimit)) * (plot.bottom - plot.top);
  const x = (index, count) => plot.left + (index / (count - 1)) * (plot.right - plot.left);
  const ticks = [-yLimit, -yLimit / 2, 0, yLimit / 2, yLimit];
  const activeSeries = MARKET_SERIES.filter(({ asset }) => visibleAssets.has(asset));

  const grid = ticks.map((tick) => {
    const tickY = y(tick);
    return `
      <line x1="${plot.left}" y1="${tickY.toFixed(1)}" x2="${plot.right}" y2="${tickY.toFixed(1)}" class="market-chart-grid${tick === 0 ? " market-chart-grid--zero" : ""}" />
      <text x="${plot.left - 10}" y="${(tickY + 4).toFixed(1)}" class="market-chart-tick" text-anchor="end">${formatChange(tick)}</text>
    `;
  }).join("");

  const xGrid = rangeConfig.labels.map((label, index) => {
    const tickX = plot.left + (index / (rangeConfig.labels.length - 1)) * (plot.right - plot.left);
    return `
      <line x1="${tickX.toFixed(1)}" y1="${plot.top}" x2="${tickX.toFixed(1)}" y2="${plot.bottom}" class="market-chart-grid market-chart-grid--vertical" />
      <text x="${tickX.toFixed(1)}" y="${plot.bottom + 22}" class="market-chart-tick" text-anchor="middle">${label}</text>
    `;
  }).join("");

  const lines = activeSeries.map(({ asset, color }) => {
    const values = buildSeries(asset, metric, range);
    const points = values
      .map((value, index) => `${x(index, values.length).toFixed(1)},${y(value).toFixed(1)}`)
      .join(" ");
    const endX = x(values.length - 1, values.length);
    const endY = y(values.at(-1));
    return `
      <polyline points="${points}" class="market-chart-line" stroke="${color}">
        <title>${asset} demo change ${formatChange(values.at(-1))}</title>
      </polyline>
      <circle cx="${endX.toFixed(1)}" cy="${endY.toFixed(1)}" r="3.5" class="market-chart-endpoint" fill="${color}" />
    `;
  }).join("");

  const legend = MARKET_SERIES.map(({ asset, color }) => {
    const active = visibleAssets.has(asset);
    const endValue = METRICS[metric].endValues[asset] * rangeConfig.scale;
    return `
      <button type="button" class="market-legend-toggle${active ? " is-active" : ""}" data-market-asset="${asset}" aria-pressed="${active}" aria-label="Toggle ${asset} series">
        <span class="market-legend-swatch" style="--series-color:${color}"></span>
        <span class="market-legend-asset">${asset}</span>
        <span class="market-legend-value">${formatChange(endValue)}</span>
      </button>
    `;
  }).join("");

  return `
    <div class="market-overview-heading">
      <div>
        <h2>Market overview <span class="demo-tag">Demo chart</span></h2>
        <p class="panel-subtitle">Compare relative movement across major assets. Values are synthetic and not live prices.</p>
      </div>
    </div>
    <div class="market-chart-toolbar">
      <div class="market-chart-tabs" role="tablist" aria-label="Market chart metric">
        ${Object.entries(METRICS).map(([key, item]) => `
          <button type="button" role="tab" class="market-chart-tab${metric === key ? " is-active" : ""}" data-market-metric="${key}" aria-selected="${metric === key}">${item.label}</button>
        `).join("")}
      </div>
      <div class="market-chart-ranges" role="group" aria-label="Market chart time range">
        ${Object.keys(RANGES).map((item) => `
          <button type="button" class="market-chart-range${range === item ? " is-active" : ""}" data-market-range="${item}" aria-pressed="${range === item}">${item}</button>
        `).join("")}
      </div>
    </div>
    <div class="market-chart-legend" aria-label="Chart series">${legend}</div>
    <figure class="market-chart-figure">
      <div class="market-chart-scroll">
        <svg viewBox="0 0 ${width} ${height}" class="market-chart-svg" role="img" aria-labelledby="market-chart-title market-chart-description">
          <title id="market-chart-title">Synthetic ${metricConfig.label.toLowerCase()} comparison</title>
          <desc id="market-chart-description">${metricConfig.axisLabel} over ${range}. Use the legend buttons to show or hide an asset.</desc>
          ${grid}
          ${xGrid}
          ${lines || `<text x="${(plot.left + plot.right) / 2}" y="${(plot.top + plot.bottom) / 2}" class="market-chart-empty" text-anchor="middle">Select an asset to display its series.</text>`}
          <text x="18" y="${(plot.top + plot.bottom) / 2}" class="market-chart-axis-title" text-anchor="middle" transform="rotate(-90 18 ${(plot.top + plot.bottom) / 2})">${metricConfig.axisLabel}</text>
          <text x="${(plot.left + plot.right) / 2}" y="${height - 5}" class="market-chart-axis-title" text-anchor="middle">Time (${range})</text>
        </svg>
      </div>
      <figcaption class="chart-caption">Y-axis: change from the start of the selected range · X-axis: time · Synthetic demo data only.</figcaption>
    </figure>
  `;
}

export function renderMarketOverviewChart({ metric = "price", range = "1D", visibleAssets = new Set(MARKET_SERIES.map(({ asset }) => asset)) } = {}) {
  const selectedMetric = METRICS[metric] ? metric : "price";
  const selectedRange = RANGES[range] ? range : "1D";
  const selectedAssets = new Set(
    MARKET_SERIES
      .map(({ asset }) => asset)
      .filter((asset) => visibleAssets.has(asset)),
  );

  return chartMarkup(selectedMetric, selectedRange, selectedAssets);
}
