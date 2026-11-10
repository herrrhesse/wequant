// 选股: one decision day of a strategy (wequant screen). Every number is the backend's; the page selects and lays out.
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { screen, screenChoices, screenExplain } from "../api/runs";
import { useStock } from "../api/queries";
import type { AnomalyUse, Explain, FunnelStep, PoolCheck, Reject, ScreenResult, ScreenRow } from "../api/types.gen";
import { Card } from "../components/Card";
import { Command } from "../components/Command";
import { KChart } from "../components/KChart";
import { VirtualTable } from "../components/VirtualTable";
import { t } from "../i18n";
import { href } from "../router";
import { percent, rejectsAt } from "../runs/display";
import { AnomalyTable, WarningList } from "./runs/shared";

const p = t.picks;
const num = (v: number | null | undefined, digits = 3) => (typeof v === "number" ? Number(v.toPrecision(digits + 1)).toLocaleString("zh-CN") : t.common.none);
const pct = (v: number | null | undefined) => (typeof v === "number" ? percent(v, 1) : t.common.none);
const stepName = (step: string) => p.steps[step] ?? step;

export function PicksPage({ name, look }: { name: string | null; look: string }) {
  const choices = useQuery({ queryKey: ["screen", "choices"], queryFn: screenChoices });
  const result = useQuery({ queryKey: ["screen", name], queryFn: () => screen(name) });
  if (result.error) return <main className="page"><p className="note">{String(result.error)}</p></main>;
  if (!result.data) return <main className="page"><p className="note">{t.app.loading}</p></main>;
  const { data, command, name: shown } = result.data;
  return (
    <main className="page">
      <div className="card-head">
        <h1>{p.title}</h1>
        <span className="note">{p.lead}</span>
      </div>
      <Card title={p.choose} version={data.data_version} command={command}>
        <div className="filters">
          <label className="field">
            {p.choose}
            <select value={shown} onChange={(e) => (location.hash = href.picks(e.target.value))}>
              {(choices.data ?? []).map((c) => (
                <option key={c.name} value={c.name}>
                  {c.strategy} · {c.date}
                </option>
              ))}
            </select>
          </label>
          <Meta label={p.date} value={data.date} />
          <Meta label={p.nextDay} value={data.next_day ?? p.nextDayNone} />
          <Meta label={p.hash} value={data.strategy_hash} mono />
          <Meta label={p.savedAs} value={data.saved_as.length ? data.saved_as.map((s) => p.saved(s.id, s.n)).join("、") : p.unsaved} />
        </div>
        <span className="note">{p.chooseNote}</span>
        <span className="note">{p.noReweight}</span>
      </Card>
      <ScreenView key={shown} data={data} name={shown} look={look} />
    </main>
  );
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="stat meta">
      <span className="stat-l">{label}</span>
      <span className={mono ? "mono" : "num"}>{value}</span>
    </div>
  );
}

function ScreenView({ data, name, look }: { data: ScreenResult; name: string; look: string }) {
  const [step, setStep] = useState<string | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const rejects = useMemo(() => (step ? rejectsAt(data.rejects, step) : []), [data.rejects, step]);
  return (
    <>
      <div className="row2">
        <Card title={p.warnings}>
          <WarningList warnings={data.warnings} empty={p.noWarnings} />
        </Card>
        <Card title={p.anomalies}>
          {data.anomalies ? (
            <>
              <span className="note">{p.anomalyLine(data.anomalies.codes, data.anomalies.cells)}</span>
              <AnomalyTable rows={data.anomalies.sample} />
            </>
          ) : (
            <span className="note">{p.noAnomalies}</span>
          )}
        </Card>
      </div>
      <div className="row2 picks-row">
        <Card title={p.funnel}>
          <span className="note">{p.funnelNote}</span>
          <Funnel funnel={data.funnel} rejects={data.rejects} selected={step} onSelect={(s) => setStep(s === step ? null : s)} />
        </Card>
        <div className="picks-main">
          <Card
            title={step ? p.rejectsTable(stepName(step)) : p.scoredTable}
            extra={
              <>
                {step && (
                  <button type="button" className="cmd" onClick={() => setStep(null)}>
                    {p.backToScored}
                  </button>
                )}
                <button type="button" className="cmd" title={p.exportNote} onClick={() => (step ? exportRejects(rejects, name, step) : exportRows(data.rows, name))}>
                  {p.exportCsv}
                </button>
              </>
            }
          >
            <span className="note">
              {p.scoredCount(data.scored_count)}
              {data.cap_cash !== 0 && <> · {p.capCash(percent(data.cap_cash))}</>}
            </span>
            {step ? (
              <RejectTable rows={rejects} onRow={setCode} selected={code} />
            ) : (
              <ScoredTable rows={data.rows} onRow={setCode} selected={code} />
            )}
          </Card>
        </div>
        {code && <SidePanel data={data} name={name} code={code} look={look} onClose={() => setCode(null)} />}
      </div>
    </>
  );
}

