import { poolIndexCodes } from "../../api/contract-info";
import type { FactorSpec, PoolSpec, StrategySpec } from "../../api/types.gen";
import { t } from "../../i18n";
import { metricForm, parseMetric, type Form } from "../../strategy/expr";
import { FormEditor, MetricPicker, knownMetric } from "./builder";
import { Field, NumberInput, Seg, Switch, backendNote, locAttr } from "./shared";

/** Writes key = value, or removes the key when value is undefined (the backend's default then applies). */
function put<T extends object, K extends keyof T>(obj: T, key: K, value: T[K] | undefined): T {
  const next = { ...obj };
  if (value === undefined) delete next[key];
  else next[key] = value;
  return next;
}

const BOARDS = ["main", "chinext", "star"] as const;
type Board = (typeof BOARDS)[number];

export function PoolStep({ pool, onChange }: { pool: PoolSpec; onChange: (p: PoolSpec) => void }) {
  const p = t.strategy.pool;
  const boards = pool.boards ?? BOARDS;
  const toggleBoard = (b: Board) => {
    const next = BOARDS.filter((x) => (x === b ? !boards.includes(x) : boards.includes(x)));
    if (!next.length) return;
    // All three selected means no board limit: written as null (the default, left out).
    onChange(put(pool, "boards", next.length === BOARDS.length ? undefined : (next as PoolSpec["boards"])));
  };
  const minDays = pool.min_list_days ?? null;
  return (
    <>
      <Field loc={["pool", "index"]} label={p.scope} note={p.indexNote}>
        <Seg
          label={p.scope}
          value={pool.index ?? "all"}
          options={[["all", p.all], ...poolIndexCodes().map((c): [string, string] => [c, p.indexNames[c] ?? c])]}
          onChange={(v) => onChange(put(pool, "index", v === "all" ? undefined : (v as PoolSpec["index"])))}
        />
      </Field>
      <Field loc={["pool", "boards"]} label={p.boards} note={p.boardsNote}>
        {BOARDS.map((b) => (
          <label key={b} className="inline">
            <input type="checkbox" checked={boards.includes(b)} onChange={() => toggleBoard(b)} />
            {p.boardNames[b]}
          </label>
        ))}
      </Field>
      <Field loc={["pool", "exclude_st"]} label={p.excludeSt} note={p.stNote}>
        <Switch on={pool.exclude_st === true} label={p.excludeSt} onChange={(on) => onChange(put(pool, "exclude_st", on ? true : undefined))} />
      </Field>
      <Field
        loc={["pool", "min_list_days"]}
        label={p.minListDays}
        note={p.minListDaysNote}
        hint={minDays !== null && (!Number.isInteger(minDays) || minDays < 2) ? t.strategy.hints.atLeast(2) : null}
      >
        <Switch on={minDays !== null} label={p.minListDays} onChange={(on) => onChange(put(pool, "min_list_days", on ? 250 : undefined))} />
        {minDays !== null && (
          // N = 1 behaves as no limit, so it is written as null (S3.6 design R-01).
          <NumberInput value={minDays} width={80} onChange={(v) => onChange(put(pool, "min_list_days", v === null || v === 1 ? undefined : v))} />
        )}
      </Field>
      <Field loc={["pool", "suspended"]} label={p.suspended} note={p.suspendedNote}>
        <Switch on label={p.suspended} onChange={() => {}} />
      </Field>
    </>
  );
}

export function newFactor(i: number): FactorSpec {
  return { name: t.strategy.factors.defaultName(i), expr: metricForm({ kind: "change", n: 20 }) as FactorSpec["expr"] };
}

