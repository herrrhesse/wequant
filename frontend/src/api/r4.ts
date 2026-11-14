// Round 4 (S4.8 weekly and monthly bars, S4.1 text formulas, S4.2 sell rules) as the backend described it before its
// schema snapshot has it (notes/wequant-backend/round4-shapes.md). TEMPORARY: replaced by the generated types and
// useCall once main carries round 4. No formula is parsed or evaluated here.
import { useQuery, type UseQueryOptions } from "@tanstack/react-query";
import { useMemo } from "react";
import { fill } from "../labels/fill";
import { useCall } from "./client";
import { call } from "./http";
import type { Operation } from "./operations.gen";
import type { JsonValue, Warning } from "./types.gen";

import type { FormulaPeriod } from "../strategy/formula";
export { formulaExpr, formulaOf, type Formula, type FormulaPeriod } from "../strategy/formula";

export type FormulaRole = "condition" | "factor" | "universe" | "formula" | "sell" | "keep";

export interface FormulaIssue {
  code: string;
  message: string;
  hint: string;
  line: number;
  col: number;
  end_line: number;
  end_col: number;
  token: string | null;
}

export interface FormulaVariable {
  name: string;
  line: number;
  kind: "num" | "bool" | "const";
  value: number | null;
  output: boolean;
  used: boolean;
}

export interface FormulaCheckRequest {
  text: string;
  period?: FormulaPeriod;
  role?: FormulaRole | null;
}

export interface FormulaCheck {
  ok: boolean;
  tree: JsonValue[] | string | null;
  key: string | null;
  kind: "num" | "bool" | null;
  output: string | null;
  variables: FormulaVariable[];
  errors: FormulaIssue[];
  warnings: FormulaIssue[];
}

export interface FormulaTrialRequest {
  text: string;
  period?: FormulaPeriod;
  code: string;
  start?: string | null;
  end?: string | null;
  anchor?: string | null;
  data_version?: number | null;
  max_bars?: number;
}

export interface FormulaTrialRow {
  date: string;
  start: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  raw_close: number | null;
  /** Forward-adjusted prices, only to compare price levels with 通达信. */
  tdx?: { open: number | null; high: number | null; low: number | null; close: number | null } | null;
  values: Record<string, number | null>;
}

export interface FormulaTrial {
  data_version: number;
  data_version_name: string;
  code: string;
  name: string | null;
  period: FormulaPeriod;
  columns: { name: string; kind: "num" | "bool"; output: boolean }[];
  rows: FormulaTrialRow[];
  open_period: string | null;
  warnings: Warning[];
}

export interface FormulaFormat {
  formulas: { text: string; period: FormulaPeriod }[];
}

export interface VocabFunction {
  name: string;
  aliases: string[];
  args: string[];
  returns: "num" | "bool" | "same";
  category: string;
  description: string;
  example: string;
  tdx: boolean;
}

export interface VocabField {
  name: string;
  aliases: string[];
  form: JsonValue[] | string;
  unit: string | null;
  description: string;
}

export interface FormulaVocab {
  functions: VocabFunction[];
  fields: VocabField[];
  unsupported: { name: string; code: string; reason: string }[];
  /** Asked for (11-14): whole examples, the weekly KDJ first. */
  examples?: { title: string; description?: string; text: string; period: FormulaPeriod; role?: FormulaRole }[];
}

/** Which week (month) a weekly (monthly) condition's value came from: its last trading day; null: none complete yet. */
export interface PeriodValue {
  period: "week" | "month";
  end: string | null;
}

interface R4Operations {
  formula_check: { request: FormulaCheckRequest; result: FormulaCheck };
  formula_trial: { request: FormulaTrialRequest; result: FormulaTrial };
  formula_format: { request: { forms: (JsonValue[] | string)[] }; result: FormulaFormat };
  formula_vocab: { request: Record<string, never>; result: FormulaVocab };
}
type R4 = keyof R4Operations;

/** Every service operation is POST /api/<name>: the generated routes do not list these yet. */
export function r4call<O extends R4>(op: O, request: R4Operations[O]["request"], signal?: AbortSignal): Promise<R4Operations[O]["result"]> {
  return call(op as unknown as Operation, request as never, signal) as Promise<R4Operations[O]["result"]>;
}

export function useR4<O extends R4>(
  op: O,
  request: R4Operations[O]["request"] | null,
  options: Omit<UseQueryOptions<R4Operations[O]["result"], Error, R4Operations[O]["result"], readonly unknown[]>, "queryKey" | "queryFn"> = {},
) {
  return useQuery({
    ...options,
    queryKey: [op, request] as const,
    queryFn: ({ signal }) => r4call(op, request as R4Operations[O]["request"], signal),
    enabled: request !== null && (options.enabled ?? true),
  });
}


interface R4Labels {
  periods?: { code: string; name?: string; label?: string; template?: string }[];
  formula_categories?: { code: string; label: string }[];
}

/** Round 4's labels: a period's name ("周线"), which week a value came from ("周线（截至 2026-11-06 那周）"), and a
 * formula category's name ("均线"). Empty strings while the labels load or before the backend has them. */
export function useR4Labels() {
  const q = useCall("labels", {});
  return useMemo(() => {
    const raw = q.data as unknown as R4Labels | undefined;
    const periods = new Map((raw?.periods ?? []).map((p) => [p.code, p]));
    const cats = new Map((raw?.formula_categories ?? []).map((c) => [c.code, c.label]));
    const periodName = (p: string) => periods.get(p)?.name ?? periods.get(p)?.label ?? "";
    return {
      periodName,
      periodValue: (v: PeriodValue) => {
        const t = periods.get(v.period)?.template;
        return t && v.end ? fill({ template: t, params: ["end"] }, { end: v.end }) : periodName(v.period);
      },
      category: (c: string) => cats.get(c) ?? c,
    };
  }, [q.data]);
}

/** screen's periods (S4.8): which week (month) each weekly (monthly) condition or factor used, by name. */
export const screenPeriods = (screen: unknown): Record<string, PeriodValue> => (screen as { periods?: Record<string, PeriodValue> } | null | undefined)?.periods ?? {};

/** explain StepCheck's periods (S4.8): empty for a daily condition. */
export const stepPeriods = (step: unknown): PeriodValue[] => (step as { periods?: PeriodValue[] } | null | undefined)?.periods ?? [];
