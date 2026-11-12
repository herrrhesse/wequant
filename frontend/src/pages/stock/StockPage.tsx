import { useCallback, useMemo, useState } from "react";
import { BAR_FIELDS, commandOf, MA_WINDOWS, useCall, type PriceMode, type Request } from "../../api/client";
import { ApiError } from "../../api/http";
import type { Bars, EvalResult, IssueRow, Row } from "../../api/types.gen";
import { Card } from "../../components/Card";
import { Command } from "../../components/Command";
import { KChart, MA_COLORS } from "../../components/KChart";
import { Problem } from "../../components/Problem";
import { show } from "../../format";
import { t } from "../../i18n";
import { href, type Route } from "../../router";
import { StatusBar } from "../../components/StatusBar";
import { Seg } from "../../components/Seg";
import { lb } from "../../labels";
import { useUi } from "../../ui";

const RANGES: [string, string][] = [
  ["0.25", "3 个月"],
  ["0.5", "半年"],
  ["1", "1 年"],
  ["3", "3 年"],
  ["all", "全部"],
];

type StockRoute = Extract<Route, { page: "stock" }>;

/** ISO date `months` before `iso` (the requested range only). */
function monthsBefore(iso: string, months: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() - months);
  return d.toISOString().slice(0, 10);
}

/** ISO date `years` before `iso` (the requested range only; never applied to results). */
function yearsBefore(iso: string, years: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCFullYear(d.getUTCFullYear() - years);
  return d.toISOString().slice(0, 10);
}

/** The stock page: daily K-line in raw or back-adjusted prices, backend moving averages, limits, halts, ST, issues,
 * and optionally a run's orders of this code. The range is the two years up to the focus day or the last trading day. */
export function StockPage({ route }: { route: StockRoute }) {
  const { look } = useUi();
  const [range, setRange] = useState("1");
  const status = useCall("data_status", {});
  const calendar = useCall("data_calendar", {});
  const last = status.data?.last_trade_date ?? null;
  // A focus day sits a year before the range end when the data allows.
  const ahead = route.date ? yearsBefore(route.date, -1) : null;
  const end = last && ahead && ahead < last ? ahead : last;
  const start = end ? (range === "all" ? (calendar.data?.first ?? null) : monthsBefore(end, Number(range) * 12)) : null;
  const [mode, setMode] = useState<PriceMode>("raw");
  const barsReq: Request<"data_bars"> | null = end ? { codes: [route.code], start, end, fields: [...BAR_FIELDS] } : null;
  const bars = useCall("data_bars", barsReq);
  const field = mode === "raw" ? "close" : "adj_close";
  const maReqs = MA_WINDOWS.map((n): Request<"expr_eval"> | null => (end ? { form: JSON.stringify(["MA", field, n]), codes: [route.code], start, end } : null));
  const ma0 = useCall("expr_eval", maReqs[0]!);
  const ma1 = useCall("expr_eval", maReqs[1]!);
  const ma2 = useCall("expr_eval", maReqs[2]!);
  const ma3 = useCall("expr_eval", maReqs[3]!);
  const issuesReq: Request<"data_issues"> | null = end ? { codes: [route.code], start, end, limit: 500 } : null;
  const issues = useCall("data_issues", issuesReq);
  const ordersReq: Request<"runs_orders"> | null = route.run ? { id: route.run, code: route.code, limit: 500 } : null;
  const orders = useCall("runs_orders", ordersReq);
  const mas = [ma0, ma1, ma2, ma3];
  const ma = useMemo(
    () => (mas.every((m) => m.data) ? mas.map((m, i) => ({ n: MA_WINDOWS[i] ?? 0, result: m.data! })) : null),
    [ma0.data, ma1.data, ma2.data, ma3.data],
  );

  const error = status.error ?? bars.error;
  return (
    <main className="page">
      <StatusBar />
      <div className="row">
        <Seg label="区间" value={range} options={RANGES} onChange={setRange} />
        <span className="small muted">滚轮缩放 · 拖动平移</span>
      </div>
      {route.from && (
        <span className="note">
          {t.stock.from(t.stock.fromNames[route.from] ?? route.from)} ·{" "}
          <a className="link" href="#" onClick={(e) => (e.preventDefault(), history.back())}>
            {t.stock.back}
          </a>
        </span>
      )}
      {error ? (
        <Card title={route.code}>
          {error instanceof ApiError && error.status === 404 ? <p className="problem">{t.stock.notFound(route.code)}</p> : <Problem error={error} />}
        </Card>
      ) : !bars.data || !ma || !issues.data ? (
        <p className="note">{t.app.loading}</p>
      ) : bars.data.rows.length === 0 ? (
        <Card title={route.code}>
          <span className="note">{t.stock.noBars}</span>
        </Card>
      ) : (
        <StockView
          code={route.code}
          bars={bars.data}
          barsCmd={commandOf("data_bars", barsReq)}
          ma={ma}
          maCmds={maReqs.map((r) => commandOf("expr_eval", r))}
          issues={issues.data.rows}
          issuesCmd={commandOf("data_issues", issuesReq)}
          issuesVersion={issues.data.data_version}
          trades={orders.data?.rows ?? []}
          run={route.run}
          mode={mode}
          setMode={setMode}
          focus={route.date}
          look={look}
        />
      )}
    </main>
  );
}


