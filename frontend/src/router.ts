import { useEffect, useState } from "react";

/** Hash routes keep every page's state in the URL, so the built files work from any path and back/forward restore it:
 * #/data[?code&date], #/stock/CODE[?date&run&from], #/strategy[?id&n], #/picks[?id&n&draft&date],
 * #/backtest[?cmp=A,B], #/backtest/RUN_ID[?day]. */
export type Route =
  | { page: "data"; code: string | null; date: string | null }
  | { page: "stock"; code: string; date: string | null; run: string | null; from: string | null }
  | { page: "strategy"; id: string | null; n: number | null }
  | { page: "picks"; id: string | null; n: number | null; draft: boolean; date: string | null }
  | { page: "backtest"; cmp: string[] }
  | { page: "run"; id: string; day: string | null };

const int = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : null);

export function parse(hash: string): Route {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?");
  const q = new URLSearchParams(query);
  if (path === "/strategy") return { page: "strategy", id: q.get("id"), n: int(q.get("n")) };
  if (path === "/picks") return { page: "picks", id: q.get("id"), n: int(q.get("n")), draft: q.get("draft") === "1", date: q.get("date") };
  if (path === "/backtest") return { page: "backtest", cmp: (q.get("cmp") ?? "").split(",").filter(Boolean).slice(0, 2) };
  const run = path.match(/^\/backtest\/([^/]+)$/);
  if (run?.[1]) return { page: "run", id: decodeURIComponent(run[1]), day: q.get("day") };
  const stock = path.match(/^\/stock\/([^/]+)$/);
  if (stock?.[1]) return { page: "stock", code: decodeURIComponent(stock[1]).toUpperCase(), date: q.get("date"), run: q.get("run"), from: q.get("from") };
  return { page: "data", code: q.get("code"), date: q.get("date") };
}

const qs = (params: Record<string, string | number | null | undefined | false>) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== false && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
};

export const href = {
  strategy: (id?: string | null, n?: number | null) => `#/strategy${qs({ id, n })}`,
  data: (code?: string, date?: string) => `#/data${qs({ code, date })}`,
  stock: (code: string, opts: { date?: string | null; run?: string | null; from?: string | null } = {}) =>
    `#/stock/${encodeURIComponent(code)}${qs({ date: opts.date, run: opts.run, from: opts.from })}`,
  picks: (opts: { id?: string | null; n?: number | null; draft?: boolean; date?: string | null } = {}) =>
    `#/picks${qs({ id: opts.id, n: opts.n, draft: opts.draft ? 1 : null, date: opts.date })}`,
  backtest: (cmp: string[] = []) => `#/backtest${qs({ cmp: cmp.join(",") })}`,
  run: (id: string, day?: string | null) => `#/backtest/${encodeURIComponent(id)}${qs({ day })}`,
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
