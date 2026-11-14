// 回测 → 运行详情: runs show, orders, anomalies, a day, rerun and the report, each a backend request. Every number and
// curve is a backend column (daily, periods, metrics); the page converts units and lays out, nothing more.
import { Stock } from "../../components/Stock";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { commandOf, useCall, useCallMutation, type Request } from "../../api/client";
import type { OrderRow, RunDetail } from "../../api/types.gen";
import { Problem } from "../../components/Problem";
import { Card } from "../../components/Card";
import { Command } from "../../components/Command";
import { cssVar, EChart, type ChartOption } from "../../components/EChart";
import { VirtualTable } from "../../components/VirtualTable";
import { localTime } from "../../format";
import { useRunningSeconds } from "../../hooks";
import { t } from "../../i18n";
import { lb } from "../../labels";
import { href } from "../../router";
import { metricShown, percent, shares } from "../../runs/display";
import { ChainView } from "../../components/ChainView";
import { AnomalyTable } from "./shared";

const r = t.runs;
const pct = (v: number | null | undefined) => (typeof v === "number" ? percent(v) : t.common.none);
const yuan = (v: number | null | undefined) => (typeof v === "number" ? v.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) : t.common.none);

/** The KPIs F6 requires, in display order; others follow under 其他指标. */
const KPIS = [
  "annual_return",
  "annual_return_ex_best",
  "max_drawdown",
  "max_drawdown_peak",
  "max_drawdown_trough",
  "max_drawdown_recovery",
  "max_drawdown_days",
  "sharpe",
  "annual_volatility",
  "information_ratio",
  "tracking_error",
  "turnover",
  "executable_order_share",
  "max_days_without_new_high",
  "capacity_p95",
  "capacity_max",
  "capacity_unknown_fills",
];

