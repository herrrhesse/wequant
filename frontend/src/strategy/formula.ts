// A formula object in a strategy (S4.1): the text the user wrote and its period; the backend's compiled tree comes
// back with it but is never sent (the backend compiles the text). TEMPORARY types until the schema snapshot has them.
import type { ConditionSpec, JsonValue } from "../api/types.gen";

export type FormulaPeriod = "day" | "week" | "month";

export interface Formula {
  text: string;
  period?: FormulaPeriod;
  tree?: JsonValue[] | string;
}

type Expr = ConditionSpec["expr"];

/** A formula object (text written by the user) rather than a JSON tree or a string expression. */
export function formulaOf(expr: unknown): Formula | null {
  return expr !== null && typeof expr === "object" && !Array.isArray(expr) && typeof (expr as Formula).text === "string" ? (expr as Formula) : null;
}

/** A formula as the spec stores it: text and period only. */
export const formulaExpr = (text: string, period: FormulaPeriod = "day"): Expr => ({ text, period }) as unknown as Expr;

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
