// The scope a strategy draws its stocks from, as the backend lists them (labels.scopes): 沪深全部, the offered
// indexes and 自定义名单 (a fixed list copied from a stock pool). A strategy saved with an index no longer offered
// keeps it, shown as an old option it can be moved off. A scope whose stocks the data does not all hold (data_status
// scope.covers) can still be chosen; it is marked, and the backend's warning says what that means for the results.
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

/** Which scopes the data holds every stock of, and from when; null before S2.11 or without data (nothing marked). */
export function useCovers() {
  const scope = useCall("data_status", {}).data?.scope;
  if (!scope || !scope.first_date) return null;
  const since = new Map(scope.covers.map((c) => [c.code, c.since]));
  return {
    /** The scope's stocks are not all in the data (a list is checked on its own, stock by stock). */
    partial: (code: string) => code !== "stocks" && !since.has(code),
    /** The day the data holds all of the scope's stocks from, when later than the data's first day. */
    from: (code: string) => {
      const d = since.get(code);
      return d && scope.first_date && d > scope.first_date ? d : null;
    },
  };
}

export function IndexSeg({ value, onChange, label }: { value: string; onChange: (code: string) => void; label: string }) {
  const { scopes } = useScopes();
  const covers = useCovers();
  const current = scopes.find((s) => s.code === value);
  const options = scopes.filter((s) => !s.legacy || s.code === value);
  return (
    <div className="col tight">
      <div className="seg" role="group" aria-label={label}>
        {options.map((s) => (
          <button key={s.code} type="button" aria-pressed={value === s.code} title={s.help} onClick={() => onChange(s.code)}>
            {s.label}
            {covers?.partial(s.code) ? <span className="seg-note">数据只含部分股票</span> : covers?.from(s.code) ? <span className="seg-note">{covers.from(s.code)!.slice(0, 7)} 起完整</span> : null}
          </button>
        ))}
      </div>
      {current?.legacy && <span className="small warn-text">{st.scope.legacy(current.label)}</span>}
    </div>
  );
}
