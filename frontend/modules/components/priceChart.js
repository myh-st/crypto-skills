import { escapeHtml, formatPrice } from "../format.js";

const CHART_RANGES = {
  "1D": ["00:00", "06:00", "12:00", "18:00", "24:00"],
  "1W": ["Mon", "Tue", "Wed", "Thu", "Fri"],
  "1M": ["Sep 01", "Sep 08", "Sep 15", "Sep 22", "Sep 27"],
  "3M": ["Jul 01", "Aug 01", "Sep 01", "Oct 01", "Oct 27"],
};

const RANGE_WINDOW_MS = {
  "1D": 24 * 60 * 60 * 1000,
  "1W": 7 * 24 * 60 * 60 * 1000,
  "1M": 30 * 24 * 60 * 60 * 1000,
  "3M": 90 * 24 * 60 * 60 * 1000,
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
  if (Array.isArray(run.marketCandles) && run.marketCandles.length) {
    const rawCandles = run.marketCandles;
    const lastCloseTime = Date.parse(rawCandles[rawCandles.length - 1].close_time);
    const windowStart = lastCloseTime - RANGE_WINDOW_MS[range];
    return rawCandles
      .filter((candle) => Date.parse(candle.open_time) >= windowStart)
      .map((candle) => ({
      open: Number(candle.open),
      high: Number(candle.high),
      low: Number(candle.low),
      close: Number(candle.close),
      volume: Number(candle.volume),
      openTime: candle.open_time,
      closeTime: candle.close_time,
      }));
  }
  if (run.runtimeMode === "live") return [];
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

function formatUtcTimestamp(value, includeTime = true, includeYear = true) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value ?? "");
  const options = {
    timeZone: "UTC",
    month: "short",
    day: "numeric",
    ...(includeYear ? { year: "numeric" } : {}),
    ...(includeTime ? { hour: "2-digit", minute: "2-digit", hourCycle: "h23" } : {}),
  };
  return new Intl.DateTimeFormat("en-US", options).format(date);
}

function formatAxisTimestamp(value, spanMilliseconds) {
  return formatUtcTimestamp(
    value,
    spanMilliseconds <= 2 * 24 * 60 * 60 * 1000,
    false,
  );
}

function overlayLevels(run) {
  const { priceLevels } = run;
  const levels = [];

  if (Array.isArray(priceLevels.entryZone) && priceLevels.entryZone.length === 2) {
    const [zoneLow, zoneHigh] = priceLevels.entryZone;
    const isTrigger = run.entryKind === "breakout";
    const isImmediate = run.entryKind === "immediate";
    const entryLabel = isTrigger
      ? "ENTRY TRIGGER"
      : isImmediate
        ? "ENTRY REFERENCE"
        : "ENTRY ZONE";
    levels.push({
      value: (zoneLow + zoneHigh) / 2,
      range: [zoneLow, zoneHigh],
      label: entryLabel,
      tone: "entry",
      type: isTrigger ? "entry-trigger" : isImmediate ? "entry-reference" : "entry",
    });
  }
  if (Array.isArray(priceLevels.secondaryEntry) && priceLevels.secondaryEntry.length === 2) {
    const [zoneLow, zoneHigh] = priceLevels.secondaryEntry;
    levels.push({
      value: (zoneLow + zoneHigh) / 2,
      range: [zoneLow, zoneHigh],
      label: "SECONDARY ENTRY",
      tone: "secondary",
      type: "entry",
    });
  }
  if (Number.isFinite(priceLevels.invalidation)) {
    levels.push({
      value: priceLevels.invalidation,
      label: "INVALIDATION",
      tone: "invalidation",
      type: "invalidation",
    });
  }
  priceLevels.targets.forEach((value, index) => {
    if (Number.isFinite(value)) {
      levels.push({
        value,
        label: `TP${index + 1}`,
        tone: "target",
        type: "target",
      });
    }
  });
  if (Number.isFinite(priceLevels.current)) {
    levels.push({
      value: priceLevels.current,
      label: "REFERENCE",
      tone: "current",
      type: "reference",
    });
  }

  const keyLevels = Array.isArray(run.marketStructure?.keyLevels)
    ? run.marketStructure.keyLevels
    : [];
  keyLevels.forEach((keyLevel) => {
    if (
      keyLevel
      && typeof keyLevel.label === "string"
      && keyLevel.label.trim()
      && Number.isFinite(keyLevel.price)
    ) {
      levels.push({
        value: keyLevel.price,
        label: keyLevel.label.trim(),
        tone: "structure",
        type: "structure",
      });
    }
  });
  return levels;
}

