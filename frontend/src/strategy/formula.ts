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
