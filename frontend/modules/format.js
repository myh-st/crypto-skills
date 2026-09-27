// Small formatting helpers shared across views. No dependencies.

export function formatPrice(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  const decimals = abs >= 100 ? 2 : abs >= 1 ? 3 : 4;
  return `$${value.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`;
}

export function formatRange(range) {
  if (!range || range.length !== 2) return "—";
  return `${formatPrice(range[0])} – ${formatPrice(range[1])}`;
}

export function formatTimestamp(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function relativeTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diffMs = Date.now() - then;
  const diffMin = Math.round(diffMs / 60000);
  if (Math.abs(diffMin) < 1) return "just now";
  if (Math.abs(diffMin) < 60) return `${diffMin > 0 ? diffMin : -diffMin}m ${diffMin > 0 ? "ago" : "from now"}`;
  const diffHr = Math.round(diffMin / 60);
  if (Math.abs(diffHr) < 24) return `${diffHr > 0 ? diffHr : -diffHr}h ${diffHr > 0 ? "ago" : "from now"}`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay > 0 ? diffDay : -diffDay}d ${diffDay > 0 ? "ago" : "from now"}`;
}

export function titleCase(value) {
  if (!value) return "";
  return String(value)
    .toLowerCase()
    .split(/[_\s]+/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => (
    {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]
  ));
}
