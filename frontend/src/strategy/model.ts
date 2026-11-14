// The strategy editor's view of a StrategySpec: filter groups as the page shows them, metric choices, automatic
// names and the funnel numbers each row shows. Only the user's choices are written; no stock data is computed here.
import type { ConditionSpec, FactorSpec, FunnelStep, GroupSpec, StepCheck, StrategySpec } from "../api/types.gen";
import { conditionForm, metricForm, parseCondition, parseMetric, type Compare, type Form, type Metric } from "./expr.ts";
import { metricUnit, toBackend, toDisplay, type Unit } from "./fields.ts";

export type Filter = ConditionSpec | GroupSpec;
export const isGroup = (f: Filter): f is GroupSpec => "conditions" in f;

/** Field names the metric menu uses (interface wording for panel fields). */
export const FIELD_NAMES: Record<string, string> = {
  open: "开盘价", high: "最高价", low: "最低价", close: "收盘价", preclose: "前收盘价", volume: "成交量", amount: "成交额",
  adj_open: "后复权开盘价", adj_high: "后复权最高价", adj_low: "后复权最低价", adj_close: "后复权收盘价", adj_preclose: "后复权前收盘价",
  factor: "复权因子", limit_up_price: "涨停价", limit_down_price: "跌停价", list_days: "上市交易日数",
};

export const UNIT_TEXT: Record<Unit, string> = { yuan: "元", yi_yuan: "亿元", shares: "股", percent: "%", ratio: "", trading_days: "个交易日" };

/** A metric's name: "20 日涨跌幅", "20 日平均成交额", "收盘价". */
export function metricName(m: Metric): string {
  switch (m.kind) {
    case "field":
      return FIELD_NAMES[m.field] ?? m.field;
    case "change":
      return `${m.n} 日涨跌幅`;
    case "avg":
      return `${m.n} 日平均${FIELD_NAMES[m.field] ?? m.field}`;
    case "volatility":
      return `${m.n} 日波动率`;
    case "vs_ma":
      return `相对 ${m.n} 日均线`;
    case "from_high":
      return `距 ${m.n} 日最高`;
  }
}

/** What the metric menu offers first; a metric the strategy already uses is added when it is not among these. */
export const METRIC_PRESETS: Metric[] = [
  { kind: "avg", field: "amount", n: 20 },
  { kind: "avg", field: "amount", n: 60 },
  { kind: "field", field: "amount" },
  { kind: "field", field: "close" },
  { kind: "change", n: 5 },
  { kind: "change", n: 20 },
  { kind: "change", n: 60 },
  { kind: "change", n: 120 },
  { kind: "volatility", n: 20 },
  { kind: "volatility", n: 60 },
  { kind: "vs_ma", n: 20 },
  { kind: "vs_ma", n: 60 },
  { kind: "from_high", n: 60 },
  { kind: "from_high", n: 250 },
];

/** Short explanations shown in the "添加条件" panel. */
export function metricDesc(m: Metric): string {
  switch (m.kind) {
    case "field":
      return m.field === "amount" ? "当天成交额" : m.field === "close" ? "当天收盘价（不复权）" : "当天的值";
    case "change":
      return `后复权收盘价相对 ${m.n} 个交易日前的涨跌`;
    case "avg":
      return `最近 ${m.n} 个交易日的平均值`;
    case "volatility":
      return `最近 ${m.n} 个交易日日收益率的标准差`;
    case "vs_ma":
      return `收盘价高出 ${m.n} 日均线多少`;
    case "from_high":
      return `收盘价比 ${m.n} 日最高价低多少`;
  }
}

export const metricKey = (m: Metric) => JSON.stringify(metricForm(m));

export type Mode = "gt" | "lt" | "topn" | "toppct";

/** A condition row as the page edits it; `custom` when its expression is not one the builder writes. */
export type Row =
  | { custom: false; metric: Metric; mode: Mode; dir: "high" | "low"; value: string; unit: Unit }
  | { custom: true; text: string };

export function readCondition(expr: ConditionSpec["expr"]): Row {
  const parsed = parseCondition(expr as Form);
  if (!parsed) return { custom: true, text: typeof expr === "string" ? expr : JSON.stringify(expr) };
  const { metric, compare } = parsed;
  const unit = metricUnit(metric);
  if (compare.op === "gt" || compare.op === "lt") return { custom: false, metric, mode: compare.op, dir: "high", value: String(toDisplay(compare.value, unit)), unit };
  if (compare.op === "top_n") return { custom: false, metric, mode: "topn", dir: compare.dir, value: String(compare.n), unit };
  if (compare.op === "top_pct") return { custom: false, metric, mode: "toppct", dir: compare.dir, value: String(compare.pct), unit };
  return { custom: true, text: JSON.stringify(expr) };
}

export type Expr = ConditionSpec["expr"];
/** A builder form as a spec expression (forms the builder writes are strings or arrays). */
export const exprOf = (f: Form): Expr => f as Expr;

/** The expression of a row; null while its number is not a number (the row keeps the text, the spec its last form). */
export function writeCondition(r: Exclude<Row, { custom: true }>): Expr | null {
  const v = Number(r.value);
  if (r.value.trim() === "" || !Number.isFinite(v)) return null;
  let c: Compare;
  if (r.mode === "gt" || r.mode === "lt") c = { op: r.mode, value: toBackend(v, metricUnit(r.metric)) };
  else if (r.mode === "topn") c = { op: "top_n", n: v, dir: r.dir };
  else c = { op: "top_pct", pct: v, dir: r.dir };
  return exprOf(conditionForm(r.metric, c));
}

