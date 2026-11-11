// The one way the UI reaches the backend: `wequant serve`, POST /api/<operation> (routes from the schema snapshot).
// Request and result types come from operations.gen.ts; an error is the backend's ErrorBody, or "unreachable" when
// nothing that speaks the API answered.
import { ROUTES, type Operation, type Operations } from "./operations.gen.ts";
import type { ErrorBody } from "./types.gen";

/** The backend answered with an ErrorBody (422 USAGE, 404, 409 LOCKED, 503, 500 ...). */
export class ApiError extends Error {
  readonly status: number;
  readonly body: ErrorBody;
  readonly operation: Operation;
  constructor(status: number, body: ErrorBody, operation: Operation) {
    super(body.message);
    this.status = status;
    this.body = body;
    this.operation = operation;
  }
}

/** No `wequant serve` answered: not started, or the dev proxy has no target. */
export class Unreachable extends Error {
  readonly operation: Operation;
  constructor(operation: Operation) {
    super(`no backend for ${operation}`);
    this.operation = operation;
  }
}

export type Reply = { kind: "ok"; value: unknown } | { kind: "error"; body: ErrorBody } | { kind: "unreachable" };

function isErrorBody(v: unknown): v is ErrorBody {
  return typeof v === "object" && v !== null && typeof (v as ErrorBody).exit === "number" && typeof (v as ErrorBody).name === "string";
}

/** What a response means: a 2xx JSON result, the backend's ErrorBody, or no backend at all (anything else, e.g. the
 * dev server's 404 page or a proxy error without an ErrorBody). */
export function classify(status: number, text: string): Reply {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return { kind: "unreachable" };
  }
  if (status >= 200 && status < 300) return { kind: "ok", value };
  return isErrorBody(value) ? { kind: "error", body: value } : { kind: "unreachable" };
}

/** Listeners told when the backend becomes unreachable or answers again (the App shows a full-page notice). */
const listeners = new Set<(down: boolean) => void>();
let down = false;
function setDown(value: boolean) {
  if (value === down) return;
  down = value;
  for (const l of listeners) l(down);
}
export const backendState = {
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  get: () => down,
  reset: () => setDown(false),
};

/** Call one operation. Rejects with ApiError or Unreachable. */
export async function call<O extends Operation>(op: O, request: Operations[O]["request"], signal?: AbortSignal): Promise<Operations[O]["result"]> {
  const { method, path } = ROUTES[op];
  let response: Response;
  try {
    response = await fetch(path, { method, headers: { "content-type": "application/json" }, body: JSON.stringify(request), signal });
  } catch (e) {
    if (signal?.aborted) throw e;
    setDown(true);
    throw new Unreachable(op);
  }
  const reply = classify(response.status, await response.text());
  if (reply.kind === "unreachable") {
    setDown(true);
    throw new Unreachable(op);
  }
  setDown(false);
  if (reply.kind === "error") throw new ApiError(response.status, reply.body, op);
  return reply.value as Operations[O]["result"];
}

/** The same request as a shell command, for the "equivalent command" shown next to each result. */
export function curl<O extends Operation>(op: O, request: Operations[O]["request"], origin = "http://127.0.0.1:8731"): string {
  const { method, path } = ROUTES[op];
  const body = JSON.stringify(request).replaceAll("'", "'\\''");
  return `curl -s -X ${method} ${origin}${path} -H 'content-type: application/json' -d '${body}'`;
}
