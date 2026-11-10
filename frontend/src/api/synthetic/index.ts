// Synthetic data (D20): codes, dates and values are made up. It covers the branches the pages draw:
// - data page: an old store version, null and non-object details, an unknown issue kind, a malformed code;
// - stock page (10 trading days; 999901.SH): a 2-day suspension, a limit-up day, a locked limit-down day, a day with unknown
//   limits, a null amount and a data issue; 999902.SZ: ChiNext-like 20% band and a known ST run.
// - screen page: a capped weight, chain=false conditions in a group, a between reject, a missing-data reject;
// - runs: a null metric with its reason, a delisted holding, an error-level anomaly, a rejected order, a full exit.
// Each file is checked against the generated types, so a contract change fails `npm run typecheck`.
import { bars, delistings as checkDelistings, evalResult, type Fixture } from "../contract";
import type { Named, Source, StockSource } from "../source";
import type {
  Bars,
  DataStatus,
  Delistings,
  EvalResult,
  Issues,
  OrdersPage,
  RunDay,
  RunDetail,
  RunDiff,
  RunsPage,
  ScreenResult,
} from "../types.gen";
import runDay from "./run-day.json";
import runDetail from "./run-detail.json";
import runOrders from "./run-orders.json";
import runsDiff from "./runs-diff.json";
import runsList from "./runs-list.json";
import screen from "./screen.json";
import delistings from "./data-delistings.json";
import bars1 from "./999901.SH-bars.json";
import issues1 from "./999901.SH-issues.json";
import adj1_10 from "./999901.SH-ma-adj-10.json";
import adj1_20 from "./999901.SH-ma-adj-20.json";
import adj1_5 from "./999901.SH-ma-adj-5.json";
import adj1_60 from "./999901.SH-ma-adj-60.json";
import raw1_10 from "./999901.SH-ma-raw-10.json";
import raw1_20 from "./999901.SH-ma-raw-20.json";
import raw1_5 from "./999901.SH-ma-raw-5.json";
import raw1_60 from "./999901.SH-ma-raw-60.json";
import bars2 from "./999902.SZ-bars.json";
import issues2 from "./999902.SZ-issues.json";
import adj2_10 from "./999902.SZ-ma-adj-10.json";
import adj2_20 from "./999902.SZ-ma-adj-20.json";
import adj2_5 from "./999902.SZ-ma-adj-5.json";
import adj2_60 from "./999902.SZ-ma-adj-60.json";
import raw2_10 from "./999902.SZ-ma-raw-10.json";
import raw2_20 from "./999902.SZ-ma-raw-20.json";
import raw2_5 from "./999902.SZ-ma-raw-5.json";
import raw2_60 from "./999902.SZ-ma-raw-60.json";
import issuesAdjBlip from "./data-issues-adj-blip.json";
import issues from "./data-issues.json";
import statusOld from "./data-status-old.json";
import status from "./data-status.json";

const now = <T>(value: T) => () => Promise.resolve(value);
/** After the satisfies check on the widened JSON type: the enums back (too deep for a plain `as`). */
const typed = <T>(value: unknown) => value as T;

const ma = (code: string, mode: "raw" | "adj", results: Fixture<EvalResult>[]) =>
  results.map((r, i) => ({ name: `stock/${code}/ma-${mode}-${[5, 10, 20, 60][i]}`, load: now(evalResult(r, code)) }));

function stock(code: string, b: Fixture<Bars>, is: Issues, raw: Fixture<EvalResult>[], adj: Fixture<EvalResult>[]): StockSource {
  return {
    bars: { name: `stock/${code}/bars`, load: now(bars(b, code)) },
    issues: { name: `stock/${code}/issues`, load: now(is) },
    ma: { raw: ma(code, "raw", raw), adj: ma(code, "adj", adj) },
  };
}

