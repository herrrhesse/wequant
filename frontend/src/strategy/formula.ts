// A formula object in a strategy (S4.1): the text the user wrote and its period; the backend's compiled tree comes
// back with it but is never sent (the backend compiles the text). Sell rules (S4.2) below are TEMPORARY types until
// the schema snapshot has them.
import type { ConditionSpec, FormulaRequest, FormulaSpec, FormulaValue } from "../api/types.gen";

export type FormulaPeriod = NonNullable<FormulaSpec["period"]>;
export type FormulaRole = NonNullable<FormulaRequest["role"]>;
export type Formula = FormulaSpec;
export type { FormulaValue };

type Expr = ConditionSpec["expr"];

/** A formula object (text written by the user) rather than a JSON tree or a string expression. */
export function formulaOf(expr: unknown): FormulaSpec | null {
  return expr !== null && typeof expr === "object" && !Array.isArray(expr) && typeof (expr as FormulaSpec).text === "string" ? (expr as FormulaSpec) : null;
}

/** A formula as the spec stores it: text and period only (never the tree). */
export const formulaExpr = (text: string, period: FormulaPeriod = "day"): Expr => ({ text, period });

/** S4.2: how holdings are sold, as the strategy stores it (all defaults: not written, the rebalance engine). */
export interface SellSpec {
  mode?: "rebalance" | "rules";
  stop_loss?: number | null;
  take_profit?: number | null;
  trailing_stop?: number | null;
  max_hold_days?: number | null;
  sell_when?: ConditionSpec | null;
  keep_when?: ConditionSpec | null;
  keep_over_stops?: boolean;
  min_hold_days?: number | null;
  cooldown_days?: number;
}

/** The strategy's sell section (absent on strategies saved before S4.2: rebalance, no rules). */
export const sellOf = (spec: object): SellSpec => (spec as { sell?: SellSpec | null }).sell ?? {};

/** Whether a sell section is all defaults (then it is left out, so an older strategy keeps its hash). */
export function sellIsDefault(s: SellSpec): boolean {
  return (
    (s.mode ?? "rebalance") === "rebalance" &&
    s.stop_loss == null &&
    s.take_profit == null &&
    s.trailing_stop == null &&
    s.max_hold_days == null &&
    s.sell_when == null &&
    s.keep_when == null &&
    !s.keep_over_stops &&
    s.min_hold_days == null &&
    !s.cooldown_days
  );
}

/** Change the sell section of a draft; an all-default section is removed. */
export function editSell(spec: object, fn: (s: SellSpec) => void) {
  const s: SellSpec = { ...sellOf(spec) };
  fn(s);
  const d = spec as { sell?: SellSpec };
  if (sellIsDefault(s)) delete d.sell;
  else d.sell = s;
}

const num = (v: number) => String(Number(v.toFixed(Math.abs(v) >= 1000 ? 0 : 2)));

/** The words for a formula condition's values: its direct parts as ✓ / ✗ (the failed ones first, as they explain
 * the result), then the numbers. Nothing is computed: the values are the backend's. */
export function formulaWords(values: FormulaValue[]): { parts: string[]; numbers: string[] } {
  const direct = values.filter((v) => v.direct);
  const mark = (v: FormulaValue) => (v.kind === "bool" ? `${v.name} ${v.value === null ? "—" : v.value ? "✓" : "✗"}` : `${v.name} ${v.value === null ? "—" : num(v.value)}`);
  const failed = (v: FormulaValue) => v.kind === "bool" && v.value === 0;
  const parts = [...direct.filter(failed), ...direct.filter((v) => !failed(v))].map(mark);
  const numbers = values.filter((v) => !v.direct && v.kind === "num").map(mark);
  return { parts, numbers };
}

/** formula_values of an explain StepCheck (S4.1): named values; empty when the condition is not a text formula. */
export const formulaValuesOf = (x: unknown): FormulaValue[] => (x as { formula_values?: FormulaValue[] } | null | undefined)?.formula_values ?? [];

/** A reject's formula values (S4.1, compact): the screen gives each formula condition's lines once
 * (formula_lines[step]) and each reject only the numbers, in the same order. */
export function rejectFormulaValues(screen: unknown, reject: { step: string }): FormulaValue[] {
  const lines = (screen as { formula_lines?: Record<string, { name: string; kind: "num" | "bool"; direct: boolean }[]> } | null | undefined)?.formula_lines?.[reject.step] ?? [];
  const values = (reject as { formula_values?: (number | null)[] }).formula_values ?? [];
  return lines.map((l, i) => ({ ...l, value: values[i] ?? null }));
}

/** Every formula of a strategy as written (text and period, by place). Two strategies with the same hash can differ
 * here: a comment or a variable name changes the text, not the compiled tree, and is still worth saving. */
export function formulaTexts(spec: object | null | undefined): string {
  if (!spec) return "";
  const s = spec as { universe?: unknown; formula?: unknown; filters?: { expr?: unknown; conditions?: { expr: unknown }[] }[]; factors?: { expr: unknown }[]; sell?: SellSpec | null };
  const out: [string, string, string][] = [];
  const add = (at: string, e: unknown) => {
    const f = formulaOf(e);
    if (f) out.push([at, f.text, f.period ?? "day"]);
  };
  add("universe", s.universe);
  add("formula", s.formula);
  (s.filters ?? []).forEach((f, i) => {
    add(`filters.${i}`, f.expr);
    (f.conditions ?? []).forEach((c, j) => add(`filters.${i}.${j}`, c.expr));
  });
  (s.factors ?? []).forEach((f, i) => add(`factors.${i}`, f.expr));
  add("sell_when", s.sell?.sell_when?.expr);
  add("keep_when", s.sell?.keep_when?.expr);
  return JSON.stringify(out);
}
