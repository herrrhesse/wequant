// A thin ECharts wrapper. Charts draw the backend's columns as given (runs show daily / periods); nothing is derived.
import { BarChart, HeatmapChart, LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, VisualMapComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

echarts.use([LineChart, BarChart, HeatmapChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, VisualMapComponent, MarkLineComponent, CanvasRenderer]);

export type ChartOption = echarts.EChartsCoreOption;

/** A CSS custom property of the page (theme tokens), for chart colours. */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

interface Props {
  option: ChartOption;
  height: number;
  /** Changes when the theme changes, so colours are read again. */
  look: string;
  /** The category (x) value clicked, e.g. a date. */
  onPick?: (category: string) => void;
  label: string;
}

export function EChart({ option, height, look, onPick, label }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const pick = useRef(onPick);
  pick.current = onPick;

  useEffect(() => {
    if (!ref.current) return;
    const c = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chart.current = c;
    // A click anywhere on the plot picks the nearest category, not only a click on a point.
    c.getZr().on("click", (e) => {
      if (!pick.current) return;
      const at = c.convertFromPixel({ gridIndex: 0 }, [e.offsetX, e.offsetY]) as number[] | undefined;
      const axis = (c.getOption() as { xAxis?: { data?: string[] }[] }).xAxis?.[0]?.data;
      const i = at?.[0];
      if (axis && typeof i === "number" && i >= 0 && i < axis.length) pick.current(axis[Math.round(i)]!);
    });
    const resize = () => c.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      c.dispose();
      chart.current = null;
    };
  }, [look]);

  useEffect(() => {
    chart.current?.setOption(option, true);
  }, [option, look]);

  return <div ref={ref} style={{ width: "100%", height }} role="img" aria-label={label} />;
}
