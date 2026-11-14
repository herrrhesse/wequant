// A backend warning as its sentence; the data-scope warnings (the strategy's scope goes beyond the stocks the data
// holds, wholly or before a date) carry a link to widen the data on the data page.
import type { Warning } from "../api/types.gen";
import { lb } from "../labels";
import { href } from "../router";

export const SCOPE_WARNINGS = new Set(["scope_beyond_data", "scope_beyond_data_before"]);

export function WarningText({ w }: { w: Warning }) {
  return (
    <>
      {lb().warning(w)}
      {SCOPE_WARNINGS.has(w.code) && (
        <>
          {" "}
          <a className="b nowrap" href={href.dataExtend()}>
            去扩大数据范围 ›
          </a>
        </>
      )}
    </>
  );
}
