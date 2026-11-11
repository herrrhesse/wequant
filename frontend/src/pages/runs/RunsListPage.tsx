// 回测: the run settings form (backtest), the archived runs (runs list) and two runs compared (runs diff).
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { commandOf, modelDefaults, useCall, useCallMutation, type Request } from "../../api/client";
import { ApiError } from "../../api/http";
import type { DataSpec, ExecutionSpec, RunRow } from "../../api/types.gen";
import { Card } from "../../components/Card";
import { Problem } from "../../components/Problem";
import { localTime } from "../../format";
import { useRunningSeconds } from "../../hooks";
import { t } from "../../i18n";
import { href } from "../../router";
import { metricShown, percent } from "../../runs/display";
import { storedDraft } from "../../strategy/draft";

const r = t.runs;
const pct = (v: number | null) => (v === null ? t.common.none : percent(v));
const LIST: Request<"runs_list"> = { limit: 100, offset: 0 };

export function RunsListPage({ cmp }: { cmp: string[] }) {
  const list = useCall("runs_list", LIST, { staleTime: 0 });
  const toggle = (id: string) => {
    const next = cmp.includes(id) ? cmp.filter((x) => x !== id) : cmp.length >= 2 ? [cmp[1]!, id] : [...cmp, id];
    location.hash = href.backtest(next);
  };
  return (
    <main className="page">
      <div className="card-head">
        <h1>{r.title}</h1>
      </div>
      <BacktestForm />
      <Card title={r.listTitle} command={commandOf("runs_list", LIST)}>
        <span className="note">{r.listLead}</span>
        {list.error && <Problem error={list.error} />}
        {list.data && list.data.rows.length === 0 && <span className="note">{r.noRuns}</span>}
        {list.data && list.data.rows.length > 0 && (
          <div className="tbl-wrap">
            <table className="tbl" data-testid="runs">
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
                {list.data.rows.map((row) => (
                  <Row key={row.id} row={row} picked={cmp.includes(row.id)} onToggle={() => toggle(row.id)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        <span className="note">
          {r.pickTwo} · {r.pickedMax}
        </span>
      </Card>
      {cmp.length === 2 && <Diff a={cmp[0]!} b={cmp[1]!} />}
    </main>
  );
}

/** The settings of a run; defaults come from the snapshot (BacktestRequest, ExecutionSpec, DataSpec). */
function BacktestForm() {
  const defaults = modelDefaults("BacktestRequest");
  const list = useCall("strategy_list", {});
  const client = useQueryClient();
  const [strategy, setStrategy] = useState<string>(storedDraft() ? "__draft" : "");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [cash, setCash] = useState(String(defaults.cash ?? ""));
  const [execution, setExecution] = useState<ExecutionSpec>({ ...(defaults.execution as ExecutionSpec) });
  const [data, setData] = useState<DataSpec>({ ...(defaults.data as DataSpec) });
  const [benchmark, setBenchmark] = useState(String(defaults.benchmark ?? ""));
  const backtest = useCallMutation("backtest", ["runs_list"]);
  const seconds = useRunningSeconds(backtest.isPending);
  const subject = strategy === "__draft" ? { strategy: storedDraft() ?? {} } : strategy ? { saved: { id: strategy } } : null;
  const request: Request<"backtest"> | null = subject
    ? { ...subject, start: start || null, end: end || null, cash: Number(cash), execution, data, benchmark: benchmark.trim() || null, save: true, report: true }
    : null;
  const usage = backtest.error instanceof ApiError && backtest.error.status === 422 ? backtest.error.body : null;
  const bad = (field: string) => usage?.detail?.some((d) => (d.loc ?? []).includes(field)) ?? false;
  const run = () => {
    if (!request) return;
    backtest.mutate(request, {
      onSuccess: (result) => {
        // The report comes with this result only (F-28); the run page reads it from here.
        client.setQueryData(["backtest_report", result.run_id], result);
        location.hash = href.run(result.run_id);
      },
    });
  };
  const ex = (k: keyof ExecutionSpec, v: unknown) => setExecution({ ...execution, [k]: v });
  return (
    <Card title={r.form.title} command={commandOf("backtest", request)}>
      <div className="form-grid">
        <label className={bad("strategy") || bad("saved") ? "field has-error" : "field"}>
          {r.form.strategy}
          <select aria-label={r.form.strategy} value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="">{r.form.pick}</option>
            <option value="__draft" disabled={!storedDraft()}>
              {t.picks.draftOption}
            </option>
            {(list.data?.rows ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} · v{s.current_n}
              </option>
            ))}
          </select>
        </label>
        <label className={bad("start") ? "field has-error" : "field"}>
          {r.form.start}
          <input type="date" aria-label={r.form.start} value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className={bad("end") ? "field has-error" : "field"}>
          {r.form.end}
          <input type="date" aria-label={r.form.end} value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <label className={bad("cash") ? "field has-error" : "field"}>
          {r.form.cash}
          <input inputMode="decimal" aria-label={r.form.cash} value={cash} onChange={(e) => setCash(e.target.value)} />
        </label>
        <label className={bad("benchmark") ? "field has-error" : "field"}>
          {r.form.benchmark}
          <input aria-label={r.form.benchmark} value={benchmark} placeholder={r.form.noBenchmark} onChange={(e) => setBenchmark(e.target.value)} />
        </label>
        <label className={bad("price") ? "field has-error" : "field"}>
          {r.exec.priceLabel}
          <select value={execution.price} onChange={(e) => ex("price", e.target.value)}>
            {Object.entries(r.exec.price).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <NumberField label={r.exec.commission_rate} value={execution.commission_rate} bad={bad("commission_rate")} onChange={(v) => ex("commission_rate", v)} />
        <NumberField label={r.exec.min_commission} value={execution.min_commission} bad={bad("min_commission")} onChange={(v) => ex("min_commission", v)} />
        <NumberField label={r.exec.slippage_bps} value={execution.slippage_bps} bad={bad("slippage_bps")} onChange={(v) => ex("slippage_bps", v)} />
        <label className={bad("delisting") ? "field has-error" : "field"}>
          {r.exec.delisting}
          <select value={execution.delisting} onChange={(e) => ex("delisting", e.target.value)}>
            {Object.entries(r.delistingModes).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <NumberField label={r.exec.failure_recovery} value={execution.failure_recovery} bad={bad("failure_recovery")} onChange={(v) => ex("failure_recovery", v)} />
        <label className={bad("anomalies") ? "field has-error" : "field"}>
          {r.data}
          <select value={data.anomalies} onChange={(e) => setData({ ...data, anomalies: e.target.value as DataSpec["anomalies"] })}>
            {Object.entries(r.dataModes).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label className="field inline-check">
          <input type="checkbox" checked={!!data.exclude_suspicious} onChange={(e) => setData({ ...data, exclude_suspicious: e.target.checked })} />
          {r.form.excludeSuspicious}
        </label>
      </div>
      <span className="note">{r.form.note}</span>
      <div className="cond-row">
        <button type="button" className="btn" disabled={!request || backtest.isPending} onClick={run}>
          {backtest.isPending ? r.form.running(seconds) : r.form.run}
        </button>
        {backtest.isPending && seconds >= 30 && <span className="note">{r.form.slow}</span>}
      </div>
      {backtest.error && <Problem error={backtest.error} />}
    </Card>
  );
}

function NumberField({ label, value, bad, onChange }: { label: string; value: number | undefined; bad: boolean; onChange: (v: number) => void }) {
  const [text, setText] = useState(value === undefined ? "" : String(value));
  return (
    <label className={bad ? "field has-error" : "field"}>
      {label}
      <input
        inputMode="decimal"
        aria-label={label}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          onChange(Number(e.target.value));
        }}
      />
    </label>
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
  const request = { a, b };
  const diff = useCall("runs_diff", request);
  if (!diff.data) return <Card title={r.diffTitle}>{diff.error ? <Problem error={diff.error} /> : <span className="note">{t.app.loading}</span>}</Card>;
  const d = diff.data;
  return (
    <Card title={r.diffTitle} command={commandOf("runs_diff", request)}>
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
