// Display rules for screen and runs results. Only unit conversion, rounding and selection: every number shown is
// the backend's (D20/F6: no returns, drawdowns, excess, percentiles or counts are computed here).
import type { MetricOut, Reject } from "../api/types.gen";

/** The rejects of one funnel level: the backend's rows whose step is that level (no subtraction of `left`). */
export function rejectsAt(rejects: Reject[], step: string): Reject[] {
  return rejects.filter((r) => r.step === step);
}

/** A ratio as a percent string; small values keep two significant digits instead of rounding to 0. */
export function percent(value: number, digits = 2): string {
  const p = value * 100;
  if (p !== 0 && Math.abs(p) < 0.01) return `${Number(p.toPrecision(2))}%`;
  return `${p.toFixed(digits)}%`;
}

export interface MetricShown {
  text: string;
  /** The backend's reason when there is no value. */
  reason: string | null;
}

/** A metric as the page shows it, by the unit the backend gave: fraction → %, ratio → 2 decimals, days, count, date. */
export function metricShown(m: MetricOut | undefined, none = "—"): MetricShown {
  if (!m) return { text: none, reason: null };
  if (m.value === null) return { text: none, reason: m.reason };
  if (typeof m.value === "string") return { text: m.value, reason: null };
  switch (m.unit) {
    case "fraction":
      return { text: percent(m.value), reason: null };
    case "ratio":
      return { text: m.value.toFixed(2), reason: null };
    case "days":
      return { text: `${Math.round(m.value)} 天`, reason: null };
    case "count":
      return { text: String(Math.round(m.value)), reason: null };
    default:
      return { text: String(m.value), reason: null };
  }
}

/** Held shares can be fractional (adjustment factors applied to holdings, D3): shown whole, the exact value on hover. */
export function shares(value: number): { text: string; title: string } {
  return { text: Math.round(value).toLocaleString("zh-CN"), title: String(value) };
}
