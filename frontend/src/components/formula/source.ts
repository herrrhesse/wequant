// Pure helpers for the formula editor: where a line and column sit in the text, and a trial table as text that
// pastes into a spreadsheet one cell per value. No computation of formulas here: the backend parses and evaluates.

/** Offset of a 1-based line and column in the text (clamped to the text), the way the backend reports a place. */
export function offsetOf(text: string, line: number, col: number): number {
  let at = 0;
  for (let l = 1; l < line; l++) {
    const nl = text.indexOf("\n", at);
    if (nl < 0) return text.length;
    at = nl + 1;
  }
  const end = text.indexOf("\n", at);
  const lineEnd = end < 0 ? text.length : end;
  return Math.min(at + Math.max(0, col - 1), lineEnd);
}

/** The span to underline: from the place, the word given (or one character), never past the line's end. */
export function spanOf(text: string, line: number, col: number, length?: number | null): [number, number] {
  const from = offsetOf(text, line, col);
  const end = text.indexOf("\n", from);
  const lineEnd = end < 0 ? text.length : end;
  const n = Math.max(1, length ?? 1);
  return [from, Math.max(from, Math.min(from + n, lineEnd))];
}

/** Number of lines, counting a trailing empty one (what the gutter shows). */
export function lineCount(text: string): number {
  let n = 1;
  for (let i = 0; i < text.length; i++) if (text.charCodeAt(i) === 10) n++;
  return n;
}

/** Rows as tab-separated text with a header line; cells lose tabs and line breaks so each stays one cell. */
export function toTsv(head: string[], rows: (string | number | boolean | null)[][]): string {
  const cell = (v: string | number | boolean | null) => (v === null ? "" : String(v)).replace(/[\t\r\n]+/g, " ");
  return [head, ...rows].map((r) => r.map(cell).join("\t")).join("\n");
}

/** Insert text at a selection, returning the new text and where the caret goes. */
export function insertAt(text: string, start: number, end: number, piece: string): { text: string; caret: number } {
  return { text: text.slice(0, start) + piece + text.slice(end), caret: start + piece.length };
}