/** A custom expression typed by the user: JSON when it parses as an array, else the text as a string expression. */
export function readCustom(text: string): Expr {
  const s = text.trim();
  if (s.startsWith("[")) {
    try {
      return JSON.parse(s) as Expr;
    } catch {
      return s;
    }
  }
  return s;
}

export function autoName(r: Row): string {
  if (r.custom) return r.text.length > 24 ? `${r.text.slice(0, 24)}…` : r.text;
  const m = metricName(r.metric);
  if (r.mode === "gt") return `${m}大于 ${r.value}${UNIT_TEXT[r.unit]}`;
  if (r.mode === "lt") return `${m}小于 ${r.value}${UNIT_TEXT[r.unit]}`;
  const side = r.dir === "high" ? "最高" : "最低";
  return r.mode === "topn" ? `${m}${side}的前 ${r.value} 只` : `${m}${side}的前 ${r.value}%`;
}

/** A name not used by any other condition, group or factor (funnel steps and errors are keyed by name). */
export function uniqueName(base: string, taken: Set<string>): string {
  if (!taken.has(base)) return base;
  for (let i = 2; ; i++) if (!taken.has(`${base} ${i}`)) return `${base} ${i}`;
}

export function namesIn(spec: StrategySpec): Set<string> {
  const out = new Set<string>();
  for (const f of spec.filters ?? []) {
    out.add(f.name);
    if (isGroup(f)) for (const c of f.conditions) out.add(c.name);
  }
  for (const f of spec.factors ?? []) out.add(f.name);
  return out;
}

/** How the page groups the filters: each GroupSpec is a box; consecutive plain conditions share one "全部满足" box. */
export interface Box {
  /** Index of the GroupSpec in filters, or null for a run of plain conditions. */
  group: number | null;
  mode: "all" | "any";
  /** [path, condition] of each condition, path relative to the strategy. */
  items: { path: (string | number)[]; c: ConditionSpec }[];
  name: string | null;
}

export function boxes(spec: StrategySpec): Box[] {
  const out: Box[] = [];
  (spec.filters ?? []).forEach((f, i) => {
    if (isGroup(f)) {
      out.push({ group: i, mode: f.mode ?? "all", items: f.conditions.map((c, j) => ({ path: ["filters", i, "conditions", j], c })), name: f.name });
      return;
    }
    const last = out[out.length - 1];
    if (last && last.group === null) last.items.push({ path: ["filters", i], c: f });
    else out.push({ group: null, mode: "all", items: [{ path: ["filters", i], c: f }], name: null });
  });
  return out;
}

/** The funnel numbers of each step, by step name. */
export interface Counts {
  /** Left after the step, in chain order. */
  left: Map<string, number>;
  /** Passed on its own. */
  passed: Map<string, number>;
  /** The stock pool size: left after the last pool step (or the scope when there is none). */
  pool: number | null;
  /** Left after the scope (before the pool steps). */
  scope: number | null;
}

export function counts(funnel: FunnelStep[] | undefined): Counts {
  const left = new Map<string, number>();
  const passed = new Map<string, number>();
  let pool: number | null = null;
  let scope: number | null = null;
  for (const s of funnel ?? []) {
    if (s.left !== null) left.set(s.step, s.left);
    passed.set(s.step, s.passed);
    if (s.kind === "universe") scope = s.left;
    if (s.kind === "universe" || s.kind === "pool") pool = s.left;
  }
  return { left, passed, pool, scope };
}

/** "单独剔除 Y 只": the pool size minus what passes this step on its own (the one subtraction the page does). */
export function cutAlone(c: Counts, step: string, kind: "pool" | "condition"): number | null {
  const base = kind === "pool" ? c.scope : c.pool;
  const p = c.passed.get(step);
  return base === null || p === undefined ? null : base - p;
}

/** Changes of left counts between two funnels, as "+3" / "−2", by step name. */
export function deltas(before: Counts | null, after: Counts): Map<string, string> {
  const out = new Map<string, string>();
  if (!before) return out;
  for (const [k, v] of after.left) {
    const was = before.left.get(k);
    if (was !== undefined && was !== v) out.set(k, `${v > was ? "+" : "−"}${Math.abs(v - was)}`);
  }
  for (const [k, v] of after.passed) {
    if (after.left.has(k)) continue;
    const was = before.passed.get(k);
    if (was !== undefined && was !== v) out.set(k, `${v > was ? "+" : "−"}${Math.abs(v - was)}`);
  }
  return out;
}

/** A factor's metric when the builder can show it as a menu choice. */
export function factorMetric(f: FactorSpec): Metric | null {
  return parseMetric(f.expr as Form);
}

/** A new, empty strategy (the backend fills its other defaults). */
export function blank(): StrategySpec {
  return { universe: ["NOT", "exchange@BJ"], filters: [], factors: [], top_n: 10, backups: 0, period: "week", every: 1, weighting: "equal", pool: { index: null, boards: null, exclude_st: true, exclude_delisting: true, min_list_days: null } };
}

/** A condition's value as the user reads it: in the condition's unit, a rank for top-N conditions, a percentile for
 * top-x% conditions; nothing for expressions the page cannot read (the raw number is not shown). */
export function stepValue(spec: StrategySpec | null | undefined, s: Pick<StepCheck, "name" | "value">): string {
  if (s.value === null || !spec) return "";
  for (const b of boxes(spec))
    for (const { c } of b.items) {
      if (c.name !== s.name) continue;
      const r = readCondition(c.expr);
      if (r.custom) return "";
      if (r.mode === "gt" || r.mode === "lt") return `${Number(toDisplay(s.value, r.unit).toPrecision(4))}${UNIT_TEXT[r.unit]}`;
      if (r.mode === "topn") return `第 ${Math.round(s.value)} 名`;
      return `百分位 ${(s.value * 100).toFixed(0)}`;
    }
  return "";
}

