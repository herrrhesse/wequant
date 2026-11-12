// Display formatting only: unit changes, rounding and layout. A missing value is shown as unknown, never as 0.

const NONE = "—";

/** A value as text; null and undefined show as "—". */
export function show(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return NONE;
  return typeof value === "number" ? value.toLocaleString("zh-CN") : value;
}

/** A fraction as a percentage: 0.123 → "12.3%"; sign adds "+" to positive values. */
export function pct(value: number | null | undefined, digits = 1, sign = false): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NONE;
  return `${sign && value >= 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
}

/** A number with fixed decimals. */
export function fixed(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NONE;
  return value.toFixed(digits);
}

/** Yuan or shares in 万 / 亿 above ten thousand. */
export function wan(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NONE;
  const a = Math.abs(value);
  if (a >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`;
  if (a >= 1e4) return `${(value / 1e4).toFixed(1)} 万`;
  return value.toFixed(0);
}

/** An ISO timestamp in local time, to the minute. */
export function localTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Up or down colour class of a signed value. */
export function tone(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "" : value >= 0 ? "up" : "down";
}
