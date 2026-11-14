// The left column of the strategy editor: scope, filters, ranking and trading. Each row shows the backend's funnel
// numbers for it ("剩余" = the step's left, "单独剔除" = pool size minus the step's own pass) and flashes the change.
import { useState, type ReactNode } from "react";
import type { ConditionSpec, FactorSpec, GroupSpec, PoolDrift, Schedule, StrategySpec } from "../../api/types.gen";
import { useLabels } from "../../labels";
import { NumInput } from "../../components/NumInput";
import { IndexSeg, scopeOf } from "../../components/IndexSeg";
import { fromPool, PoolPicker } from "./PoolPicker";
import { MetricSelect, useIndicators } from "./indicators";
import { metricForm, type Metric } from "../../strategy/expr";
import { concerns, type Loc } from "../../strategy/locate";
import {
  autoName,
  boxes,
  exprOf,
  factorMetric,
  isGroup,
  metricKey,
  metricName,
  namesIn,
  readCondition,
  readCustom,
  uniqueName,
  UNIT_TEXT,
  writeCondition,
  type Mode,
  type Row,
} from "../../strategy/model";
import { metricUnit } from "../../strategy/fields";
import { cnDate, tx } from "../../text";
import { st } from "./text";

export interface Err {
  loc: Loc;
  text: string;
}

export interface EditorProps {
  spec: StrategySpec;
  edit: (fn: (d: StrategySpec) => void) => void;
  errors: Err[];
  /** The funnel step highlighted: from the preview, or while the pointer is over a row here. */
  hl: string | null;
  setHl: (step: string | null) => void;
  /** The data has no ST records at all (the backend's st_unknown warning with a known share of 0). */
  stNone: boolean;
  /** The stock pool the scope's list came from has changed since (strategy validate's pool_drift). */
  drift?: PoolDrift | null;
}

/** Whether a name is the automatic one (or it with a number added to keep it unique). */
const isAuto = (name: string, auto: string) => name === auto || new RegExp(`^${auto.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\d+$`).test(name);
const errsAt = (errors: Err[], at: Loc) => errors.filter((e) => concerns(e.loc, at));

/** A section's plain-words explanation, folded until asked for. */
function What({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="what-btn" aria-expanded={open} onClick={() => setOpen(!open)}>
        {st.what} {open ? "▾" : "▸"}
      </button>
      {open && <p className="what-text">{text}</p>}
    </>
  );
}

function ErrText({ errors }: { errors: Err[] }) {
  return errors.length ? <span className="err-text">{errors.map((e) => e.text).join(" ")}</span> : null;
}

/* ---------- 1 · scope ---------- */

