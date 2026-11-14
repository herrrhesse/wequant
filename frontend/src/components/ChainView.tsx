// The judgment chain of one stock on one decision day (explain): pool checks, conditions, score and role.
import type { Explain, StrategySpec } from "../api/types.gen";
import { stepValue } from "../strategy/model";
import { pct } from "../format";
import { useLabels } from "../labels";
import { POOL_STEP_NAMES } from "../pages/strategy/text";
import { formulaValuesOf, formulaWords, ruleWords, type FormulaValue } from "../strategy/formula";

const ROLE_STEP = { target: "targets", backup: "backups", none: "scored" } as const;

export function ChainView({ e, spec }: { e: Explain; spec?: StrategySpec | null }) {
  const L = useLabels();
  const mark = (ok: boolean | null) => (ok === null ? <span className="muted">·</span> : ok ? <span className="ok">✓</span> : <span className="bad">✗</span>);
  return (
    <div className="col tight">
      {e.pool.map((p) => (
        <div key={p.name} className="chain-row">
          {mark(p.passed)}
          <span>{L.of("pool_items", p.name) || POOL_STEP_NAMES[p.name] || L.of("funnel_kinds", p.name) || p.name}</span>
          {p.note && <span className="muted small">{p.note}</span>}
        </div>
      ))}
      {e.steps.map((s) => (
        <div key={s.name} className={`chain-row ${s.chain ? "" : "sub-row"}`}>
          {mark(s.passed)}
          <span>{s.name}</span>
          {(s.periods ?? []).map((p, i) => (
            <span key={i} className="muted small">
              {L.period(p)}
            </span>
          ))}
          {stepValue(spec, s) && <span className="muted small num">{stepValue(spec, s)}</span>}
          <FormulaParts values={formulaValuesOf(s)} />
        </div>
      ))}
      {e.score?.factors.map((f) => (
        <div key={f.name} className="chain-row">
          <span className="muted">·</span>
          <span>
            {f.name} 排名分 {(f.adjusted_pct * 100).toFixed(1)} × {Math.round(f.weight_share * 100)}%
          </span>
          <span className="muted small num">= {(f.contribution * 100).toFixed(1)}</span>
        </div>
      ))}
      {e.score && (
        <span className="small b">
          综合分 {(e.score.score * 100).toFixed(1)}，第 {e.score.rank} 名，{L.of("funnel_kinds", ROLE_STEP[e.role])}
          {e.weight !== null ? `，目标权重 ${pct(e.weight, 1)}` : ""}
        </span>
      )}
      {e.sell_reason && <span className="small">将卖出：{L.of("sell_kinds", e.sell_reason.kind)}</span>}
      {/* Sell rules (S4.2) for a stock held that day: each rule's state in the backend's words. */}
      {e.rules?.map((r) => (
        <div key={r.rule} className="chain-row">
          {/* The keep formula and the minimum hold hold a sale back when they fire; the others sell. */}
          {r.triggered === null ? <span className="muted">·</span> : !r.triggered ? <span className="ok">✓</span> : r.rule === "keep_formula" || r.rule === "min_hold" ? <span className="warn-text">挡</span> : <span className="bad">卖</span>}
          <span>{L.of("sell_rules", r.rule)}</span>
          <span className="muted small">{ruleWords(r)}</span>
          {r.blocked_by === "keep" && <span className="muted small">被{L.of("sell_rules", "keep_formula") || "不卖公式"}挡住</span>}
        </div>
      ))}
      {e.warnings.map((w, i) => (
        <span key={i} className="note">
          {L.warning(w)}
        </span>
      ))}
    </div>
  );
}

/** A text formula condition's parts and numbers on the day (S4.1 formula_values): "B ✗ · A ✓ · C ✓", then the numbers. */
function FormulaParts({ values }: { values: FormulaValue[] }) {
  const w = formulaWords(values);
  if (!w.parts.length && !w.numbers.length) return null;
  return (
    <span className="chain-formula">
      {w.parts.length > 0 && <span className="small">{w.parts.join(" · ")}</span>}
      {w.numbers.length > 0 && <span className="muted small num">{w.numbers.join(" · ")}</span>}
    </span>
  );
}
