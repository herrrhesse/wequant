// How the builder shows each metric: its unit and the display factor (display value × factor = backend value).
// Interim: field kinds and units move to the field registry (F-07, S3.8); until then only the snapshot's field
// names are trusted and units follow schema.py as the backend replies documented.
import type { Metric } from "./expr";

/** Panel fields that are flags (1.0 / 0.0), not quantities; the builder does not offer them as metrics. */
export const FLAG_FIELDS = new Set(["limit_up", "limit_down", "limit_locked", "limit_known", "st", "st_known"]);

export type Unit = "yuan" | "yi_yuan" | "shares" | "percent" | "ratio" | "trading_days";

export function metricUnit(m: Metric): Unit {
  if (m.kind === "change" || m.kind === "volatility" || m.kind === "vs_ma" || m.kind === "from_high") return "percent";
  const field = m.field;
  if (field === "amount") return "yi_yuan";
  if (field === "volume") return "shares";
  if (field === "factor") return "ratio";
  if (field === "list_days") return "trading_days";
  return "yuan";
}

/** Display value × factor = the backend's value (a display-layer unit change only). */
export const UNIT_FACTOR: Record<Unit, number> = { yuan: 1, yi_yuan: 1e8, shares: 1, percent: 0.01, ratio: 1, trading_days: 1 };

/** A backend value in display units, without float noise from the unit change. */
export function toDisplay(value: number, unit: Unit): number {
  return Number((value / UNIT_FACTOR[unit]).toPrecision(12));
}

export function toBackend(display: number, unit: Unit): number {
  return Number((display * UNIT_FACTOR[unit]).toPrecision(12));
}
