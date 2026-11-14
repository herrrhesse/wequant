// "周线" / "月线" beside a condition with weekly (monthly) parts; nothing for a daily one. Names from labels.periods.
import type { PeriodValue } from "../api/types.gen";
import { useLabels } from "../labels";

export function PeriodTag({ value }: { value: PeriodValue[] | undefined }) {
  const L = useLabels();
  const kinds = [...new Set((value ?? []).map((v) => v.period))];
  return (
    <>
      {kinds.map((k) =>
        L.periodName(k) ? (
          <span key={k} className="tag period-tag">
            {L.periodName(k)}
          </span>
        ) : null,
      )}
    </>
  );
}