/** The commands the synthetic stocks stand for, written the way gen-fixtures.sh runs them. */
function stockCommands(code: string): Record<string, string> {
  const fields = "open high low close volume amount adj_open adj_high adj_low adj_close limit_up_price limit_down_price limit_up limit_down limit_locked limit_known st st_known factor";
  const out: Record<string, string> = {
    [`stock/${code}/bars`]: `wequant data bars --codes ${code} --start 2031-01-02 --fields ${fields} --json`,
    [`stock/${code}/issues`]: `wequant data issues --codes ${code} --json`,
  };
  for (const n of [5, 10, 20, 60]) {
    out[`stock/${code}/ma-raw-${n}`] = `wequant expr eval --form '["MA","close",${n}]' --codes ${code} --start 2031-01-02 --json`;
    out[`stock/${code}/ma-adj-${n}`] = `wequant expr eval --form '["MA","adj_close",${n}]' --codes ${code} --start 2031-01-02 --json`;
  }
  return out;
}

/** Delisted codes, one per kind and both classifications, approximate and official dates (F-22). */
export const syntheticDelistings: Named<Delistings> = {
  name: "data-delistings",
  load: now(checkDelistings(delistings satisfies Fixture<Delistings>, "data-delistings.json")),
};

export const source: Source = {
  kind: "synthetic",
  commands: {
    "data-status": "wequant data status --json",
    "data-status-old": "wequant data status --data-version 6 --json",
    "data-issues": "wequant data issues --json",
    "data-issues-adj-blip": "wequant data issues --table adj_factor --kind adj_blip --json",
    "data-delistings": "wequant data delistings --json",
    "screen/s1": "wequant screen --strategy s1.json --date 2031-01-10 --json",
    "runs/list": "wequant runs list --json",
    "runs/aaaa000000000001/show": "wequant runs show aaaa000000000001 --json",
    "runs/aaaa000000000001/orders": "wequant runs orders aaaa000000000001 --limit 500 --json",
    "runs/aaaa000000000001/day": "wequant runs day aaaa000000000001 2031-01-10 --json",
    "runs/diff": "wequant runs diff aaaa000000000001 aaaa000000000002 --json",
    ...stockCommands("999901.SH"),
    ...stockCommands("999902.SZ"),
  },
  statuses: [
    { name: "data-status", load: now(status satisfies DataStatus) },
    { name: "data-status-old", load: now(statusOld satisfies DataStatus) },
  ],
  issues: [
    { name: "data-issues", table: null, kind: null, load: now(issues satisfies Issues) },
    { name: "data-issues-adj-blip", table: "adj_factor", kind: "adj_blip", load: now(issuesAdjBlip satisfies Issues) },
  ],
  screens: [{ name: "s1", strategy: "s1", result: { name: "screen/s1", load: now(typed<ScreenResult>(screen satisfies Fixture<ScreenResult>)) }, explains: {} }],
  runs: {
    list: { name: "runs/list", load: now(typed<RunsPage>(runsList satisfies Fixture<RunsPage>)) },
    diffs: [{ name: "runs/diff", a: "aaaa000000000001", b: "aaaa000000000002", load: now(typed<RunDiff>(runsDiff satisfies Fixture<RunDiff>)) }],
    runs: {
      aaaa000000000001: {
        show: { name: "runs/aaaa000000000001/show", load: now(typed<RunDetail>(runDetail satisfies Fixture<RunDetail>)) },
        orders: [{ name: "runs/aaaa000000000001/orders", load: now(typed<OrdersPage>(runOrders satisfies Fixture<OrdersPage>)) }],
        days: { "2031-01-10": { name: "runs/aaaa000000000001/day", load: now(typed<RunDay>(runDay satisfies Fixture<RunDay>)) } },
        explains: {},
      },
    },
  },
  stocks: {
    "999901.SH": stock(
      "999901.SH",
      bars1 satisfies Fixture<Bars>,
      issues1 satisfies Issues,
      [raw1_5, raw1_10, raw1_20, raw1_60] satisfies Fixture<EvalResult>[],
      [adj1_5, adj1_10, adj1_20, adj1_60] satisfies Fixture<EvalResult>[],
    ),
    "999902.SZ": stock(
      "999902.SZ",
      bars2 satisfies Fixture<Bars>,
      issues2 satisfies Issues,
      [raw2_5, raw2_10, raw2_20, raw2_60] satisfies Fixture<EvalResult>[],
      [adj2_5, adj2_10, adj2_20, adj2_60] satisfies Fixture<EvalResult>[],
    ),
  },
};

export { syntheticDelistings as delistings };