function distributeLevelLabels(levels, scaleY, top, bottom, minimum, maximum) {
  const labels = levels
    .map((level) => {
      const actualY = scaleY(level.value);
      return {
        ...level,
        actualY,
        labelAnchorY: Math.max(top + 7, Math.min(bottom - 7, actualY)),
        offScale: (level.range?.[0] ?? level.value) > maximum
          ? "above"
          : (level.range?.[1] ?? level.value) < minimum
            ? "below"
            : null,
      };
    })
    .sort((first, second) => first.actualY - second.actualY);

  for (let index = 0; index < labels.length; index += 1) {
    const previous = labels[index - 1];
    labels[index].labelY = Math.max(
      labels[index].labelAnchorY,
      previous ? previous.labelY + 17 : top + 8,
    );
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

function levelLabel(level) {
  const price = level.range
    ? `${formatPrice(level.range[0])}–${formatPrice(level.range[1])}`
    : formatPrice(level.value);
  const direction = level.offScale === "above" ? " ↑" : level.offScale === "below" ? " ↓" : "";
  return `${level.label}${direction} ${price}`;
}

function renderLevelLabels(levels, scaleY, plot, minimum, maximum) {
  return distributeLevelLabels(
    levels,
    scaleY,
    plot.top,
    plot.bottom,
    minimum,
    maximum,
  )
    .map((level) => `
      <line x1="${plot.right}" y1="${level.labelAnchorY.toFixed(1)}" x2="${plot.right + 5}" y2="${level.labelY.toFixed(1)}" class="chart-level-leader chart-level-leader--${level.tone}" />
      <text x="${plot.right + 9}" y="${(level.labelY + 3.5).toFixed(1)}" class="chart-level-label chart-level-label--${level.tone}">${escapeHtml(levelLabel(level))}</text>
    `)
    .join("");
}

export function renderPriceChart(
  run,
  selectedRange = "1M",
  fitAllLevels = run.runtimeMode !== "live",
) {
  const range = CHART_RANGES[selectedRange] ? selectedRange : "1M";
  const candles = buildCandles(run, range);
  if (!candles.length) {
    return '<p class="muted">No closed market candles are available for this chart.</p>';
  }
  const { priceLevels } = run;
  const isLive = run.runtimeMode === "live";
  const levels = overlayLevels(run);
  const candleMinimum = Math.min(
    ...candles.map((candle) => candle.low),
    ...levels.filter((level) => level.type === "structure").map((level) => level.value),
  );
  const candleMaximum = Math.max(
    ...candles.map((candle) => candle.high),
    ...levels.filter((level) => level.type === "structure").map((level) => level.value),
  );
  const decisionValues = levels
    .filter((level) => level.type !== "structure")
    .flatMap((level) => level.range || [level.value]);
  const dataMinimum = fitAllLevels
    ? Math.min(candleMinimum, ...decisionValues)
    : candleMinimum;
  const dataMaximum = fitAllLevels
    ? Math.max(candleMaximum, ...decisionValues)
    : candleMaximum;
  const nonZeroSpan = dataMaximum - dataMinimum;
  const scaleSpan = nonZeroSpan > 0
    ? nonZeroSpan
    : Math.max(Math.abs(dataMinimum) * 0.001, 0.000001);
  const axisMinimum = Math.max(0, dataMinimum - scaleSpan * 0.08);
  const axisMaximum = dataMaximum + scaleSpan * 0.08;
  const width = 1050;
  const height = 455;
  const plot = { left: 84, right: 824, top: 38, bottom: 312 };
  const volume = { top: 352, bottom: 407 };
  const scaleY = (value) => (
    plot.bottom
    - ((value - axisMinimum) / (axisMaximum - axisMinimum || 1))
      * (plot.bottom - plot.top)
  );
  const clampY = (value) => Math.max(plot.top, Math.min(plot.bottom, scaleY(value)));
  const candleStep = (plot.right - plot.left) / candles.length;
  const candleWidth = Math.max(2, Math.min(12, candleStep * 0.65));
  const maxVolume = Math.max(1, ...candles.map((candle) => candle.volume));
  const chartTitleId = `price-chart-title-${escapeHtml(run.id)}`;
  const chartDescId = `price-chart-description-${escapeHtml(run.id)}`;

  const yTicks = Array.from(
    { length: 6 },
    (_, index) => axisMaximum - ((axisMaximum - axisMinimum) * index) / 5,
  );
  const horizontalGrid = yTicks.map((tick) => {
    const tickY = scaleY(tick);
    return `
      <line x1="${plot.left}" y1="${tickY.toFixed(1)}" x2="${plot.right}" y2="${tickY.toFixed(1)}" class="chart-gridline" />
      <text x="${plot.left - 10}" y="${(tickY + 4).toFixed(1)}" class="chart-axis-tick" text-anchor="end">${formatPrice(tick)}</text>
    `;
  }).join("");

  const liveStartTime = isLive ? Date.parse(candles[0].openTime) : null;
  const liveEndTime = isLive ? Date.parse(candles[candles.length - 1].closeTime) : null;
  const liveSpanMilliseconds = isLive ? Math.max(0, liveEndTime - liveStartTime) : 0;
  const liveTickCount = Math.min(5, candles.length);
  const liveTickIndexes = liveTickCount === 1
    ? [0]
    : Array.from(
      { length: liveTickCount },
      (_, index) => Math.round((index / (liveTickCount - 1)) * (candles.length - 1)),
    );
  const xTicks = isLive
    ? liveTickIndexes.map((sourceIndex) => {
      const candle = candles[sourceIndex];
      const tickX = plot.left + candleStep * (sourceIndex + 0.5);
      return `
        <line x1="${tickX.toFixed(1)}" y1="${plot.top}" x2="${tickX.toFixed(1)}" y2="${volume.bottom}" class="chart-gridline chart-gridline--vertical" />
        <text x="${tickX.toFixed(1)}" y="${volume.bottom + 19}" class="chart-axis-tick" text-anchor="middle">${escapeHtml(formatAxisTimestamp(candle.closeTime, liveSpanMilliseconds))}</text>
      `;
    }).join("")
    : CHART_RANGES[range].map((label, index) => {
      const denominator = CHART_RANGES[range].length - 1;
      const tickX = plot.left + (index / denominator) * (plot.right - plot.left);
      return `
        <line x1="${tickX.toFixed(1)}" y1="${plot.top}" x2="${tickX.toFixed(1)}" y2="${volume.bottom}" class="chart-gridline chart-gridline--vertical" />
        <text x="${tickX.toFixed(1)}" y="${volume.bottom + 19}" class="chart-axis-tick" text-anchor="middle">${escapeHtml(label)}</text>
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
    const timeLabel = candle.closeTime
      ? new Date(candle.closeTime).toISOString()
      : CHART_RANGES[range][Math.min(
      CHART_RANGES[range].length - 1,
      Math.floor((index / candles.length) * CHART_RANGES[range].length),
    )];

    return `
      <g class="chart-candle chart-candle--${direction}">
        <title>${escapeHtml(timeLabel)} · O ${formatPrice(candle.open)} · H ${formatPrice(candle.high)} · L ${formatPrice(candle.low)} · C ${formatPrice(candle.close)} · Volume ${candle.volume.toFixed(0)} ${isLive ? "base units" : "demo units"}</title>
        <line x1="${centerX.toFixed(1)}" y1="${highY.toFixed(1)}" x2="${centerX.toFixed(1)}" y2="${lowY.toFixed(1)}" class="chart-candle-wick" />
        <rect x="${(centerX - candleWidth / 2).toFixed(1)}" y="${bodyTop.toFixed(1)}" width="${candleWidth.toFixed(1)}" height="${bodyHeight.toFixed(1)}" class="chart-candle-body" />
        <rect x="${(centerX - candleWidth / 2).toFixed(1)}" y="${(volume.bottom - volumeHeight).toFixed(1)}" width="${candleWidth.toFixed(1)}" height="${volumeHeight.toFixed(1)}" class="chart-volume-bar" />
      </g>
    `;
  }).join("");

  const edgeOrFullLine = (level, className) => {
    const isVisible = level.value >= axisMinimum && level.value <= axisMaximum;
    const y = clampY(level.value);
    return `<line x1="${isVisible ? plot.left : plot.right - 10}" y1="${y.toFixed(1)}" x2="${plot.right}" y2="${y.toFixed(1)}" class="${className}${isVisible ? "" : " chart-level-edge"}" />`;
  };
  const targetLines = levels
    .filter((level) => level.type === "target")
    .map((level) => edgeOrFullLine(level, "chart-target-line"))
    .join("");
  const invalidationLevel = levels.find((level) => level.type === "invalidation");
  const invalidationLine = invalidationLevel
    ? edgeOrFullLine(invalidationLevel, "chart-invalidation-line")
    : "";
  const referenceLevel = levels.find((level) => level.type === "reference");
  const referenceLine = referenceLevel
    ? edgeOrFullLine(referenceLevel, "chart-current-line")
    : "";
  const structureLines = levels
    .filter((level) => level.type === "structure")
    .map((level) => edgeOrFullLine(level, "chart-structure-line"))
    .join("");

  const renderEntryLevel = (level, bandClass, lineClass) => {
    if (!level?.range) return "";
    const [low, high] = level.range;
    const isOutside = high < axisMinimum || low > axisMaximum;
    if (isOutside) {
      const edge = high < axisMinimum ? "below" : "above";
      const edgeY = edge === "below" ? plot.bottom : plot.top;
      return `<line x1="${plot.right - 10}" y1="${edgeY}" x2="${plot.right}" y2="${edgeY}" class="${lineClass} chart-level-edge" />`;
    }
    if (level.type === "entry-trigger" || level.type === "entry-reference") {
      return edgeOrFullLine(level, lineClass);
    }
    const visibleLow = Math.max(low, axisMinimum);
    const visibleHigh = Math.min(high, axisMaximum);
    const top = scaleY(visibleHigh);
    const bottom = scaleY(visibleLow);
    return `<rect x="${plot.left}" y="${Math.min(top, bottom).toFixed(1)}" width="${plot.right - plot.left}" height="${Math.max(3, Math.abs(bottom - top)).toFixed(1)}" class="${bandClass}" />`;
  };
  const primaryEntryLevel = levels.find((level) => (
    level.type === "entry" || level.type === "entry-trigger" || level.type === "entry-reference"
  ));
  const secondaryEntryLevel = levels.find((level) => level.type === "entry" && level.label === "SECONDARY ENTRY");
  const primaryEntry = renderEntryLevel(
    primaryEntryLevel,
    "chart-entry-band",
    primaryEntryLevel?.type === "entry-trigger" ? "chart-entry-trigger-line" : "chart-entry-band-line",
  );
  const secondaryEntry = renderEntryLevel(
    secondaryEntryLevel,
    "chart-secondary-band",
    "chart-secondary-band-line",
  );

  const legendItems = [
    ["Candles", "chart-legend-candle"],
    ...(primaryEntryLevel ? [[primaryEntryLevel.label, "chart-legend-entry"]] : []),
    ...(secondaryEntryLevel ? [["Secondary entry", "chart-legend-secondary"]] : []),
    ...(levels.some((level) => level.type === "target") ? [["Targets", "chart-legend-target"]] : []),
    ...(invalidationLevel ? [["Invalidation", "chart-legend-invalidation"]] : []),
    ...(referenceLevel ? [["Reference price", "chart-legend-current"]] : []),
    ...(levels.some((level) => level.type === "structure") ? [["Snapshot levels", "chart-legend-structure"]] : []),
    ["Volume", "chart-legend-volume"],
  ];
  const provenance = isLive ? "Binance Spot · closed OHLCV" : "Synthetic OHLC";
  const volumeUnits = isLive ? "base units" : "demo units";
  const latestCandle = candles[candles.length - 1];
  const intervalLabel = run.requestSettings?.interval || "bar";
  const timeWindow = isLive
    ? `${formatUtcTimestamp(candles[0].openTime)} – ${formatUtcTimestamp(latestCandle.closeTime)} UTC · ${candles.length} of ${run.marketCandles.length} closed ${intervalLabel} candles`
    : `Fixture ${range} · ${candles.length} synthetic candles`;
  const latestOhlc = `
    <div class="chart-ohlc-summary" aria-label="Latest candle OHLCV">
      <span>O <strong>${formatPrice(latestCandle.open)}</strong></span>
      <span>H <strong>${formatPrice(latestCandle.high)}</strong></span>
      <span>L <strong>${formatPrice(latestCandle.low)}</strong></span>
      <span>C <strong>${formatPrice(latestCandle.close)}</strong></span>
      <span>V <strong>${latestCandle.volume.toLocaleString("en-US", { maximumFractionDigits: 2 })}</strong></span>
    </div>
  `;
  const fitButtonText = fitAllLevels ? "Fit candles" : "Fit all levels";
  const chartCaption = isLive
    ? "UTC timestamps and OHLCV use only the frozen closed-candle snapshot. Off-range decision levels are marked at the chart edge with their exact prices; use Fit all levels to expand the price scale."
    : "Axes and overlays are labeled for quick reading. Price candles and volume are synthetic fixture data, not live exchange data.";

  return `
    <div class="price-chart-shell">
      <div class="price-chart-toolbar">
        <div class="price-chart-instrument">
          <strong>${escapeHtml(run.asset)} / USDT</strong>
          <span class="demo-tag">${provenance}</span>
        </div>
        <div class="price-chart-ranges" role="group" aria-label="Price chart time range">
          ${Object.keys(CHART_RANGES).map((item) => `
            <button type="button" class="chart-range-button${range === item ? " is-active" : ""}" data-chart-range="${item}" aria-pressed="${range === item}">${item}</button>
          `).join("")}
          <button type="button" class="chart-range-button" data-chart-fit-levels aria-pressed="${fitAllLevels}">${fitButtonText}</button>
        </div>
        <span class="price-chart-type-label">Candlestick · Volume</span>
        <span class="price-chart-window">${escapeHtml(timeWindow)}</span>
      </div>
      ${latestOhlc}
      <div class="price-chart-legend" aria-label="Price chart legend">
        ${legendItems.map(([label, style]) => `
          <span class="price-chart-legend-item"><span class="price-chart-legend-swatch ${style}" aria-hidden="true"></span>${label}</span>
        `).join("")}
      </div>
      <div class="price-chart-scroll">
        <svg viewBox="0 0 ${width} ${height}" class="price-chart" role="img" aria-labelledby="${chartTitleId} ${chartDescId}">
          <title id="${chartTitleId}">${escapeHtml(run.asset)} / USDT ${isLive ? "Binance Spot" : "synthetic"} candlestick price chart</title>
          <desc id="${chartDescId}">Price in USDT over ${range}. ${isLive ? "Candles and volume are closed Binance Spot observations with UTC timestamps." : "Candlesticks and volume bars are synthetic."} Reference, entry, invalidation, targets and snapshot levels are rendered only when supplied.</desc>
          <rect x="${plot.left}" y="${plot.top}" width="${plot.right - plot.left}" height="${plot.bottom - plot.top}" class="chart-plot-background" />
          ${primaryEntry}
          ${secondaryEntry}
          ${structureLines}
          ${horizontalGrid}
          ${xTicks}
          ${invalidationLine}
          ${targetLines}
          ${referenceLine}
          ${candleMarkup}
          <line x1="${plot.left}" y1="${volume.top}" x2="${plot.right}" y2="${volume.top}" class="chart-volume-baseline" />
          <text x="${plot.left}" y="22" class="chart-axis-title">Price (USDT)</text>
          <text x="${plot.left}" y="${volume.top - 7}" class="chart-axis-title">Volume (${volumeUnits})</text>
          <text x="${(plot.left + plot.right) / 2}" y="${height - 3}" class="chart-axis-title" text-anchor="middle">${isLive ? "Time (UTC)" : `Time (${range})`}</text>
          ${renderLevelLabels(levels, scaleY, plot, axisMinimum, axisMaximum)}
        </svg>
      </div>
      <p class="chart-caption">${escapeHtml(chartCaption)}</p>
    </div>
  `;
}