/** Factors; each one's share of the weight comes from strategy validate (weight_shares), never computed here. */
export function FactorsStep({ factors, shares, onChange }: { factors: FactorSpec[]; shares: Record<string, number> | null; onChange: (f: FactorSpec[]) => void }) {
  const f = t.strategy.factors;
  const set = (i: number, x: FactorSpec) => onChange(factors.map((y, k) => (k === i ? x : y)));
  return (
    <>
      <p className="lead">{f.help}</p>
      {factors.map((factor, i) => {
        const loc = ["factors", i];
        const read = parseMetric(factor.expr as Form);
        const metric = knownMetric(read) ? read : null;
        const share = shares?.[factor.name];
        return (
          <div key={i} className="cond" data-loc={locAttr(loc)}>
            <div className="cond-row">
              <Switch
                on={factor.enabled !== false}
                label={t.strategy.filters.enabled}
                onChange={(on) => set(i, put(factor, "enabled", on ? undefined : false))}
              />
              <Field loc={[...loc, "name"]} label={t.strategy.filters.name} hint={factor.name.trim() ? null : t.strategy.hints.required}>
                <input value={factor.name} onChange={(e) => set(i, { ...factor, name: e.target.value })} />
              </Field>
              <button type="button" className="cmd" onClick={() => onChange(factors.filter((_, k) => k !== i))}>
                {t.strategy.filters.remove}
              </button>
            </div>
            <div className="cond-row">
              <Field loc={[...loc, "expr"]} label={t.strategy.metric.label}>
                {metric ? (
                  <MetricPicker metric={metric} onChange={(m) => set(i, { ...factor, expr: metricForm(m) as FactorSpec["expr"] })} />
                ) : (
                  <FormEditor form={factor.expr as Form} label={t.strategy.metric.label} onChange={(x) => set(i, { ...factor, expr: x as FactorSpec["expr"] })} />
                )}
              </Field>
              <Field loc={[...loc, "prefer"]} label="">
                <Seg
                  label={t.strategy.factors.prefer.high!}
                  value={factor.prefer ?? "high"}
                  options={[["high", f.prefer.high!], ["low", f.prefer.low!]]}
                  onChange={(v) => set(i, put(factor, "prefer", v === "high" ? undefined : v))}
                />
              </Field>
              <Field loc={[...loc, "weight"]} label={f.weight} hint={factor.weight !== undefined && factor.weight <= 0 ? t.strategy.hints.atLeast(0) : null}>
                <NumberInput value={factor.weight ?? 1} width={60} onChange={(v) => set(i, put(factor, "weight", v === null || v === 1 ? undefined : v))} />
                <span className="note">
                  {f.share} {share === undefined ? backendNote("strategy_validate", "wequant strategy validate") : `${(share * 100).toFixed(1)}%`}
                </span>
              </Field>
            </div>
          </div>
        );
      })}
      <div className="cond-row">
        <button type="button" className="cmd" onClick={() => onChange([...factors, newFactor(factors.length + 1)])}>
          + {f.add}
        </button>
        <Field loc={["factors", "neutral"]} label={f.neutral} note={f.neutralWhy}>
          <input type="checkbox" disabled aria-label={f.neutral} />
        </Field>
      </div>
    </>
  );
}

