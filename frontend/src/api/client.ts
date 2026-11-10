// The only place the UI reads data. Today it serves the local fixtures, or the synthetic data when there are none;
// after S5.1a it calls the HTTP API with the same requests and result types, and no page changes.
import { operationAvailable } from "./contract-info";
import { EXIT, ServiceError } from "./errors";
import { loadSource, type Named, type Source } from "./source";
import type { Bars, DataStatus, Delistings, EvalResult, Issues } from "./types.gen";

/** A result, the wequant command that produced it, and whether it is synthetic example data. */
export interface Sourced<T> {
  data: T;
  command: string;
  synthetic: boolean;
}

export interface IssuesFilter {
  table: string | null;
  kind: string | null;
  /** One code: `wequant data issues --codes C`. */
  code?: string | null;
}

async function sourced<T>(source: Source, named: Named<T>): Promise<Sourced<T>> {
  return { data: await named.load(), command: source.commands[named.name] ?? "", synthetic: source.kind === "synthetic" };
}

/** `wequant data status [--data-version N]`. */
export async function dataStatus(version: number | null): Promise<Sourced<DataStatus>> {
  const source = await loadSource();
  for (const [i, named] of source.statuses.entries()) {
    const status = await named.load();
    if (version === null ? i === 0 : status.data_version === version) return sourced(source, named);
  }
  throw new ServiceError(EXIT.NOT_FOUND, `no fixture for data version ${version}`);
}

/** Store versions the fixtures cover; with the HTTP API every kept version works. */
export async function statusVersions(): Promise<number[]> {
  const source = await loadSource();
  return Promise.all(source.statuses.map(async (s) => (await s.load()).data_version));
}

/** `wequant data issues [--table T] [--kind K] [--codes C]`; the fixtures cover the combinations the pages offer. */
export async function dataIssues(filter: IssuesFilter): Promise<Sourced<Issues>> {
  if (filter.code) return (await stockData(filter.code)).issues;
  const source = await loadSource();
  const found = source.issues.find((f) => f.table === filter.table && f.kind === filter.kind);
  if (!found) throw new ServiceError(EXIT.NOT_FOUND, `no fixture for ${filter.table ?? "*"} / ${filter.kind ?? "*"}`);
  return sourced(source, found);
}

/** Filters the page can request from the fixtures; with the HTTP API every table and kind works. */
export async function issueFilters(): Promise<IssuesFilter[]> {
  return (await loadSource()).issues.map(({ table, kind }) => ({ table, kind }));
}

/** Whether the page is showing synthetic example data. */
export async function isSynthetic(): Promise<boolean> {
  return (await loadSource()).kind === "synthetic";
}

/** Moving-average windows the stock page shows; each comes from `wequant expr eval`. */
export const MA_WINDOWS = [5, 10, 20, 60] as const;
export type PriceMode = "raw" | "adj";

export interface StockData {
  code: string;
  bars: Sourced<Bars>;
  issues: Sourced<Issues>;
  /** ma[mode][i] is ["MA", close or adj_close, MA_WINDOWS[i]]. */
  ma: Record<PriceMode, Sourced<EvalResult>[]>;
}

/** Codes the fixtures cover; with the HTTP API every code works. */
export async function stockCodes(): Promise<string[]> {
  return Object.keys((await loadSource()).stocks);
}

/** `wequant data bars`, `data issues --codes` and the moving averages of one code. */
export async function stockData(code: string): Promise<StockData> {
  const source = await loadSource();
  const stock = source.stocks[code];
  if (!stock) throw new ServiceError(EXIT.NOT_FOUND, `no fixture for ${code}`);
  const ma = (mode: PriceMode) => Promise.all(stock.ma[mode].map((m) => sourced(source, m)));
  const [bars, issues, adj, raw] = await Promise.all([sourced(source, stock.bars), sourced(source, stock.issues), ma("adj"), ma("raw")]);
  return { code, bars, issues, ma: { adj, raw } };
}

/** `wequant data delistings` (F-22). Until the command exists (or the fixtures lack it) the synthetic example is
 * returned with pending = true, so the page can say so. */
export async function dataDelistings(): Promise<Sourced<Delistings> & { pending: boolean }> {
  const source = await loadSource();
  if (operationAvailable("data_delistings") && source.delistings) return { ...(await sourced(source, source.delistings)), pending: false };
  const synthetic = await import("./synthetic");
  return { ...(await sourced(synthetic.source, synthetic.delistings)), synthetic: true, pending: true };
}