export function Info({ run, command }: { run: RunDetail; command: string }) {
  const ex = run.execution;
  return (
    <Card title={r.info} version={run.store_version} command={command}>
      <dl className="info-grid">
        <Item k="ID" v={<span className="mono">{run.id}</span>} />
        <Item k={r.engine} v={run.engine} />
        <Item k={r.cols.code} v={<span className="mono">{run.code_version}</span>} />
        <Item k={r.cols.store} v={`v${run.store_version}`} />
        <Item k={r.cols.range} v={`${run.start} ~ ${run.end}`} />
        <Item k={r.anchor} v={run.anchor} />
        <Item k={r.cols.created} v={localTime(run.created_at)} />
        <Item k={r.cols.rerunnable} v={run.rerunnable ? r.yes : r.no} />
        <Item
          k={r.strategy}
          v={
            <>
              <span className="mono">{run.strategy_hash}</span>
              {run.saved_as.length > 0 && <span className="code-sub"> {run.saved_as.map((s) => `${s.id} v${s.n}`).join("、")}</span>}
            </>
          }
        />
        <Item k={r.cash} v={`${yuan(run.cash)} 元`} />
        <Item k={r.benchmark} v={run.benchmark ?? t.common.none} />
        <Item k={r.data} v={`${r.dataModes[run.data.anomalies ?? "flag"]}${run.data.exclude_suspicious ? " · exclude_suspicious" : ""}`} />
      </dl>
      <details className="sources">
        <summary>{r.strategyView}</summary>
        <pre className="code-pre">{JSON.stringify(run.strategy, null, 2)}</pre>
        <span className="note">
          <a className="link" href={href.strategy()}>
            {t.nav.strategy}
          </a>
        </span>
      </details>
      <h3 className="sub-h">{r.execution}</h3>
      <span className="note">{r.executionNote}</span>
      <dl className="info-grid">
        <Item k={r.exec.priceLabel} v={r.exec.price[ex.price ?? "open"] ?? String(ex.price)} />
        <Item k={r.exec.commission_rate} v={String(ex.commission_rate)} />
        <Item k={r.exec.min_commission} v={String(ex.min_commission)} />
        <Item k={r.exec.slippage_bps} v={String(ex.slippage_bps)} />
        <Item k={r.exec.delisting} v={r.delistingModes[ex.delisting ?? "auto"] ?? String(ex.delisting)} />
        <Item k={r.exec.failure_recovery} v={String(ex.failure_recovery)} />
      </dl>
      <h3 className="sub-h">{r.versions}</h3>
      <p className="note mono">
        {Object.entries(run.versions)
          .map(([k, v]) => `${k} ${v}`)
          .join(" · ")}
      </p>
      <h3 className="sub-h">{r.fees}</h3>
      <table className="tbl">
        <tbody>
          {run.fees.map((f) => (
            <tr key={f.id}>
              <td className="mono">{f.id}</td>
              <td className="num">
                {f.since} ~ {f.until ?? ""}
              </td>
              <td className="num">
                买 {f.buy_rate} · 卖 {f.sell_rate}
                {f.basis && ` · ${f.basis}`}
              </td>
              <td className="note">
                {f.exchanges?.join("、") ?? ""} {f.note ?? ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Item({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </div>
  );
}

export function Kpis({ run }: { run: RunDetail }) {
  const others = Object.keys(run.metrics).filter((k) => !KPIS.includes(k));
  const tile = (k: string) => {
    const m = metricShown(run.metrics[k]);
    return (
      <div key={k} className="stat" tabIndex={0} data-tip={[lb().of("metrics", k), lb().help("metrics", k)].filter(Boolean).join("：")}>
        <span className="stat-l">{lb().of("metrics", k)}</span>
        <span className="stat-v num">{m.text}</span>
        {m.reason && <span className="stat-s">{lb().of("metric_reasons", m.reason)}</span>}
      </div>
    );
  };
  return (
    <Card title={r.kpi}>
      <span className="note">{r.kpiNote}</span>
      <div className="grid kpis">{KPIS.map(tile)}</div>
      <details className="sources">
        <summary>{r.otherMetrics}</summary>
        <div className="grid kpis">{others.map(tile)}</div>
      </details>
    </Card>
  );
}

export function Periods({ run, look, conv }: { run: RunDetail; look: string; conv: "cn" | "intl" }) {
  const option = useMemo<ChartOption>(() => {
    const months = run.periods.month;
    // Layout only: "2024-03" is placed at row 2024, column 03.
    const years = [...new Set(months.map((m) => m.period.slice(0, 4)))];
    const up = cssVar("--red");
    const down = cssVar("--green");
    const ink = cssVar("--sub");
    const values = months.map((m) => Math.abs(m.return));
    const top = Math.max(...values, 0.0001);
    return {
      animation: false,
      grid: { left: 50, right: 70, top: 10, bottom: 30 },
      tooltip: { formatter: (p: { data: [number, number, number] }) => `${years[p.data[1]]}-${String(p.data[0] + 1).padStart(2, "0")} ${percent(p.data[2])}` },
      xAxis: { type: "category", data: Array.from({ length: 12 }, (_, i) => `${i + 1}月`), axisLabel: { color: ink } },
      yAxis: { type: "category", data: years, axisLabel: { color: ink } },
      // The legend sits right of the grid, clear of the month axis.
      visualMap: { min: -top, max: top, calculable: false, orient: "vertical", right: 0, top: "middle", itemHeight: 100, textStyle: { color: ink }, inRange: { color: conv === "cn" ? [down, cssVar("--card"), up] : [up, cssVar("--card"), down] }, formatter: (v: number) => percent(v, 0) },
      series: [
        {
          type: "heatmap",
          data: months.map((m) => [Number(m.period.slice(5, 7)) - 1, years.indexOf(m.period.slice(0, 4)), m.return]),
          label: { show: true, color: cssVar("--ink"), fontSize: 10, formatter: (p: { data: [number, number, number] }) => percent(p.data[2], 1) },
        },
      ],
    };
  }, [run.periods.month, look, conv]);
  return (
    <Card title={r.periods}>
      <div className="row2">
        <div>
          <h3 className="sub-h">{r.year}</h3>
          <table className="tbl">
            <thead>
              <tr>
                <th>{r.year}</th>
                <th className="r">{r.ret}</th>
                <th className="r">{r.bench}</th>
                <th className="r">{r.excess}</th>
                <th className="r">{r.periodMdd}</th>
              </tr>
            </thead>
            <tbody>
              {run.periods.year.map((y) => (
                <tr key={y.period}>
                  <td className="num">{y.period}</td>
                  <td className="r num">{pct(y.return)}</td>
                  <td className="r num">{pct(y.benchmark_return)}</td>
                  <td className="r num">{pct(y.excess)}</td>
                  <td className="r num">{pct(y.max_drawdown)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <h3 className="sub-h">{r.monthHeat}</h3>
          <EChart option={option} height={Math.max(160, 40 * new Set(run.periods.month.map((m) => m.period.slice(0, 4))).size + 70)} look={`${look}/${conv}`} label={r.monthHeat} />
        </div>
      </div>
    </Card>
  );
}

/** Why an order was placed (labels.order_causes) and, when it did not fill, why not. */
function orderWhy(o: OrderRow): string {
  const cause = lb().cause(o.cause, o.cause_params);
  const miss = o.reason ? `没成交：${lb().of("order_reasons", o.reason)}` : "";
  return [cause, miss].filter(Boolean).join("；");
}

type OrderFilter = { status: "all" | "rejected"; reason: string };

/** Rows per page of runs orders (its limit is at most 500). */
const ORDERS_PAGE = 500;

export function Orders({ run }: { run: RunDetail }) {
  const counts = run.orders;
  const [filter, setFilter] = useState<OrderFilter>({ status: "all", reason: "" });
  const [offset, setOffset] = useState(0);
  const request: Request<"runs_orders"> = { id: run.id, status: filter.status === "rejected" ? "rejected" : null, limit: ORDERS_PAGE, offset };
  const page = useCall("runs_orders", request, { placeholderData: (prev) => prev });
  const total = page.data?.total ?? 0;
  // Selection only: the page's backend rows with that reason (runs orders filters by status, not by reason).
  const rows = useMemo(() => (page.data?.rows ?? []).filter((o) => !filter.reason || o.reason === filter.reason), [page.data, filter.reason]);
  const columns = useMemo<ColumnDef<OrderRow, unknown>[]>(
    () => [
      { header: r.ocols.date, accessorKey: "date", meta: { className: "num" } },
      { header: r.ocols.code, id: "code", cell: (c) => <Stock code={c.row.original.code} name={c.row.original.name} /> },
      { header: r.ocols.side, id: "side", cell: (c) => r.sides[c.row.original.side] },
      { header: r.ocols.status, id: "status", cell: (c) => <span className={c.row.original.status === "rejected" ? "badge warn" : "badge"}>{lb().of("order_statuses", c.row.original.status)}</span> },
      { header: r.ocols.requested, id: "requested", meta: { className: "r num" }, cell: (c) => (c.row.original.requested === null ? r.wholeHolding : c.row.original.requested.toLocaleString("zh-CN")) },
      { header: r.ocols.shares, id: "shares", meta: { className: "r num" }, cell: (c) => c.row.original.shares.toLocaleString("zh-CN") },
      { header: r.ocols.price, id: "price", meta: { className: "r num" }, cell: (c) => yuan(c.row.original.price) },
      { header: r.ocols.value, id: "value", meta: { className: "r num" }, cell: (c) => yuan(c.row.original.value) },
      {
        header: r.ocols.fees,
        id: "fees",
        meta: { className: "r num" },
        cell: (c) => {
          const o = c.row.original;
          return <span title={`佣金 ${o.commission} · 印花税 ${o.stamp_tax} · 过户费 ${o.transfer_fee} · 滑点 ${o.slippage}`}>{`${o.commission} / ${o.stamp_tax} / ${o.transfer_fee} / ${o.slippage}`}</span>;
        },
      },
      { header: r.ocols.reason, id: "reason", cell: (c) => orderWhy(c.row.original) },
    ],
    [],
  );
  return (
    <Card title={r.orders} command={commandOf("runs_orders", request)}>
      <p className="lead">{r.ordersCounts(counts.total, counts.filled, counts.partial, counts.rejected)}</p>
      <p className="note">
        {r.rejectedBy}：
        {Object.entries(counts.rejected_by_reason)
          .map(([k, n]) => `${lb().of("order_reasons", k)} ${n}`)
          .join(" · ") || t.common.none}
      </p>
      <p className="note">
        {r.forced}：
        {Object.entries(counts.forced)
          .map(([k, n]) => `${lb().of("order_reasons", k)} ${n}`)
          .join(" · ") || r.noForced}
      </p>
      <div className="filters">
        <div className="seg" role="group" aria-label={r.ordersFilter}>
          {(["all", "rejected"] as const).map((s) => (
            <button
              key={s}
              type="button"
              aria-pressed={filter.status === s}
              onClick={() => {
                setFilter({ ...filter, status: s });
                setOffset(0);
              }}
            >
              {s === "all" ? r.allOrders : r.rejectedOnly}
            </button>
          ))}
        </div>
        <label className="field">
          <select value={filter.reason} onChange={(e) => setFilter({ ...filter, reason: e.target.value })} aria-label={r.ocols.reason}>
            <option value="">{r.reasonAll}</option>
            {[...Object.keys(counts.rejected_by_reason), ...Object.keys(counts.forced)].map((k) => (
              <option key={k} value={k}>
                {lb().of("order_reasons", k)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {page.error && <Problem error={page.error} />}
      <div className="cond-row">
        <span className="note">{r.ordersPage(offset + 1, Math.min(offset + ORDERS_PAGE, total), total)}</span>
        <button type="button" className="cmd" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - ORDERS_PAGE))}>
          {r.prevPage}
        </button>
        <button type="button" className="cmd" disabled={offset + ORDERS_PAGE >= total} onClick={() => setOffset(offset + ORDERS_PAGE)}>
          {r.nextPage}
        </button>
      </div>
      <div className={page.isPlaceholderData ? "pending" : undefined}>
        <VirtualTable
          rows={rows}
          columns={columns}
          rowKey={(o, i) => `${o.date}/${o.code}/${o.side}/${i}`}
          onRow={(o) => (location.hash = href.stock(o.code, { date: o.date, run: run.id, from: "run" }))}
          height={420}
          label={r.orders}
        />
      </div>
    </Card>
  );
}

export function Delisted({ run }: { run: RunDetail }) {
  const approx = run.warnings.some((w) => w.code === "delisting_approximate");
  return (
    <Card title={r.delisted}>
      {approx && <p className="approx-note">{r.delistedApprox}</p>}
      {run.delisted.length === 0 ? (
        <span className="note">{r.delistedNone}</span>
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th>{r.dcols.date}</th>
              <th>{r.dcols.code}</th>
              <th>{r.dcols.treatment}</th>
              <th className="r">{r.dcols.shares}</th>
              <th className="r">{r.dcols.value}</th>
              <th className="r">{r.dcols.cash}</th>
            </tr>
          </thead>
          <tbody>
            {run.delisted.map((d) => (
              <tr key={`${d.date}/${d.code}`} className={approx ? "approx-row" : undefined}>
                <td className="num">
                  {d.date}
                  {approx && <span className="badge approx">{t.data.delistings.approximate}</span>}
                </td>
                <td>{d.code}</td>
                <td>{lb().of("delisting_treatments", d.treatment)}</td>
                <td className="r num" title={String(d.shares)}>{shares(d.shares).text}</td>
                <td className="r num">{yuan(d.value)}</td>
                <td className="r num">{yuan(d.cash)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

export function Anomalies({ run }: { run: RunDetail }) {
  const a = run.anomalies;
  const request: Request<"runs_anomalies"> = { id: run.id, limit: 500 };
  const page = useCall("runs_anomalies", a ? request : null);
  const rows = page.data?.rows ?? a?.sample ?? [];
  return (
    <Card title={r.anomalies} command={a ? commandOf("runs_anomalies", request) : undefined}>
      <span className="note">{r.anomaliesNote}</span>
      {a ? (
        <>
          <div className="grid">
            {(["error", "suspicious", "notice"] as const).map((lv) => (
              <div key={lv} className={`stat level-${lv}`}>
                <span className="stat-l">{lb().of("anomaly_levels", lv)}</span>
                <span className="stat-v num">{a.levels[lv]}</span>
              </div>
            ))}
          </div>
          <p className="note">
            {r.anomalySummary(a.codes, a.cells, a.decisions, a.fills)} ·{" "}
            {Object.entries(a.kinds)
              .map(([k, n]) => `${lb().of("issue_kinds", k)} ${n}`)
              .join(" · ")}
          </p>
          <AnomalyTable rows={rows} />
          {page.data && <span className="note">{r.ordersShown(page.data.rows.length, page.data.total)}</span>}
        </>
      ) : (
        <span className="note">{r.anomaliesNone}</span>
      )}
    </Card>
  );
}

export function Day({ run, day, onPick }: { run: RunDetail; day: string | null; onPick: (d: string) => void }) {
  const id = run.id;
  const shown = day;
  const request: Request<"runs_day"> | null = shown ? { id, date: shown } : null;
  const q = useCall("runs_day", request);
  const [explained, setExplained] = useState<string | null>(null);
  const first = run.daily[0]?.date;
  const last = run.daily[run.daily.length - 1]?.date;
  return (
    <section className="card" aria-label={r.day} data-testid="day">
      <div className="card-head">
        <h2>{`${r.day}${shown ? ` · ${shown}` : ""}`}</h2>
        <div className="card-meta">{request && <Command command={commandOf("runs_day", request)} />}</div>
      </div>
      <div className="cond-row">
        <span className="note">{r.dayPick}</span>
        <input type="date" aria-label={r.day} value={shown ?? ""} min={first} max={last} onChange={(e) => e.target.value && onPick(e.target.value)} />
      </div>
      {q.error && <Problem error={q.error} />}
      {!shown && <span className="note">{r.dayHint}</span>}
      {q.data && (
        <>
          <span className="note">
            {r.dayEquity(yuan(q.data.equity), yuan(q.data.cash))}
            {!q.data.decision_day && ` · ${r.dayNotDecision}`}
          </span>
          <div className="row2">
            <div>
              <h3 className="sub-h">{r.dayPositions}</h3>
              <span className="note">{r.sharesNote}</span>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>{r.pcols.code}</th>
                    <th className="r">{r.pcols.shares}</th>
                    <th className="r">{r.pcols.value}</th>
                    <th className="r">{r.pcols.weight}</th>
                  </tr>
                </thead>
                <tbody>
                  {q.data.positions.map((p) => {
                    const s = shares(p.shares);
                    return (
                      <tr key={p.code}>
                        <td>
                          <a className="link" href={href.stock(p.code, { date: shown, run: id, from: "day" })}>
                            <Stock code={p.code} name={p.name} />
                          </a>
                        </td>
                        <td className="r num" title={s.title}>
                          {s.text}
                        </td>
                        <td className="r num">{yuan(p.value)}</td>
                        <td className="r num">{pct(p.weight)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {q.data.decision_day && (
              <div>
                <h3 className="sub-h">{r.dayDecisions}</h3>
                <span className="note">{r.explainHint}</span>
                <span className="note">— = {r.scoreNone}</span>
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>{r.dcols2.code}</th>
                      <th>{r.dcols2.role}</th>
                      <th className="r">{r.dcols2.rank}</th>
                      <th className="r">{r.dcols2.score}</th>
                      <th className="r">{r.dcols2.weight}</th>
                      <th>{r.dcols2.held}</th>
                      <th>{r.dcols2.action}</th>
                      <th>{r.dcols2.sell}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {q.data.decisions.map((d) => (
                      <tr key={d.code} className="clickable" aria-selected={explained === d.code || undefined} onClick={() => setExplained(d.code)}>
                        <td>
                          <span className="link"><Stock code={d.code} name={d.name} /></span>
                        </td>
                        <td>{lb().of("funnel_kinds", d.role === "target" ? "targets" : d.role === "backup" ? "backups" : "scored")}</td>
                        <td className="r num">{d.rank ?? "—"}</td>
                        <td className="r num">{d.score === null ? "—" : d.score.toFixed(3)}</td>
                        <td className="r num">{pct(d.weight)}</td>
                        <td>{d.held ? r.yes : ""}</td>
                        <td>{r.actions[d.action]}</td>
                        <td>
                          {d.sell_reason &&
                            `${lb().of("sell_kinds", d.sell_reason.kind) ?? d.sell_reason.kind}${d.sell_reason.step ? ` · ${d.sell_reason.step}` : ""}${d.sell_reason.rank !== null ? ` · ${d.sell_reason.rank}/${d.sell_reason.limit ?? ""}` : ""}`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <h3 className="sub-h">{r.dayOrders}</h3>
          {q.data.orders.length ? (
            <table className="tbl">
              <thead>
                <tr>
                  <th>{r.ocols.code}</th>
                  <th>{r.ocols.side}</th>
                  <th>{r.ocols.status}</th>
                  <th className="r">{r.ocols.requested}</th>
                  <th className="r">{r.ocols.shares}</th>
                  <th className="r">{r.ocols.price}</th>
                  <th className="r">{r.ocols.value}</th>
                  <th>{r.ocols.reason}</th>
                </tr>
              </thead>
              <tbody>
                {q.data.orders.map((o, i) => (
                  <tr key={`${o.code}/${o.side}/${i}`}>
                    <td><Stock code={o.code} name={o.name} /></td>
                    <td>{r.sides[o.side]}</td>
                    <td>{lb().of("order_statuses", o.status)}</td>
                    <td className="r num">{o.requested === null ? r.wholeHolding : o.requested.toLocaleString("zh-CN")}</td>
                    <td className="r num">{o.shares.toLocaleString("zh-CN")}</td>
                    <td className="r num">{yuan(o.price)}</td>
                    <td className="r num">{yuan(o.value)}</td>
                    <td>{orderWhy(o)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <span className="note">{t.common.none}</span>
          )}
          {explained && <RunExplain key={explained} id={id} date={shown!} code={explained} />}
        </>
      )}
    </section>
  );
}

function RunExplain({ id, date, code }: { id: string; date: string; code: string }) {
  const request: Request<"explain"> = { run: id, code, date };
  const q = useCall("explain", request);
  return (
    <div className="card inset">
      <h3 className="sub-h">
        {r.explain} · {code}
      </h3>
      {q.error ? <Problem error={q.error} /> : q.data ? <ChainView e={q.data} /> : <span className="note">{t.app.loading}</span>}
    </div>
  );
}

export function Rerun({ id }: { id: string }) {
  const rerun = useCallMutation("runs_rerun");
  const seconds = useRunningSeconds(rerun.isPending);
  const result = rerun.data;
  return (
    <Card title={r.rerun} command={commandOf("runs_rerun", { id })}>
      <span className="note">{r.rerunNote}</span>
      <div className="cond-row">
        <button type="button" className="cmd" disabled={rerun.isPending} onClick={() => rerun.mutate({ id })}>
          {rerun.isPending ? r.rerunning(seconds) : r.rerunButton}
        </button>
      </div>
      {rerun.error && <Problem error={rerun.error} />}
      {result && (
        <div data-testid="rerun">
          <p className={result.identical ? "lead" : "approx-note"}>{result.identical ? r.identical : r.different}</p>
          {Object.entries(result.differences).map(([file, what]) => (
            <div key={file} className="note mono">
              {file}: {what}
            </div>
          ))}
          {result.notes.map((n) => (
            <div key={n} className="note">
              {n}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export function Report({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const report = useCall("runs_report", open ? { id } : null);
  const text = report.data?.report ?? null;
  const copy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard unavailable: the text stays visible to copy by hand
    }
  };
  return (
    <Card title={r.report} command={commandOf("runs_report", { id })}>
      <span className="note">{r.reportNote}</span>
      <div className="cond-row">
        <button type="button" className="cmd" onClick={() => setOpen(!open)}>
          {open ? r.reportHide : r.reportShow}
        </button>
        {text && (
          <button type="button" className="cmd" onClick={copy}>
            {copied ? r.copied : r.copy}
          </button>
        )}
      </div>
      {report.error && <Problem error={report.error} />}
      {open && (text ? <pre className="code-pre report">{text}</pre> : !report.error && <span className="note">{t.app.loading}</span>)}
    </Card>
  );
}
