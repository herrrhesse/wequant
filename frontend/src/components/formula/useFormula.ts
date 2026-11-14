// A formula being written: the draft as typed, checked by the backend (formula_check) a moment after typing stops.
// While the text has changed since the last answer, the answer's words stay but its underline is not drawn (its
// place belongs to the older text).
import { keepPreviousData } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useR4, type FormulaCheck, type FormulaPeriod, type FormulaRole } from "../../api/r4";
import { useDebounced } from "../../hooks";
import type { FormulaStatus } from "./FormulaBox";

const KIND: Record<string, string> = { bool: "条件", num: "数值" };

export function statusOf(check: FormulaCheck | undefined, stale: boolean): FormulaStatus | null {
  if (!check) return null;
  return {
    pending: false,
    stale,
    problems: check.errors.map((e) => ({
      line: e.line,
      col: e.col,
      length: e.token ? e.token.length : e.end_line === e.line ? e.end_col - e.col : null,
      text: e.message,
      hint: e.hint,
    })),
    notes: check.warnings.map((w) => w.hint || w.message),
    kind: check.kind ? KIND[check.kind] : null,
    output: check.output,
    vars: check.variables.filter((v) => v.kind !== "const" && !v.output).map((v) => v.name),
  };
}

export function useFormula(text: string, period: FormulaPeriod, role: FormulaRole) {
  const [draft, setDraft] = useState(text);
  // A change from outside (another version opened, a template) replaces the draft.
  useEffect(() => setDraft(text), [text]);
  const settled = useDebounced(draft, 300);
  const check = useR4("formula_check", { text: settled, period, role }, { placeholderData: keepPreviousData, staleTime: Infinity });
  const answered = check.data && !check.isPlaceholderData;
  const status: FormulaStatus | null = check.data ? statusOf(check.data, draft !== settled || !answered) : check.isError ? null : { pending: true, problems: [] };
  return { draft, setDraft, status, check: check.data, error: check.error };
}
