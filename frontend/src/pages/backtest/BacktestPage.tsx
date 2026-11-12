// 回测: the selected run's results first (metrics, curves operated like the K-line, a picked day, periods, orders,
// delistings, anomalies, run info), the "新建回测" form (a background job with progress and cancel) and, at the
// bottom, the run records; ticking 2 to 3 overlays their net values, ticking 2 lists their differences.
import { jobView } from "../../api/jobstate";
import { useEffect, useMemo, useState } from "react";
import { commandOf, modelDefaults, useCall, useCallMutation } from "../../api/client";
import { cancelJob, useJob } from "../../api/jobs";
import { exportFile, importFile } from "../../api/files";
import { FileButton } from "../../components/FileButton";
import type { DataSpec, ExecutionSpec, RunDetail, RunRow, StrategySpec } from "../../api/types.gen";
import { cssVar, EChart } from "../../components/EChart";
import { Problem } from "../../components/Problem";
import { StatusBar } from "../../components/StatusBar";
import { localTime, pct, tone } from "../../format";
import { useLabels } from "../../labels";
import { href, type Route } from "../../router";
import { metricShown } from "../../runs/display";
import { tx } from "../../text";
import { useUi } from "../../ui";
import { NavChart } from "./NavChart";
import { Anomalies, Day, Delisted, Info, Kpis, Orders, Periods, Report, Rerun } from "./RunResult";
import { WarningList } from "./shared";

type BacktestRoute = Extract<Route, { page: "backtest" }>;
type NameOf = (r: Pick<RunRow, "saved_as">) => string;
const DRAFT = "wequant.strategy.draft";
const COLORS = ["--acc", "--ink", "--warn"];

function draftSpec(): { spec: StrategySpec; name: string } | null {
  try {
    const v = JSON.parse(localStorage.getItem(DRAFT) ?? "null") as { spec: StrategySpec; name: string } | null;
    return v && typeof v.spec === "object" ? v : null;
  } catch {
    return null;
  }
}

export function BacktestPage({ route }: { route: BacktestRoute }) {
  const runs = useCall("runs_list", { limit: 100 });
  const list = useCall("strategy_list", {});
  const names = useMemo(() => new Map((list.data?.rows ?? []).map((s) => [s.id, s.name])), [list.data]);
  const id = route.run ?? runs.data?.rows[0]?.id ?? null;
  const nameOf: NameOf = (r) => (r.saved_as[0] && names.get(r.saved_as[0].id)) || "未保存的策略";
  return (
    <main className="page">
      <StatusBar />
      {runs.isError && <Problem error={runs.error} />}
      {id && <Result key={id} id={id} day={route.day} nameOf={nameOf} />}
      <NewBacktest />
      {runs.data && <RunList rows={runs.data.rows} selected={id} cmp={route.cmp} nameOf={nameOf} />}
    </main>
  );
}