export function ScopeCard({ spec, edit, errors, hl, setHl, stNone, drift }: EditorProps) {
  const L = useLabels();
  const pool = spec.pool ?? {};
  const [custom, setCustom] = useState(false);
  const scope = custom && !pool.stocks ? "stocks" : scopeOf(pool);
  const setPool = (fn: (p: NonNullable<StrategySpec["pool"]>) => void) =>
    edit((d) => {
      d.pool = { ...(d.pool ?? {}) };
      fn(d.pool);
    });
  const boards = ["main", "chinext", "star"] as const;
  const onBoards = pool.boards ?? [...boards];
  const universeDefault = JSON.stringify(spec.universe ?? ["NOT", "exchange@BJ"]) === JSON.stringify(["NOT", "exchange@BJ"]);
  const row = (key: string, label: string, body: ReactNode, path: Loc) => {
    const errs = errsAt(errors, path);
    return (
      <div className={`scope-row ${hl === key ? "hl" : ""}`} data-step={key} onMouseEnter={() => setHl(key)} onMouseLeave={() => setHl(null)}>
        <span id={`loc-${path.join("-")}`} className={`scope-label ${errs.length ? "has-err" : ""}`}>
          {label}
        </span>
        <div className="col tight">
          {body}
          <ErrText errors={errs} />
        </div>
      </div>
    );
  };
  return (
    <section className="card" aria-label="范围">
      <div className="card-head">
        <h2>{st.scope.title}</h2>
        <What text={st.help.scope} />
        <span className="push small muted">{st.scope.lead}</span>
      </div>
      {!universeDefault && <span className="warnbox">{st.scope.customUniverse}</span>}
      <div className="scope-grid">
        {row(
          "index",
          st.scope.index,
          <>
            <IndexSeg
              label={st.scope.index}
              value={scope}
              onChange={(code) => {
                setCustom(code === "stocks");
                if (code === "stocks") return;
                setPool((p) => {
                  p.stocks = null;
                  p.index = (code === "all" ? null : code) as typeof p.index;
                });
              }}
            />
            {scope === "stocks" && <PoolPicker pool={pool} drift={drift} apply={(p) => edit((d) => void (d.pool = fromPool(d.pool, p)))} />}
          </>,
          ["pool", "index"],
        )}
        {row(
          "boards",
          st.scope.boards,
          <div className="row">
            {boards.map((b) => (
              <label key={b} className="check">
                <input
                  type="checkbox"
                  checked={onBoards.includes(b)}
                  onChange={() =>
                    setPool((p) => {
                      const next = onBoards.includes(b) ? onBoards.filter((x) => x !== b) : [...onBoards, b];
                      p.boards = next.length === boards.length ? null : (next as typeof p.boards);
                    })
                  }
                />
                {tx.boards[b]}
              </label>
            ))}
          </div>,
          ["pool", "boards"],
        )}
        {row(
          "exclude_st",
          st.scope.st,
          <>
          <label className="check">
            <input type="checkbox" checked={pool.exclude_st ?? false} onChange={() => setPool((p) => void (p.exclude_st = !(pool.exclude_st ?? false)))} />
            {st.scope.stText}
          </label>
          {stNone && <span className="warn-text small">{st.scope.stNone}</span>}
          </>,
          ["pool", "exclude_st"],
        )}
        {row(
          "exclude_delisting",
          L.of("pool_items", "exclude_delisting") || st.scope.delisting,
          <label className="check">
            <input
              type="checkbox"
              checked={pool.exclude_delisting ?? false}
              onChange={() => setPool((p) => void (p.exclude_delisting = !(pool.exclude_delisting ?? false)))}
            />
            {L.help("pool_items", "exclude_delisting") || st.scope.delistingText}
          </label>,
          ["pool", "exclude_delisting"],
        )}
        {row(
          "min_list_days",
          st.scope.ipo,
          <span className="row small">
            {st.scope.ipoBefore}
            <NumInput aria-label={st.scope.ipo} style={{ width: 64 }} value={pool.min_list_days ?? null} onValue={(v) => setPool((p) => void (p.min_list_days = v))} />
            {st.scope.ipoAfter}
          </span>,
          ["pool", "min_list_days"],
        )}
      </div>
      <span className="note">{st.scope.note}</span>
    </section>
  );
}

/* ---------- 2 · filters ---------- */

