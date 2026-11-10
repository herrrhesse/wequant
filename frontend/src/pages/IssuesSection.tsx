import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  useReactTable,
  type ColumnFiltersState,
} from "@tanstack/react-table";
import { useMemo, useState } from "react";
import type { IssuesFilter } from "../api/client";
import { useDataIssues, useIssueFilters } from "../api/queries";
import type { IssueCount, IssueRow } from "../api/types.gen";
import { Card } from "../components/Card";
import { Coded } from "../components/Coded";
import { prettyDetail, show } from "../format";
import { t } from "../i18n";
import { href } from "../router";

const col = createColumnHelper<IssueRow>();

const columns = [
  col.accessor("table", { header: t.data.status.table }),
  col.accessor("code", {
    header: t.data.issues.code,
    cell: (c) => (
      <a className="link" href={href.stock(c.getValue())} title={t.data.issues.openStock}>
        {c.getValue()}
      </a>
    ),
    filterFn: (row, id, value: string) => String(row.getValue(id)).includes(value.trim().toUpperCase()),
  }),
  col.accessor("date", {
    header: t.data.issues.date,
    cell: (c) => <span className="num">{c.getValue()}</span>,
    filterFn: (row, id, value: [string, string]) => {
      const d = String(row.getValue(id));
      return (!value[0] || d >= value[0]) && (!value[1] || d <= value[1]);
    },
  }),
  col.accessor("kind", { header: t.data.issues.kind, cell: (c) => <Coded code={c.getValue()} names={t.data.kinds} /> }),
  col.accessor("action", { header: t.data.issues.action, cell: (c) => <Coded code={c.getValue()} names={t.data.actions} /> }),
  col.accessor("detail", { header: t.data.issues.detail, cell: (c) => <div className="detail mono">{prettyDetail(c.getValue())}</div> }),
  col.accessor("source", { header: t.data.issues.source, cell: (c) => <span className="note">{c.getValue()}</span> }),
];

const ALL: IssuesFilter = { table: null, kind: null };
const same = (a: IssuesFilter, b: IssuesFilter) => a.table === b.table && a.kind === b.kind && (a.code ?? null) === (b.code ?? null);

/** data_issues: counts by table, kind and action, and the rows of one table and kind. */
interface Props {
  counts: IssueCount[];
  total: number;
  version: number;
  /** Opened from a stock page: that code's issues, with the date range set to that day. */
  focusCode: string | null;
  focusDate: string | null;
}

export function IssuesSection({ counts, total, version, focusCode, focusDate }: Props) {
  const [filter, setFilter] = useState<IssuesFilter>(focusCode ? { ...ALL, code: focusCode } : ALL);
  const issues = useDataIssues(filter);
  const offered = useIssueFilters().data ?? [];
  const has = (f: IssuesFilter) => offered.some((o) => same(o, f));

  return (
    <Card title={t.data.issues.title} version={issues.data?.data.data_version ?? version} command={issues.data?.command}>
      <p className="lead">{t.data.issues.lead}</p>
      {filter.code && (
        <span className="chip">
          {t.data.issues.focus(filter.code, focusDate)}
          <a className="link" href={href.stock(filter.code)}>
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
            <tr className="clickable" aria-selected={same(filter, ALL)} onClick={() => setFilter(ALL)}>
              <td colSpan={3}>{t.data.issues.all}</td>
              <td className="r num">{show(total)}</td>
            </tr>
            {counts.map((c) => {
              const f = { table: c.table, kind: c.kind };
              return (
                <tr
                  key={`${c.table}/${c.kind}/${c.action}`}
                  className={has(f) ? "clickable" : undefined}
                  aria-selected={same(filter, f)}
                  title={has(f) ? undefined : t.data.issues.noFixture}
                  onClick={() => has(f) && setFilter(f)}
                >
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
      {issues.isError && <span className="note">{t.data.issues.noFixture}</span>}
      {issues.data && (
        <IssueRows
          key={`${filter.table}/${filter.kind}/${filter.code}`}
          rows={issues.data.data.rows}
          total={issues.data.data.total}
          date={filter.code ? focusDate : null}
        />
      )}
    </Card>
  );
}

function IssueRows({ rows, total, date }: { rows: IssueRow[]; total: number; date: string | null }) {
  const [code, setCode] = useState("");
  const [from, setFrom] = useState(date ?? "");
  const [to, setTo] = useState(date ?? "");
  const columnFilters = useMemo<ColumnFiltersState>(() => {
    const f: ColumnFiltersState = [];
    if (code.trim()) f.push({ id: "code", value: code });
    if (from || to) f.push({ id: "date", value: [from, to] });
    return f;
  }, [code, from, to]);
  const table = useReactTable({
    data: rows,
    columns,
    state: { columnFilters },
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });
  const shown = table.getRowModel().rows;

  return (
    <>
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
        <span className="note">{t.data.issues.filterHint}</span>
      </div>
      <span className="note">
        {t.data.issues.shown(total, rows.length)}
        {columnFilters.length > 0 && ` · ${t.data.issues.filtered(shown.length)}`} · {t.data.issues.pagingNote}
      </span>
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
    </>
  );
}
