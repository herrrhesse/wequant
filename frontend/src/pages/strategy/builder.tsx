import { useEffect, useState } from "react";
import { panelFields } from "../../api/contract-info";
import { t } from "../../i18n";
import type { Compare, Form, Metric } from "../../strategy/expr";
import { FLAG_FIELDS, metricUnit, toBackend, toDisplay } from "../../strategy/fields";
import { NumberInput } from "./shared";

const KINDS = ["field", "change", "avg", "volatility", "vs_ma", "from_high"] as const;
const numericFields = () => panelFields().filter((f) => !FLAG_FIELDS.has(f));
const fieldName = (f: string) => t.strategy.metric.fieldNames[f] ?? f;

/** Whether the builder can show this metric: its field, if any, is one the snapshot lists. */
export const knownMetric = (m: Metric | null): m is Metric =>
  m !== null && (m.kind === "field" || m.kind === "avg" ? numericFields().includes(m.field) : true);

/** A metric: a panel field (names from the snapshot) or a common indicator with its window N. */
export function MetricPicker({ metric, onChange }: { metric: Metric; onChange: (m: Metric) => void }) {
  const n = metric.kind === "field" ? 20 : metric.n;
  const field = metric.kind === "field" || metric.kind === "avg" ? metric.field : "amount";
  const setKind = (kind: Metric["kind"]) => {
    if (kind === "field") onChange({ kind, field });
    else if (kind === "avg") onChange({ kind, field, n });
    else onChange({ kind, n });
  };
  return (
    <span className="builder">
      <select aria-label={t.strategy.metric.label} value={metric.kind} onChange={(e) => setKind(e.target.value as Metric["kind"])}>
        {KINDS.map((k) => (
          <option key={k} value={k}>
            {t.strategy.metric.kinds[k]}
          </option>
        ))}
      </select>
      {(metric.kind === "field" || metric.kind === "avg") && (
        <select
          aria-label={t.strategy.metric.kinds.field}
          value={metric.field}
          onChange={(e) => onChange(metric.kind === "avg" ? { ...metric, field: e.target.value } : { kind: "field", field: e.target.value })}
        >
          {numericFields().map((f) => (
            <option key={f} value={f}>
              {fieldName(f)}
            </option>
          ))}
        </select>
      )}
      {metric.kind !== "field" && (
        <label className="inline">
          {t.strategy.metric.n}
          <NumberInput value={metric.n} width={56} onChange={(v) => v !== null && Number.isInteger(v) && v >= 1 && onChange({ ...metric, n: v })} />
        </label>
      )}
    </span>
  );
}

/** The comparison of a condition; values are typed in display units and stored in the backend's (yuan, shares, ratio). */
export function CompareEditor({ metric, compare, onChange }: { metric: Metric; compare: Compare; onChange: (c: Compare) => void }) {
  const unit = metricUnit(metric);
  const dir = compare.op === "top_n" || compare.op === "top_pct" ? compare.dir : "high";
  const setOp = (op: Compare["op"]) => {
    if (op === "gt" || op === "lt") onChange({ op, value: compare.op === "gt" || compare.op === "lt" ? compare.value : 0 });
    else if (op === "top_n") onChange({ op, n: 20, dir });
    else onChange({ op, pct: 30, dir });
  };
  return (
    <span className="builder">
      <select aria-label={t.strategy.compare.label} value={compare.op} onChange={(e) => setOp(e.target.value as Compare["op"])}>
        {(["gt", "lt", "top_n", "top_pct"] as const).map((op) => (
          <option key={op} value={op}>
            {t.strategy.compare.ops[op]}
          </option>
        ))}
      </select>
      {(compare.op === "top_n" || compare.op === "top_pct") && (
        <select aria-label={t.strategy.compare.label} value={compare.dir} onChange={(e) => onChange({ ...compare, dir: e.target.value as "high" | "low" })}>
          {(["high", "low"] as const).map((d) => (
            <option key={d} value={d}>
              {t.strategy.compare.dirs[d]}
            </option>
          ))}
        </select>
      )}
      {(compare.op === "gt" || compare.op === "lt") && (
        <label className="inline">
          <NumberInput value={toDisplay(compare.value, unit)} width={90} onChange={(v) => v !== null && onChange({ ...compare, value: toBackend(v, unit) })} />
          {t.strategy.units[unit]}
        </label>
      )}
      {compare.op === "top_n" && <NumberInput value={compare.n} width={60} onChange={(v) => v !== null && Number.isInteger(v) && v >= 1 && onChange({ ...compare, n: v })} />}
      {compare.op === "top_pct" && (
        <label className="inline">
          <NumberInput value={compare.pct} width={60} onChange={(v) => v !== null && v > 0 && v <= 100 && onChange({ ...compare, pct: v })} />%
        </label>
      )}
    </span>
  );
}

/** An expression form edited as JSON text; it is applied when the text parses. */
export function FormEditor({ form, onChange, label }: { form: Form; onChange: (f: Form) => void; label: string }) {
  const [text, setText] = useState(JSON.stringify(form));
  const [bad, setBad] = useState(false);
  useEffect(() => setText(JSON.stringify(form)), [form]);
  return (
    <span className="form-editor">
      <textarea
        aria-label={label}
        className="mono"
        rows={2}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          try {
            onChange(JSON.parse(e.target.value) as Form);
            setBad(false);
          } catch {
            setBad(true);
          }
        }}
      />
      {bad && <span className="sfield-hint">{t.strategy.filters.badJson}</span>}
    </span>
  );
}
