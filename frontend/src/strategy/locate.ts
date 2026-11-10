// Where a backend error points: ErrorDetail.loc is pydantic style from the request root ("strategy", then the
// strategy's own path). Controls register their own path; the code view maps paths to JSON lines.

export type Loc = (string | number)[];

/** The error's path inside the strategy: loc without the leading "strategy". */
export function strategyLoc(loc: Loc): Loc {
  return loc[0] === "strategy" ? loc.slice(1) : loc;
}

/** Whether an error at err concerns the control at at: the same path, or a path inside it. */
export function concerns(err: Loc, at: Loc): boolean {
  return err.length >= at.length && at.every((part, i) => err[i] === part);
}

/** value as indented JSON, and the 1-based line where each path starts (the path joined by "."). */
export function jsonLines(value: unknown): { text: string; lines: Map<string, number> } {
  const out: string[] = [];
  const lines = new Map<string, number>();
  const write = (v: unknown, path: Loc, indent: string, prefix: string, suffix: string) => {
    lines.set(path.join("."), out.length + 1);
    if (Array.isArray(v) && v.length && v.some((x) => typeof x === "object" && x !== null)) {
      out.push(`${indent}${prefix}[`);
      v.forEach((x, i) => write(x, [...path, i], `${indent}  `, "", i < v.length - 1 ? "," : ""));
      out.push(`${indent}]${suffix}`);
    } else if (v && typeof v === "object" && !Array.isArray(v)) {
      const entries = Object.entries(v);
      out.push(`${indent}${prefix}{`);
      entries.forEach(([k, x], i) => write(x, [...path, k], `${indent}  `, `${JSON.stringify(k)}: `, i < entries.length - 1 ? "," : ""));
      out.push(`${indent}}${suffix}`);
    } else {
      out.push(`${indent}${prefix}${JSON.stringify(v)}${suffix}`);
    }
  };
  write(value, [], "", "", "");
  return { text: out.join("\n"), lines };
}
