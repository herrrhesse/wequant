// Where the UI's data comes from. D20: real market data never enters the repository. Locally,
// `npm run gen:fixtures` writes real CLI output plus a typed index to fixtures/ (not tracked); a clone without it
// gets the synthetic data in src/api/synthetic/, and the page says so.
import type {
  AnomaliesPage,
  BacktestResult,
  Bars,
  DataStatus,
  Delistings,
  ErrorBody,
  EvalResult,
  Explain,
  Issues,
  OrdersPage,
  RunComparison,
  RunDay,
  RunDetail,
  RunDiff,
  RunsPage,
  ScreenResult,
  StrategyCheck,
  StrategySpec,
  StrategySummary,
} from "./types.gen";

/** A result loader and the name of the wequant command that produced it (a key of commands). */
export interface Named<T> {
  name: string;
  load: () => Promise<T>;
}

export interface Source {
  /** real: local fixtures from the CLI; synthetic: made-up data shipped with the code. */
  kind: "real" | "synthetic";
  /** Fixture name → the wequant command behind it. */
  commands: Record<string, string>;
  /** data status per store version; the first is the current version. */
  statuses: Named<DataStatus>[];
  /** data issues by the filter they were fetched with; null means "any". */
  issues: (Named<Issues> & { table: string | null; kind: string | null })[];
  /** Per code: bars, issues and the moving averages, ma[mode][i] for window MA_WINDOWS[i]. */
  stocks: Record<string, StockSource>;
  /** `wequant data delistings` (F-22); absent until the command exists, then the synthetic example is shown. */
  delistings?: Named<Delistings>;
  /** Local only: the backend's output for fixed example strategies (gen-fixtures.sh), valid and invalid. */
  strategyExamples?: StrategyExample[];
  /** `wequant screen` results the fixtures hold (a strategy on a day), with `explain` for some of their codes. */
  screens?: ScreenSource[];
  /** The archived runs the fixtures hold and what the runs commands returned for them. */
  runs?: RunsSource;
}

export interface ScreenSource {
  /** e.g. s1-last: strategy s1 on the last trading day. */
  name: string;
  /** The example strategy's short name (gen-fixtures.sh). */
  strategy: string;
  result: Named<ScreenResult>;
  /** `wequant explain --code C` for the same strategy and day, by code. */
  explains: Record<string, Named<Explain>>;
}

export interface RunsSource {
  list: Named<RunsPage>;
  runs: Record<string, RunSource>;
  /** `wequant runs diff A B`. */
  diffs: (Named<RunDiff> & { a: string; b: string })[];
  /** An invalid backtest request's ErrorBody. */
  error?: Named<ErrorBody>;
}

/** One run: `runs show` and the fixtures of its other runs commands (any may be missing). */
export interface RunSource {
  show: Named<RunDetail>;
  anomalies?: Named<AnomaliesPage>;
  /** `runs orders` pages in order. */
  orders: Named<OrdersPage>[];
  /** `runs orders --status rejected`. */
  rejected?: Named<OrdersPage>;
  rerun?: Named<RunComparison>;
  /** `runs day ID DATE`, by date. */
  days: Record<string, Named<RunDay>>;
  /** `explain --run ID --date D --code C`, by "D/C". */
  explains: Record<string, Named<Explain>>;
  /** The `backtest` output that archived it, and the one with --report (same run id). */
  backtest?: Named<BacktestResult>;
  report?: Named<BacktestResult>;
}

/** A fixed strategy and what strategy validate / summary returned for it: a StrategyCheck, or an ErrorBody. */
export interface StrategyExample {
  name: string;
  strategy: Named<StrategySpec>;
  validate?: Named<StrategyCheck>;
  summary?: Named<StrategySummary>;
  error?: Named<ErrorBody>;
}

export interface StockSource {
  bars: Named<Bars>;
  issues: Named<Issues>;
  ma: Record<"raw" | "adj", Named<EvalResult>[]>;
}

const local = import.meta.glob<{ source: Source }>("../../fixtures/index.ts");

let cached: Promise<Source> | null = null;

/** The local fixtures when they exist, else the synthetic data. */
export function loadSource(): Promise<Source> {
  cached ??= (async () => {
    const load = local["../../fixtures/index.ts"];
    if (load) return (await load()).source;
    return (await import("./synthetic")).source;
  })();
  return cached;
}
