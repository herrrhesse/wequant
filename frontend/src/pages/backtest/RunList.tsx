// The run records (recent first; the shown run is marked; keep, export, delete and import) and the comparison of
// 2 to 3 ticked runs (overlaid net values; for 2, the differing metrics and settings).
import { useMemo, useState } from "react";
import { useCall, useCallMutation } from "../../api/client";
import { exportFile, importFile } from "../../api/files";
import type { RunDetail, RunRow } from "../../api/types.gen";
import { cssVar, EChart } from "../../components/EChart";
import { FileButton } from "../../components/FileButton";
import { Problem } from "../../components/Problem";
import { localTime, pct, tone } from "../../format";
import { useLabels } from "../../labels";
import { href } from "../../router";
import { metricShown } from "../../runs/display";
import { useUi } from "../../ui";

export type NameOf = (r: Pick<RunRow, "saved_as">) => string;
const COLORS = ["--acc", "--ink", "--warn"];
const RECENT = 5;

export function RunList({ rows, selected, cmp, nameOf }: { rows: RunRow[]; selected: string | null; cmp: string[]; nameOf: NameOf }) {
  const pin = useCallMutation("runs_pin", ["runs_list", "settings_show"]);
  const del = useCallMutation("runs_delete", ["runs_list", "storage_usage"]);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [all, setAll] = useState(false);
  // The recent runs, plus the shown one when it is older.
  const shown = all ? rows : rows.filter((r, i) => i < RECENT || r.id === selected);
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
                {shown.map((r) => (
                  <tr key={r.id} className="clickable" aria-selected={r.id === selected} onClick={() => (location.hash = href.backtest({ run: r.id, cmp }))}>
                    <td onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" aria-label="勾选比较" checked={cmp.includes(r.id)} onChange={() => toggle(r.id)} />
                    </td>
                    <td className="b">
                      {nameOf(r)}
                      {r.id === selected && <span className="tag acc current-tag">当前显示</span>}
                    </td>
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
        {rows.length > RECENT && (
          <button type="button" className="link small inline-btn" style={{ alignSelf: "flex-start", marginLeft: 0 }} onClick={() => setAll(!all)}>
            {all ? "只看最近 5 条" : `全部 ${rows.length} 条`}
          </button>
        )}
      </section>
    </>
  );
}

export function Compare({ ids, nameOf }: { ids: string[]; nameOf: NameOf }) {
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