function Funnel({ funnel, rejects, selected, onSelect }: { funnel: FunnelStep[]; rejects: Reject[]; selected: string | null; onSelect: (step: string) => void }) {
  const widest = Math.max(...funnel.map((f) => f.left ?? f.passed), 1);
  return (
    <ol className="funnel">
      {funnel.map((f) => {
        const n = rejectsAt(rejects, f.step).length;
        // Layout only: the bar's width is the backend's count scaled to the widest level.
        const width = `${Math.max(((f.left ?? f.passed) / widest) * 100, 1)}%`;
        return (
          <li key={f.step} className={f.chain ? "on-chain" : "off-chain"}>
            <button type="button" aria-pressed={selected === f.step} disabled={n === 0} onClick={() => onSelect(f.step)}>
              <span className="funnel-name">
                {stepName(f.step)}
                <span className="code-sub">
                  {[stepName(f.step) !== p.kinds[f.kind] ? p.kinds[f.kind] ?? f.kind : null, f.mode ? p.modes[f.mode] : null, f.chain ? null : p.offChain]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </span>
              <span className="funnel-bar">
                <span style={{ width }} />
              </span>
              <span className="funnel-n num">
                {f.left !== null ? `${p.left} ${f.left.toLocaleString("zh-CN")}` : `${p.passed} ${f.passed.toLocaleString("zh-CN")}`}
                {f.chain && f.left !== null && f.passed !== f.left && <span className="code-sub">{p.passed} {f.passed.toLocaleString("zh-CN")}</span>}
              </span>
              <span className="funnel-r num">{n > 0 ? p.rejected(n) : ""}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function ScoredTable({ rows, onRow, selected }: { rows: ScreenRow[]; onRow: (code: string) => void; selected: string | null }) {
  const factorNames = rows[0]?.factors.map((f) => f.name) ?? [];
  const valueNames = Object.keys(rows[0]?.values ?? {});
  const columns = useMemo<ColumnDef<ScreenRow, unknown>[]>(
    () => [
      { header: p.cols.rank, accessorKey: "rank", meta: { className: "r num" } },
      { header: p.cols.code, accessorKey: "code", cell: (c) => <span className="link">{c.row.original.code}</span> },
      { header: p.cols.role, id: "role", cell: (c) => <span className={`badge role-${c.row.original.role}`}>{p.roles[c.row.original.role]}</span> },
      { header: p.cols.score, id: "score", meta: { className: "r num" }, cell: (c) => num(c.row.original.score) },
      ...factorNames.map(
        (name, i): ColumnDef<ScreenRow, unknown> => ({
          id: `f${i}`,
          header: () => (
            <span>
              {name}
              <span className="code-sub"> {p.cols.value} / {p.cols.pct} / {p.cols.adjPct} / {p.cols.share} / {p.cols.contribution}</span>
            </span>
          ),
          cell: (c) => {
            const f = c.row.original.factors[i];
            return f ? (
              <span className="num factor-cell">
                {num(f.value)} · {pct(f.pct)} · {pct(f.adjusted_pct)} · {pct(f.weight_share)} · {num(f.contribution)}
              </span>
            ) : (
              t.common.none
            );
          },
        }),
      ),
      {
        header: `${p.cols.weight} / ${p.cols.rawWeight}`,
        id: "weight",
        meta: { className: "r num" },
        cell: (c) => {
          const r = c.row.original;
          return (
            <>
              {pct(r.weight)} / {pct(r.raw_weight)}
              {r.capped && <span className="badge warn capped">{p.capped}</span>}
            </>
          );
        },
      },
      ...valueNames.map((name): ColumnDef<ScreenRow, unknown> => ({ id: `v:${name}`, header: name, meta: { className: "r num" }, cell: (c) => num(c.row.original.values[name] ?? null) })),
    ],
    [factorNames.join("|"), valueNames.join("|")],
  );
  return <VirtualTable rows={rows} columns={columns} rowKey={(r) => r.code} onRow={(r) => onRow(r.code)} selected={selected} label={p.scoredTable} />;
}

function RejectTable({ rows, onRow, selected }: { rows: Reject[]; onRow: (code: string) => void; selected: string | null }) {
  const columns = useMemo<ColumnDef<Reject, unknown>[]>(
    () => [
      { header: p.cols.code, accessorKey: "code", cell: (c) => <span className="link">{c.row.original.code}</span> },
      { header: p.cols.step, id: "step", cell: (c) => `${stepName(c.row.original.step)}（${p.kinds[c.row.original.kind] ?? c.row.original.kind}）` },
      { header: p.cols.op, id: "op", cell: (c) => (c.row.original.op ? p.ops[c.row.original.op] : t.common.none) },
      { header: p.cols.actual, id: "value", meta: { className: "r num" }, cell: (c) => num(c.row.original.value) },
      {
        header: p.cols.threshold,
        id: "threshold",
        meta: { className: "r num" },
        cell: (c) => {
          const r = c.row.original;
          if (r.threshold === null) return t.common.none;
          return r.threshold_hi !== null ? `${num(r.threshold)} ~ ${num(r.threshold_hi)}` : num(r.threshold);
        },
      },
      {
        header: p.cols.members,
        id: "members",
        cell: (c) =>
          c.row.original.members?.map((m) => (
            <span key={m.name} className={m.passed ? "badge" : "badge warn"}>
              {m.name} {m.passed === null ? p.memberNone : m.passed ? "✓" : "✗"}
            </span>
          )) ?? t.common.none,
      },
      { header: p.cols.missing, id: "missing", cell: (c) => (c.row.original.missing ? p.missingFactor(c.row.original.missing) : t.common.none) },
    ],
    [],
  );
  return <VirtualTable rows={rows} columns={columns} rowKey={(r) => `${r.code}/${r.step}`} onRow={(r) => onRow(r.code)} selected={selected} label={p.cols.step} />;
}

function SidePanel({ data, name, code, look, onClose }: { data: ScreenResult; name: string; code: string; look: string; onClose: () => void }) {
  const explain = useQuery({ queryKey: ["screen", name, "explain", code], queryFn: () => screenExplain(name, code) });
  const row = data.rows.find((r) => r.code === code);
  const used: AnomalyUse[] = data.anomalies?.sample.filter((a) => a.code === code) ?? [];
  return (
    <aside className="side picks-side">
      <Card
        title={`${p.side.title} ${code}`}
        extra={
          <>
            <a className="link" href={href.stock(code, data.date)}>
              {p.side.open}
            </a>
            <button type="button" className="cmd" onClick={onClose}>
              {p.side.close}
            </button>
          </>
        }
      >
        {row ? (
          <>
            <span className="note">
              {p.cols.rank} {row.rank} · {p.roles[row.role]} · {p.cols.score} {num(row.score)}
            </span>
            <table className="tbl">
              <thead>
                <tr>
                  <th>{p.side.factors}</th>
                  <th className="r">{p.cols.value}</th>
                  <th className="r">{p.cols.pct}</th>
                  <th className="r">{p.cols.adjPct}</th>
                  <th className="r">{p.cols.contribution}</th>
                </tr>
              </thead>
              <tbody>
                {row.factors.map((f) => (
                  <tr key={f.name}>
                    <td>{f.name}</td>
                    <td className="r num">{num(f.value)}</td>
                    <td className="r num">{pct(f.pct)}</td>
                    <td className="r num">{pct(f.adjusted_pct)}</td>
                    <td className="r num">{num(f.contribution)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : (
          <span className="note">{p.side.noScore}</span>
        )}
        <h3 className="sub-h">{p.side.anomalies}</h3>
        {used.length ? <AnomalyTable rows={used} /> : <span className="note">{p.side.noAnomalies}</span>}
        <h3 className="sub-h">{p.side.chain}</h3>
        {explain.data ? <Chain explain={explain.data.data} command={explain.data.command} /> : <span className="note">{explain.isLoading ? t.app.loading : p.side.chainNone}</span>}
        <h3 className="sub-h">{p.side.chart}</h3>
        <MiniChart code={code} date={data.date} look={look} />
      </Card>
    </aside>
  );
}

/** A pool check's value as the page shows it: the backend's value with its unit or a yes/no word (layout only). */
function poolValue(name: string, value: PoolCheck["value"]): string {
  const kind = p.pool[name]?.kind;
  if (kind === "yesno") return value === null ? p.yesNo.unknown : value === true ? p.yesNo.yes : value === false ? p.yesNo.no : String(value);
  if (value === null) return name === "universe" ? "" : p.yesNo.unknown;
  if (kind === "yuan") return `${value} 元`;
  if (kind === "days") return `${value} 天`;
  return String(value);
}

/** explain: the backend's checks in order; nothing is re-derived. */
export function Chain({ explain, command }: { explain: Explain; command: string }) {
  const mark = (passed: boolean | null) => (passed === null ? <span className="badge warn">{p.memberNone}</span> : passed ? <span className="badge">✓</span> : <span className="badge warn">✗</span>);
  return (
    <div className="chain">
      <span className="stat-l">{p.side.pool}</span>
      {explain.pool.map((c) => (
        <div key={c.name} className="chain-row">
          {mark(c.passed)}
          <span>{p.pool[c.name]?.label ?? stepName(c.name)}</span>
          <span className="num">{poolValue(c.name, c.value)}</span>
          {c.note && <span className="note">{t.explainNotes[c.note] ?? c.note}</span>}
        </div>
      ))}
      <span className="stat-l">{p.side.stepsTitle}</span>
      {explain.steps.map((s) => (
        <div key={s.name} className={s.chain ? "chain-row" : "chain-row off"}>
          {mark(s.passed)}
          <span>
            {s.name}
            {s.mode && ` · ${p.modes[s.mode]}`}
          </span>
          <span className="num">
            {num(s.value)} {s.op ? p.ops[s.op] : ""} {s.threshold_hi !== null ? `${num(s.threshold)} ~ ${num(s.threshold_hi)}` : num(s.threshold)}
          </span>
          {s.members && <span className="note">{s.members.map((m) => `${m.name} ${m.passed === null ? p.memberNone : m.passed ? "✓" : "✗"}`).join(" · ")}</span>}
        </div>
      ))}
      <span className="stat-l">{p.side.score}</span>
      {explain.score ? (
        <span className="note">
          {p.cols.rank} {explain.score.rank} / {explain.score.scored_count} · {p.cols.score} {num(explain.score.score)} · {p.roles[explain.role]}
          {explain.weight !== null && ` · ${p.cols.weight} ${pct(explain.weight)}`}
        </span>
      ) : (
        <span className="note">
          {p.side.noScore}
          {explain.missing && ` · ${p.missingFactor(explain.missing)}`}
        </span>
      )}
      <Command command={command} />
    </div>
  );
}

function MiniChart({ code, date, look }: { code: string; date: string; look: string }) {
  const stock = useStock(code);
  if (stock.error) return <span className="note">{p.side.noChart}</span>;
  if (!stock.data) return <span className="note">{t.app.loading}</span>;
  const rows = stock.data.bars.data.rows;
  return (
    <div className="mini-chart">
      <KChart rows={rows} mode="raw" ma={[]} issues={stock.data.issues.data.rows} focus={rows.some((r) => r.date === date) ? date : null} look={look} onHover={() => {}} onIssue={() => {}} />
    </div>
  );
}

/** CSV of the backend's rows as given: raw values, no computation. */
function download(lines: (string | number | boolean | null)[][], file: string) {
  const cell = (v: string | number | boolean | null) => {
    const s = v === null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
  };
  const blob = new Blob(["﻿" + lines.map((l) => l.map(cell).join(",")).join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = file;
  a.click();
  URL.revokeObjectURL(a.href);
}

function exportRows(rows: ScreenRow[], name: string) {
  const factors = rows[0]?.factors.map((f) => f.name) ?? [];
  const values = Object.keys(rows[0]?.values ?? {});
  const fields = ["value", "pct", "adjusted_pct", "weight_share", "contribution"] as const;
  const head = ["rank", "code", "role", "score", "weight", "raw_weight", "capped", "formula", ...factors.flatMap((f) => fields.map((k) => `${f}.${k}`)), ...values];
  const body = rows.map((r) => [
    r.rank, r.code, r.role, r.score, r.weight, r.raw_weight, r.capped, r.formula,
    ...factors.flatMap((_, i) => fields.map((k) => r.factors[i]?.[k] ?? null)),
    ...values.map((v) => r.values[v] ?? null),
  ]);
  download([head, ...body], `screen-${name}-scored.csv`);
}

function exportRejects(rows: Reject[], name: string, step: string) {
  const head = ["code", "step", "kind", "passed", "value", "op", "threshold", "threshold_hi", "members", "missing"];
  const body = rows.map((r) => [r.code, r.step, r.kind, r.passed, r.value, r.op, r.threshold, r.threshold_hi, r.members ? JSON.stringify(r.members) : null, r.missing]);
  download([head, ...body], `screen-${name}-rejects-${step}.csv`);
}

