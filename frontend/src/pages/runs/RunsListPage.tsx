// 回测 → 运行记录 (wequant runs list) and the comparison of two runs (runs diff).
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { backtestError, runsDiff, runsList } from "../../api/runs";
import type { RunRow } from "../../api/types.gen";
import { Card } from "../../components/Card";
import { localTime } from "../../format";
import { t } from "../../i18n";
import { href } from "../../router";
import { metricShown, percent } from "../../runs/display";

const r = t.runs;
const pct = (v: number | null) => (v === null ? t.common.none : percent(v));

export function RunsListPage() {
  const list = useQuery({ queryKey: ["runs", "list"], queryFn: runsList });
  const error = useQuery({ queryKey: ["runs", "error"], queryFn: backtestError });
  const [picked, setPicked] = useState<string[]>([]);
  if (!list.data) return <main className="page"><p className="note">{list.error ? String(list.error) : t.app.loading}</p></main>;
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length >= 2 ? [p[1]!, id] : [...p, id]));
  return (
    <main className="page">
      <div className="card-head">
        <h1>{r.title}</h1>
      </div>
      <Card title={r.listTitle} command={list.data.command}>
        <span className="note">{r.listLead}</span>
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th />
                <th>{r.cols.strategy}</th>
                <th>{r.cols.range}</th>
                <th className="r">{r.cols.annual}</th>
                <th className="r">{r.cols.mdd}</th>
                <th className="r">{r.cols.sharpe}</th>
                <th>{r.cols.store}</th>
                <th>{r.cols.code}</th>
                <th>{r.cols.created}</th>
                <th>{r.cols.rerunnable}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.data.data.rows.map((row) => (
                <Row key={row.id} row={row} picked={picked.includes(row.id)} onToggle={() => toggle(row.id)} />
              ))}
            </tbody>
          </table>
        </div>
        <span className="note">
          {r.pickTwo} · {r.pickedMax}
        </span>
      </Card>
      {picked.length === 2 && <Diff a={picked[0]!} b={picked[1]!} />}
      {error.data && (
        <Card title={r.error} command={error.data.command}>
          <span className="note">{r.errorNote}</span>
          <ul className="serrors">
            <li>
              <span className="mono">{error.data.data.name}</span> · {error.data.data.message}
            </li>
            {(error.data.data.detail ?? []).map((d, i) => (
              <li key={i}>
                <span className="mono">{d.code}</span> · {(d.loc ?? []).join(".")} · {d.message}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </main>
  );
}

function Row({ row, picked, onToggle }: { row: RunRow; picked: boolean; onToggle: () => void }) {
  return (
    <tr aria-selected={picked || undefined}>
      <td>
        <input type="checkbox" checked={picked} onChange={onToggle} aria-label={row.id} />
      </td>
      <td>
        <div className="mono">{row.strategy_hash}</div>
        {row.saved_as.length > 0 && <div className="code-sub">{row.saved_as.map((s) => `${s.id} v${s.n}`).join("、")}</div>}
      </td>
      <td className="num">
        {row.start} ~ {row.end}
      </td>
      <td className="r num">{pct(row.annual_return)}</td>
      <td className="r num">{pct(row.max_drawdown)}</td>
      <td className="r num">{row.sharpe === null ? t.common.none : row.sharpe.toFixed(2)}</td>
      <td className="num">v{row.store_version}</td>
      <td className="mono">{row.code_version}</td>
      <td className="num">{localTime(row.created_at)}</td>
      <td>{row.rerunnable ? r.yes : r.no}</td>
      <td>
        <a className="link" href={href.run(row.id)}>
          {r.open}
        </a>
      </td>
    </tr>
  );
}

function Diff({ a, b }: { a: string; b: string }) {
  const diff = useQuery({ queryKey: ["runs", "diff", a, b], queryFn: () => runsDiff(a, b) });
  if (!diff.data) return <Card title={r.diffTitle}><span className="note">{diff.isLoading ? t.app.loading : r.diffNone}</span></Card>;
  const d = diff.data.data;
  return (
    <Card title={r.diffTitle} command={diff.data.command}>
      <span className="note mono">
        A {d.a} · B {d.b}
      </span>
      <h3 className="sub-h">{r.diffInputs}</h3>
      {d.inputs.length ? (
        <ul className="diff-inputs mono">
          {d.inputs.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : (
        <span className="note">{r.same}</span>
      )}
      <h3 className="sub-h">{r.diffMetrics}</h3>
      <table className="tbl">
        <thead>
          <tr>
            <th>{r.kpi}</th>
            <th className="r">A</th>
            <th className="r">B</th>
          </tr>
        </thead>
        <tbody>
          {d.metrics.map((m) => (
            <tr key={m.name}>
              <td>
                {r.metrics[m.name] ?? m.name} <span className="code-sub">{m.name}</span>
              </td>
              <td className="r num">{metricShown({ value: m.a, unit: m.unit, reason: null }).text}</td>
              <td className="r num">{metricShown({ value: m.b, unit: m.unit, reason: null }).text}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
