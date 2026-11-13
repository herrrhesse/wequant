import { useEffect, useState } from "react";

/** Hash routes keep every page's state in the URL, so the built files work from any path and back/forward restore it:
 * #/ (today), #/data[?code&date], #/stock/CODE[?date&run&from], #/strategy[?id&view=list],
 * #/picks[?id&n&draft&date], #/backtest[?use&run&cmp=A,B&day]. */
export type Route =
  | { page: "today" }
  | { page: "data"; code: string | null; date: string | null }
  | { page: "stock"; code: string; date: string | null; run: string | null; from: string | null }
  | { page: "strategy"; id: string | null; list: boolean; fresh: boolean }
  | { page: "picks"; id: string | null; n: number | null; draft: boolean; date: string | null }
  | { page: "backtest"; run: string | null; cmp: string[]; day: string | null; use: string | null };

export type Page = Route["page"];

const int = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : null);

export function parse(hash: string): Route {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?");
  const q = new URLSearchParams(query);
  if (path === "/strategy") return { page: "strategy", id: q.get("id"), list: q.get("view") === "list", fresh: q.get("new") === "1" };
  if (path === "/picks") return { page: "picks", id: q.get("id"), n: int(q.get("n")), draft: q.get("draft") === "1", date: q.get("date") };
  if (path === "/backtest")
    return { page: "backtest", run: q.get("run"), cmp: (q.get("cmp") ?? "").split(",").filter(Boolean).slice(0, 3), day: q.get("day"), use: q.get("use") };
  const stock = path.match(/^\/stock\/([^/]+)$/);
  if (stock?.[1]) return { page: "stock", code: decodeURIComponent(stock[1]).toUpperCase(), date: q.get("date"), run: q.get("run"), from: q.get("from") };
  if (path === "/data") return { page: "data", code: q.get("code"), date: q.get("date") };
  return { page: "today" };
}

const qs = (params: Record<string, string | number | null | undefined | false>) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== false && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
};

export const href = {
  today: () => "#/",
  strategy: (id?: string | null, list = false) => `#/strategy${qs({ id, view: list ? "list" : null })}`,
  newStrategy: () => "#/strategy?new=1",
  data: (code?: string | null, date?: string | null) => `#/data${qs({ code, date })}`,
  stock: (code: string, opts: { date?: string | null; run?: string | null; from?: string | null } = {}) =>
    `#/stock/${encodeURIComponent(code)}${qs({ date: opts.date, run: opts.run, from: opts.from })}`,
  picks: (opts: { id?: string | null; n?: number | null; draft?: boolean; date?: string | null } = {}) =>
    `#/picks${qs({ id: opts.id, n: opts.n, draft: opts.draft ? 1 : null, date: opts.date })}`,
  /** use: a strategy id, or "draft" for the strategy page's draft, preselected in "新建回测". */
  backtest: (opts: { run?: string | null; cmp?: string[]; day?: string | null; use?: string | null } = {}) =>
    `#/backtest${qs({ use: opts.use, run: opts.run, cmp: (opts.cmp ?? []).join(","), day: opts.day })}`,
};

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parse(location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
