// An expression of the strategy as a formula: one the user wrote is shown as written; a JSON tree (an older
// strategy, a template) is shown as text through the backend's formatting (formula_format) and stays a tree until
// the user changes it. A change goes into the strategy as {text, period}; the backend compiles it.
import type { ConditionSpec } from "../../api/types.gen";
import { useCall } from "../../api/client";
import { formulaExpr, formulaOf, type FormulaRole } from "../../strategy/formula";
import { FormulaField } from "../../components/formula/FormulaField";
import { Problem } from "../../components/Problem";
import { useLabels } from "../../labels";

type Expr = ConditionSpec["expr"];

export function ExprFormula({ expr, role, label, onCommit, rows }: { expr: Expr; role: FormulaRole; label: string; onCommit: (e: Expr) => void; rows?: number }) {
  const L = useLabels();
  const f = formulaOf(expr);
  const shown = useCall("formula_format", f ? null : { forms: [expr as string | unknown[]] }, { staleTime: Infinity });
  const commit = (text: string, period: "day" | "week" | "month") => onCommit(formulaExpr(text, period));
  if (f) return <FormulaField label={label} text={f.text} period={f.period ?? "day"} role={role} onCommit={commit} rows={rows} />;
  if (shown.isError) return <Problem error={shown.error} />;
  const g = shown.data?.formulas[0];
  if (!g) return <span className="note">正在读取公式…</span>;
  // A tree the backend cannot write as a formula (an invalid one): shown as it is, with the backend's reason.
  if (g.text === null)
    return (
      <div className="col tight">
        <code className="mono small">{typeof expr === "string" ? expr : JSON.stringify(expr)}</code>
        {g.error && <span className="err-text">{L.detail(g.error)}</span>}
      </div>
    );
  return <FormulaField label={label} text={g.text} period={g.period ?? "day"} role={role} onCommit={commit} rows={rows} />;
}

/** The backend's short example for a use (formula_vocab examples): the default text of a new formula there. */
export function useExample(role: FormulaRole) {
  const vocab = useCall("formula_vocab", {}, { staleTime: Infinity });
  const list = vocab.data?.examples ?? [];
  // A role's first example that is not a whole one (complete) is its default text; a sell or keep formula is a
  // condition too, so the condition's serves until there is one of its own.
  const start = (r: FormulaRole) => list.find((x) => x.role === r && !x.complete);
  const mine = start(role) ?? (role === "sell" || role === "keep" ? start("condition") : undefined);
  return mine ?? null;
}
