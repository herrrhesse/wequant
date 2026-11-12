// The change history drawer: saved versions by time (no version numbers shown). Picking one compares its summary with
// the draft's, part by part as the backend wrote them, and can restore it into the editor.
import { useState } from "react";
import { useCall } from "../../api/client";
import type { StrategySpec, SummaryPart } from "../../api/types.gen";
import { localTime } from "../../format";
import { st } from "./text";

export function History({ id, spec, onClose, onRestore }: { id: string; spec: StrategySpec; onClose: () => void; onRestore: (s: StrategySpec) => void }) {
  const versions = useCall("strategy_versions", { id });
  const rows = [...(versions.data?.rows ?? [])].reverse();
  const [pick, setPick] = useState<number | null>(null);
  const n = pick ?? rows[0]?.n ?? null;
  const then = useCall("strategy_show", n !== null ? { id, n } : null);
  const a = useCall("strategy_summary", then.data ? { strategy: then.data.strategy } : null);
  const b = useCall("strategy_summary", { strategy: spec });
  const key = (p: SummaryPart) => JSON.stringify(p.loc);
  const before = new Map((a.data?.parts ?? []).map((p) => [key(p), p.text]));
  const after = new Map((b.data?.parts ?? []).map((p) => [key(p), p.text]));
  const changed = [...new Set([...before.keys(), ...after.keys()])].filter((k) => before.get(k) !== after.get(k)).map((k) => ({ k, was: before.get(k) ?? "", now: after.get(k) ?? "" }));
  const at = rows.find((r) => r.n === n)?.saved_at;
  return (
    <section className="card history" aria-label={st.history.title}>
      <div className="card-head">
        <h2>{st.history.title}</h2>
        <span className="small muted">{st.history.lead}</span>
        <button type="button" className="btn sm soft push" onClick={onClose}>
          {st.history.close}
        </button>
      </div>
      {rows.length === 0 && versions.isSuccess && <span className="note">{st.history.none}</span>}
      <div className="split">
        <div className="hist-list">
          {rows.map((r) => (
            <button key={r.n} type="button" aria-pressed={r.n === n} onClick={() => setPick(r.n)}>
              <span className="num">{localTime(r.saved_at)}</span>
              {r.current && <span className="tag">{st.history.current}</span>}
            </button>
          ))}
        </div>
        {at && (
          <div className="main col">
            <span className="b">{st.history.compare(localTime(at))}</span>
            {a.data && b.data && changed.length === 0 && <span className="note">{st.history.same}</span>}
            {changed.map((c) => (
              <div key={c.k} className="hist-diff small">
                <span className="muted">{st.history.then}</span>
                <span className="was">{c.was || "—"}</span>
                <span className="muted">{st.history.now}</span>
                <span className="now">{c.now || "—"}</span>
              </div>
            ))}
            {then.data && changed.length > 0 && (
              <button type="button" className="btn sm" style={{ alignSelf: "flex-start" }} onClick={() => onRestore(then.data.strategy)}>
                {st.history.restore}
              </button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
