// One run's results: the header, metrics and the net value chart stay on top (a picked day opens under the chart);
// the rest is in tabs so the page stays two to three screens tall: periods, holdings, orders, delistings with
// anomalies and warnings (marked when there are warnings), and run information.
import { useMemo, useState } from "react";
import { commandOf, useCall } from "../../api/client";
import type { RunDetail } from "../../api/types.gen";
import { cssVar, EChart } from "../../components/EChart";
import { Problem } from "../../components/Problem";
import { SCOPE_WARNINGS, WarningText } from "../../components/WarningText";
import { localTime } from "../../format";
import { href } from "../../router";
import { tx } from "../../text";
import { useUi } from "../../ui";
import { NavChart } from "./NavChart";
import type { NameOf } from "./RunList";
import { Anomalies, Day, Delisted, Info, Kpis, Orders, Periods, Report, Rerun } from "./RunResult";
import { WarningList } from "./shared";

type Tab = "periods" | "holdings" | "orders" | "risks" | "info";

export function ResultView({ id, day, nameOf }: { id: string; day: string | null; nameOf: NameOf }) {
  const ui = useUi();
  const show = useCall("runs_show", { id });
  const run = show.data;
  const saved = run?.saved_as[0];
  const now = useCall("strategy_show", saved ? { id: saved.id } : null);
  const [tab, setTab] = useState<Tab>("periods");
  if (show.isError) return <Problem error={show.error} />;
  if (!run)
    return (
      <section className="card">
        <span className="note">{tx.app.loading}</span>
      </section>
    );
  const changed = !!now.data && now.data.strategy_hash !== run.strategy_hash;
  const pick = (d: string) => (location.hash = href.backtest({ run: id, day: d }));
  const warned = run.warnings.length > 0 || (run.anomalies?.cells ?? 0) > 0;
  const tabs: [Tab, string, boolean][] = [
    ["periods", "年度月度", false],
    ["holdings", "持仓", false],
    ["orders", "订单", false],
    ["risks", "退市与异常", warned],
    ["info", "运行信息", false],
  ];
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
        {changed && <span className="warnbox">这次回测之后策略改过，结果按回测时的策略。</span>}
        {/* The scope went beyond the stocks the data holds: said where the result is read, with a way to widen it. */}
        {run.warnings
          .filter((w) => SCOPE_WARNINGS.has(w.code))
          .map((w, i) => (
            <span key={i} className="warnbox" role="note" aria-label="数据范围提示">
              <WarningText w={w} />
            </span>
          ))}
        <Kpis run={run} />
        <NavChart daily={run.daily} look={ui.look} selected={day} onPick={pick} />
      </section>
      {day && <Day run={run} day={day} onPick={pick} />}
      <section className="card result-tabs" aria-label="更多结果">
        <div className="seg tabs" role="tablist" aria-label="更多结果">
          {tabs.map(([k, label, mark]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k} aria-pressed={tab === k} onClick={() => setTab(k)}>
              {label}
              {mark && <span className="tab-dot" aria-label="有提示" />}
            </button>
          ))}
        </div>
        <div role="tabpanel" className="col">
          {tab === "periods" && <Periods run={run} look={ui.look} conv={ui.conv} />}
          {tab === "holdings" && <Holdings run={run} />}
          {tab === "orders" && <Orders run={run} />}
          {tab === "risks" && (
            <>
              <section className="card inset" aria-label="提示">
                <div className="card-head">
                  <h2>提示</h2>
                </div>
                <WarningList warnings={run.warnings} empty="没有提示。" />
              </section>
              <Delisted run={run} />
              <Anomalies run={run} />
            </>
          )}
          {tab === "info" && (
            <>
              <Rerun id={run.id} />
              {ui.dev && <Info run={run} command={commandOf("runs_show", { id: run.id })} />}
              {ui.dev && <Report id={run.id} />}
            </>
          )}
        </div>
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