export function FiltersCard({ spec, edit, errors, hl, setHl }: EditorProps) {
  const [adding, setAdding] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [cat, setCat] = useState("");
  const ind = useIndicators();
  const list = boxes(spec);
  const at = (d: StrategySpec, path: Loc): ConditionSpec => {
    const f = d.filters![path[1] as number]!;
    return path.length === 2 ? (f as ConditionSpec) : (f as GroupSpec).conditions[path[3] as number]!;
  };
  const addTo = (bi: number, metric: Metric | null) =>
    edit((d) => {
      const b = bi < 0 ? null : boxes(d)[bi];
      if (bi >= 0 && !b) return;
      const row: Row = metric ? { custom: false, metric, mode: "gt", dir: "high", value: "0", unit: metricUnit(metric) } : { custom: true, text: "GT(close, 0)" };
      const expr = row.custom ? readCustom(row.text) : (writeCondition(row) ?? exprOf(metricForm(row.metric)));
      const c: ConditionSpec = { name: uniqueName(autoName(row), namesIn(d)), expr };
      if (!b) d.filters = [...(d.filters ?? []), c];
      else if (b.group !== null) (d.filters![b.group] as GroupSpec).conditions.push(c);
      else {
        const last = b.items[b.items.length - 1]!.path[1] as number;
        d.filters!.splice(last + 1, 0, c);
      }
    });
  const addPanel = (bi: number) => (
                <div className="add-panel">
                  <input className="input" aria-label={st.filters.addSearch} placeholder={st.filters.addPlaceholder} value={query} onChange={(e) => setQuery(e.target.value)} />
                  {ind.categories.length > 0 && (
                    <div className="seg" role="group" aria-label="指标分类">
                      <button type="button" aria-pressed={cat === ""} onClick={() => setCat("")}>
                        全部
                      </button>
                      {ind.categories.map((c) => (
                        <button key={c.code} type="button" aria-pressed={cat === c.code} title={c.help} onClick={() => setCat(c.code)}>
                          {c.label}
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="add-grid">
                    {shown.map((i) => (
                      <button
                        key={i.key}
                        type="button"
                        disabled={!i.available}
                        onClick={() => {
                          addTo(bi, i.metric);
                          setAdding(null);
                        }}
                      >
                        <span className="b">
                          {i.label}
                          {i.category && <span className="tag ind-cat">{ind.catName(i.category)}</span>}
                        </span>
                        <span className="tiny muted">{i.help}</span>
                        {!i.available && <span className="tiny warn-text">{i.reason || st.filters.unavailable}</span>}
                        {i.available && i.needsMerge && <span className="tiny muted">{st.filters.needsMerge}</span>}
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={() => {
                        addTo(bi, null);
                        setAdding(null);
                      }}
                    >
                      <span className="b">{st.filters.customItem}</span>
                      <span className="tiny muted">{st.filters.customItemDesc}</span>
                    </button>
                  </div>
                  <button type="button" className="btn sm soft push" onClick={() => setAdding(null)}>
                    {st.filters.collapse}
                  </button>
                </div>
    );
  const q = query.trim();
  const shown = ind.list.filter((i) => !i.legacy && (!cat || i.category === cat) && (!q || i.label.includes(q) || i.help.includes(q)));
  return (
    <section className="card" aria-label="筛选条件">
      <div className="card-head">
        <h2>{st.filters.title}</h2>
        <What text={st.help.filters} />
        <span className="push small muted">{st.filters.lead}</span>
      </div>
      {list.length === 0 && (
        <div className="fgroup">
          <span className="note">{st.filters.empty}</span>
          <div className="row">
            <button type="button" className="btn sm card-btn" onClick={() => setAdding(adding === -1 ? null : -1)}>
              {st.filters.add}
            </button>
          </div>
          {adding === -1 && addPanel(-1)}
        </div>
      )}
      {list.map((b, bi) => {
        const enabled = b.items.filter((i) => i.c.enabled !== false).length;
        const any = b.mode === "any" && enabled > 1;
        const groupStep = b.group !== null ? (spec.filters![b.group] as GroupSpec).name : null;
        return (
          <div key={bi} className={`fgroup ${groupStep && hl === groupStep ? "hl" : ""}`} data-step={groupStep ?? undefined}>
            <div className="row small" onMouseEnter={() => groupStep && setHl(groupStep)} onMouseLeave={() => groupStep && setHl(null)}>
              {bi > 0 && <span className="tag">{st.filters.and}</span>}
              <span>{st.filters.before}</span>
              <select
                className="select b"
                aria-label="组内关系"
                value={b.mode}
                onChange={(e) => {
                  const mode = e.target.value as "all" | "any";
                  edit((d) => {
                    if (b.group !== null) (d.filters![b.group] as GroupSpec).mode = mode;
                    else if (mode === "any") {
                      const idx = b.items.map((i) => i.path[1] as number);
                      const conds = idx.map((i) => d.filters![i] as ConditionSpec);
                      d.filters!.splice(idx[0]!, idx.length, { name: uniqueName("条件组", namesIn(d)), mode: "any", conditions: conds as [ConditionSpec, ...ConditionSpec[]] });
                    }
                  });
                }}
              >
                <option value="all">{st.filters.all}</option>
                <option value="any">{st.filters.any}</option>
              </select>
              <span>{st.filters.after}</span>
              {list.length > 1 && (
                <button
                  type="button"
                  className="btn sm ghost push"
                  onClick={() =>
                    edit((d) => {
                      const idx = b.group !== null ? [b.group] : b.items.map((i) => i.path[1] as number);
                      d.filters!.splice(idx[0]!, idx.length);
                    })
                  }
                >
                  {st.filters.removeGroup}
                </button>
              )}
            </div>
            {b.items.map(({ path, c }) => (
              <CondRow
                key={path.join(".")}
                path={path}
                c={c}
                any={any}
                inGroup={b.group !== null}
                setHl={setHl}
                errors={errsAt(errors, path)}
                hl={hl === c.name || (any && hl === groupStep)}
                taken={namesIn(spec)}
                update={(fn) => edit((d) => fn(at(d, path)))}
                remove={() =>
                  edit((d) => {
                    if (path.length === 2) d.filters!.splice(path[1] as number, 1);
                    else {
                      const g = d.filters![path[1] as number] as GroupSpec;
                      if (g.conditions.length === 1) d.filters!.splice(path[1] as number, 1);
                      else g.conditions.splice(path[3] as number, 1);
                    }
                  })
                }
              />
            ))}
            <div className="row">
              <button type="button" className="btn sm card-btn" onClick={() => setAdding(adding === bi ? null : bi)}>
                {st.filters.add}
              </button>
            </div>
            {adding === bi && addPanel(bi)}
          </div>
        );
      })}
      <button
        type="button"
        className="btn sm soft"
        style={{ alignSelf: "flex-start" }}
        onClick={() =>
          edit((d) => {
            const row: Row = { custom: false, metric: { kind: "change", n: 20 }, mode: "gt", dir: "high", value: "0", unit: "percent" };
            const names = namesIn(d);
            const c: ConditionSpec = { name: uniqueName(autoName(row), names), expr: writeCondition(row)! };
            names.add(c.name);
            d.filters = [...(d.filters ?? []), { name: uniqueName("条件组", names), mode: "any", conditions: [c] }];
          })
        }
      >
        {st.filters.addGroup}
      </button>
      <span className="note">{st.filters.note}</span>
    </section>
  );
}

interface CondProps {
  path: Loc;
  c: ConditionSpec;
  any: boolean;
  inGroup: boolean;
  setHl: (step: string | null) => void;
  errors: Err[];
  hl: boolean;
  taken: Set<string>;
  update: (fn: (c: ConditionSpec) => void) => void;
  remove: () => void;
}

function CondRow({ path, c, errors, hl, setHl, taken, update, remove }: CondProps) {
  const indicators = useIndicators();
  const row = readCondition(c.expr);
  const info = row.custom ? undefined : indicators.byKey.get(metricKey(row.metric));
  const off = c.enabled === false;
  const auto = autoName(row);
  const named = !isAuto(c.name, auto);
  // Rewrite the expression, and the name when it was automatic, keeping names unique.
  const write = (next: Row) =>
    update((x) => {
      const wasAuto = isAuto(x.name, autoName(readCondition(x.expr)));
      const expr = next.custom ? readCustom(next.text) : writeCondition(next);
      if (expr === null) return;
      x.expr = expr;
      if (wasAuto) {
        const others = new Set(taken);
        others.delete(x.name);
        x.name = uniqueName(autoName(next), others);
      }
    });
  let hint = "";
  if (!row.custom && (row.mode === "topn" || row.mode === "toppct")) {
    const side = row.dir === "high" ? st.filters.high : st.filters.low;
    hint = st.filters.rankBase(side, row.mode === "topn" ? st.filters.topN(row.value) : st.filters.topPct(row.value));
  } else if (row.custom) hint = st.filters.customHint;
  else if (info) hint = `${info.label}：${info.help}`;
  return (
    <div
      id={`loc-${path.join("-")}`}
      data-step={c.name}
      className={`cond ${hl ? "hl" : ""} ${errors.length ? "has-err" : ""} ${off ? "off" : ""}`}
      onMouseEnter={() => setHl(c.name)}
      onMouseLeave={() => setHl(null)}
    >
      <div className="cond-grid">
        <input type="checkbox" checked={!off} aria-label={st.filters.enable} onChange={() => update((x) => void (x.enabled = off ? true : false))} />
        {row.custom ? (
          <input className="input mono grow" aria-label={st.filters.custom} defaultValue={row.text} onBlur={(e) => e.target.value !== row.text && write({ custom: true, text: e.target.value })} style={{ gridColumn: "span 2" }} />
        ) : (
          <>
            <div className="row nowrap">
              <MetricSelect className="grow" label={st.filters.field} value={row.metric} onChange={(m) => write({ ...row, metric: m, unit: metricUnit(m), value: row.mode === "gt" || row.mode === "lt" ? "0" : row.value })} />
              <select className="select" style={{ width: 112 }} aria-label={st.filters.mode} value={row.mode} onChange={(e) => {
                const mode = e.target.value as Mode;
                const value = mode === "topn" ? (Number(row.value) >= 1 && row.mode !== "gt" && row.mode !== "lt" ? row.value : "50") : mode === "toppct" ? (Number(row.value) > 0 && Number(row.value) <= 100 && row.mode === "toppct" ? row.value : "30") : row.mode === mode ? row.value : "0";
                write({ ...row, mode, value });
              }}>
                {(["gt", "lt", "topn", "toppct"] as const).map((m) => (
                  <option key={m} value={m}>
                    {st.filters.modes[m]}
                  </option>
                ))}
              </select>
            </div>
            <div className="row nowrap">
              {(row.mode === "topn" || row.mode === "toppct") && (
                <select className="select" style={{ width: 76 }} aria-label={st.filters.dir} value={row.dir} onChange={(e) => write({ ...row, dir: e.target.value as "high" | "low" })}>
                  <option value="high">{st.filters.high}</option>
                  <option value="low">{st.filters.low}</option>
                </select>
              )}
              <NumInput aria-label={st.filters.value} style={{ width: 64 }} value={Number(row.value)} onValue={(v) => v !== null && write({ ...row, value: String(v) })} />
              <span className="small muted">{row.mode === "topn" ? "只" : row.mode === "toppct" ? "%" : UNIT_TEXT[row.unit]}</span>
            </div>
          </>
        )}
        <div className="row nowrap push">
          {off && <span className="tag">{st.filters.off}</span>}
          <button type="button" className="icon-btn" aria-label={st.filters.remove} onClick={remove}>
            ×
          </button>
        </div>
      </div>
      <div className="cond-meta">
        {hint && <span>{hint}</span>}
        <span className="row nowrap">
          {st.filters.name}
          <input
            className="name-input"
            aria-label={st.filters.name}
            defaultValue={c.name}
            key={c.name}
            onBlur={(e) => {
              const v = e.target.value.trim();
              if (!v || v === c.name) return;
              const others = new Set(taken);
              others.delete(c.name);
              update((x) => void (x.name = uniqueName(v, others)));
            }}
          />
          （{named ? st.filters.nameHand : st.filters.nameAuto}）
        </span>
      </div>
      <ErrText errors={errors} />
    </div>
  );
}

/* ---------- 3 · factors ---------- */

export function FactorsCard({ spec, edit, errors, shares }: EditorProps & { shares: Record<string, number> | null }) {
  const factors = spec.factors ?? [];
  const up = (i: number, fn: (f: FactorSpec) => void) => edit((d) => fn(d.factors![i]!));
  const errs = errsAt(errors, ["factors"]);
  return (
    <section id="loc-factors" className={`card ${errs.some((e) => e.loc.length === 1) ? "has-err" : ""}`} aria-label="排名打分">
      <div className="card-head">
        <h2>{st.factors.title}</h2>
        <What text={st.help.factors} />
        <span className="push small muted">{st.factors.lead}</span>
      </div>
      {factors.length === 0 && <span className="note">{st.factors.empty}</span>}
      {factors.length > 0 && (
        <div className="factor-grid">
          <span className="head" />
          <span className="head">{st.factors.metric}</span>
          <span className="head">{st.factors.prefer}</span>
          <span className="head r">{st.factors.weight}</span>
          <span className="head r">{st.factors.share}</span>
          <span className="head" />
          {factors.map((f, i) => {
            const m = factorMetric(f);
            const fe = errsAt(errors, ["factors", i]);
            const share = shares?.[f.name];
            return (
              <div key={i} id={`loc-factors-${i}`} className={`factor-row ${f.enabled === false ? "off" : ""} ${fe.length ? "has-err" : ""}`}>
                <input type="checkbox" aria-label={st.factors.on} checked={f.enabled !== false} onChange={() => up(i, (x) => void (x.enabled = f.enabled === false))} />
                <div className="col tight">
                  {m ? (
                    <MetricSelect
                      label={st.factors.metric}
                      value={m}
                      onChange={(next) =>
                        edit((d) => {
                          const x = d.factors![i]!;
                          const wasAuto = isAuto(x.name, metricName(m));
                          x.expr = exprOf(metricForm(next));
                          if (wasAuto) {
                            const others = namesIn(d);
                            others.delete(x.name);
                            x.name = uniqueName(metricName(next), others);
                          }
                        })
                      }
                    />
                  ) : (
                    <input className="input mono" aria-label={st.factors.metric} defaultValue={typeof f.expr === "string" ? f.expr : JSON.stringify(f.expr)} onBlur={(e) => up(i, (x) => void (x.expr = readCustom(e.target.value)))} />
                  )}
                  <span className="row nowrap tiny muted">
                    {st.factors.name}
                    <input
                      className="name-input"
                      aria-label={st.factors.name}
                      defaultValue={f.name}
                      key={f.name}
                      onBlur={(e) => {
                        const v = e.target.value.trim();
                        if (!v || v === f.name) return;
                        edit((d) => {
                          const others = namesIn(d);
                          others.delete(f.name);
                          d.factors![i]!.name = uniqueName(v, others);
                        });
                      }}
                    />
                  </span>
                </div>
                <div className="seg" role="group" aria-label={st.factors.prefer}>
                  <button type="button" aria-pressed={(f.prefer ?? "high") === "high"} onClick={() => up(i, (x) => void (x.prefer = "high"))}>
                    {st.factors.high}
                  </button>
                  <button type="button" aria-pressed={f.prefer === "low"} onClick={() => up(i, (x) => void (x.prefer = "low"))}>
                    {st.factors.low}
                  </button>
                </div>
                <NumInput aria-label={st.factors.weight} style={{ width: 64, justifySelf: "end" }} value={f.weight ?? 1} onValue={(v) => v !== null && up(i, (x) => void (x.weight = v))} />
                <span className="num r">{share === undefined || f.enabled === false ? "—" : `${Math.round(share * 100)}%`}</span>
                <button type="button" className="icon-btn" aria-label={st.factors.remove} onClick={() => edit((d) => void d.factors!.splice(i, 1))}>
                  ×
                </button>
                <ErrText errors={fe} />
              </div>
            );
          })}
        </div>
      )}
      <ErrText errors={errs.filter((e) => e.loc.length === 1)} />
      <button
        type="button"
        className="btn sm soft"
        style={{ alignSelf: "flex-start" }}
        onClick={() =>
          edit((d) => {
            const m: Metric = { kind: "change", n: 60 };
            d.factors = [...(d.factors ?? []), { name: uniqueName(metricName(m), namesIn(d)), expr: exprOf(metricForm(m)), prefer: "high", weight: 1 }];
          })
        }
      >
        {st.factors.add}
      </button>
    </section>
  );
}

/* ---------- 4 · trading ---------- */

export function TradeCard({ spec, edit, errors, schedule }: EditorProps & { schedule: Schedule | null }) {
  const L = useLabels();
  const [adv, setAdv] = useState(false);
  const n = spec.top_n ?? 0;
  const k = spec.hold_rank && spec.hold_rank > n ? spec.hold_rank : n;
  const m = spec.backups ?? 0;
  const span = Math.max(k, n + m) + Math.max(5, Math.round(n * 0.3));
  const w = (x: number) => `${((x / Math.max(1, span)) * 100).toFixed(1)}%`;
  const every = spec.every ?? 1;
  const period = spec.period ?? "week";
  const field = (key: keyof StrategySpec, label: ReactNode, body: ReactNode, note?: string) => {
    const errs = errsAt(errors, [key]);
    return (
      <div id={`loc-${key}`} className={`tfield ${errs.length ? "has-err" : ""}`}>
        <span className="row small">
          {label}
          {body}
        </span>
        {note && <span className="note">{note}</span>}
        <ErrText errors={errs} />
      </div>
    );
  };
  return (
    <section className="card" aria-label="交易与仓位">
      <div className="card-head">
        <h2>{st.trade.title}</h2>
        <What text={st.help.trade} />
      </div>
      <div className="row">
        {field("top_n", st.trade.hold, <><NumInput aria-label={st.trade.hold} style={{ width: 60 }} value={spec.top_n ?? null} onValue={(v) => edit((d) => void (d.top_n = v))} />{st.trade.holdUnit}</>)}
        {field("backups", st.trade.backups, <><NumInput aria-label={st.trade.backups} style={{ width: 60 }} value={spec.backups ?? 0} onValue={(v) => edit((d) => void (d.backups = v ?? 0))} />{st.trade.holdUnit}</>)}
        {field("hold_rank", st.trade.holdRank, <><NumInput aria-label={st.trade.holdRank} style={{ width: 60 }} value={spec.hold_rank ?? null} onValue={(v) => edit((d) => void (d.hold_rank = v))} />{st.trade.holdRankAfter}</>, st.trade.holdRankNote)}
      </div>
      {n > 0 && (
        <div className="axis" aria-hidden="true">
          <div className="axis-bar">
            <span style={{ width: w(n), background: "var(--inv)", color: "var(--on-inv)" }}>{st.trade.axisBuy(n)}</span>
            {k > n && <span style={{ width: w(k - n), background: "var(--chip)" }}>{st.trade.axisKeep(n + 1, k)}</span>}
            <span style={{ flex: 1, background: "var(--soft)", color: "var(--muted)" }}>{st.trade.axisSell}</span>
            {m > 0 && (
              <span className="axis-backup" style={{ left: w(n), width: w(m) }}>
                {st.trade.axisBackup(n + 1, n + m)}
              </span>
            )}
          </div>
          <div className="axis-ticks">
            <span>{st.trade.tick(1)}</span>
            <span>{st.trade.tick(span)}</span>
          </div>
        </div>
      )}
      {n > 0 && <span className="small sub">{st.trade.axisText(n, k, m)}</span>}
      {field(
        "every",
        st.trade.every,
        <>
          <NumInput aria-label="每几期" style={{ width: 48 }} value={every} onValue={(v) => v !== null && edit((d) => void (d.every = v))} />
          <select className="select" aria-label={st.trade.period} value={period} onChange={(e) => edit((d) => void (d.period = e.target.value as typeof period))}>
            {(["day", "week", "month"] as const).map((p) => (
              <option key={p} value={p}>
                {st.trade.periods[p]}
              </option>
            ))}
          </select>
          ，{period === "day" ? (every === 1 ? st.trade.daily : st.trade.everyN(every)) : st.trade.lastDay}，{st.trade.fill}
        </>,
      )}
      {schedule && schedule.days.length > 0 && (
        <span className="small sub">
          {st.trade.nextDays}
          {schedule.days.map((d) => cnDate(d.date)).join("、")}
          {schedule.days.some((d) => d.source === "estimated") && <span className="muted">（{L.help("day_sources", "estimated")}）</span>}
        </span>
      )}
      {period === "day" && every <= 2 && <span className="warnbox">{st.trade.freqWarn}</span>}
      {field(
        "weighting",
        st.trade.weighting,
        <div className="seg" role="group" aria-label={st.trade.weighting}>
          <button type="button" aria-pressed={(spec.weighting ?? "equal") === "equal"} onClick={() => edit((d) => void (d.weighting = "equal"))}>
            {st.trade.equal}
          </button>
          <button type="button" aria-pressed={spec.weighting === "score"} onClick={() => edit((d) => void (d.weighting = "score"))}>
            {st.trade.score}
          </button>
          {spec.weighting === "formula" && <button type="button" aria-pressed>{st.trade.formula}</button>}
        </div>,
        spec.weighting === "formula" ? st.trade.formulaNote : undefined,
      )}
      {field("max_weight", st.trade.cap, <><NumInput aria-label={st.trade.cap} style={{ width: 60 }} value={spec.max_weight ?? null} scale={0.01} onValue={(v) => edit((d) => void (d.max_weight = v))} />{st.trade.pct}</>)}
      <button type="button" className="link small" style={{ alignSelf: "flex-start", border: 0, background: "none", padding: 0 }} aria-expanded={adv} onClick={() => setAdv(!adv)}>
        {st.trade.adv} {adv ? "▾" : "▸"}
      </button>
      {adv && (
        <>
          {field("band", st.trade.band, <><NumInput aria-label={st.trade.band} style={{ width: 60 }} value={spec.band ?? null} scale={0.01} onValue={(v) => edit((d) => void (d.band = v))} />{st.trade.pct}</>, st.trade.bandNote)}
          {field("anchor", st.trade.anchor, <input className="input" type="date" aria-label={st.trade.anchor} value={spec.anchor ?? ""} onChange={(e) => edit((d) => void (d.anchor = e.target.value || null))} />)}
        </>
      )}
    </section>
  );
}

export { isGroup };
