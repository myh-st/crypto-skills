import { escapeHtml } from "../format.js";

export function renderSparkline(asset, values, change24h) {
  const width = 116;
  const height = 26;
  const inset = 2;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const valueRange = max - min || 1;
  const points = values
    .map((value, index) => {
      const x = inset + (index / Math.max(1, values.length - 1)) * (width - inset * 2);
      const y = height - inset - ((value - min) / valueRange) * (height - inset * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const color = change24h >= 0 ? "#16865c" : "#d14343";

  return `
    <svg viewBox="0 0 ${width} ${height}" class="asset-sparkline" role="img" aria-label="${escapeHtml(asset)} synthetic 24 hour price trend">
      <polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  `;
}
