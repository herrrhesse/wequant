// Every Chinese label the pages show for a backend code comes from the `labels` operation (read once per visit).
// Until it has answered, a code shows as an empty string, never as the raw code.
import { useMemo } from "react";
import { useCall } from "../api/client";
import type { Labels, Warning } from "../api/types.gen";
import { fill } from "./fill";

export { fill } from "./fill";

type Table = Exclude<keyof Labels, "warnings" | "version_reasons" | "job_stages" | "update_statuses" | "order_causes">;

export interface LabelBook {
  ready: boolean;
  /** The label of a code in a table; "" while labels load or when the code is unknown. */
  of: (table: Table, code: string | null | undefined) => string;
  /** The help sentence of a code. */
  help: (table: Table, code: string | null | undefined) => string;
  /** A warning as a full sentence, filled from its template and params. */
  warning: (w: Warning) => string;
  /** A warning's short name. */
  warningName: (code: string) => string;
  /** A version reason as a sentence. */
  reason: (code: string, params: Record<string, number | string | null>) => string;
  /** A background job's stage as a sentence. */
  stage: (code: string, params: Record<string, number | string | null>) => string;
  /** A data update's outcome as a sentence. */
  updateStatus: (code: string, params: Record<string, number | string | null>) => string;
  /** An error detail in Chinese: the backend's hint (the broken rule and how to fix it), else the code's label. */
  detail: (d: { code: string; hint?: string | null }) => string;
  /** Why an order was placed, as a sentence. */
  cause: (code: string | null | undefined, params: Record<string, number | string | null> | null | undefined) => string;
}

export function makeBook(labels: Labels | undefined): LabelBook {
  const index = new Map<string, Map<string, { label: string; help: string }>>();
  if (labels) {
    for (const [table, rows] of Object.entries(labels)) {
      const m = new Map<string, { label: string; help: string }>();
      for (const r of rows as { code: string; label: string; help?: string }[]) m.set(r.code, { label: r.label, help: r.help ?? "" });
      index.set(table, m);
    }
  }
  const warnings = new Map((labels?.warnings ?? []).map((w) => [w.code, w]));
  const reasons = new Map((labels?.version_reasons ?? []).map((r) => [r.code, r]));
  const stages = new Map((labels?.job_stages ?? []).map((r) => [r.code, r]));
  const updates = new Map((labels?.update_statuses ?? []).map((r) => [r.code, r]));
  const causes = new Map((labels?.order_causes ?? []).map((r) => [r.code, r]));
  return {
    ready: labels !== undefined,
    of: (table, code) => (code ? (index.get(table)?.get(code)?.label ?? "") : ""),
    help: (table, code) => (code ? (index.get(table)?.get(code)?.help ?? "") : ""),
    warning: (w) => {
      const label = warnings.get(w.code);
      return label ? fill(label, w.params) : "";
    },
    warningName: (code) => warnings.get(code)?.label ?? "",
    reason: (code, params) => {
      const label = reasons.get(code);
      return label ? fill(label, params) : "";
    },
    stage: (code, params) => {
      const label = stages.get(code);
      return label ? fill(label, params) : "";
    },
    updateStatus: (code, params) => {
      const label = updates.get(code);
      return label ? fill(label, params) : "";
    },
    detail: (d) => d.hint || [index.get("error_codes")?.get(d.code)?.label, index.get("error_codes")?.get(d.code)?.help].filter(Boolean).join("："),
    cause: (code, params) => {
      const label = code ? causes.get(code) : undefined;
      return label ? fill(label, params ?? {}) : "";
    },
  };
}

export function useLabels(): LabelBook {
  const q = useCall("labels", {});
  return useMemo(() => makeBook(q.data), [q.data]);
}

let current: LabelBook = makeBook(undefined);
/** The labels the app last read, for table cells and helpers outside a component body. The App re-renders when the
 * labels arrive, so children read the loaded book. */
export function lb(): LabelBook {
  return current;
}
export function setBook(b: LabelBook) {
  current = b;
}
