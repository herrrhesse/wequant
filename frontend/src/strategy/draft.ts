import { useEffect, useState } from "react";
import type { StrategySpec } from "../api/types.gen";

const KEY = "wequant.strategy-draft";

/** The saved version a draft started from; null: a new strategy. */
export interface Origin {
  id: string;
  n: number | null;
}

interface Stored {
  origin: Origin | null;
  draft: StrategySpec;
}

const sameOrigin = (a: Origin | null, b: Origin | null) => (a?.id ?? null) === (b?.id ?? null) && (a?.n ?? null) === (b?.n ?? null);

function load(origin: Origin | null): StrategySpec | null {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null") as Stored | null;
    if (!v || typeof v.draft !== "object" || Array.isArray(v.draft)) return null;
    return sameOrigin(v.origin ?? null, origin) ? v.draft : null;
  } catch {
    return null;
  }
}

/** The strategy being edited, kept in this browser per origin, so a reload keeps unsaved edits. `restored` says whether
 * a stored draft for this origin existed (else the page loads the saved version, or starts empty). */
export function useDraft(origin: Origin | null) {
  const [initial] = useState(() => load(origin));
  const [draft, setDraft] = useState<StrategySpec>(initial ?? {});
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify({ origin, draft } satisfies Stored));
    } catch {
      // storage unavailable: the draft lasts for this visit only
    }
  }, [draft, origin]);
  return { draft, setDraft, restored: initial !== null };
}

/** The last draft in this browser, whatever its origin (the screen page can run it). */
export function storedDraft(): StrategySpec | null {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null") as Stored | null;
    return v && typeof v.draft === "object" ? v.draft : null;
  } catch {
    return null;
  }
}
