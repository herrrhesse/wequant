// The data status strip under the top bar, on every page: the local latest trading day, the data version's display
// name and how many data problems wait for a decision. "更新到今天" and the old-version notice arrive with the
// backend's round 2 (background jobs, version switching) and are not shown before.
import type { ReactNode } from "react";
import { useCall } from "../api/client";
import { href } from "../router";
import { tx, weekday } from "../text";

export function StatusBar({ right }: { right?: ReactNode }) {
  const status = useCall("data_status", {});
  const pending = useCall("data_resolve_list", { status: "proposed", limit: 1 });
  const s = status.data;
  return (
    <section className="statusbar" aria-label={tx.status.label}>
      {s ? (
        <span className="status-day">
          <span className="dot ok" />
          {tx.status.latest} <b>{s.last_trade_date ?? tx.common.none}</b>
          {s.last_trade_date && `（${weekday(s.last_trade_date)}）`}
          <span className="tag">{tx.status.version(s.data_version_name)}</span>
        </span>
      ) : (
        <span className="status-day muted">{tx.app.loading}</span>
      )}
      {pending.data && (
        <a className={pending.data.total > 0 ? "link small" : "small sub"} href={href.data()}>
          {tx.status.pending(pending.data.total)}
        </a>
      )}
      {right && <div className="status-right">{right}</div>}
    </section>
  );
}
