// 选股: one decision day of a strategy (wequant screen). Every number is the backend's; the page selects and lays out.
import type { ColumnDef } from "@tanstack/react-table";
import { lazy, Suspense, useMemo, useState } from "react";
import { BAR_FIELDS, commandOf, useCall, type Request } from "../api/client";
import type { AnomalyUse, Explain, FunnelStep, PoolCheck, Reject, ScreenResult, ScreenRow, StrategyRef, StrategySpec } from "../api/types.gen";
import { Card } from "../components/Card";
import { Command } from "../components/Command";
import { Problem } from "../components/Problem";
import { VirtualTable } from "../components/VirtualTable";
import { useRunningSeconds } from "../hooks";
import { t } from "../i18n";
import { href, type Route } from "../router";
import { percent, rejectsAt } from "../runs/display";
import { storedDraft } from "../strategy/draft";
import { AnomalyTable, WarningList } from "./runs/shared";

const KChart = lazy(() => import("../components/KChart").then((m) => ({ default: m.KChart })));

const p = t.picks;
const num = (v: number | null | undefined, digits = 3) => (typeof v === "number" ? Number(v.toPrecision(digits + 1)).toLocaleString("zh-CN") : t.common.none);
const pct = (v: number | null | undefined) => (typeof v === "number" ? percent(v, 1) : t.common.none);
const stepName = (step: string) => p.steps[step] ?? step;

type PicksRoute = Extract<Route, { page: "picks" }>;

/** The strategy a screen runs: a saved one (id, version) or this browser's draft. */
type Subject = { saved: StrategyRef; strategy?: undefined } | { strategy: StrategySpec; saved?: undefined };

function subjectOf(route: PicksRoute): Subject | null {
  if (route.id) return { saved: { id: route.id, n: route.n } };
  if (route.draft) {
    const d = storedDraft();
    return d ? { strategy: d } : null;
  }
  return null;
}

export function PicksPage({ route, look }: { route: PicksRoute; look: string }) {
  const status = useCall("data_status", {}, { staleTime: 0 });
  const last = status.data?.last_trade_date ?? "";
  const subject = subjectOf(route);
  // The URL holds the submitted choice; the screen runs for it (back/forward restore it).
  const request: Request<"screen"> | null = subject ? { ...subject, date: route.date } : null;
  const result = useCall("screen", request);
  const elapsed = useRunningSeconds(result.isFetching);
  return (
    <main className="page">
      <div className="card-head">
        <h1>{p.title}</h1>
        <span className="note">{p.lead}</span>
      </div>
      <Chooser route={route} last={last} running={result.isFetching} elapsed={elapsed} command={commandOf("screen", request)} version={result.data?.data_version} />
      {status.error && <Problem error={status.error} />}
      {result.error && <Problem error={result.error} />}
      {result.data && subject && (
        <div className={result.isFetching ? "pending" : undefined}>
          <Card title={p.resultTitle(result.data.date)}>
            <div className="filters">
              <Meta label={p.date} value={result.data.date} />
              <Meta label={p.nextDay} value={result.data.next_day ?? p.nextDayNone} />
              <Meta label={p.hash} value={result.data.strategy_hash} mono />
              <Meta label={p.savedAs} value={result.data.saved_as.length ? result.data.saved_as.map((x) => p.saved(x.id, x.n)).join("、") : p.unsaved} />
            </div>
            <span className="note">{p.noReweight}</span>
          </Card>
          <ScreenView key={JSON.stringify(request)} data={result.data} subject={subject} look={look} />
        </div>
      )}
      {!subject && <p className="note">{route.draft ? p.noDraft : p.pickFirst}</p>}
    </main>
  );
}

/** Strategy (saved + version, or the draft) and decision day; "选股" submits them to the URL. */
function Chooser({ route, last, running, elapsed, command, version }: { route: PicksRoute; last: string; running: boolean; elapsed: number; command: string; version?: number }) {
  const list = useCall("strategy_list", {});
  const [id, setId] = useState(route.id ?? (route.draft ? "" : ""));
  const [useDraft, setUseDraft] = useState(route.draft || !route.id);
  const [n, setN] = useState<number | null>(route.n);
  const [date, setDate] = useState(route.date ?? "");
  const versions = useCall("strategy_versions", id && !useDraft ? { id } : null);
  const draftExists = storedDraft() !== null;
  const submit = () => {
    location.hash = useDraft ? href.picks({ draft: true, date: date || null }) : href.picks({ id, n, date: date || null });
  };
  return (
    <Card title={p.choose} version={version} command={command}>
      <div className="filters">
        <label className="field">
          {p.strategy}
          <select
            aria-label={p.strategy}
            value={useDraft ? "__draft" : id}
            onChange={(e) => {
              const v = e.target.value;
              setUseDraft(v === "__draft");
              setId(v === "__draft" ? "" : v);
              setN(null);
            }}
          >
            <option value="__draft" disabled={!draftExists}>
              {p.draftOption}
            </option>
            {(list.data?.rows ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        {!useDraft && id && (
          <label className="field">
            {p.version}
            <select aria-label={p.version} value={n ?? ""} onChange={(e) => setN(e.target.value ? Number(e.target.value) : null)}>
              <option value="">{p.currentVersion}</option>
              {(versions.data?.rows ?? []).map((v) => (
                <option key={v.n} value={v.n}>
                  v{v.n}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          {p.date}
          <input type="date" aria-label={p.date} value={date} max={last || undefined} onChange={(e) => setDate(e.target.value)} />
        </label>
        <button type="button" className="btn" disabled={running || (!useDraft && !id) || (useDraft && !draftExists)} onClick={submit}>
          {running ? p.running(elapsed) : p.run}
        </button>
      </div>
      <span className="note">{p.chooseNote(last)}</span>
    </Card>
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

function ScreenView({ data, subject, look }: { data: ScreenResult; subject: Subject; look: string }) {
  const name = data.date;
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
        {code && <SidePanel data={data} subject={subject} code={code} look={look} onClose={() => setCode(null)} />}
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

function SidePanel({ data, subject, code, look, onClose }: { data: ScreenResult; subject: Subject; code: string; look: string; onClose: () => void }) {
  const explainRequest: Request<"explain"> = { ...subject, code, date: data.date };
  const explain = useCall("explain", explainRequest);
  const row = data.rows.find((r) => r.code === code);
  const used: AnomalyUse[] = data.anomalies?.sample.filter((a) => a.code === code) ?? [];
  return (
    <aside className="side picks-side">
      <Card
        title={`${p.side.title} ${code}`}
        extra={
          <>
            <a className="link" href={href.stock(code, { date: data.date, from: "picks" })}>
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
        {explain.data ? (
          <Chain explain={explain.data} command={commandOf("explain", explainRequest)} />
        ) : explain.error ? (
          <Problem error={explain.error} />
        ) : (
          <span className="note">{t.app.loading}</span>
        )}
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
  const start = `${Number(date.slice(0, 4)) - 1}${date.slice(4)}`;
  const bars = useCall("data_bars", { codes: [code], start, end: date, fields: [...BAR_FIELDS] });
  if (bars.error) return <span className="note">{p.side.noChart}</span>;
  if (!bars.data) return <span className="note">{t.app.loading}</span>;
  const rows = bars.data.rows;
  return (
    <div className="mini-chart">
      <Suspense fallback={<span className="note">{t.app.loading}</span>}>
        <KChart rows={rows} mode="raw" ma={[]} issues={[]} focus={rows.some((r) => r.date === date) ? date : null} look={look} onHover={() => {}} onIssue={() => {}} />
      </Suspense>
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

