// A formula's values bar by bar, as the backend computed them: one row per bar (a weekly formula: one row per week,
// with its last trading day), one column per variable and the output. Made to be checked line by line against
// another program, so it copies as a table: pasted into a spreadsheet, every value lands in its own cell.
import { useState } from "react";
import { toTsv } from "./source";

export type TrialCell = string | number | boolean | null;

export interface TrialColumn {
  key: string;
  label: string;
  output?: boolean;
}

export interface TrialRow {
  /** The bar's date (a week or month: its last trading day). */
  date: string;
  /** For a week or month, e.g. "2024-01-01 至 2024-01-05". */
  span?: string;
  values: Record<string, TrialCell>;
}

// Shown with up to 4 decimals; copied with 10 significant digits (enough to compare with any other program).
const show = (v: TrialCell) => (v === null ? "—" : typeof v === "boolean" ? (v ? "1" : "0") : typeof v === "number" ? String(Number(v.toFixed(4))) : v);
const exact = (v: TrialCell) => (v === null ? "" : typeof v === "boolean" ? (v ? "1" : "0") : typeof v === "number" ? String(Number(v.toPrecision(10))) : v);

export function TrialTable({ columns, rows, dateLabel = "日期", spanLabel }: { columns: TrialColumn[]; rows: TrialRow[]; dateLabel?: string; spanLabel?: string }) {
  const [copied, setCopied] = useState<string | null>(null);
  const head = [dateLabel, ...(spanLabel ? [spanLabel] : []), ...columns.map((c) => c.label)];
  const cells = (fmt: (v: TrialCell) => string) => rows.map((r) => [r.date, ...(spanLabel ? [r.span ?? ""] : []), ...columns.map((c) => fmt(r.values[c.key] ?? null))]);
  const body = cells(show);
  const copy = async () => {
    const text = toTsv(head, cells(exact));
    try {
      await navigator.clipboard.writeText(text);
      setCopied(`已复制 ${rows.length} 行，可以直接粘贴到表格里。`);
    } catch {
      setCopied("浏览器不让复制，请在表格里选中后按 Ctrl+C。");
    }
  };
  return (
    <div className="col tight">
      <div className="row">
        <span className="small muted">共 {rows.length} 行</span>
        <button type="button" className="btn sm soft push" disabled={rows.length === 0} onClick={copy}>
          复制表格
        </button>
      </div>
      {copied && (
        <span className="small ok" role="status">
          {copied}
        </span>
      )}
      <div className="tbl-wrap fx-trial">
        <table className="tbl" aria-label="试算结果">
          <thead>
            <tr>
              {head.map((h, i) => (
                <th key={i} className={i > (spanLabel ? 1 : 0) ? "r" : undefined}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((r, i) => (
              <tr key={i}>
                {r.map((v, j) => (
                  <td key={j} className={j > (spanLabel ? 1 : 0) ? `r num ${columns[j - (spanLabel ? 2 : 1)]?.output ? "b" : ""}` : "num"}>
                    {v}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
