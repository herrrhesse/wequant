// Query hooks over `call`: the cache key is [operation, request], so one request is fetched once and an outdated
// request is cancelled (its signal) when the key changes. Every result shown on a page comes through here.
import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";
import { MODEL_DEFAULTS } from "./contract.gen";
import { call, curl } from "./http";
import type { Operation, Operations } from "./operations.gen";

export type Request<O extends Operation> = Operations[O]["request"];
export type Result<O extends Operation> = Operations[O]["result"];

/** A query of one operation; `request` null: not yet (disabled). */
export function useCall<O extends Operation>(
  op: O,
  request: Request<O> | null,
  options: Omit<UseQueryOptions<Result<O>, Error, Result<O>, readonly unknown[]>, "queryKey" | "queryFn"> = {},
) {
  return useQuery({
    ...options,
    queryKey: [op, request] as const,
    queryFn: ({ signal }) => call(op, request as Request<O>, signal),
    enabled: request !== null && (options.enabled ?? true),
  });
}

/** A mutation (save, archive, backtest, rerun): not cached; `invalidate` lists operations whose results it changes. */
export function useCallMutation<O extends Operation>(op: O, invalidate: Operation[] = []) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (request: Request<O>) => call(op, request),
    onSuccess: () => Promise.all(invalidate.map((o) => client.invalidateQueries({ queryKey: [o] }))),
  });
}

/** The equivalent request as a copyable shell command. */
export function commandOf<O extends Operation>(op: O, request: Request<O> | null): string {
  return request === null ? "" : curl(op, request);
}

/** A request model's defaults from the snapshot (BacktestRequest.cash, .execution, ...), never written in the UI. */
export function modelDefaults(model: string): Record<string, unknown> {
  return { ...(MODEL_DEFAULTS[model] ?? {}) };
}

/** Moving-average windows the stock page shows; each is one `expr_eval`. */
export const MA_WINDOWS = [5, 10, 20, 60] as const;
export type PriceMode = "raw" | "adj";

/** The bar fields the stock page reads. */
export const BAR_FIELDS = [
  "open", "high", "low", "close", "volume", "amount", "adj_open", "adj_high", "adj_low", "adj_close",
  "limit_up_price", "limit_down_price", "limit_up", "limit_down", "limit_locked", "limit_known", "st", "st_known", "factor",
] as const;
