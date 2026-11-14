// The indicators conditions and factors can use, as the backend describes them (expr indicators): Chinese name,
// category, a one-sentence explanation and whether the data has it. Only the ones the builder can write (numbers
// it reads back as a metric) are offered; until the list has loaded, the builder's own presets stand in.
import { useMemo } from "react";
import { useCall } from "../../api/client";
import type { Form, Metric } from "../../strategy/expr";
import { parseMetric } from "../../strategy/expr";
import { METRIC_PRESETS, metricDesc, metricKey, metricName } from "../../strategy/model";

export interface Indicator {
  key: string;
  metric: Metric;
  label: string;
  help: string;
  category: string;
  available: boolean;
  /** Why it is not available (e.g. it needs data merged first). */
  reason: string | null;
  needsMerge: boolean;
  legacy: boolean;
}

export function useIndicators() {
  const q = useCall("expr_indicators", {});
  const cats = useCall("labels", {}).data?.indicator_categories ?? [];
  const list = useMemo<Indicator[]>(() => {
    if (!q.data)
      return METRIC_PRESETS.map((m) => ({ key: metricKey(m), metric: m, label: metricName(m), help: metricDesc(m), category: "", available: true, reason: null, needsMerge: false, legacy: false }));
    const out: Indicator[] = [];
    for (const r of q.data.rows) {
      if (r.kind !== "num") continue;
      const metric = parseMetric(r.form as Form);
      if (!metric) continue;
      out.push({ key: metricKey(metric), metric, label: r.label, help: r.help, category: r.category, available: r.available, reason: r.reason, needsMerge: r.needs_merge, legacy: r.legacy });
    }
    return out;
  }, [q.data]);
  const byKey = useMemo(() => new Map(list.map((i) => [i.key, i])), [list]);
  const catName = (code: string) => cats.find((c) => c.code === code)?.label ?? "";
  // Categories in the backend's order, as far as offered indicators use them.
  const categories = cats.filter((c) => list.some((i) => i.category === c.code));
  return { list, byKey, catName, categories };
}

/** A metric menu grouped by category; a metric the strategy uses that is not offered stays selectable. */
export function MetricSelect({ value, onChange, label, className, style }: { value: Metric; onChange: (m: Metric) => void; label: string; className?: string; style?: React.CSSProperties }) {
  const { list, byKey, catName, categories } = useIndicators();
  const key = metricKey(value);
  const current = byKey.get(key);
  const offered = list.filter((i) => !i.legacy || i.key === key);
  const groups = categories.length ? categories.map((c) => ({ code: c.code, name: c.label, items: offered.filter((i) => i.category === c.code) })) : [{ code: "", name: "", items: offered }];
  const option = (i: Indicator) => (
    <option key={i.key} value={i.key} disabled={!i.available && i.key !== key} title={i.help}>
      {i.label}
      {!i.available ? "（暂不可用）" : i.needsMerge ? "（需要合并数据）" : ""}
    </option>
  );
  return (
    <select
      className={`select ${className ?? ""}`}
      style={style}
      aria-label={label}
      title={current ? `${current.label}：${current.help}` : undefined}
      value={key}
      onChange={(e) => {
        const m = list.find((i) => i.key === e.target.value)?.metric;
        if (m) onChange(m);
      }}
    >
      {!current && <option value={key}>{metricName(value)}</option>}
      {groups.map((g) =>
        g.name ? (
          <optgroup key={g.code} label={g.name || catName(g.code)}>
            {g.items.map(option)}
          </optgroup>
        ) : (
          g.items.map(option)
        ),
      )}
    </select>
  );
}
