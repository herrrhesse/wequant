import { useEffect, useState } from "react";
import type { StrategySpec } from "../api/types.gen";

const KEY = "wequant.strategy-draft";

function load(): StrategySpec {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? (v as StrategySpec) : {};
  } catch {
    return {};
  }
}

/** The strategy being edited, kept in this browser until it can be saved (strategy save, D16). */
export function useDraft() {
  const [draft, setDraft] = useState<StrategySpec>(load);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(draft));
    } catch {
      // storage unavailable: the draft lasts for this visit only
    }
  }, [draft]);
  return [draft, setDraft] as const;
}