function NewBacktest() {
  const L = useLabels();
  const ui = useUi();
  const defaults = modelDefaults("BacktestRequest");
  const list = useCall("strategy_list", {});
  const draft = draftSpec();
  const [strategy, setStrategy] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [cash, setCash] = useState(String(defaults.cash ?? ""));
  const [benchmark, setBenchmark] = useState(String(defaults.benchmark ?? ""));
  const [execution, setExecution] = useState<ExecutionSpec>({ ...(defaults.execution as ExecutionSpec) });
  const [data] = useState<DataSpec>({ ...(defaults.data as DataSpec) });
  const [adv, setAdv] = useState(false);
  const [jobId, setJobId] = useState<number | null>(null);
  const begin = useCallMutation("jobs_start", ["jobs_list"]);
  const job = useJob(jobId);
  const chosen = strategy || list.data?.rows[0]?.id || "";
  const subject = chosen === "__draft" ? (draft ? { strategy: draft.spec } : null) : chosen ? { saved: { id: chosen } } : null;
  const request = subject ? { ...subject, start: start || null, end: end || null, cash: Number(cash), execution, data, benchmark: benchmark.trim() || null, save: true } : null;
  const running = jobView(job).active;
  const result = useCall("jobs_result", job?.state === "succeeded" ? { id: job.id } : null);
  useEffect(() => {
    if (result.data && "run_id" in result.data) location.hash = href.backtest({ run: result.data.run_id });
  }, [result.data]);
  const run = async () => {
    if (!request) return;
    const j = await begin.mutateAsync({ kind: "backtest", request: request as never });
    setJobId(j.id);
  };
  const ex = (k: keyof ExecutionSpec, v: unknown) => setExecution({ ...execution, [k]: v });
  return (
    <section className="card" aria-label="新建回测">
      <div className="card-head">
        <h2>新建回测</h2>
      </div>
      <div className="row picks-bar">
        <label className="field">
          策略
          <select className="select" aria-label="策略" value={chosen} onChange={(e) => setStrategy(e.target.value)}>
            {draft && <option value="__draft">策略页的草稿：{draft.name}</option>}
            {(list.data?.rows ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          开始日
          <input className="input" type="date" aria-label="开始日" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="field">
          结束日
          <input className="input" type="date" aria-label="结束日" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <label className="field">
          初始资金（元）
          <input className="input num" inputMode="decimal" aria-label="初始资金" value={cash} onChange={(e) => setCash(e.target.value)} />
        </label>
        <label className="field">
          基准
          <input className="input" aria-label="基准" value={benchmark} placeholder="不设基准" onChange={(e) => setBenchmark(e.target.value)} />
        </label>
        <button type="button" className="btn acc" disabled={!request || running || begin.isPending} onClick={() => void run()}>
          开始回测
        </button>
      </div>
      <span className="small sub">
        开始日和结束日不填就用全部数据。决策日收盘后选股，下一交易日按{execution.price === "close" ? "收盘价" : "开盘价"}成交；涨跌停、停牌、不足一手的订单不成交。
        <button type="button" className="link small inline-btn" aria-expanded={adv} onClick={() => setAdv(!adv)}>
          高级 {adv ? "▾" : "▸"}
        </button>
      </span>
      {adv && (
        <div className="row">
          <label className="field">
            成交价
            <select className="select" value={execution.price ?? "open"} onChange={(e) => ex("price", e.target.value)}>
              <option value="open">开盘价</option>
              <option value="close">收盘价</option>
            </select>
          </label>
          <label className="field">
            佣金率
            <input className="input num" value={String(execution.commission_rate ?? "")} onChange={(e) => ex("commission_rate", Number(e.target.value))} />
          </label>
          <label className="field">
            最低佣金（元）
            <input className="input num" value={String(execution.min_commission ?? "")} onChange={(e) => ex("min_commission", Number(e.target.value))} />
          </label>
          <label className="field">
            滑点（基点）
            <input className="input num" value={String(execution.slippage_bps ?? "")} onChange={(e) => ex("slippage_bps", Number(e.target.value))} />
          </label>
          <label className="field">
            退市处理
            <select className="select" value={execution.delisting ?? "auto"} onChange={(e) => ex("delisting", e.target.value)}>
              <option value="auto">按原因自动</option>
              {["write_down", "exit", "hold"].map((k) => (
                <option key={k} value={k}>
                  {L.of("delisting_treatments", k)}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      {running && job && (
        <div className="inner" aria-label="回测进度">
          <div className="row small">
            <span className="b">{L.of("job_states", job.state)}</span>
            <span className="sub grow">{L.stage(job.stage, job.params)}</span>
            <button type="button" className="btn sm ghost" onClick={() => void cancelJob(job.id)}>
              取消
            </button>
          </div>
          <div className="progress">
            <span style={{ width: `${Math.round(job.fraction * 100)}%` }} />
          </div>
        </div>
      )}
      {job && (job.state === "failed" || job.state === "cancelled") && <span className={job.state === "failed" ? "problem" : "note"}>{L.stage(job.stage, job.params)}</span>}
      {begin.isError && <Problem error={begin.error} />}
      {ui.dev && request && (
        <div className="dev-row">
          <button type="button" className="dev-btn" onClick={() => void navigator.clipboard?.writeText(commandOf("backtest", request as never))}>
            复制回测命令
          </button>
        </div>
      )}
    </section>
  );
}

function Result({ id, day, nameOf }: { id: string; day: string | null; nameOf: NameOf }) {
  const ui = useUi();
  const show = useCall("runs_show", { id });
  const run = show.data;
  const saved = run?.saved_as[0];
  const now = useCall("strategy_show", saved ? { id: saved.id } : null);
  const summary = useCall("strategy_summary", run ? { strategy: run.strategy } : null);
  const [info, setInfo] = useState(false);
  if (show.isError) return <Problem error={show.error} />;
  if (!run) return <section className="card"><span className="note">{tx.app.loading}</span></section>;
  const changed = !!now.data && now.data.strategy_hash !== run.strategy_hash;
  const pick = (d: string) => (location.hash = href.backtest({ run: id, day: d }));
  return (
    <>
      <section className="card" aria-label="回测结果">
        <div className="card-head">
          <h2>{nameOf(run)}</h2>
          <span className="small muted">
            {run.start} 至 {run.end} · 基准 {run.benchmark ?? "无"} · 数据版本 {run.data_version_name ?? "已清理"} · {localTime(run.created_at)}
          </span>
          {saved && (
            <a className="link small push" href={href.strategy(saved.id)}>
              打开策略 ›
            </a>
          )}
        </div>
        {summary.data && <p className="summary">{summary.data.text}</p>}
        {changed && <span className="warnbox">这次回测之后策略改过，结果按回测时的策略。</span>}
        <Kpis run={run} />
        <NavChart daily={run.daily} look={ui.look} selected={day} onPick={pick} />
      </section>
      {day && <Day run={run} day={day} onPick={pick} />}
      <Periods run={run} look={ui.look} conv={ui.conv} />
      <Holdings run={run} />
      <Orders run={run} />
      <Delisted run={run} />
      <Anomalies run={run} />
      <section className="card" aria-label="提示">
        <div className="card-head">
          <h2>提示</h2>
        </div>
        <WarningList warnings={run.warnings} empty="没有提示。" />
      </section>
      <section className="card" aria-label="运行信息">
        <button type="button" className="card-head fold" aria-expanded={info} onClick={() => setInfo(!info)}>
          <h2>运行信息</h2>
          <span className="push small muted">{info ? "收起 ▾" : "展开 ▸"}</span>
        </button>
        {info && (
          <>
            <Rerun id={run.id} />
            {ui.dev && <Info run={run} command={commandOf("runs_show", { id: run.id })} />}
            {ui.dev && <Report id={run.id} />}
          </>
        )}
      </section>
    </>
  );
}

function Holdings({ run }: { run: RunDetail }) {
  const ui = useUi();
  const option = useMemo(() => {
    const ink = cssVar("--muted");
    const line = cssVar("--line");
    return {
      animation: false,
      grid: { left: 50, right: 50, top: 24, bottom: 28 },
      legend: { top: 0, textStyle: { color: ink } },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: run.daily.map((d) => d.date), axisLabel: { color: ink }, axisLine: { lineStyle: { color: line } } },
      yAxis: [
        { type: "value", axisLabel: { color: ink }, splitLine: { lineStyle: { color: line } } },
        { type: "value", max: 1, axisLabel: { color: ink, formatter: (v: number) => `${Math.round(v * 100)}%` }, splitLine: { show: false } },
      ],
      series: [
        { name: "持仓数", type: "bar", data: run.daily.map((d) => d.positions), itemStyle: { color: cssVar("--track") } },
        { name: "仓位", type: "line", yAxisIndex: 1, data: run.daily.map((d) => d.exposure), showSymbol: false, lineStyle: { color: cssVar("--acc") }, itemStyle: { color: cssVar("--acc") } },
      ],
    };
    // look: re-read the theme colours.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run.daily, ui.look]);
  return (
    <section className="card" aria-label="持仓数与仓位">
      <div className="card-head">
        <h2>持仓数与仓位</h2>
      </div>
      <EChart option={option} height={200} look={ui.look} label="持仓数与仓位" />
    </section>
  );
}

function RunList({ rows, selected, cmp, nameOf }: { rows: RunRow[]; selected: string | null; cmp: string[]; nameOf: NameOf }) {
  const pin = useCallMutation("runs_pin", ["runs_list", "settings_show"]);
  const del = useCallMutation("runs_delete", ["runs_list", "storage_usage"]);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const act = async (fn: () => Promise<unknown>, done: string) => {
    setErr(null);
    try {
      await fn();
      setNote(done);
    } catch (e) {
      setErr(e);
    }
  };
  const toggle = (id: string) => {
    const next = cmp.includes(id) ? cmp.filter((x) => x !== id) : [...cmp, id].slice(-3);
    location.hash = href.backtest({ run: selected, cmp: next });
  };
  return (
    <>
      {cmp.length >= 2 && <Compare ids={cmp} nameOf={nameOf} />}
      <section className="card" aria-label="运行记录">
        <div className="card-head">
          <h2>运行记录</h2>
          <span className="small muted">勾选 2 到 3 条：叠加净值；勾选 2 条时列出差别。保留的存档不会被自动清理。</span>
          <span className="push" />
          <FileButton
            accept=".wqrun"
            onFile={(f) =>
              void act(async () => {
                const r = await importFile("runs_import", f);
                location.hash = href.backtest({ run: r.id });
              }, "已导入回测存档")
            }
          >
            导入回测存档
          </FileButton>
        </div>
        {note && (
          <span role="status" className="small ok b">
            {note}
          </span>
        )}
        {err !== null && <Problem error={err} />}
        {rows.length === 0 && <span className="note">还没有回测过。</span>}
        {rows.length > 0 && (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th />
                  <th>策略</th>
                  <th>区间</th>
                  <th className="r">年化</th>
                  <th className="r">最大回撤</th>
                  <th className="r">夏普</th>
                  <th>数据版本</th>
                  <th>运行时间</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="clickable" aria-selected={r.id === selected} onClick={() => (location.hash = href.backtest({ run: r.id, cmp }))}>
                    <td onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" aria-label="勾选比较" checked={cmp.includes(r.id)} onChange={() => toggle(r.id)} />
                    </td>
                    <td className="b">{nameOf(r)}</td>
                    <td className="sub small">
                      {r.start} 至 {r.end}
                    </td>
                    <td className={`num r b ${tone(r.annual_return)}`}>{pct(r.annual_return, 1, true)}</td>
                    <td className="num r">{pct(r.max_drawdown, 1)}</td>
                    <td className="num r">{r.sharpe === null ? "—" : r.sharpe.toFixed(2)}</td>
                    <td className="small">{r.data_version_name ?? <span className="muted">数据版本已清理</span>}</td>
                    <td className="small muted">{localTime(r.created_at)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="row nowrap">
                        <button type="button" className="btn sm soft" aria-pressed={r.pinned} onClick={() => void act(() => pin.mutateAsync({ id: r.id, pinned: !r.pinned }), r.pinned ? "已取消保留" : "已保留")}>
                          {r.pinned ? "已保留" : "保留"}
                        </button>
                        <button type="button" className="btn sm soft" onClick={() => void act(() => exportFile("runs_export", { id: r.id }), "已导出，浏览器开始下载")}>
                          导出
                        </button>
                        <button
                          type="button"
                          className="btn sm ghost"
                          disabled={r.pinned}
                          onClick={() => {
                            if (window.confirm(`删除这次回测（${nameOf(r)}，${r.start} 至 ${r.end}）？删除后不能恢复。`)) void act(() => del.mutateAsync({ id: r.id }), "已删除");
                          }}
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function Compare({ ids, nameOf }: { ids: string[]; nameOf: NameOf }) {
  const ui = useUi();
  const L = useLabels();
  const a = useCall("runs_show", ids[0] ? { id: ids[0] } : null);
  const b = useCall("runs_show", ids[1] ? { id: ids[1] } : null);
  const c = useCall("runs_show", ids[2] ? { id: ids[2] } : null);
  const diff = useCall("runs_diff", ids.length === 2 ? { a: ids[0]!, b: ids[1]! } : null);
  const option = useMemo(() => {
    const shown = [a.data, b.data, c.data].filter((x): x is RunDetail => !!x);
    const ink = cssVar("--muted");
    const line = cssVar("--line");
    const dates = [...new Set(shown.flatMap((r) => r.daily.map((d) => d.date)))].sort();
    return {
      animation: false,
      grid: { left: 50, right: 20, top: 28, bottom: 28 },
      legend: { top: 0, textStyle: { color: ink } },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: dates, axisLabel: { color: ink }, axisLine: { lineStyle: { color: line } } },
      yAxis: { type: "value", scale: true, axisLabel: { color: ink }, splitLine: { lineStyle: { color: line } } },
      series: shown.map((r, i) => {
        const by = new Map(r.daily.map((d) => [d.date, d.nav]));
        const color = cssVar(COLORS[i]!);
        return { name: `${nameOf(r)} · ${localTime(r.created_at)}`, type: "line", data: dates.map((d) => by.get(d) ?? null), showSymbol: false, lineStyle: { width: 1.6, color }, itemStyle: { color } };
      }),
    };
    // look: re-read the theme colours.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a.data, b.data, c.data, ui.look]);
  return (
    <section className="card" aria-label="比较">
      <div className="card-head">
        <h2>比较 {ids.length} 次回测</h2>
      </div>
      <EChart option={option} height={240} look={ui.look} label="叠加净值" />
      {ids.length === 2 && diff.data && (
        <div className="col">
          <span className="small b">不同的指标</span>
          <table className="tbl">
            <thead>
              <tr>
                <th>指标</th>
                <th className="r">A</th>
                <th className="r">B</th>
              </tr>
            </thead>
            <tbody>
              {diff.data.metrics.map((m) => (
                <tr key={m.name}>
                  <td>{L.of("metrics", m.name)}</td>
                  <td className="r num">{metricShown({ value: m.a, unit: m.unit, reason: null }).text}</td>
                  <td className="r num">{metricShown({ value: m.b, unit: m.unit, reason: null }).text}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {a.data && b.data && a.data.strategy_hash !== b.data.strategy_hash && <CompareSummaries a={a.data} b={b.data} />}
          {a.data && b.data && (a.data.start !== b.data.start || a.data.end !== b.data.end) && (
            <span className="small">
              区间：A {a.data.start} 至 {a.data.end}；B {b.data.start} 至 {b.data.end}
            </span>
          )}
          {ui.dev && diff.data.inputs.length > 0 && <pre className="code-pre">{diff.data.inputs.join("\n")}</pre>}
        </div>
      )}
    </section>
  );
}

function CompareSummaries({ a, b }: { a: RunDetail; b: RunDetail }) {
  const sa = useCall("strategy_summary", { strategy: a.strategy });
  const sb = useCall("strategy_summary", { strategy: b.strategy });
  return (
    <div className="col tight small">
      <span className="b">策略不同</span>
      <span>A：{sa.data?.text}</span>
      <span>B：{sb.data?.text}</span>
    </div>
  );
}