const STATUS: Record<Row["status"], string> = {
  traded: t.stock.day.traded,
  suspended: t.stock.day.suspended,
  missing: t.stock.day.missing,
  not_listed: t.stock.day.notListed,
};

interface ViewProps {
  code: string;
  bars: Bars;
  barsCmd: string;
  ma: { n: number; result: EvalResult }[];
  maCmds: string[];
  issues: IssueRow[];
  issuesCmd: string;
  issuesVersion: number;
  trades: { date: string; side: "buy" | "sell"; status: string }[];
  run: string | null;
  mode: PriceMode;
  setMode: (m: PriceMode) => void;
  focus: string | null;
  look: string;
}

function StockView({ code, bars, barsCmd, ma, maCmds, issues, issuesCmd, issuesVersion, trades, run, mode, setMode, focus, look }: ViewProps) {
  const rows = bars.rows;
  const focusIndex = focus ? rows.findIndex((r) => r.date === focus) : -1;
  const [hovered, setHovered] = useState(focusIndex >= 0 ? focusIndex : rows.length - 1);
  const [renderMs, setRenderMs] = useState<{ chart: number; page: number } | null>(null);
  const version = bars.data_version;
  const last = rows[rows.length - 1];
  const day = rows[Math.min(Math.max(hovered, 0), rows.length - 1)];
  const onRendered = useCallback((ms: number) => setRenderMs({ chart: Math.round(ms), page: Math.round(performance.now()) }), []);
  const onIssue = useCallback((date: string) => (location.hash = href.data(code, date)), [code]);
  const suspended = rows.some((r) => r.status === "suspended");
  const limitUnknown = rows.some((r) => r.status === "traded" && r.values.limit_known === 0);
  const stDays = rows.some((r) => r.values.st === 1);
  const stUnknown = rows.some((r) => r.status === "traded" && r.values.st_known === 0);
  const data = { code };

  return (
    <main className="page" data-render-ms={renderMs?.chart} data-page-ms={renderMs?.page}>
      <div className="card-head">
        <h1>{data.code}</h1>
        {last && <span className="note">{t.stock.latest(last.date)} · {t.stock.day.close} {show(last.values.close)} · {STATUS[last.status]}</span>}
      </div>
      <div className="row2 stock-row">
        <Card
          title={t.stock.chart}
          version={version}
          command={barsCmd}
          extra={
            <div className="seg" role="group" aria-label={t.stock.chart}>
              {(["raw", "adj"] as const).map((k) => (
                <button key={k} type="button" aria-pressed={mode === k} onClick={() => setMode(k)}>
                  {t.stock[k]}
                </button>
              ))}
            </div>
          }
        >
          <ul className="notes">
            <li>{t.stock.fillNote}</li>
            <li>{mode === "raw" ? t.stock.rawMaNote : t.stock.maSource}</li>
            {suspended && <li>{t.stock.suspendedDays}</li>}
            {limitUnknown && <li>{t.stock.limitUnknown}</li>}
            {stDays && <li>{t.stock.stMarked}</li>}
            {stUnknown && <li>{t.stock.stUnknown}</li>}
          </ul>
          {day && <Legend day={day} ma={ma} />}
          <KChart
            rows={rows}
            mode={mode}
            ma={ma}
            issues={issues}
            focus={focusIndex >= 0 ? focus : null}
            look={look}
            onHover={setHovered}
            onIssue={onIssue}
            onRendered={onRendered}
            trades={trades}
          />
          {run && <span className="note">{t.stock.runTrades(run, trades.length)}</span>}
          <span className="note">{t.stock.exNote}</span>
          <details className="sources">
            <summary>{t.stock.sources}</summary>
            {maCmds.map((c) => (
              <Command key={c} command={c} />
            ))}
          </details>
        </Card>
        <div className="side">
          {day && <DayPanel row={day} version={version} />}
          <Card title={t.stock.issues} version={issuesVersion} command={issuesCmd}>
            {issues.length === 0 && <span className="note">{t.stock.noIssues}</span>}
            {issues.map((is) => (
              <div key={`${is.table}/${is.date}/${is.kind}`} className="issue-item">
                <span className="num">{is.date}</span>
                <span>{lb().of("issue_kinds", is.kind)}</span>
                <span className="muted">{lb().of("issue_actions", is.action)}</span>
                <a className="link" href={href.data(data.code, is.date)}>
                  {t.stock.issueGoto}
                </a>
              </div>
            ))}
          </Card>
        </div>
      </div>
    </main>
  );
}

