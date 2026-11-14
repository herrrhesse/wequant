// "周线" / "月线" beside a condition computed on weekly (monthly) bars; nothing for a daily one. Names from labels.
import { useR4Labels, type PeriodValue } from "../api/r4";

export function PeriodTag({ value }: { value: PeriodValue[] | undefined }) {
  const R = useR4Labels();
  const kinds = [...new Set((value ?? []).map((v) => v.period))];
  return (
    <>
      {kinds.map((k) => {
        const name = R.periodName(k);
        return name ? (
          <span key={k} className="tag period-tag">
            {name}
          </span>
        ) : null;
      })}
    </>
  );
}
