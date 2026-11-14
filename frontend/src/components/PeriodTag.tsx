// "周线" / "月线" beside a condition computed on weekly (monthly) bars; nothing for a daily one. Names from labels.
import { useR4Labels, type PeriodValue } from "../api/r4";

export function PeriodTag({ value }: { value: PeriodValue | undefined }) {
  const R = useR4Labels();
  const name = value ? R.periodName(value.period) : "";
  return name ? <span className="tag period-tag">{name}</span> : null;
}
