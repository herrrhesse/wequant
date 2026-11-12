// Net value, benchmark, excess and drawdown of a run, operated like the stock K-line: wheel to zoom, drag to pan,
// range buttons, a crosshair with axis labels and no floating tooltip; the hovered day's values show in the legend
// row above. A click without dragging picks the day. Every value is the backend's daily row.
import { AxisPointerComponent } from "echarts/components";
import { useEffect, useMemo, useRef, useState } from "react";
import type { DailyRow } from "../../api/types.gen";
import { cssVar, echarts } from "../../components/EChart";
import { pct, tone } from "../../format";
import { weekday } from "../../text";

echarts.use([AxisPointerComponent]);

const RANGES: [string, number][] = [
  ["3 个月", 60],
  ["半年", 121],
  ["1 年", 242],
  ["3 年", 726],
  ["全部", 0],
];

interface Props {
  daily: DailyRow[];
  look: string;
  selected: string | null;
  onPick: (date: string) => void;
}

export function NavChart({ daily, look, selected, onPick }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [win, setWin] = useState<[number, number]>([0, Math.max(0, daily.length - 1)]);
  const pick = useRef(onPick);
  pick.current = onPick;
  const dates = useMemo(() => daily.map((d) => d.date), [daily]);

  const option = useMemo(() => {
    const ink = cssVar("--muted");
    const line = cssVar("--line");
    const acc = cssVar("--acc");
    const inv = cssVar("--inv");
    const onInv = cssVar("--on-inv");
    const label = { backgroundColor: inv, color: onInv, fontSize: 11, padding: [2, 6], borderRadius: 4 };
    const grids = [
      { left: 56, right: 16, top: 8, height: 230 },
      { left: 56, right: 16, top: 252, height: 80 },
      { left: 56, right: 16, top: 346, height: 80 },
    ];
    const x = (i: number) => ({
      type: "category",
      gridIndex: i,
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: line } },
      axisTick: { show: false },
      axisLabel: { show: i === 2, color: ink, fontSize: 11 },
      axisPointer: { show: true, type: "line", lineStyle: { color: ink, type: "dashed" }, label: { show: i === 2, ...label } },
    });
    const y = (i: number, fmt: (v: number) => string, inverse = false) => ({
      type: "value",
      gridIndex: i,
      scale: true,
      inverse,
      position: "left",
      splitNumber: i === 0 ? 4 : 2,
      axisLabel: { color: ink, fontSize: 10, formatter: fmt },
      splitLine: { lineStyle: { color: line } },
      axisPointer: { show: true, type: "line", lineStyle: { color: ink, type: "dashed" }, label: { show: true, ...label, formatter: (p: { value: number }) => fmt(p.value) } },
    });
    const pctFmt = (v: number) => `${(v * 100).toFixed(1)}%`;
    return {
      animation: false,
      grid: grids,
      axisPointer: { link: [{ xAxisIndex: "all" }], triggerOn: "mousemove" },
      tooltip: { trigger: "axis", showContent: false },
      xAxis: [x(0), x(1), x(2)],
      yAxis: [y(0, (v) => v.toFixed(2)), y(1, pctFmt), y(2, pctFmt, true)],
      dataZoom: [{ type: "inside", xAxisIndex: [0, 1, 2], zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false, minValueSpan: 20 }],
      series: [
        { id: "bench", name: "基准", type: "line", data: daily.map((d) => d.benchmark_nav), showSymbol: false, lineStyle: { width: 1.3, color: ink, type: "dashed" }, itemStyle: { color: ink } },
        { id: "nav", name: "净值", type: "line", data: daily.map((d) => d.nav), showSymbol: false, lineStyle: { width: 2, color: acc }, itemStyle: { color: acc } },
        { id: "excess", name: "超额", type: "line", xAxisIndex: 1, yAxisIndex: 1, data: daily.map((d) => d.excess), showSymbol: false, lineStyle: { width: 1.5, color: cssVar("--sub") }, itemStyle: { color: cssVar("--sub") } },
        { id: "dd", name: "回撤", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: daily.map((d) => d.drawdown), showSymbol: false, lineStyle: { width: 1, color: acc }, areaStyle: { color: acc, opacity: 0.15 }, itemStyle: { color: acc } },
      ],
    };
    // look: re-read the theme colours.
  }, [daily, dates, look]);

  useEffect(() => {
    if (!ref.current) return;
    const c = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chart.current = c;
    c.setOption(option);
    let down: { x: number; y: number } | null = null;
    const zr = c.getZr();
    zr.on("mousedown", (e) => {
      down = { x: e.offsetX, y: e.offsetY };
    });
    zr.on("mouseup", (e) => {
      const d = down;
      down = null;
      if (!d || Math.abs(e.offsetX - d.x) > 4 || Math.abs(e.offsetY - d.y) > 4) return;
      const at = c.convertFromPixel({ gridIndex: 0 }, [e.offsetX, 0]) as number[] | undefined;
      const i = at?.[0];
      if (typeof i === "number" && i >= 0 && i < dates.length) pick.current(dates[Math.round(i)]!);
    });
    zr.on("globalout", () => setHover(null));
    c.on("updateAxisPointer", (e) => {
      const info = (e as { axesInfo?: { axisDim: string; value: number }[] }).axesInfo?.find((a) => a.axisDim === "x");
      setHover(info ? info.value : null);
    });
    c.on("dataZoom", () => {
      const z = (c.getOption() as { dataZoom?: { startValue?: number; endValue?: number }[] }).dataZoom?.[0];
      if (z && typeof z.startValue === "number" && typeof z.endValue === "number") setWin([z.startValue, z.endValue]);
    });
    const resize = () => c.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      c.dispose();
      chart.current = null;
    };
  }, [option, dates]);

  // The picked day: a solid line on all three panes, without resetting the zoom.
  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const i = selected ? dates.indexOf(selected) : -1;
    const mark = i >= 0 ? { symbol: "none", silent: true, label: { show: false }, lineStyle: { color: cssVar("--ink"), width: 1.5, type: "solid" }, data: [{ xAxis: i }] } : { data: [] };
    c.setOption({ series: [{ id: "nav", markLine: mark }, { id: "excess", markLine: mark }, { id: "dd", markLine: mark }] });
  }, [selected, dates, option]);

  const n = daily.length;
  const zoomTo = (days: number) => {
    const span = days ? Math.min(n, days) : n;
    chart.current?.dispatchAction({ type: "dataZoom", startValue: n - span, endValue: n - 1 });
    setWin([n - span, n - 1]);
  };
  const shown = hover ?? (selected ? dates.indexOf(selected) : -1);
  const d = daily[shown >= 0 ? shown : n - 1];

  return (
    <div className="col">
      <div className="row">
        <span className="b">净值、基准与超额</span>
        <span className="small muted">滚轮缩放 · 拖动平移 · 点一下看当天的持仓、决策和成交</span>
        <div className="seg push" role="group" aria-label="区间">
          {RANGES.map(([label, days]) => {
            const span = days ? Math.min(n, days) : n;
            const on = win[1] === n - 1 && win[1] - win[0] + 1 === span;
            return (
              <button key={label} type="button" aria-pressed={on} onClick={() => zoomTo(days)}>
                {label}
              </button>
            );
          })}
        </div>
      </div>
      {d && (
        <div className="row small legend-row" aria-live="polite">
          <span className="b">
            {d.date}（{weekday(d.date)}）
          </span>
          <span className="lg">
            <i className="lg-nav" />
            净值 <b className="num acc">{d.nav.toFixed(3)}</b>
          </span>
          <span className="lg">
            <i className="lg-bench" />
            基准 <b className="num">{d.benchmark_nav === null ? "—" : d.benchmark_nav.toFixed(3)}</b>
          </span>
          <span>
            超额 <b className={`num ${tone(d.excess)}`}>{pct(d.excess, 1, true)}</b>
          </span>
          <span>
            回撤 <b className="num">{pct(d.drawdown, 1)}</b>
          </span>
        </div>
      )}
      <div ref={ref} className="nav-chart" role="img" aria-label="净值曲线" />
      <span className="note">上：净值与基准 · 中：超额 · 下：回撤</span>
    </div>
  );
}
