// Pure numeric/label formatters shared by PAPER views. Missing values render as "—", never 0.
import { titleCase } from "./format.js";

export function money(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  if (!Number.isFinite(Number(value))) return "—";
  return `${Number(value).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })} USDT`;
}

export function usd(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return `$${Number(value).toFixed(6)}`;
}

export function displayValue(value) {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

export function numericValue(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function fixedNumber(value, digits = 2) {
  const number = numericValue(value);
  return number === null ? "—" : number.toFixed(digits);
}

export function percent(value, digits = 1) {
  const number = numericValue(value);
  return number === null ? "—" : `${(number * 100).toFixed(digits)}%`;
}

export function dataOriginLabel(value) {
  if (value === null || value === undefined || value === "") return "—";
  return titleCase(String(value).replaceAll("_", " "));
}

export function yesNo(value) {
  return typeof value === "boolean" ? (value ? "Yes" : "No") : "Unknown";
}

export function usdCost(value, digits = 4) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return `$${Number(value).toFixed(digits)}`;
}
