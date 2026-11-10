import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { operationAvailable } from "../../api/contract-info";
import type { ErrorDetail } from "../../api/types.gen";
import { t } from "../../i18n";
import { concerns, strategyLoc, type Loc } from "../../strategy/locate";

/** The backend's error details for the strategy being edited (from strategy validate, or the synthetic example). */
export const ErrorsContext = createContext<ErrorDetail[]>([]);

/** Error details that concern the control at loc. */
export function useErrorsAt(loc: Loc): ErrorDetail[] {
  return useContext(ErrorsContext).filter((d) => concerns(strategyLoc(d.loc ?? []), loc));
}

export const locAttr = (loc: Loc) => loc.join(".");

/** A labelled control registered at its strategy path; backend errors there outline it and are listed under it. */
export function Field({ loc, label, note, hint, children }: { loc: Loc; label: string; note?: ReactNode; hint?: string | null; children: ReactNode }) {
  const errors = useErrorsAt(loc).filter((d) => strategyLoc(d.loc ?? []).length === loc.length);
  return (
    <div className={errors.length ? "sfield has-error" : "sfield"} data-loc={locAttr(loc)}>
      <span className="sfield-label">{label}</span>
      <div className="sfield-body">{children}</div>
      {hint && <span className="sfield-hint">{hint}</span>}
      {note && <span className="note">{note}</span>}
      <Errors details={errors} />
    </div>
  );
}

export function Errors({ details }: { details: ErrorDetail[] }) {
  if (!details.length) return null;
  return (
    <ul className="serrors">
      {details.map((d, i) => (
        <li key={i}>
          {t.strategy.codes[d.code] ?? d.code} <span className="mono">{d.code}</span>
          <span className="note"> · {d.message}</span>
          {d.path && d.path.length > 0 && <span className="note"> · {t.strategy.exprPath(JSON.stringify(d.path))}</span>}
        </li>
      ))}
    </ul>
  );
}

/** A number box that keeps what is typed until it parses; empty means null. */
export function NumberInput({ value, onChange, step, width }: { value: number | null | undefined; onChange: (v: number | null) => void; step?: number; width?: number }) {
  const [text, setText] = useState(value === null || value === undefined ? "" : String(value));
  useEffect(() => {
    setText(value === null || value === undefined ? "" : String(value));
  }, [value]);
  return (
    <input
      className="num-input num"
      inputMode="decimal"
      style={width ? { width } : undefined}
      value={text}
      step={step}
      onChange={(e) => {
        setText(e.target.value);
        const v = e.target.value.trim();
        if (v === "") onChange(null);
        else if (Number.isFinite(Number(v))) onChange(Number(v));
      }}
    />
  );
}

export function Switch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} className={on ? "switch on" : "switch"} onClick={() => onChange(!on)}>
      <span />
    </button>
  );
}

export function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([k, l]) => (
        <button key={k} type="button" aria-pressed={value === k} onClick={() => onChange(k)}>
          {l}
        </button>
      ))}
    </div>
  );
}

export function StepCard({ n, title, children, disabled }: { n: number; title: string; children: ReactNode; disabled?: boolean }) {
  return (
    <section className={disabled ? "card step disabled" : "card step"} aria-disabled={disabled}>
      <div className="step-head">
        <span className="step-n">{n}</span>
        <h2>{title}</h2>
      </div>
      {children}
    </section>
  );
}

/** What to say while a backend result is missing: "pending" until the snapshot marks the operation available, then
 * "the command exists, the page connects with S5.1a" (the browser cannot call the CLI). */
export function backendNote(operation: string, command: string): string {
  return operationAvailable(operation) ? t.strategy.awaitingUi(command) : t.strategy.pending(command);
}