export function TradeStep({ spec, onChange }: { spec: StrategySpec; onChange: (s: StrategySpec) => void }) {
  const tr = t.strategy.trade;
  const topN = spec.top_n ?? null;
  const holdRank = spec.hold_rank ?? null;
  const maxWeight = spec.max_weight ?? null;
  return (
    <>
      <Field loc={["period"]} label={tr.period}>
        <Seg
          label={tr.period}
          value={spec.period ?? "week"}
          options={[["day", tr.periods.day!], ["week", tr.periods.week!], ["month", tr.periods.month!]]}
          onChange={(v) => onChange(put(spec, "period", v === "week" ? undefined : v))}
        />
      </Field>
      <Field loc={["top_n"]} label={tr.topN} hint={topN !== null && topN < 1 ? t.strategy.hints.atLeast(1) : null}>
        <Switch on={topN === null} label={tr.unlimited} onChange={(on) => onChange(put(spec, "top_n", on ? undefined : 20))} />
        <span className="note">{tr.unlimited}</span>
        {topN !== null && <NumberInput value={topN} width={70} onChange={(v) => onChange(put(spec, "top_n", v === null ? undefined : v))} />}
      </Field>
      <Field loc={["backups"]} label={tr.backups} hint={(spec.backups ?? 0) < 0 ? t.strategy.hints.atLeast(0) : null}>
        <NumberInput value={spec.backups ?? 0} width={70} onChange={(v) => onChange(put(spec, "backups", v === null || v === 0 ? undefined : v))} />
      </Field>
      <Field loc={["hold_rank"]} label={tr.holdRank} note={tr.holdRankNote}>
        <Switch on={holdRank !== null} label={tr.holdRank} onChange={(on) => onChange(put(spec, "hold_rank", on ? (topN ?? 20) * 2 : undefined))} />
        {holdRank !== null && (
          // K = N behaves as no buffer, so it is written as null (F-01).
          <NumberInput value={holdRank} width={70} onChange={(v) => onChange(put(spec, "hold_rank", v === null || v === topN ? undefined : v))} />
        )}
      </Field>
      <Field loc={["weighting"]} label={tr.weighting}>
        <Seg
          label={tr.weighting}
          value={spec.weighting ?? "equal"}
          options={[["equal", tr.weightings.equal!], ["score", tr.weightings.score!], ["formula", tr.weightings.formula!]]}
          onChange={(v) => {
            const next = put(spec, "weighting", v === "equal" ? undefined : v);
            onChange(v === "formula" ? put(next, "formula", spec.formula ?? ["DIV", 1, ["STD", ["SUB", ["DIV", "adj_close", ["REF", "adj_close", 1]], 1], 60]]) : put(next, "formula", undefined));
          }}
        />
      </Field>
      {spec.weighting === "formula" && (
        <Field loc={["formula"]} label={tr.formula}>
          <FormEditor form={(spec.formula ?? null) as Form} label={tr.formula} onChange={(f) => onChange(put(spec, "formula", f as StrategySpec["formula"]))} />
        </Field>
      )}
      <Field loc={["max_weight"]} label={tr.maxWeight} note={tr.maxWeightNote} hint={maxWeight !== null && (maxWeight <= 0 || maxWeight >= 1) ? t.strategy.hints.between("0%", "100%") : null}>
        <Switch on={maxWeight !== null} label={tr.maxWeight} onChange={(on) => onChange(put(spec, "max_weight", on ? 0.1 : undefined))} />
        {maxWeight !== null && (
          // Percent in the box, a ratio in the form; 100% behaves as no cap, so it is written as null (F-16).
          <label className="inline">
            <NumberInput
              value={Number((maxWeight * 100).toPrecision(12))}
              width={70}
              onChange={(v) => onChange(put(spec, "max_weight", v === null || v === 100 ? undefined : Number((v / 100).toPrecision(12))))}
            />
            %
          </label>
        )}
      </Field>
      <details className="advanced">
        <summary>{tr.advanced}</summary>
        <Field loc={["every"]} label={tr.every} hint={(spec.every ?? 1) < 1 ? t.strategy.hints.atLeast(1) : null}>
          <NumberInput value={spec.every ?? 1} width={60} onChange={(v) => onChange(put(spec, "every", v === null || v === 1 ? undefined : v))} />
        </Field>
        <Field loc={["band"]} label={tr.band}>
          <NumberInput
            value={spec.band === null || spec.band === undefined ? null : Number((spec.band * 100).toPrecision(12))}
            width={70}
            onChange={(v) => onChange(put(spec, "band", v === null ? undefined : Number((v / 100).toPrecision(12))))}
          />
        </Field>
      </details>
      <Field loc={["industry_cap"]} label={tr.industryCap} note={tr.industryCapWhy}>
        <input className="num-input" disabled style={{ width: 70 }} />
      </Field>
      <p className="note">{tr.execution}</p>
    </>
  );
}
