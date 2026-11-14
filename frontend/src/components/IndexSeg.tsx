// The index a strategy's scope is limited to. New choices are 沪深全部, 沪深 300 and 中证 500 (user, 2026-11-14);
// a strategy saved with an index no longer offered keeps it, shown as an old option it can be moved off.
import { st } from "../pages/strategy/text";

/** Indexes offered for new choices; null is 沪深全部. */
export const INDEX_CHOICES: (string | null)[] = [null, "000300.SH", "000905.SH"];

export const indexName = (code: string | null) => (code ? (st.scope.indexNames[code] ?? code) : st.scope.all);

export function IndexSeg({ value, onChange, label }: { value: string | null; onChange: (code: string | null) => void; label: string }) {
  const legacy = value !== null && !INDEX_CHOICES.includes(value);
  const options = legacy ? [...INDEX_CHOICES, value] : INDEX_CHOICES;
  return (
    <div className="col tight">
      <div className="seg" role="group" aria-label={label}>
        {options.map((code) => (
          <button key={code ?? "all"} type="button" aria-pressed={value === code} onClick={() => onChange(code)}>
            {indexName(code)}
          </button>
        ))}
      </div>
      {legacy && <span className="small warn-text">{st.scope.legacy(indexName(value))}</span>}
    </div>
  );
}
