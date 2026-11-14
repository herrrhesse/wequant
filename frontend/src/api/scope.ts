// S2.11 data scope (which stocks the data holds) as the backend described it before its schema snapshot has it.
// TEMPORARY: replaced by the generated types (types.gen.ts) once main carries S2.11; no field here is computed.
import type { BuildEstimate, BuildResult } from "./types.gen";

/** Which stocks: all of them, the members over time of one or both indexes, a list, or an index and a list. */
export interface DataScope {
  all?: boolean;
  indexes?: string[];
  stocks?: string[];
}

export interface ScopedEstimate extends BuildEstimate {
  scope: DataScope;
  /** Stocks to download. */
  stocks: number;
}

export interface ScopedBuildResult extends BuildResult {
  stocks?: number;
  pool_id?: string | null;
}

/** The indexes a build can download (their members over the years). */
export const SCOPE_INDEXES = ["000300.SH", "000905.SH"] as const;

/** One layer of the data: these stocks have data from `since` on. Widest first; the narrowest since is first_date. */
export interface ScopeLayer extends DataScope {
  since: string;
  /** How people say it: "沪深全部", "沪深300 + 中证500", "沪深300 + 自定义名单 12 只". */
  label: string;
}

/** data_status.scope: the stocks the data holds and from when. */
export interface DataScopeStatus {
  layers: ScopeLayer[];
  /** labels.scopes codes whose every stock the data holds, from since on; a code not listed is not covered. */
  covers: { code: string; since: string }[];
  /** Stocks with daily bars in this version. */
  stock_count: number;
  first_date: string | null;
  /** Where the history backfill stops: the index's member list goes back no further. */
  backfill_floor: { index: string; name: string; date: string } | null;
}

export interface ExtendEstimate {
  /** The widest layer after the extension. */
  scope: DataScope;
  /** Everything asked for is in the data already: nothing to download. */
  covered: boolean;
  stocks: number;
  requests: number;
  seconds: number;
  /** The new stocks are filled back to this day (the data's first day). */
  since: string;
}

/** data_status with S2.11's scope (absent before S2.11). */
export const scopeStatus = (status: unknown): DataScopeStatus | null => ((status as { scope?: DataScopeStatus } | undefined)?.scope ?? null);

/** S2.11 operations not yet in the generated routes: every service operation is POST /api/<name>. */
export async function callS211<T>(op: "data_extend_estimate", request: unknown, signal?: AbortSignal): Promise<T> {
  const r = await fetch(`/api/${op}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(request), signal });
  const body = await r.json();
  if (!r.ok) throw Object.assign(new Error(body?.message ?? op), { status: r.status, body });
  return body as T;
}
