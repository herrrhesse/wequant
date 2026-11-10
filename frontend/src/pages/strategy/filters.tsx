import type { ConditionSpec, GroupSpec } from "../../api/types.gen";
import { t } from "../../i18n";
import { conditionForm, parseCondition, type Form } from "../../strategy/expr";
import type { Loc } from "../../strategy/locate";
import { CompareEditor, FormEditor, MetricPicker, knownMetric } from "./builder";
import { Errors, Field, Seg, Switch, locAttr, useErrorsAt } from "./shared";

type Filter = ConditionSpec | GroupSpec;
const isGroup = (f: Filter): f is GroupSpec => "conditions" in f;
const enabledOf = (f: { enabled?: boolean }) => f.enabled !== false;
/** enabled is written only when it differs from the default (true). */
const withEnabled = <T extends { enabled?: boolean }>(f: T, on: boolean): T => {
  const { enabled: _, ...rest } = f;
  return (on ? rest : { ...rest, enabled: false }) as T;
};

export function newCondition(i: number): ConditionSpec {
  return { name: t.strategy.filters.defaultName(i), expr: conditionForm({ kind: "avg", field: "amount", n: 20 }, { op: "gt", value: 5e8 }) as ConditionSpec["expr"] };
}

/** One condition: the builder when the form is one it writes, else the form as JSON. */
function ConditionRow({ cond, loc, onChange, onRemove }: { cond: ConditionSpec; loc: Loc; onChange: (c: ConditionSpec) => void; onRemove: () => void }) {
  const read = parseCondition(cond.expr as Form);
  const parsed = read && knownMetric(read.metric) ? read : null;
  const exprErrors = useErrorsAt([...loc, "expr"]);
  const setExpr = (f: Form) => onChange({ ...cond, expr: f as ConditionSpec["expr"] });
  return (
    <div className={exprErrors.length ? "cond has-error" : "cond"} data-loc={locAttr(loc)}>
      <div className="cond-row">
        <Switch on={enabledOf(cond)} label={t.strategy.filters.enabled} onChange={(on) => onChange(withEnabled(cond, on))} />
        <Field loc={[...loc, "name"]} label={t.strategy.filters.name} hint={cond.name.trim() ? null : t.strategy.hints.required}>
          <input value={cond.name} onChange={(e) => onChange({ ...cond, name: e.target.value })} />
        </Field>
        <button type="button" className="cmd" onClick={onRemove}>
          {t.strategy.filters.remove}
        </button>
      </div>
      <div className="cond-row" data-loc={locAttr([...loc, "expr"])}>
        {parsed ? (
          <>
            <MetricPicker metric={parsed.metric} onChange={(m) => setExpr(conditionForm(m, parsed.compare))} />
            <CompareEditor metric={parsed.metric} compare={parsed.compare} onChange={(c) => setExpr(conditionForm(parsed.metric, c))} />
          </>
        ) : (
          <>
            <FormEditor form={cond.expr as Form} onChange={setExpr} label={t.strategy.filters.custom} />
            <span className="note">{t.strategy.filters.customNote}</span>
          </>
        )}
      </div>
      <Errors details={exprErrors} />
    </div>
  );
}

function GroupCard({ group, loc, onChange, onRemove }: { group: GroupSpec; loc: Loc; onChange: (g: GroupSpec) => void; onRemove: () => void }) {
  const setConditions = (conds: ConditionSpec[]) => conds.length && onChange({ ...group, conditions: conds as GroupSpec["conditions"] });
  return (
    <div className="group" data-loc={locAttr(loc)}>
      <div className="cond-row">
        <Switch on={enabledOf(group)} label={t.strategy.filters.enabled} onChange={(on) => onChange(withEnabled(group, on))} />
        <Field loc={[...loc, "name"]} label={t.strategy.filters.group} hint={group.name.trim() ? null : t.strategy.hints.required}>
          <input value={group.name} onChange={(e) => onChange({ ...group, name: e.target.value })} />
        </Field>
        <Seg
          label={t.strategy.filters.group}
          value={group.mode ?? "all"}
          options={[["all", t.strategy.filters.groupMode.all!], ["any", t.strategy.filters.groupMode.any!]]}
          onChange={(mode) => {
            const { mode: _, ...rest } = group;
            onChange(mode === "all" ? (rest as GroupSpec) : { ...rest, mode });
          }}
        />
        <button type="button" className="cmd" onClick={onRemove}>
          {t.strategy.filters.remove}
        </button>
      </div>
      {group.conditions.map((c, j) => (
        <ConditionRow
          key={j}
          cond={c}
          loc={[...loc, "conditions", j]}
          onChange={(nc) => setConditions(group.conditions.map((x, k) => (k === j ? nc : x)))}
          onRemove={() => setConditions(group.conditions.filter((_, k) => k !== j))}
        />
      ))}
      <button type="button" className="cmd" onClick={() => setConditions([...group.conditions, newCondition(group.conditions.length + 1)])}>
        + {t.strategy.filters.addCondition}
      </button>
    </div>
  );
}

export function FiltersEditor({ filters, onChange }: { filters: Filter[]; onChange: (f: Filter[]) => void }) {
  const set = (i: number, f: Filter) => onChange(filters.map((x, k) => (k === i ? f : x)));
  const remove = (i: number) => onChange(filters.filter((_, k) => k !== i));
  return (
    <div className="filters-editor">
      <p className="lead">{t.strategy.filters.help}</p>
      {filters.map((f, i) =>
        isGroup(f) ? (
          <GroupCard key={i} group={f} loc={["filters", i]} onChange={(g) => set(i, g)} onRemove={() => remove(i)} />
        ) : (
          <ConditionRow key={i} cond={f} loc={["filters", i]} onChange={(c) => set(i, c)} onRemove={() => remove(i)} />
        ),
      )}
      <div className="cond-row">
        <button type="button" className="cmd" onClick={() => onChange([...filters, newCondition(filters.length + 1)])}>
          + {t.strategy.filters.addCondition}
        </button>
        <button
          type="button"
          className="cmd"
          onClick={() => onChange([...filters, { name: t.strategy.filters.defaultGroup(filters.length + 1), conditions: [newCondition(1)] }])}
        >
          + {t.strategy.filters.addGroup}
        </button>
      </div>
    </div>
  );
}
