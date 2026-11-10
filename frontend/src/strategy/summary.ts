// The summary sentence is the backend's `text`, shown verbatim. `parts` only mark which stretches of it are clickable
// (each part's fields); the page never joins part texts into a sentence of its own.
import type { SavedRef, StrategyCheck, SummaryPart } from "../api/types.gen";

export interface Segment {
  text: string;
  part: SummaryPart | null;
}

/** Splits `text` into stretches: each part found in order after the previous match, the text between kept as is.
 * A part that is not found is skipped, so its words stay plain text. Joining the segments gives `text` back. */
export function summarySegments(text: string, parts: SummaryPart[]): Segment[] {
  const out: Segment[] = [];
  let at = 0;
  for (const part of parts) {
    if (!part.text) continue;
    const i = text.indexOf(part.text, at);
    if (i < 0) continue;
    if (i > at) out.push({ text: text.slice(at, i), part: null });
    out.push({ text: part.text, part });
    at = i + part.text.length;
  }
  if (at < text.length) out.push({ text: text.slice(at), part: null });
  return out;
}

/** "有未保存修改" from strategy validate: saved_as lists the saved versions with exactly this content (D16).
 * `saved` is the strategy the draft was opened from; null while there is none (strategy save is not wired yet). */
export function dirtyState(check: StrategyCheck, saved: { id: string } | null): "clean" | "dirty" | "never" {
  if (check.saved_as.some((v: SavedRef) => !saved || v.id === saved.id)) return "clean";
  return saved ? "dirty" : "never";
}
