import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { useState } from "react";
import { commandOf, useCall, type Request } from "../api/client";
import type { IssueCount, IssueRow } from "../api/types.gen";
import { Card } from "../components/Card";
import { Coded } from "../components/Coded";
import { Problem } from "../components/Problem";
import { prettyDetail, show } from "../format";
import { useDebounced } from "../hooks";
import { t } from "../i18n";
import { href } from "../router";

const col = createColumnHelper<IssueRow>();

const columns = [
  col.accessor("table", { header: t.data.status.table }),
  col.accessor("code", {
    header: t.data.issues.code,
    cell: (c) => (
      <a className="link" href={href.stock(c.getValue(), { date: c.row.original.date, from: "data" })} title={t.data.issues.openStock}>
        {c.getValue()}
      </a>
    ),
  }),
  col.accessor("date", { header: t.data.issues.date, cell: (c) => <span className="num">{c.getValue()}</span> }),
  col.accessor("kind", { header: t.data.issues.kind, cell: (c) => <Coded code={c.getValue()} names={t.data.kinds} /> }),
  col.accessor("action", { header: t.data.issues.action, cell: (c) => <Coded code={c.getValue()} names={t.data.actions} /> }),
  col.accessor("detail", { header: t.data.issues.detail, cell: (c) => <div className="detail mono">{prettyDetail(c.getValue())}</div> }),
  col.accessor("source", { header: t.data.issues.source, cell: (c) => <span className="note">{c.getValue()}</span> }),
];

/** Rows per request; "load more" asks for more (data issues pages by limit, F-06). */
const PAGE = 200;

interface Group {
  table: string | null;
  kind: string | null;
}
const same = (a: Group, b: Group) => a.table === b.table && a.kind === b.kind;

interface Props {
  counts: IssueCount[];
  total: number;
  version: number;
  /** Opened from a stock page: that code's issues, from that day. */
  focusCode: string | null;
  focusDate: string | null;
}

/** data_issues: counts by table, kind and action, and the matching rows; every filter is a backend request. */
export function IssuesSection({ counts, total, version, focusCode, focusDate }: Props) {
  const [group, setGroup] = useState<Group>({ table: null, kind: null });
  const [code, setCode] = useState(focusCode ?? "");
  const [from, setFrom] = useState(focusDate ?? "");
  const [to, setTo] = useState(focusDate ?? "");
  const [limit, setLimit] = useState(PAGE);
  const typed = useDebounced({ code: code.trim().toUpperCase(), from, to }, 300);
  const request: Request<"data_issues"> = {
    table: group.table,
    kind: group.kind,
    codes: typed.code ? [typed.code] : null,
    start: typed.from || null,
    end: typed.to || null,
    limit,
  };
  const issues = useCall("data_issues", request, { placeholderData: (prev) => prev });
  const pick = (g: Group) => {
    setGroup(g);
    setLimit(PAGE);
  };

  return (
    <Card title={t.data.issues.title} version={issues.data?.data_version ?? version} command={commandOf("data_issues", request)}>
      <p className="lead">{t.data.issues.lead}</p>
      {focusCode && (
        <span className="chip">
          {t.data.issues.focus(focusCode, focusDate)}
          <a className="link" href={href.stock(focusCode, { date: focusDate, from: "data" })}>
            {t.data.issues.openStock}
          </a>
          <button type="button" onClick={() => (location.hash = href.data())}>
            {t.data.issues.clear}
          </button>
        </span>
      )}
      <span className="stat-l">{t.data.issues.groups}</span>
      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>{t.data.status.table}</th>
              <th>{t.data.issues.kind}</th>
              <th>{t.data.issues.action}</th>
              <th className="r">{t.data.status.rows}</th>
            </tr>
          </thead>
          <tbody>
            <tr className="clickable" aria-selected={same(group, { table: null, kind: null })} onClick={() => pick({ table: null, kind: null })}>
              <td colSpan={3}>{t.data.issues.all}</td>
              <td className="r num">{show(total)}</td>
            </tr>
            {counts.map((c) => {
              const g = { table: c.table, kind: c.kind };
              return (
                <tr key={`${c.table}/${c.kind}/${c.action}`} className="clickable" aria-selected={same(group, g)} onClick={() => pick(g)}>
                  <td>{c.table}</td>
                  <td>
                    <Coded code={c.kind} names={t.data.kinds} />
                  </td>
                  <td>
                    <Coded code={c.action} names={t.data.actions} />
                  </td>
                  <td className="r num">{show(c.rows)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="filters">
        <label className="field">
          {t.data.issues.code}
          <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="600000.SH" />
        </label>
        <label className="field">
          {t.data.issues.from}
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label className="field">
          {t.data.issues.to}
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
      </div>
      {issues.error && <Problem error={issues.error} />}
      {issues.data && (
        <div className={issues.isPlaceholderData ? "pending" : undefined}>
          <span className="note">{t.data.issues.shown(issues.data.total, issues.data.rows.length)}</span>
          <IssueRows rows={issues.data.rows} />
          {issues.data.rows.length < issues.data.total && (
            <button type="button" className="cmd" onClick={() => setLimit(limit + PAGE)}>
              {t.data.issues.more}
            </button>
          )}
        </div>
      )}
    </Card>
  );
}

function IssueRows({ rows }: { rows: IssueRow[] }) {
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel() });
  const shown = table.getRowModel().rows;
  return (
    <div className="tbl-wrap">
      <table className="tbl">
        <thead>
          {table.getHeaderGroups().map((g) => (
            <tr key={g.id}>
              {g.headers.map((h) => (
                <th key={h.id}>{flexRender(h.column.columnDef.header, h.getContext())}</th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.id}>
              {r.getVisibleCells().map((c) => (
                <td key={c.id}>{flexRender(c.column.columnDef.cell, c.getContext())}</td>
              ))}
            </tr>
          ))}
          {shown.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="note">
                {t.data.issues.empty}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
