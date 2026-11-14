// An expression of the strategy as a formula: one the user wrote is shown as written; a JSON tree (an older
// strategy, a template) is shown as text through the backend's formatting (formula_format) and stays a tree until
// the user changes it. A change goes into the strategy as {text, period}; the backend compiles it.
import type { ConditionSpec } from "../../api/types.gen";
import { formulaExpr, formulaOf, useR4, type FormulaRole } from "../../api/r4";
import { FormulaField } from "../../components/formula/FormulaField";
import { Problem } from "../../components/Problem";

type Expr = ConditionSpec["expr"];

export function ExprFormula({ expr, role, label, onCommit, rows }: { expr: Expr; role: FormulaRole; label: string; onCommit: (e: Expr) => void; rows?: number }) {
  const f = formulaOf(expr);
  const shown = useR4("formula_format", f ? null : { forms: [expr] }, { staleTime: Infinity });
  const commit = (text: string, period: "day" | "week" | "month") => onCommit(formulaExpr(text, period));
  if (f) return <FormulaField label={label} text={f.text} period={f.period ?? "day"} role={role} onCommit={commit} rows={rows} />;
  if (shown.isError) return <Problem error={shown.error} />;
  const g = shown.data?.formulas[0];
  if (!g) return <span className="note">正在读取公式…</span>;
  return <FormulaField label={label} text={g.text} period={g.period} role={role} onCommit={commit} rows={rows} />;
}

/** The backend's short example for a use (formula_vocab examples): the default text of a new formula there. */
export function useExample(role: FormulaRole) {
  const vocab = useR4("formula_vocab", {}, { staleTime: Infinity });
  const list = vocab.data?.examples ?? [];
  const short = list.filter((x) => !x.text.includes("\n"));
  const mine = short.find((x) => x.role === role) ?? (role === "condition" ? short[0] : undefined);
  return mine ?? null;
}
