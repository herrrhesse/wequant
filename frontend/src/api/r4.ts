// S4.2 (sell rules) as the backend described it before its schema snapshot has it (notes/wequant-backend/
// round4-shapes.md). TEMPORARY: replaced by the generated types once main carries S4.2. S4.8 and S4.1 come from
// types.gen.ts. Nothing is computed here.
import { useMemo } from "react";
import { useCall } from "./client";

interface R4Labels {
  sell_modes?: { code: string; label: string; help?: string }[];
  sell_rules?: { code: string; label: string; help?: string }[];
}

/** S4.2's labels not in the generated types yet: the sell modes and rules (empty until the backend has them). */
export function useR4Labels() {
  const q = useCall("labels", {});
  return useMemo(() => {
    const raw = q.data as unknown as R4Labels | undefined;
    const modes = new Map((raw?.sell_modes ?? []).map((m) => [m.code, m]));
    const rules = new Map((raw?.sell_rules ?? []).map((m) => [m.code, m]));
    return {
      /** labels.sell_modes: name and one-sentence difference; empty until the backend has them. */
      sellMode: (code: string) => ({ label: modes.get(code)?.label ?? "", help: modes.get(code)?.help ?? "" }),
      /** labels.sell_rules: name and sentence of a sell rule. */
      sellRule: (code: string) => ({ label: rules.get(code)?.label ?? "", help: rules.get(code)?.help ?? "" }),
    };
  }, [q.data]);
}

/** S4.2 explain / holdings: one sell rule's state for a stock held that day. */
export interface RuleCheck {
  rule: string;
  triggered: boolean | null;
  value: number | null;
  threshold: number | null;
  blocked_by: "keep" | "min_hold" | null;
  text: string;
}

export interface HoldingRow {
  code: string;
  name: string | null;
  shares: number;
  weight: number;
  cost: number | null;
  close: number | null;
  change: number | null;
  hold_days: number;
  peak_close: number | null;
  action: "sell" | "keep" | "blocked";
  cause: string | null;
  rules: RuleCheck[];
}

/** screen's holdings (S4.2, with simulate): simulated, never the user's own. */
export interface ScreenHoldings {
  simulated_from: string;
  note: string;
  rows: HoldingRow[];
}

export const screenHoldings = (screen: unknown): ScreenHoldings | null => (screen as { holdings?: ScreenHoldings | null } | null | undefined)?.holdings ?? null;

/** explain's rules (S4.2): null when the stock is not held that day. */
export const explainRules = (e: unknown): RuleCheck[] | null => (e as { rules?: RuleCheck[] | null } | null | undefined)?.rules ?? null;