function DayPanel({ row, version }: { row: Row; version: number }) {
  const v = row.values;
  const d = t.stock.day;
  const price = (x: number | null | undefined) => (typeof x === "number" ? t.stock.yuan(x.toFixed(2)) : t.common.none);
  const flag = (x: number | null | undefined, known: number | null | undefined, unknown: string) =>
    known === 0 ? unknown : x === 1 ? d.yes : x === 0 ? d.no : t.common.none;
  const limit =
    row.status !== "traded"
      ? t.common.none
      : v.limit_known === 0
        ? d.limitUnknown
        : v.limit_up === 1
          ? t.stock.mark.up + (v.limit_locked === 1 ? `（${t.stock.mark.locked}）` : "")
          : v.limit_down === 1
            ? t.stock.mark.down + (v.limit_locked === 1 ? `（${t.stock.mark.locked}）` : "")
            : d.limitNone;
  const items: [string, string][] = [
    [d.date, row.date],
    [d.status, STATUS[row.status]],
    [d.open, price(v.open)],
    [d.high, price(v.high)],
    [d.low, price(v.low)],
    [d.close, price(v.close)],
    [d.adjOpen, show(v.adj_open)],
    [d.adjHigh, show(v.adj_high)],
    [d.adjLow, show(v.adj_low)],
    [d.adjClose, show(v.adj_close)],
    // Shares, exactly as the backend gives them: seed volumes are not whole lots of 100.
    [d.volume, typeof v.volume === "number" ? d.shares(show(v.volume)) : t.common.none],
    [d.amount, typeof v.amount === "number" ? t.stock.yuan(show(v.amount)) : t.common.none],
    [d.factor, show(v.factor)],
    [d.limitUpPrice, price(v.limit_up_price)],
    [d.limitDownPrice, price(v.limit_down_price)],
    [d.limit, limit],
    [d.st, row.status === "traded" ? flag(v.st, v.st_known, d.stUnknown) : t.common.none],
  ];
  return (
    <Card title={d.title} version={version}>
      <span className="note">{d.hint}</span>
      <dl className="day">
        {items.map(([k, val]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd className="num">{val}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

/** The hovered day's backend values: each moving average and the volume; "—" where the backend has none. */
function Legend({ day, ma }: { day: Row; ma: { n: number; result: EvalResult }[] }) {
  const value = (result: EvalResult) => result.rows.find((r) => r.date === day.date)?.values.value;
  const fmt = (v: number | null | undefined) => (typeof v === "number" ? v.toFixed(2) : t.common.none);
  return (
    <div className="legend">
      <span className="num">{t.stock.legendDay(day.date)}</span>
      {ma.map(({ n, result }, i) => (
        <span key={n} style={{ color: MA_COLORS[i % MA_COLORS.length] }}>
          MA{n} <span className="num">{fmt(value(result))}</span>
        </span>
      ))}
      <span>
        {t.stock.legendVolume} <span className="num">{show(day.values.volume)}</span>
      </span>
    </div>
  );
}
