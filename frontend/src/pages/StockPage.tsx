import { useCallback, useMemo, useState } from "react";
import { ServiceError } from "../api/errors";
import { MA_WINDOWS, type PriceMode, type StockData } from "../api/client";
import { useStock, useStockCodes } from "../api/queries";
import type { EvalResult, Row } from "../api/types.gen";
import { Card } from "../components/Card";
import { Coded } from "../components/Coded";
import { Command } from "../components/Command";
import { KChart, MA_COLORS } from "../components/KChart";
import { show } from "../format";
import { t } from "../i18n";
import type { Conv, Theme } from "../prefs";
import { href } from "../router";

/** The stock page: daily K-line in raw or back-adjusted prices, backend moving averages, limits, halts, ST and issues. */
export function StockPage({ code, date, theme, conv }: { code: string; date: string | null; theme: Theme; conv: Conv }) {
  const stock = useStock(code);
  const codes = useStockCodes().data ?? [];
  if (stock.isPending) return <main className="page">{t.app.loading}</main>;
  if (stock.isError) {
    const missing = stock.error instanceof ServiceError && stock.error.exit === 3;
    return (
      <main className="page">
        <h1>{code}</h1>
        <Card title={missing ? t.stock.notInFixtures(code) : t.app.error}>
          <span className="note">
            {t.stock.available}
            {codes.map((c) => (
              <a key={c} className="link" href={href.stock(c)} style={{ marginLeft: 10 }}>
                {c}
              </a>
            ))}
          </span>
        </Card>
      </main>
    );
  }
  return <StockView data={stock.data} focus={date} look={`${theme}/${conv}`} />;
}

const STATUS: Record<Row["status"], string> = {
  traded: t.stock.day.traded,
  suspended: t.stock.day.suspended,
  missing: t.stock.day.missing,
  not_listed: t.stock.day.notListed,
};

function StockView({ data, focus, look }: { data: StockData; focus: string | null; look: string }) {
  const [mode, setMode] = useState<PriceMode>("raw");
  const rows = data.bars.data.rows;
  const focusIndex = focus ? rows.findIndex((r) => r.date === focus) : -1;
  const [hovered, setHovered] = useState(focusIndex >= 0 ? focusIndex : rows.length - 1);
  const [renderMs, setRenderMs] = useState<{ chart: number; page: number } | null>(null);
  const version = data.bars.data.data_version;
  const ma = useMemo(() => data.ma[mode].map((m, i) => ({ n: MA_WINDOWS[i] ?? 0, result: m.data })), [data, mode]);
  const issues = data.issues.data.rows;
  const last = rows[rows.length - 1];
  const day = rows[Math.min(Math.max(hovered, 0), rows.length - 1)];
  // chart: init to first frame; page: navigation start to first frame (includes loading the data). Read by tests.
  const onRendered = useCallback((ms: number) => setRenderMs({ chart: Math.round(ms), page: Math.round(performance.now()) }), []);
  const onIssue = useCallback((date: string) => (location.hash = href.data(data.code, date)), [data.code]);

  // Which notes apply, from the backend's flags; nothing is counted.
  const suspended = rows.some((r) => r.status === "suspended");
  const limitUnknown = rows.some((r) => r.status === "traded" && r.values.limit_known === 0);
  const stDays = rows.some((r) => r.values.st === 1);
  const stUnknown = rows.some((r) => r.status === "traded" && r.values.st_known === 0);

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
          command={data.bars.command}
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
          />
          <span className="note">{t.stock.exNote}</span>
          <details className="sources">
            <summary>{t.stock.sources}</summary>
            {data.ma[mode].map((m) => (
              <Command key={m.command} command={m.command} />
            ))}
          </details>
        </Card>
        <div className="side">
          {day && <DayPanel row={day} version={version} />}
          <Card title={t.stock.issues} version={data.issues.data.data_version} command={data.issues.command}>
            {issues.length === 0 && <span className="note">{t.stock.noIssues}</span>}
            {issues.map((is) => (
              <div key={`${is.table}/${is.date}/${is.kind}`} className="issue-item">
                <span className="num">{is.date}</span>
                <Coded code={is.kind} names={t.data.kinds} />
                <Coded code={is.action} names={t.data.actions} />
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
