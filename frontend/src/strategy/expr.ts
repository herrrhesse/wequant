// The visual condition and factor builder: what a row of controls means as an expression form, and back.
// This only writes the user's choice as a form (strategy-field-map.md 1.3 and 1.7); it computes no data.

export type Form = string | number | null | Form[];

/** A metric the builder offers: a panel field, or one of the common indicators built from fields. */
export type Metric =
  | { kind: "field"; field: string }
  | { kind: "change"; n: number }
  | { kind: "avg"; field: string; n: number }
  | { kind: "volatility"; n: number }
  | { kind: "vs_ma"; n: number }
  | { kind: "from_high"; n: number };

/** How a condition compares its metric: a value, or a rank within the pool (D13). */
export type Compare =
  | { op: "gt" | "lt"; value: number }
  | { op: "top_n"; n: number; dir: "high" | "low" }
  | { op: "top_pct"; pct: number; dir: "high" | "low" };

const ret = (n: number): Form => ["SUB", ["DIV", "adj_close", ["REF", "adj_close", n]], 1];

export function metricForm(m: Metric): Form {
  switch (m.kind) {
    case "field":
      return m.field;
    case "change":
      return ret(m.n);
    case "avg":
      return ["MA", m.field, m.n];
    case "volatility":
      return ["STD", ret(1), m.n];
    case "vs_ma":
      return ["SUB", ["DIV", "adj_close", ["MA", "adj_close", m.n]], 1];
    case "from_high":
      return ["SUB", ["DIV", "adj_close", ["HHV", "adj_high", m.n]], 1];
  }
}

export function conditionForm(m: Metric, c: Compare): Form {
  const x = metricForm(m);
  switch (c.op) {
    case "gt":
      return ["GT", x, c.value];
    case "lt":
      return ["LT", x, c.value];
    case "top_n":
      return ["LE", ["CS_RANK", c.dir === "high" ? ["MUL", x, -1] : x], c.n];
    case "top_pct":
      return c.dir === "high" ? ["GT", ["CS_PCT", x], 1 - c.pct / 100] : ["LE", ["CS_PCT", x], c.pct / 100];
  }
}

const same = (a: Form, b: Form) => JSON.stringify(a) === JSON.stringify(b);
const isInt = (v: Form): v is number => typeof v === "number" && Number.isInteger(v) && v > 0;
/** p of a top-p% threshold written as 1 - p/100 or p/100, rounded away from float noise. */
const pct = (v: number) => Math.round(v * 100 * 1e6) / 1e6;

export function parseMetric(f: Form): Metric | null {
  if (typeof f === "string") return { kind: "field", field: f };
  if (!Array.isArray(f)) return null;
  if (f[0] === "MA" && typeof f[1] === "string" && isInt(f[2] ?? null) && f.length === 3) return { kind: "avg", field: f[1], n: f[2] as number };
  for (const kind of ["change", "volatility", "vs_ma", "from_high"] as const) {
    const n = findWindow(f, kind);
    if (n !== null) return { kind, n };
  }
  return null;
}

/** The window N for which metricForm({kind, n}) equals f, or null. */
function findWindow(f: Form[], kind: "change" | "volatility" | "vs_ma" | "from_high"): number | null {
  const ints = JSON.stringify(f).match(/\d+/g)?.map(Number).filter((n) => n > 1) ?? [];
  for (const n of ints) if (same(metricForm({ kind, n }), f)) return n;
  return null;
}

export function parseCondition(f: Form): { metric: Metric; compare: Compare } | null {
  if (!Array.isArray(f) || f.length !== 3) return null;
  const [op, a, b] = f;
  if ((op === "GT" || op === "LT") && typeof b === "number") {
    if (Array.isArray(a) && a[0] === "CS_PCT" && op === "GT") return withMetric(a[1] ?? null, { op: "top_pct", pct: pct(1 - b), dir: "high" });
    const metric = parseMetric(a ?? null);
    return metric && { metric, compare: { op: op === "GT" ? "gt" : "lt", value: b } };
  }
  if (op === "LE" && Array.isArray(a) && typeof b === "number") {
    if (a[0] === "CS_PCT") return withMetric(a[1] ?? null, { op: "top_pct", pct: pct(b), dir: "low" });
    if (a[0] === "CS_RANK" && isInt(b)) {
      const inner = a[1] ?? null;
      if (Array.isArray(inner) && inner[0] === "MUL" && inner[2] === -1) return withMetric(inner[1] ?? null, { op: "top_n", n: b, dir: "high" });
      return withMetric(inner, { op: "top_n", n: b, dir: "low" });
    }
  }
  return null;
}

function withMetric(f: Form, compare: Compare): { metric: Metric; compare: Compare } | null {
  const metric = parseMetric(f);
  return metric && { metric, compare };
}
