// The scope a strategy draws its stocks from, as the backend lists them (labels.scopes): 沪深全部, the offered
// indexes and 自定义名单 (a fixed list copied from a stock pool). A strategy saved with an index no longer offered
// keeps it, shown as an old option it can be moved off.
import { useCall } from "../api/client";
import type { PoolSpec } from "../api/types.gen";
import { st } from "../pages/strategy/text";

/** The scope code of a pool setting: "all", an index code, or "stocks". */
export function scopeOf(pool: PoolSpec | null | undefined): string {
  return pool?.stocks ? "stocks" : (pool?.index ?? "all");
}

/** Indexes offered as benchmarks and for adding to a pool (until labels have loaded). */
export const INDEX_CHOICES: (string | null)[] = [null, "000300.SH", "000905.SH"];

export function useScopes() {
  const scopes = useCall("labels", {}).data?.scopes ?? [];
  const name = (code: string | null) => (code === null ? (scopes.find((s) => s.kind === "all")?.label ?? st.scope.all) : (scopes.find((s) => s.code === code)?.label ?? st.scope.indexNames[code] ?? code));
  return { scopes, name };
}

export const indexName = (code: string | null) => (code ? (st.scope.indexNames[code] ?? code) : st.scope.all);

export function IndexSeg({ value, onChange, label }: { value: string; onChange: (code: string) => void; label: string }) {
  const { scopes } = useScopes();
  const current = scopes.find((s) => s.code === value);
  const options = scopes.filter((s) => !s.legacy || s.code === value);
  return (
    <div className="col tight">
      <div className="seg" role="group" aria-label={label}>
        {options.map((s) => (
          <button key={s.code} type="button" aria-pressed={value === s.code} title={s.help} onClick={() => onChange(s.code)}>
            {s.label}
          </button>
        ))}
      </div>
      {current?.legacy && <span className="small warn-text">{st.scope.legacy(current.label)}</span>}
    </div>
  );
}
