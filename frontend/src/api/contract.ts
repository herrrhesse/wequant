// Fixture contract helpers. JSON imports widen enums ("traded", "num", exit 2) to string or number, so a fixture is
// checked at compile time against Fixture<T> (T with its enums widened: renamed or retyped fields still fail
// typecheck), and its enum values are checked when it loads.
import type { Bars, DelistingRow, Delistings, ErrorBody, EvalResult, Exit, Row } from "./types.gen";

export type Fixture<T> = T extends string
  ? string
  : T extends number
    ? number
    : T extends boolean
      ? boolean // JSON imports widen literal booleans too (Reject.passed is `false | null`)
    : T extends null
      ? T
    : T extends readonly (infer U)[]
      ? Fixture<U>[]
      : T extends object
        ? string extends keyof T
          ? // A JSON array of maps with different keys infers the missing keys as optional (undefined).
            { [k: string]: Fixture<T[keyof T]> | undefined }
          : { [K in keyof T]: Fixture<T[K]> }
        : T;

const EXITS: readonly Exit[] = [0, 1, 2, 3, 4, 5, 70, 130];

export function errorBody(value: Fixture<ErrorBody>, name: string): ErrorBody {
  if (!EXITS.includes(value.exit as Exit)) throw new Error(`${name}: unknown exit ${value.exit}`);
  return { ...value, exit: value.exit as Exit };
}

const STATUSES: readonly Row["status"][] = ["not_listed", "traded", "suspended", "missing"];
const KINDS: readonly EvalResult["kind"][] = ["num", "bool"];

function rowsOf(rows: Fixture<Row>[], name: string): Row[] {
  for (const r of rows) {
    if (!STATUSES.includes(r.status as Row["status"])) throw new Error(`${name}: unknown status ${r.status}`);
  }
  return rows as Row[];
}

export function bars(value: Fixture<Bars>, name: string): Bars {
  return { ...value, fields: value.fields as Bars["fields"], rows: rowsOf(value.rows, name) };
}

export function evalResult(value: Fixture<EvalResult>, name: string): EvalResult {
  if (!KINDS.includes(value.kind as EvalResult["kind"])) throw new Error(`${name}: unknown kind ${value.kind}`);
  return { ...value, kind: value.kind as EvalResult["kind"], rows: rowsOf(value.rows, name) };
}

const KINDS_D: readonly DelistingRow["kind"][] = ["forced", "merger", "suspension", "unknown"];
const BY: readonly DelistingRow["classified_by"][] = ["rule", "override"];
const TREATMENTS: readonly DelistingRow["treatment"][] = ["write_down", "exit", "hold"];

export function delistings(value: Fixture<Delistings>, name: string): Delistings {
  for (const r of value.rows) {
    const ok = KINDS_D.includes(r.kind as DelistingRow["kind"]) && BY.includes(r.classified_by as DelistingRow["classified_by"]) && TREATMENTS.includes(r.treatment as DelistingRow["treatment"]);
    if (!ok) throw new Error(`${name}: unknown kind, classified_by or treatment in ${r.code}`);
  }
  return value as Delistings;
}
