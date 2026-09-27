import { escapeHtml, formatPrice } from "../format.js";

const CHART_RANGES = {
  "1D": ["00:00", "06:00", "12:00", "18:00", "24:00"],
  "1W": ["Mon", "Tue", "Wed", "Thu", "Fri"],
  "1M": ["Sep 01", "Sep 08", "Sep 15", "Sep 22", "Sep 27"],
  "3M": ["Jul 01", "Aug 01", "Sep 01", "Oct 01", "Oct 27"],
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

function buildCandles(run, range, count = 48) {
  const { current } = run.priceLevels;
  const rng = seededRng(`${run.id}|${range}`);
  const first = current * (0.86 + rng() * 0.12);
  const rangeBias = range === "1D" ? 0.55 : range === "1W" ? 0.8 : range === "3M" ? 1.35 : 1;
  let previousClose = first;

  return Array.from({ length: count }, (_, index) => {
    const progress = (index + 1) / count;
    const trend = first + (current - first) * progress;
    const open = previousClose;
    const noise = (rng() - 0.48) * current * 0.018 * rangeBias;
    const close = index === count - 1
      ? current
      : Math.max(current * 0.35, open + (trend - open) * 0.32 + noise);
    const wick = current * (0.004 + rng() * 0.012) * rangeBias;
    const high = Math.max(open, close) + wick;
    const low = Math.max(current * 0.2, Math.min(open, close) - wick * (0.7 + rng() * 0.6));
    previousClose = close;
    return { open, high, low, close, volume: 16 + rng() * 84 };
  });
}

function distributeLevelLabels(levels, scaleY, top, bottom) {
  const labels = levels
    .map((level) => ({ ...level, actualY: scaleY(level.value) }))
    .sort((first, second) => first.actualY - second.actualY);

  for (let index = 0; index < labels.length; index += 1) {
    const previous = labels[index - 1];
    labels[index].labelY = Math.max(labels[index].actualY, previous ? previous.labelY + 16 : top + 8);
  }

  const overflow = labels.length ? labels[labels.length - 1].labelY - (bottom - 8) : 0;
  if (overflow > 0) {
    for (let index = labels.length - 1; index >= 0; index -= 1) {
      const next = labels[index + 1];
      labels[index].labelY = Math.min(labels[index].labelY - overflow, next ? next.labelY - 16 : bottom - 8);
    }
  }

  return labels;
}

function renderLevelLabels(run, scaleY, plot) {
  const { priceLevels } = run;
  const levels = [
    ...priceLevels.targets.map((value, index) => ({ value, label: `TP${index + 1} ${formatPrice(value)}`, tone: "target" })),
    { value: priceLevels.current, label: `NOW ${formatPrice(priceLevels.current)}`, tone: "current" },
    { value: (priceLevels.entryZone[0] + priceLevels.entryZone[1]) / 2, label: `ENTRY ${formatPrice(priceLevels.entryZone[0])}–${formatPrice(priceLevels.entryZone[1])}`, tone: "entry" },
    { value: (priceLevels.secondaryEntry[0] + priceLevels.secondaryEntry[1]) / 2, label: `ENTRY 2 ${formatPrice(priceLevels.secondaryEntry[0])}–${formatPrice(priceLevels.secondaryEntry[1])}`, tone: "secondary" },
    { value: priceLevels.invalidation, label: `INVALID ${formatPrice(priceLevels.invalidation)}`, tone: "invalidation" },
  ];

  return distributeLevelLabels(levels, scaleY, plot.top, plot.bottom)
    .map((level) => `
      <line x1="${plot.right}" y1="${level.actualY.toFixed(1)}" x2="${plot.right + 5}" y2="${level.labelY.toFixed(1)}" class="chart-level-leader chart-level-leader--${level.tone}" />
      <text x="${plot.right + 9}" y="${(level.labelY + 3.5).toFixed(1)}" class="chart-level-label chart-level-label--${level.tone}">${escapeHtml(level.label)}</text>
    `)
    .join("");
}

export function renderPriceChart(run, selectedRange = "1M") {
  const range = CHART_RANGES[selectedRange] ? selectedRange : "1M";
  const candles = buildCandles(run, range);
  const { priceLevels } = run;
  const width = 1050;
  const height = 455;
  const plot = { left: 84, right: 824, top: 38, bottom: 312 };
  const volume = { top: 352, bottom: 407 };
  const labelMin = Math.min(
    ...candles.map((candle) => candle.low),
    priceLevels.invalidation,
    priceLevels.entryZone[0],
    priceLevels.secondaryEntry[0],
  );
  const labelMax = Math.max(
    ...candles.map((candle) => candle.high),
    ...priceLevels.targets,
    priceLevels.current,
  );
  const min = labelMin * 0.98;
  const max = labelMax * 1.02;
  const scaleY = (value) => plot.bottom - ((value - min) / (max - min || 1)) * (plot.bottom - plot.top);
  const candleStep = (plot.right - plot.left) / candles.length;
  const candleWidth = Math.max(4, candleStep * 0.58);
  const maxVolume = Math.max(...candles.map((candle) => candle.volume));
  const invalidationY = scaleY(priceLevels.invalidation);
  const currentY = scaleY(priceLevels.current);
  const primaryTop = scaleY(priceLevels.entryZone[1]);
  const primaryBottom = scaleY(priceLevels.entryZone[0]);
  const secondaryTop = scaleY(priceLevels.secondaryEntry[1]);
  const secondaryBottom = scaleY(priceLevels.secondaryEntry[0]);
  const chartTitleId = `price-chart-title-${escapeHtml(run.id)}`;
  const chartDescId = `price-chart-description-${escapeHtml(run.id)}`;

  const yTicks = Array.from({ length: 6 }, (_, index) => max - ((max - min) * index) / 5);
  const horizontalGrid = yTicks.map((tick) => {
    const tickY = scaleY(tick);
    return `
      <line x1="${plot.left}" y1="${tickY.toFixed(1)}" x2="${plot.right}" y2="${tickY.toFixed(1)}" class="chart-gridline" />
      <text x="${plot.left - 10}" y="${(tickY + 4).toFixed(1)}" class="chart-axis-tick" text-anchor="end">${formatPrice(tick)}</text>
    `;
  }).join("");

  const xTicks = CHART_RANGES[range].map((label, index) => {
    const tickX = plot.left + (index / (CHART_RANGES[range].length - 1)) * (plot.right - plot.left);
    return `
      <line x1="${tickX.toFixed(1)}" y1="${plot.top}" x2="${tickX.toFixed(1)}" y2="${volume.bottom}" class="chart-gridline chart-gridline--vertical" />
      <text x="${tickX.toFixed(1)}" y="${volume.bottom + 19}" class="chart-axis-tick" text-anchor="middle">${label}</text>
    `;
  }).join("");

  const candleMarkup = candles.map((candle, index) => {
    const centerX = plot.left + candleStep * (index + 0.5);
    const openY = scaleY(candle.open);
    const closeY = scaleY(candle.close);
    const highY = scaleY(candle.high);
    const lowY = scaleY(candle.low);
    const bodyTop = Math.min(openY, closeY);
    const bodyHeight = Math.max(2, Math.abs(openY - closeY));
    const volumeHeight = (candle.volume / maxVolume) * (volume.bottom - volume.top);
    const direction = candle.close >= candle.open ? "up" : "down";
    const timeLabel = CHART_RANGES[range][Math.min(
      CHART_RANGES[range].length - 1,
      Math.floor((index / candles.length) * CHART_RANGES[range].length),
    )];

    return `
      <g class="chart-candle chart-candle--${direction}">
        <title>${escapeHtml(timeLabel)} · O ${formatPrice(candle.open)} · H ${formatPrice(candle.high)} · L ${formatPrice(candle.low)} · C ${formatPrice(candle.close)} · Volume ${candle.volume.toFixed(0)} demo units</title>
        <line x1="${centerX.toFixed(1)}" y1="${highY.toFixed(1)}" x2="${centerX.toFixed(1)}" y2="${lowY.toFixed(1)}" class="chart-candle-wick" />
        <rect x="${(centerX - candleWidth / 2).toFixed(1)}" y="${bodyTop.toFixed(1)}" width="${candleWidth.toFixed(1)}" height="${bodyHeight.toFixed(1)}" class="chart-candle-body" />
        <rect x="${(centerX - candleWidth / 2).toFixed(1)}" y="${(volume.bottom - volumeHeight).toFixed(1)}" width="${candleWidth.toFixed(1)}" height="${volumeHeight.toFixed(1)}" class="chart-volume-bar" />
      </g>
    `;
  }).join("");

  const targetLines = priceLevels.targets.map((target, index) => {
    const targetY = scaleY(target);
    return `<line x1="${plot.left}" y1="${targetY.toFixed(1)}" x2="${plot.right}" y2="${targetY.toFixed(1)}" class="chart-target-line" />`;
  }).join("");

  const legendItems = [
    ["Candles", "chart-legend-candle"],
    ["Primary entry", "chart-legend-entry"],
    ["Secondary entry", "chart-legend-secondary"],
    ["Targets", "chart-legend-target"],
    ["Invalidation", "chart-legend-invalidation"],
    ["Current price", "chart-legend-current"],
    ["Volume", "chart-legend-volume"],
  ];

  return `
    <div class="price-chart-shell">
      <div class="price-chart-toolbar">
        <div class="price-chart-instrument">
          <strong>${escapeHtml(run.asset)} / USDT</strong>
          <span class="demo-tag">Synthetic OHLC</span>
        </div>
        <div class="price-chart-ranges" role="group" aria-label="Price chart time range">
          ${Object.keys(CHART_RANGES).map((item) => `
            <button type="button" class="chart-range-button${range === item ? " is-active" : ""}" data-chart-range="${item}" aria-pressed="${range === item}">${item}</button>
          `).join("")}
        </div>
        <span class="price-chart-type-label">Candlestick · Volume</span>
      </div>
      <div class="price-chart-legend" aria-label="Price chart legend">
        ${legendItems.map(([label, style]) => `
          <span class="price-chart-legend-item"><span class="price-chart-legend-swatch ${style}" aria-hidden="true"></span>${label}</span>
        `).join("")}
      </div>
      <div class="price-chart-scroll">
        <svg viewBox="0 0 ${width} ${height}" class="price-chart" role="img" aria-labelledby="${chartTitleId} ${chartDescId}">
          <title id="${chartTitleId}">${escapeHtml(run.asset)} / USDT synthetic candlestick price chart</title>
          <desc id="${chartDescId}">Price in USDT over ${range}. Candlesticks and volume bars are synthetic. Overlays show the current price, primary and secondary entry zones, invalidation, and take-profit targets.</desc>
          <rect x="${plot.left}" y="${plot.top}" width="${plot.right - plot.left}" height="${plot.bottom - plot.top}" class="chart-plot-background" />
          <rect x="${plot.left}" y="${Math.min(primaryTop, primaryBottom).toFixed(1)}" width="${plot.right - plot.left}" height="${Math.max(4, Math.abs(primaryBottom - primaryTop)).toFixed(1)}" class="chart-entry-band" />
          <rect x="${plot.left}" y="${Math.min(secondaryTop, secondaryBottom).toFixed(1)}" width="${plot.right - plot.left}" height="${Math.max(4, Math.abs(secondaryBottom - secondaryTop)).toFixed(1)}" class="chart-secondary-band" />
          ${horizontalGrid}
          ${xTicks}
          <line x1="${plot.left}" y1="${invalidationY.toFixed(1)}" x2="${plot.right}" y2="${invalidationY.toFixed(1)}" class="chart-invalidation-line" />
          ${targetLines}
          <line x1="${plot.left}" y1="${currentY.toFixed(1)}" x2="${plot.right}" y2="${currentY.toFixed(1)}" class="chart-current-line" />
          ${candleMarkup}
          <line x1="${plot.left}" y1="${volume.top}" x2="${plot.right}" y2="${volume.top}" class="chart-volume-baseline" />
          <text x="${plot.left}" y="22" class="chart-axis-title">Price (USDT)</text>
          <text x="${plot.left}" y="${volume.top - 7}" class="chart-axis-title">Volume (demo units)</text>
          <text x="${(plot.left + plot.right) / 2}" y="${height - 3}" class="chart-axis-title" text-anchor="middle">Time (${range})</text>
          ${renderLevelLabels(run, scaleY, plot)}
        </svg>
      </div>
      <p class="chart-caption">Axes and overlays are labeled for quick reading. Price candles and volume are synthetic fixture data, not live exchange data.</p>
    </div>
  `;
}
