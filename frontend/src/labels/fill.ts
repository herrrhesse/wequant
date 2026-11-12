// The backend's label templates as sentences. Templates use Python format fields ("{share:.1%}", "{amount:,.2f}");
// this fills them exactly as wequant.service.labels.fill does: optional clauses only for set params, repeated items
// (params with suffixes _2, _3, ...) joined by the label's separator. Display only: no value is computed here.

export type Params = Record<string, number | string | null | undefined>;

export interface Template {
  template: string;
  params: string[];
  optional?: Record<string, string>;
  repeat?: string | null;
}

/** One value written with a Python format spec: "", ".1%", "+.1%", ".2f", ",", ",.2f", "d". */
export function formatValue(value: number | string | null | undefined, spec: string): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string" || !spec) return String(value);
  const m = spec.match(/^(\+)?(,)?(?:\.(\d+))?([%fd])?$/);
  if (!m) return String(value);
  const [, plus, comma, digits, type] = m;
  if (plus) return (value >= 0 ? "+" : "") + formatValue(value, spec.slice(1));
  if (digits === undefined && type !== "f" && type !== "%") return comma ? value.toLocaleString("en-US", { maximumFractionDigits: 20 }) : String(Math.trunc(value) === value || type !== "d" ? value : Math.trunc(value));
  const places = digits === undefined ? (type === "d" ? 0 : 6) : Number(digits);
  const v = type === "%" ? value * 100 : value;
  const text = comma
    ? v.toLocaleString("en-US", { minimumFractionDigits: places, maximumFractionDigits: places, useGrouping: true })
    : v.toFixed(places);
  return type === "%" ? `${text}%` : text;
}

function render(template: string, values: Params): string {
  return template.replace(/\{(\w+)(?::([^}]*))?\}/g, (_, name: string, spec: string | undefined) => formatValue(values[name], spec ?? ""));
}

/** The sentence of a label with these params. */
export function fill(label: Template, params: Params): string {
  const clauses = label.optional ?? {};
  const item = (suffix: string) => {
    const values: Params = {};
    for (const p of label.params) values[p] = params[p + suffix];
    const extra = Object.entries(clauses)
      .filter(([p]) => values[p] !== null && values[p] !== undefined)
      .map(([, c]) => c)
      .join("");
    return render(label.template + extra, values);
  };
  if (!label.repeat) return item("");
  const lead = label.params[0] ?? "";
  const numbers = Object.keys(params)
    .map((k) => k.match(new RegExp(`^${lead}_(\\d+)$`)))
    .filter((m): m is RegExpMatchArray => m !== null)
    .map((m) => Number(m[1]))
    .sort((a, b) => a - b);
  return ["", ...numbers.map((n) => `_${n}`)].map(item).join(label.repeat);
}
