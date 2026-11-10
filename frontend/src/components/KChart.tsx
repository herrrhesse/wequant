// The daily K-line on klinecharts v10. It draws what the backend returned and computes nothing:
// prices, moving averages and limit flags come from `wequant data bars` and `wequant expr eval`.
import {
  dispose,
  init,
  registerIndicator,
  registerOverlay,
  type Chart,
  type Crosshair,
  type DeepPartial,
  type KLineData,
  type Styles,
} from "klinecharts";
import { useEffect, useRef } from "react";
import type { PriceMode } from "../api/client";
import type { EvalResult, IssueRow, Row } from "../api/types.gen";
import { t } from "../i18n";

interface Props {
  rows: Row[];
  mode: PriceMode;
  /** One EvalResult per moving-average window, in the price mode shown. */
  ma: { n: number; result: EvalResult }[];
  issues: IssueRow[];
  /** A date to scroll into view, e.g. when opened from a data issue. */
  focus: string | null;
  /** Changes when the theme or the up/down colours change, so the chart re-reads its colours. */
  look: string;
  onHover: (index: number) => void;
  onIssue: (date: string) => void;
  onRendered?: (ms: number) => void;
}

interface Mark {
  text: string;
  color: string;
  background: string;
  below: boolean;
  date?: string;
}

interface Band {
  text: string;
  color: string;
}

const CANDLE_PANE = "candle_pane";
export const MA_COLORS = ["#c48a00", "#2f6fb3", "#8a4fbf", "#2f7a5f"];

/** UTC midnight of an ISO date; the chart runs in Asia/Shanghai, so the label is that same date. */
const stamp = (date: string) => Date.parse(`${date}T00:00:00Z`);
const num = (v: number | null | undefined) => (typeof v === "number" ? v : Number.NaN);

let registered = false;
function register() {
  if (registered) return;
  registered = true;
  registerIndicator<Record<string, number>>({
    name: "WQ_MA",
    shortName: "MA",
    series: "price",
    precision: 2,
    figures: [],
    regenerateFigures: () => [],
    // The values are the backend's, handed in through extendData keyed by timestamp.
    calc: (dataList, indicator) => {
      const byStamp = indicator.extendData as Record<number, Record<string, number>>;
      return dataList.map((d) => byStamp[d.timestamp] ?? {});
    },
  });
  registerIndicator<{ vol: number; up: boolean }>({
    name: "WQ_VOL",
    shortName: "",
    precision: 0,
    shouldFormatBigNumber: true,
    figures: [
      {
        key: "vol",
        title: "",
        type: "bar",
        baseValue: 0,
        styles: ({ data, indicator }) => {
          const colors = indicator.extendData as { up: string; down: string };
          return { color: data.current?.up ? colors.up : colors.down };
        },
      },
    ],
    // Shares, as the backend gives them (seed volumes are not whole lots).
    calc: (dataList) => dataList.map((d) => ({ vol: num(d.volume as number), up: d.close >= d.open })),
  });
  registerOverlay<Mark>({
    name: "wqMark",
    totalStep: 2,
    lock: true,
    needDefaultPointFigure: false,
    createPointFigures: ({ coordinates, overlay }) => {
      const c = coordinates[0];
      const m = overlay.extendData;
      if (!c || !m) return [];
      return {
        type: "text",
        attrs: { x: c.x, y: m.below ? c.y + 8 : c.y - 8, text: m.text, align: "center", baseline: m.below ? "top" : "bottom" },
        styles: { color: m.color, backgroundColor: m.background, size: 11, borderRadius: 4, paddingLeft: 3, paddingRight: 3, paddingTop: 2, paddingBottom: 2 },
      };
    },
  });
  registerOverlay<Band>({
    name: "wqBand",
    totalStep: 3,
    lock: true,
    needDefaultPointFigure: false,
    createPointFigures: ({ chart, coordinates, bounding, overlay }) => {
      const [a, b] = coordinates;
      const band = overlay.extendData;
      if (!a || !b || !band) return [];
      const half = chart.getBarSpace().halfBar;
      const x = a.x - half;
      const width = b.x - a.x + half * 2;
      return [
        { type: "rect", attrs: { x, y: 0, width, height: bounding.height }, styles: { style: "fill", color: band.color } },
        {
          type: "text",
          attrs: { x: x + width / 2, y: 6, text: band.text, align: "center", baseline: "top" },
          styles: { color: "#888", backgroundColor: "transparent", size: 11 },
        },
      ];
    },
  });
}

