import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { dataDelistings } from "../api/client";
import type { DelistingRow } from "../api/types.gen";
import { Card } from "../components/Card";
import { Seg } from "./strategy/shared";
import { show } from "../format";
import { t } from "../i18n";
import { href } from "../router";

type Kind = DelistingRow["kind"] | "all";
const KINDS: Kind[] = ["all", "forced", "merger", "suspension", "unknown"];

/** F-22: every delisted code of the store version, how it is classified and treated, and on what basis. */
export function DelistingsSection() {
  const d = t.data.delistings;
  const query = useQuery({ queryKey: ["data", "delistings"], queryFn: dataDelistings });
  const [kind, setKind] = useState<Kind>("all");
  if (!query.data) return null;
  const { data, command, pending } = query.data;
  const rows = data.rows.filter((r) => kind === "all" || r.kind === kind);
  return (
    <Card title={d.title} version={data.data_version} command={command} extra={pending ? <span className="badge warn">{d.pending}</span> : undefined}>
      <div className="grid">
        <Stat label={d.total} value={data.counts.total} />
        <Stat label={d.approximate} value={data.counts.approximate} />
        {Object.entries(data.counts.kind).map(([k, n]) => (
          <Stat key={k} label={d.kinds[k] ?? k} value={n} />
        ))}
      </div>
      <p className="note">
        {Object.entries(data.counts.treatment)
          .map(([k, n]) => `${d.treatments[k] ?? k} ${n}`)
          .join(" · ")}
        {" · "}
        {Object.entries(data.counts.classified_by)
          .map(([k, n]) => `${d.by[k] ?? k} ${n}`)
          .join(" · ")}
      </p>
      <p className="lead">{d.rules}</p>
      <p className="approx-note">{d.approxNote}</p>
      {data.overrides && <span className="note">{d.overrides}</span>}
      <Seg label={d.kind} value={kind} options={KINDS.map((k): [Kind, string] => [k, k === "all" ? d.all : d.kinds[k]!])} onChange={setKind} />
      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>{d.code}</th>
              <th>{d.name}</th>
              <th>{d.delistDate}</th>
              <th>{d.day}</th>
              <th>{d.kind}</th>
              <th>{d.basis}</th>
              <th>{d.treatment}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.code} className={r.approximate ? "approx-row" : undefined}>
                <td>
                  <a className="link" href={href.stock(r.code)}>
                    {r.code}
                  </a>
                </td>
                <td>{show(r.name)}</td>
                <td className="num">{show(r.delist_date)}</td>
                <td className="num">
                  {r.day ?? d.notInCalendar}
                  {r.approximate && <span className="badge approx">{d.approximate}</span>}
                </td>
                <td>{d.kinds[r.kind]}</td>
                <td>
                  <div>{d.by[r.classified_by]}</div>
                  {r.evidence && <div className="note">{r.evidence}</div>}
                  {r.source && <div className="note">{r.source}</div>}
                  {r.last_bar && <div className="note">{d.lastBar(r.last_bar, show(r.last_close))}</div>}
                </td>
                <td>
                  <div>{d.treatments[r.treatment]}</div>
                  {/* Display-only: a ratio shown as a percent. */}
                  {r.recovery !== null && <div className="note">{d.recovery(String(Number((r.recovery * 100).toPrecision(12))))}</div>}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="note">
                  {d.empty}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/** One backend count (data delistings counts; the page does not count rows). */
function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat">
      <span className="stat-l">{label}</span>
      <span className="stat-v num">{show(value)}</span>
    </div>
  );
}
