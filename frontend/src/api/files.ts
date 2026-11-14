// Files between the browser and wequant serve: an export op writes into <home>/exports/ and the browser downloads it
// from GET /api/exports/<name>; an upload goes to PUT /api/imports/<name> and the import op reads the returned path.
import { ApiError, call, Unreachable } from "./http.ts";
import type { Operation, Operations } from "./operations.gen";
import type { ErrorBody } from "./types.gen";

type ExportOp = "data_export" | "runs_export" | "strategy_export";
type ImportOp = "data_import" | "runs_import" | "strategy_import";

/** Run an export and hand the file to the browser as a download. */
export async function exportFile<O extends ExportOp>(op: O, request: Operations[O]["request"]): Promise<void> {
  const out = (await call(op, request)) as { path: string };
  const name = out.path.split(/[\\/]/).pop() ?? "";
  const a = document.createElement("a");
  a.href = `api/exports/${encodeURIComponent(name)}`;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** Upload a chosen file and import it with op. */
export async function importFile<O extends ImportOp>(op: O, file: File): Promise<Operations[O]["result"]> {
  let r: Response;
  try {
    r = await fetch(`api/imports/${encodeURIComponent(file.name)}`, { method: "PUT", body: file });
  } catch {
    throw new Unreachable(op as Operation);
  }
  const body = (await r.json().catch(() => null)) as { path?: string } | ErrorBody | null;
  if (!r.ok || !body || !("path" in body) || !body.path) throw new ApiError(r.status, body as ErrorBody, op as Operation);
  return call(op, { path: body.path } as Operations[O]["request"]);
}

/** Bytes as KB / MB / GB: nothing is 0 KB, a few bytes are 1 KB (not shown as empty), unknown is "—". */
export function size(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  if (bytes <= 0) return "0 KB";
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** A text the browser saves as a file (an example file, an export made in the page). */
export function downloadText(fileName: string, text: string, type = "text/csv;charset=utf-8") {
  const url = URL.createObjectURL(new Blob(["﻿", text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