/** Display-only: large volumes as 万 / 亿 on the volume axis. */
function bigNumber(value: string | number): string {
  const v = Number(value);
  if (!Number.isFinite(v)) return String(value);
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(0)}万`;
  return String(v);
}

function css(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function styles(): DeepPartial<Styles> {
  const line = css("--line");
  const muted = css("--muted");
  const up = css("--up");
  const down = css("--down");
  return {
    grid: { horizontal: { color: line }, vertical: { color: line } },
    candle: {
      bar: {
        compareRule: "current_open",
        upColor: up,
        downColor: down,
        noChangeColor: muted,
        upBorderColor: up,
        downBorderColor: down,
        noChangeBorderColor: muted,
        upWickColor: up,
        downWickColor: down,
        noChangeWickColor: muted,
      },
      priceMark: { last: { show: false }, high: { color: muted }, low: { color: muted } },
      tooltip: { showRule: "none" },
    },
    // The page draws its own legend for the hovered day, so a suspended day reads "—", not the last value.
    indicator: { tooltip: { showRule: "none" } },
    xAxis: { axisLine: { color: line }, tickText: { color: muted }, tickLine: { color: line } },
    yAxis: { axisLine: { color: line }, tickText: { color: muted }, tickLine: { color: line } },
    separator: { color: line },
    crosshair: {
      horizontal: { line: { color: muted }, text: { backgroundColor: css("--inv"), color: css("--on-inv") } },
      vertical: { line: { color: muted }, text: { backgroundColor: css("--inv"), color: css("--on-inv") } },
    },
  };
}

/** Bars as klinecharts wants them; a suspended day keeps its place with NaN prices, so nothing is drawn or joined. */
function toKLine(rows: Row[], mode: PriceMode): KLineData[] {
  const p = mode === "adj" ? "adj_" : "";
  return rows.map((r) => ({
    timestamp: stamp(r.date),
    open: num(r.values[`${p}open`]),
    high: num(r.values[`${p}high`]),
    low: num(r.values[`${p}low`]),
    close: num(r.values[`${p}close`]),
    volume: num(r.values.volume),
  }));
}

/** Runs of consecutive rows where test holds, as [first, last] indices. */
function runs(rows: Row[], test: (r: Row) => boolean): [number, number][] {
  const out: [number, number][] = [];
  rows.forEach((r, i) => {
    if (!test(r)) return;
    const last = out[out.length - 1];
    if (last && last[1] === i - 1) last[1] = i;
    else out.push([i, i]);
  });
  return out;
}

export function KChart({ rows, mode, ma, issues, focus, look, onHover, onIssue, onRendered }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const hover = useRef(onHover);
  const issue = useRef(onIssue);
  hover.current = onHover;
  issue.current = onIssue;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const started = performance.now();
    register();
    const chart: Chart | null = init(el, {
      locale: "zh-CN",
      timezone: "Asia/Shanghai",
      styles: styles(),
      formatter: { formatBigNumber: bigNumber },
    });
    if (!chart) return;
    const data = toKLine(rows, mode);
    const up = css("--up");
    const down = css("--down");

    // Y range of the price pane from finite values only; NaN on suspended days would otherwise spoil it.
    chart.overrideYAxis({
      paneId: CANDLE_PANE,
      createRange: ({ chart: c, defaultRange }) => {
        const { from, to } = c.getVisibleRange();
        const maRows = c.getIndicators({ name: "WQ_MA" })[0]?.result as Array<Record<string, number>> | undefined;
        let lo = Infinity;
        let hi = -Infinity;
        for (let i = Math.max(0, from); i < Math.min(data.length, to); i++) {
          const d = data[i];
          if (d && Number.isFinite(d.low)) lo = Math.min(lo, d.low);
          if (d && Number.isFinite(d.high)) hi = Math.max(hi, d.high);
          for (const v of Object.values(maRows?.[i] ?? {})) {
            if (Number.isFinite(v)) {
              lo = Math.min(lo, v);
              hi = Math.max(hi, v);
            }
          }
        }
        if (!Number.isFinite(lo) || !Number.isFinite(hi)) return defaultRange;
        const range = hi - lo;
        return { ...defaultRange, from: lo, to: hi, range, realFrom: lo, realTo: hi, realRange: range, displayFrom: lo, displayTo: hi, displayRange: range };
      },
    });

    chart.setSymbol({ ticker: "WQ", pricePrecision: 2, volumePrecision: 0 });
    chart.setPeriod({ span: 1, type: "day" });
    chart.setDataLoader({
      getBars: ({ callback }) => {
        callback(data, false);
        // About a year of bars in view; a focus date is scrolled to the middle.
        chart.setBarSpace(Math.max(3, el.clientWidth / 260));
        if (focus) {
          chart.scrollToTimestamp(stamp(focus));
          chart.scrollByDistance(-el.clientWidth / 2.5);
        }
        requestAnimationFrame(() => onRendered?.(performance.now() - started));
      },
    });

    // Moving averages: the backend's values by date, one line per window; NaN breaks the line.
    const byStamp: Record<number, Record<string, number>> = {};
    for (const { n, result } of ma) {
      for (const r of result.rows) {
        const s = stamp(r.date);
        byStamp[s] = { ...byStamp[s], [`ma${n}`]: num(r.values.value) };
      }
    }
    chart.createIndicator(
      {
        name: "WQ_MA",
        paneId: CANDLE_PANE,
        extendData: byStamp,
        figures: ma.map(({ n }) => ({ key: `ma${n}`, title: `MA${n}: `, type: "line" })),
        styles: { lines: ma.map((_, i) => ({ color: MA_COLORS[i % MA_COLORS.length], size: 1 })) },
      },
      true,
    );
    chart.createIndicator({ name: "WQ_VOL", paneId: "wq_vol", extendData: { up, down } });
    chart.setPaneOptions({ id: "wq_vol", height: 110 });

    // Bands: suspended runs (left blank) and ST runs.
    const band = (first: number, last: number, extend: Band) => {
      const a = data[first];
      const b = data[last];
      if (!a || !b) return;
      chart.createOverlay({
        name: "wqBand",
        paneId: CANDLE_PANE,
        points: [{ timestamp: a.timestamp, value: 0 }, { timestamp: b.timestamp, value: 0 }],
        extendData: extend,
      });
    };
    for (const [a, b] of runs(rows, (r) => r.status === "suspended")) band(a, b, { text: t.stock.mark.suspended, color: "rgba(128,128,128,0.10)" });
    for (const [a, b] of runs(rows, (r) => r.values.st === 1)) band(a, b, { text: t.stock.mark.st, color: "rgba(191,135,0,0.12)" });

    // Marks: limit up / down (and locked), unknown limits, data issues.
    const mark = (i: number, value: number, extend: Mark) => {
      const d = data[i];
      if (!d || !Number.isFinite(value)) return;
      chart.createOverlay({
        name: "wqMark",
        paneId: CANDLE_PANE,
        points: [{ timestamp: d.timestamp, value }],
        extendData: extend,
        onClick: extend.date ? () => issue.current(extend.date!) : undefined,
      });
    };
    const warn = css("--warn");
    const warnT = css("--warn-t");
    rows.forEach((r, i) => {
      const d = data[i];
      if (!d || r.status !== "traded") return;
      const locked = r.values.limit_locked === 1 ? t.stock.mark.locked : "";
      if (r.values.limit_known === 0) mark(i, d.high, { text: t.stock.mark.unknown, color: warn, background: warnT, below: false });
      else if (r.values.limit_up === 1) mark(i, d.high, { text: t.stock.mark.up + locked, color: "#ffffff", background: up, below: false });
      else if (r.values.limit_down === 1) mark(i, d.low, { text: t.stock.mark.down + locked, color: "#ffffff", background: down, below: true });
    });
    const index = new Map(rows.map((r, i) => [r.date, i]));
    for (const is of issues) {
      const i = index.get(is.date);
      const d = i === undefined ? undefined : data[i];
      if (i === undefined || !d) continue;
      const y = Number.isFinite(d.low) ? d.low : Number.NaN;
      mark(i, y, { text: `! ${t.stock.mark.issue}`, color: "#ffffff", background: warn, below: true, date: is.date });
    }

    chart.subscribeAction("onCrosshairChange", (raw) => {
      const c = raw as Crosshair | undefined;
      if (c?.dataIndex !== undefined) hover.current(c.dataIndex);
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      dispose(el);
    };
  }, [rows, mode, ma, issues, focus, look, onRendered]);

  return <div ref={ref} className="kchart" />;
}
