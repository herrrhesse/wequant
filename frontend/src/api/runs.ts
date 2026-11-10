// Screen and runs results. The browser cannot run the CLI, so until S5.1a these read the local fixtures that
// gen-fixtures.sh captured (or the synthetic samples without them); the page offers only what the fixtures hold.
import { EXIT, ServiceError } from "./errors";
import { loadSource, type Named, type RunSource, type Source } from "./source";
import type { Sourced } from "./client";
import type {
  AnomaliesPage,
  BacktestResult,
  ErrorBody,
  Explain,
  OrdersPage,
  RunComparison,
  RunDay,
  RunDetail,
  RunDiff,
  RunsPage,
  ScreenResult,
} from "./types.gen";

async function sourced<T>(source: Source, named: Named<T>): Promise<Sourced<T>> {
  return { data: await named.load(), command: source.commands[named.name] ?? "", synthetic: source.kind === "synthetic" };
}

/** The fixtures' screens, or the synthetic one. */
async function screenSource(): Promise<Source> {
  const source = await loadSource();
  return source.screens?.length ? source : (await import("./synthetic")).source;
}

async function runsSource(): Promise<Source & { runs: NonNullable<Source["runs"]> }> {
  const source = await loadSource();
  const chosen = source.runs ? source : (await import("./synthetic")).source;
  if (!chosen.runs) throw new ServiceError(EXIT.NOT_FOUND, "no runs");
  return chosen as Source & { runs: NonNullable<Source["runs"]> };
}

export interface ScreenChoice {
  name: string;
  strategy: string;
  date: string;
}

/** The screens the fixtures hold (strategy and day): the only choices until S5.1a. */
export async function screenChoices(): Promise<ScreenChoice[]> {
  const source = await screenSource();
  return Promise.all(
    (source.screens ?? []).map(async (s) => ({ name: s.name, strategy: s.strategy, date: (await s.result.load()).date })),
  );
}

/** `wequant screen --strategy S --date D`, by fixture name; the first one when name is null. */
export async function screen(name: string | null): Promise<Sourced<ScreenResult> & { name: string }> {
  const source = await screenSource();
  const found = source.screens?.find((s) => name === null || s.name === name);
  if (!found) throw new ServiceError(EXIT.NOT_FOUND, `no screen fixture ${name}`);
  return { ...(await sourced(source, found.result)), name: found.name };
}

/** `wequant explain --strategy S --date D --code C` for a screen, or null when the fixtures lack it. */
export async function screenExplain(name: string, code: string): Promise<Sourced<Explain> | null> {
  const source = await screenSource();
  const found = source.screens?.find((s) => s.name === name)?.explains[code];
  return found ? sourced(source, found) : null;
}

/** `wequant runs list`. */
export async function runsList(): Promise<Sourced<RunsPage>> {
  const source = await runsSource();
  return sourced(source, source.runs.list);
}

/** `wequant runs diff A B`, either order; null when the fixtures lack the pair. */
export async function runsDiff(a: string, b: string): Promise<Sourced<RunDiff> | null> {
  const source = await runsSource();
  const found = source.runs.diffs.find((d) => (d.a === a && d.b === b) || (d.a === b && d.b === a));
  return found ? sourced(source, found) : null;
}

export interface RunBundle {
  show: Sourced<RunDetail>;
  anomalies: Sourced<AnomaliesPage> | null;
  orders: Sourced<OrdersPage>[];
  rejected: Sourced<OrdersPage> | null;
  rerun: Sourced<RunComparison> | null;
  /** Dates with a `runs day` fixture. */
  days: string[];
  report: Sourced<BacktestResult> | null;
  backtest: Sourced<BacktestResult> | null;
}

async function run(id: string): Promise<{ source: Source; run: RunSource }> {
  const source = await runsSource();
  const found = source.runs.runs[id];
  if (!found) throw new ServiceError(EXIT.NOT_FOUND, `no fixture for run ${id}`);
  return { source, run: found };
}

const maybe = <T,>(source: Source, named: Named<T> | undefined) => (named ? sourced(source, named) : Promise.resolve(null));

/** `runs show ID` with the run's other fixtures: anomalies, orders, rerun, report. */
export async function runBundle(id: string): Promise<RunBundle> {
  const { source, run: r } = await run(id);
  const [show, anomalies, orders, rejected, rerun, report, backtest] = await Promise.all([
    sourced(source, r.show),
    maybe(source, r.anomalies),
    Promise.all(r.orders.map((o) => sourced(source, o))),
    maybe(source, r.rejected),
    maybe(source, r.rerun),
    maybe(source, r.report),
    maybe(source, r.backtest),
  ]);
  return { show, anomalies, orders, rejected, rerun, days: Object.keys(r.days).sort(), report, backtest };
}

/** `wequant runs day ID DATE`, or null when the fixtures lack that day. */
export async function runDay(id: string, date: string): Promise<Sourced<RunDay> | null> {
  const { source, run: r } = await run(id);
  return maybe(source, r.days[date]);
}

/** `wequant explain --run ID --date D --code C`, or null when the fixtures lack it. */
export async function runExplain(id: string, date: string, code: string): Promise<Sourced<Explain> | null> {
  const { source, run: r } = await run(id);
  return maybe(source, r.explains[`${date}/${code}`]);
}

/** The codes with an `explain --run` fixture on a day. */
export async function runExplainCodes(id: string, date: string): Promise<string[]> {
  const { run: r } = await run(id);
  return Object.keys(r.explains)
    .filter((k) => k.startsWith(`${date}/`))
    .map((k) => k.slice(date.length + 1));
}

/** An invalid backtest request's real ErrorBody, when the fixtures have one. */
export async function backtestError(): Promise<Sourced<ErrorBody> | null> {
  const source = await runsSource();
  return maybe(source, source.runs.error);
}
