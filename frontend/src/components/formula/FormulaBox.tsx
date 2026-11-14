// A formula as the user writes it: several lines in a monospace box with line numbers, kept exactly as typed
// (line breaks, {comments}, variable names). A problem the backend found is underlined at its line and column, with
// the backend's own words and hint below; when the formula is fine, what it gives (a condition or a number) and the
// variables it defines. Grows with the text up to maxRows lines, then scrolls.
import { useLayoutEffect, useRef, type ReactNode } from "react";
import { lineCount, spanOf } from "./source";

export type Period = "day" | "week" | "month";
export const PERIODS: [Period, string][] = [
  ["day", "日线"],
  ["week", "周线"],
  ["month", "月线"],
];

/** One problem at a place in the text, in the backend's words. */
export interface FormulaProblem {
  line: number;
  col: number;
  length?: number | null;
  text: string;
  hint?: string | null;
}

/** What the backend said about the text as it stands. */
export interface FormulaStatus {
  pending: boolean;
  problems: FormulaProblem[];
  /** The text changed since this answer: its words stay, its underline is not drawn. */
  stale?: boolean;
  /** Shown when there is no problem, e.g. "条件" or "数值". */
  kind?: string | null;
  /** The output's name (XG), when it has one. */
  output?: string | null;
  vars?: string[];
  /** The backend's warnings about the formula (e.g. an adjusted price against a fixed level). */
  notes?: string[];
}

const LINE = 20;

interface Props {
  label: string;
  text: string;
  onText: (text: string) => void;
  onBlur?: () => void;
  period?: Period;
  onPeriod?: (p: Period) => void;
  status: FormulaStatus | null;
  minRows?: number;
  maxRows?: number;
  placeholder?: string;
  /** Buttons at the right of the bar (expand, help, trial). */
  tools?: ReactNode;
  boxRef?: React.RefObject<HTMLTextAreaElement | null>;
}

export function FormulaBox({ label, text, onText, onBlur, period, onPeriod, status, minRows = 3, maxRows = 20, placeholder, tools, boxRef }: Props) {
  const own = useRef<HTMLTextAreaElement>(null);
  const area = boxRef ?? own;
  const mirror = useRef<HTMLPreElement>(null);
  const gutter = useRef<HTMLDivElement>(null);
  const lines = lineCount(text);
  const rows = Math.min(Math.max(lines, minRows), maxRows);
  const first = status && !status.pending && !status.stale ? status.problems[0] : undefined;
  const [from, to] = first ? spanOf(text, first.line, first.col, first.length) : [0, 0];
  const sync = () => {
    const a = area.current;
    if (!a) return;
    if (mirror.current) {
      mirror.current.scrollTop = a.scrollTop;
      mirror.current.scrollLeft = a.scrollLeft;
    }
    if (gutter.current) gutter.current.scrollTop = a.scrollTop;
  };
  useLayoutEffect(sync);
  return (
    <div className={`fx ${first ? "has-err" : ""}`}>
      {(onPeriod || tools) && (
        <div className="fx-bar">
          {onPeriod && (
            <div className="seg sm" role="group" aria-label="周期">
              {PERIODS.map(([p, name]) => (
                <button key={p} type="button" aria-pressed={(period ?? "day") === p} onClick={() => onPeriod(p)}>
                  {name}
                </button>
              ))}
            </div>
          )}
          <span className="push row nowrap">{tools}</span>
        </div>
      )}
      <div className="fx-edit" style={{ height: rows * LINE + 16 }}>
        <div className="fx-gutter" ref={gutter} aria-hidden="true">
          {Array.from({ length: lines }, (_, i) => (
            <span key={i} className={first?.line === i + 1 ? "err" : undefined}>
              {i + 1}
            </span>
          ))}
        </div>
        <div className="fx-text">
          <pre className="fx-mirror" ref={mirror} aria-hidden="true">
            {first ? (
              <>
                {text.slice(0, from)}
                <mark className="fx-under" data-testid="formula-underline">
                  {text.slice(from, to)}
                </mark>
                {text.slice(to)}
              </>
            ) : (
              text
            )}
            {"\n"}
          </pre>
          <textarea
            ref={area}
            className="fx-area"
            aria-label={label}
            aria-invalid={!!first}
            spellCheck={false}
            autoCapitalize="off"
            autoComplete="off"
            wrap="off"
            placeholder={placeholder}
            value={text}
            onChange={(e) => onText(e.target.value)}
            onScroll={sync}
            onBlur={onBlur}
          />
        </div>
      </div>
      <FormulaState status={status} />
    </div>
  );
}

function FormulaState({ status }: { status: FormulaStatus | null }) {
  if (!status) return null;
  if (status.pending) return <span className="fx-state muted">正在检查…</span>;
  const p = status.problems[0];
  if (p)
    return (
      <div className="fx-state err" role="alert" aria-label="公式有问题">
        <span>
          第 {p.line} 行第 {p.col} 列：{p.text}
        </span>
        {p.hint && p.hint !== p.text && <span className="fx-hint">{p.hint}</span>}
        {status.problems.length > 1 && <span className="tiny muted">还有 {status.problems.length - 1} 处，改好这一处后再看。</span>}
      </div>
    );
  return (
    <div className="fx-state">
      <span className="ok" role="status" aria-label="公式检查结果">
        {status.kind ? `${status.kind}公式` : "公式可以用"}
        {status.output ? `，输出 ${status.output}` : ""}
        {status.vars && status.vars.length > 0 ? ` · 中间变量 ${status.vars.join("、")}` : ""}
      </span>
      {status.notes?.map((n, i) => (
        <span key={i} className="fx-note" role="note" aria-label="公式提示">
          {n}
        </span>
      ))}
    </div>
  );
}
