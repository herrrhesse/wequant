// The strategy page's unsaved draft as the other pages see it (选股 and 回测 can run it): its settings, its name, the
// saved strategy it started from, and how many errors the strategy page's last check found. A draft that is not
// complete yet is never sent from those pages (the backend would refuse it); they say what is missing instead, as the
// strategy page does, with a way back to it.
import type { StrategySpec } from "../api/types.gen";
import { href } from "../router";

export const PAGE_DRAFT = "wequant.strategy.draft";

export interface PageDraft {
  /** The saved strategy the draft started from; null: a new strategy. */
  id: string | null;
  spec: StrategySpec;
  name: string;
  /** Errors the strategy page's last check found (absent: not checked yet). */
  errors?: number;
}

export function readPageDraft(): PageDraft | null {
  try {
    const v = JSON.parse(localStorage.getItem(PAGE_DRAFT) ?? "null") as PageDraft | null;
    return v && typeof v.spec === "object" && v.spec !== null ? v : null;
  } catch {
    return null;
  }
}

export function writePageDraft(d: PageDraft) {
  try {
    localStorage.setItem(PAGE_DRAFT, JSON.stringify(d));
  } catch {
    // storage unavailable: the draft lasts for this visit
  }
}

/** No enabled factor: nothing to rank, so a hold count cannot be met (the strategy page asks without one). */
export const hasNoFactors = (spec: StrategySpec) => !(spec.factors ?? []).some((f) => f.enabled !== false);

export interface DraftGap {
  title: string;
  text: string;
  /** The strategy page on this draft. */
  fix: string;
}

/** What keeps the draft from being screened or backtested yet; null when it can be. */
export function draftGap(d: PageDraft | null): DraftGap | null {
  if (!d) return null;
  const fix = d.id ? href.strategy(d.id) : href.draftStrategy();
  if (hasNoFactors(d.spec)) return { title: "这个草稿还没有打分因子", text: "在策略页添加一个打分因子，才能排出入选名单、选股和回测。", fix };
  if (d.errors) return { title: `这个草稿还有 ${d.errors} 处要改`, text: "在策略页改好标出来的地方，才能选股和回测。", fix };
  return null;
}
