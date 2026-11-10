import { useEffect, useState } from "react";

/** Hash routes, so the built files work from any path: #/data, #/data?code=C&date=D, #/stock/600000.SH[?date=D], #/strategy,
 * #/picks[?screen=NAME], #/backtest, #/backtest/RUN_ID[?day=D]. */
export type Route =
  | { page: "data"; code: string | null; date: string | null }
  | { page: "stock"; code: string; date: string | null }
  | { page: "strategy" }
  | { page: "picks"; screen: string | null }
  | { page: "backtest" }
  | { page: "run"; id: string; day: string | null };

function parse(hash: string): Route {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?");
  const q = new URLSearchParams(query);
  if (path === "/strategy") return { page: "strategy" };
  if (path === "/picks") return { page: "picks", screen: q.get("screen") };
  if (path === "/backtest") return { page: "backtest" };
  const run = path.match(/^\/backtest\/([^/]+)$/);
  if (run?.[1]) return { page: "run", id: decodeURIComponent(run[1]), day: q.get("day") };
  const stock = path.match(/^\/stock\/([^/]+)$/);
  if (stock?.[1]) return { page: "stock", code: decodeURIComponent(stock[1]).toUpperCase(), date: q.get("date") };
  return { page: "data", code: q.get("code"), date: q.get("date") };
}

export const href = {
  strategy: () => "#/strategy",
  picks: (screen?: string) => (screen ? `#/picks?screen=${encodeURIComponent(screen)}` : "#/picks"),
  backtest: () => "#/backtest",
  run: (id: string, day?: string) => `#/backtest/${encodeURIComponent(id)}${day ? `?day=${day}` : ""}`,
  data: (code?: string, date?: string) => {
    const q = new URLSearchParams();
    if (code) q.set("code", code);
    if (date) q.set("date", date);
    const s = q.toString();
    return s ? `#/data?${s}` : "#/data";
  },
  stock: (code: string, date?: string) => `#/stock/${encodeURIComponent(code)}${date ? `?date=${date}` : ""}`,
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
