// The judgment chain of one stock on one decision day (explain): pool checks, conditions, score and role.
import type { Explain } from "../api/types.gen";
import { pct } from "../format";
import { useLabels } from "../labels";
import { POOL_STEP_NAMES } from "../pages/strategy/text";

const ROLE_STEP = { target: "targets", backup: "backups", none: "scored" } as const;

export function ChainView({ e }: { e: Explain }) {
  const L = useLabels();
  const mark = (ok: boolean | null) => (ok === null ? <span className="muted">·</span> : ok ? <span className="ok">✓</span> : <span className="bad">✗</span>);
  return (
    <div className="col tight">
      {e.pool.map((p) => (
        <div key={p.name} className="chain-row">
          {mark(p.passed)}
          <span>{POOL_STEP_NAMES[p.name] ?? (L.of("funnel_kinds", p.name) || p.name)}</span>
        </div>
      ))}
      {e.steps.map((s) => (
        <div key={s.name} className={`chain-row ${s.chain ? "" : "sub-row"}`}>
          {mark(s.passed)}
          <span>{s.name}</span>
          {s.value !== null && <span className="muted small num">{Number(s.value.toPrecision(6))}</span>}
        </div>
      ))}
      {e.score && (
        <span className="small b">
          综合分 {(e.score.score * 100).toFixed(1)}，第 {e.score.rank} 名，{L.of("funnel_kinds", ROLE_STEP[e.role])}
          {e.weight !== null ? `，目标权重 ${pct(e.weight, 1)}` : ""}
        </span>
      )}
      {e.sell_reason && <span className="small">将卖出：{L.of("sell_kinds", e.sell_reason.kind)}</span>}
      {e.warnings.map((w, i) => (
        <span key={i} className="note">
          {L.warning(w)}
        </span>
      ))}
    </div>
  );
}
