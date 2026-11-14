// The today page: fixed blocks top to bottom (the data status strip, followed strategies, recent backtests, data
// problems waiting for a decision). "自定义" on the status strip picks which blocks show; kept in this browser.
// Index quotes come from data index bars: the latest close, its change and the last 60 trading days.
import { Stock } from "../../components/Stock";
import { useState } from "react";
import { useCall } from "../../api/client";
import type { ScreenRow, StrategyInfo } from "../../api/types.gen";
import { StatusBar } from "../../components/StatusBar";
import { Problem } from "../../components/Problem";
import { useLabels } from "../../labels";
import { follows, todayShow, type TodayShow } from "../../local";
import { href } from "../../router";
import { cnDate, tx, weekday } from "../../text";
import { pct, tone } from "../../format";
import { st } from "../strategy/text";
import { tt } from "./text";

export function TodayPage() {
  const [custom, setCustom] = useState(false);
  const show = todayShow.use();
  const pending = useCall("data_resolve_list", { status: "proposed", limit: 3 });
  const hasIssues = (pending.data?.total ?? 0) > 0;
  const set = (k: keyof TodayShow) => todayShow.write({ ...show, [k]: !show[k] });
  const allHidden = !show.follow && !show.runs && !show.index && !(show.issues && hasIssues);
  return (
    <main className="page">
      <StatusBar
        right={
          <>
            <button type="button" className="btn soft" aria-expanded={custom} onClick={() => setCustom(!custom)}>
              {tt.custom}
            </button>
            {custom && (
              <div className="pop" role="dialog" aria-label={tt.customLabel}>
                <span className="b">{tt.showOnToday}</span>
                <span className="note">{tt.customNote}</span>
                {(
                  [
                    ["follow", tt.follow.title, ""],
                    ["runs", tt.runs.title, ""],
                    ["index", tt.index.title, ""],
                    ["issues", tt.issues.title, tt.issues.onlyWhen],
                  ] as const
                ).map(([k, label, note]) => (
                  <label key={k} className="check pop-row">
                    <input type="checkbox" checked={show[k]} onChange={() => set(k)} />
                    {label}
                    {note && <span className="push tiny muted">{note}</span>}
                  </label>
                ))}
                <button type="button" className="btn sm push" onClick={() => setCustom(false)}>
                  {tt.done}
                </button>
              </div>
            )}
          </>
        }
      />
      {show.follow && <Followed />}
      {(show.runs || show.index) && (
        <div className="split">
          {show.runs && (
            <div className="main">
              <RecentRuns />
            </div>
          )}
          {show.index && (
            <div className="aside">
              <Indexes />
            </div>
          )}
        </div>
      )}
      {show.issues && hasIssues && pending.data && (
        <section className="card" aria-label={tt.issues.title}>
          <div className="card-head">
            <h2>{tt.issues.title}</h2>
            <span className="tag acc">{tt.issues.count(pending.data.total)}</span>
            <a className="link small push" href={href.data()}>
              {tt.issues.go}
            </a>
          </div>
          <IssueRows rows={pending.data.rows} />
        </section>
      )}
      {allHidden && (
        <section className="card row" aria-label={tx.nav.today}>
          <span className="sub">{tt.allHidden}</span>
        </section>
      )}
    </main>
  );
}

function IssueRows({ rows }: { rows: { code: string; date: string; kind: string; evidence: string }[] }) {
  const L = useLabels();
  return (
    <div className="rows" style={{ gridTemplateColumns: "120px 160px minmax(0, 1fr) 90px" }}>
      {rows.map((r) => (
        <a key={`${r.code}/${r.date}/${r.kind}`} className="line" href={href.data(r.code, r.date)}>
          <span className="b">{r.code}</span>
          <span className="sub">{L.of("issue_kinds", r.kind)}</span>
          <span className="sub ellipsis">{L.help("issue_kinds", r.kind)}</span>
          <span className="r muted small">{r.date}</span>
        </a>
      ))}
    </div>
  );
}

function Followed() {
  const ids = follows.use();
  const list = useCall("strategy_list", {});
  const status = useCall("data_status", {});
  const byId = new Map((list.data?.rows ?? []).map((s) => [s.id, s]));
  const shown = ids.map((id) => byId.get(id)).filter((s): s is StrategyInfo => !!s);
  const day = status.data?.last_trade_date;
  return (
    <section className="card" aria-label={tt.follow.title}>
      <div className="card-head">
        <h2>{tt.follow.title}</h2>
        {day && <span className="small muted">{tt.follow.lead(cnDate(day), weekday(day))}</span>}
      </div>
      {list.isSuccess && shown.length === 0 && (
        <div className="empty">
          <span>{tt.follow.empty}</span>
          <a className="btn push" href={href.strategy()}>
            {tt.follow.goStrategy}
          </a>
        </div>
      )}
      {shown.length > 0 && (
        <div className="cards">
          {shown.map((s) => (
            <FollowCard key={s.id} s={s} />
          ))}
        </div>
      )}
    </section>
  );
}

function FollowCard({ s }: { s: StrategyInfo }) {
  const L = useLabels();
  const show = useCall("strategy_show", { id: s.id });
  const screen = useCall("screen", { saved: { id: s.id } });
  const rows = screen.data?.rows ?? [];
  const picked = [...rows.filter((r) => r.role === "target"), ...rows.filter((r) => r.role === "backup")].slice(0, 10);
  const n = (role: ScreenRow["role"]) => rows.filter((r) => r.role === role).length;
  return (
    <a className="inner follow-card" href={href.picks({ id: s.id })}>
      <div className="row">
        <span className="b big">{s.name}</span>
        {screen.data && <span className="push small muted">{tt.follow.counts(n("target"), n("backup"))}</span>}
      </div>
      {show.data && <span className="small sub clamp2">{show.data.summary}</span>}
      {screen.isError && <Problem error={screen.error} />}
      {screen.isPending && <span className="note">{tx.app.loading}</span>}
      {screen.data && (
        <div className="rows" style={{ gridTemplateColumns: "30px minmax(0, 1fr) 60px 52px" }}>
          <span className="head">{tt.follow.rank}</span>
          <span className="head">{tt.follow.stock}</span>
          <span className="head r">{tt.follow.score}</span>
          <span className="head r">{tt.follow.role}</span>
          {picked.map((r) => (
            <div key={r.code} className="line compact">
              <span className="num muted">{r.rank}</span>
              <span className="ellipsis"><Stock code={r.code} name={r.name} /></span>
              <span className="num r">{(r.score * 100).toFixed(1)}</span>
              <span className="r">
                <span className={`role ${r.role}`}>{L.of("funnel_kinds", r.role === "target" ? "targets" : "backups")}</span>
              </span>
            </div>
          ))}
        </div>
      )}
      <span className="link small">{tt.follow.open}</span>
    </a>
  );
}

function RecentRuns() {
  const runs = useCall("runs_list", { limit: 5 });
  const list = useCall("strategy_list", {});
  const names = new Map((list.data?.rows ?? []).map((s) => [s.id, s.name]));
  return (
    <section className="card" aria-label={tt.runs.title}>
      <div className="card-head">
        <h2>{tt.runs.title}</h2>
        <a className="link small push" href={href.backtest()}>
          {tt.runs.all}
        </a>
      </div>
      {runs.isError && <Problem error={runs.error} />}
      {runs.data && runs.data.rows.length === 0 && (
        <div className="empty">
          <span>{tt.runs.none}</span>
          <a className="btn push" href={href.backtest()}>
            {tt.runs.go}
          </a>
        </div>
      )}
      {runs.data && runs.data.rows.length > 0 && (
        <div className="rows" style={{ gridTemplateColumns: "minmax(0, 1fr) 200px 76px 76px 56px" }}>
          <span className="head">{tt.runs.strategy}</span>
          <span className="head">{tt.runs.range}</span>
          <span className="head r">{tt.runs.ann}</span>
          <span className="head r">{tt.runs.mdd}</span>
          <span className="head r">{tt.runs.sharpe}</span>
          {runs.data.rows.map((r) => {
            const id = r.saved_as[0]?.id;
            return (
              <a key={r.id} className="line" href={href.backtest({ run: r.id })}>
                <span className="b ellipsis">{(id && names.get(id)) || tt.runs.unsaved}</span>
                <span className="sub small">{tt.runs.span(r.start, r.end)}</span>
                <span className={`num r b ${r.annual_return === null ? "" : r.annual_return >= 0 ? "up" : "down"}`}>{pct(r.annual_return, 1, true)}</span>
                <span className="num r">{pct(r.max_drawdown, 1)}</span>
                <span className="num r">{r.sharpe === null ? tx.common.none : r.sharpe.toFixed(2)}</span>
              </a>
            );
          })}
        </div>
      )}
    </section>
  );
}

const INDEXES: [string, ...string[]] = ["000300.SH", "000905.SH"];

function Indexes() {
  const status = useCall("data_status", {});
  const last = status.data?.last_trade_date ?? null;
  const start = last ? new Date(Date.parse(`${last}T00:00:00Z`) - 100 * 864e5).toISOString().slice(0, 10) : null;
  const bars = useCall("data_index_bars", start ? { codes: INDEXES, start, end: last } : null);
  // Indexes the data has no quotes for are left out; with none, the block is left out.
  if (bars.data && !INDEXES.some((c) => bars.data.rows.some((r) => r.code === c))) return null;
  return (
    <section className="card" aria-label={tt.index.title}>
      <div className="card-head">
        <h2>{tt.index.title}</h2>
        {last && <span className="push small muted">{tt.index.lead(cnDate(last))}</span>}
      </div>
      {bars.isError && <Problem error={bars.error} />}
      {bars.data &&
        INDEXES.map((code) => {
          const rows = bars.data.rows.filter((r) => r.code === code).slice(-60);
          const lastRow = rows[rows.length - 1];
          if (!lastRow) return null;
          const closes = rows.map((r) => r.close);
          const lo = Math.min(...closes);
          const hi = Math.max(...closes);
          const path = closes.map((v, i) => `${i ? "L" : "M"}${((i / Math.max(1, closes.length - 1)) * 100).toFixed(1)} ${(24 - ((v - lo) / (hi - lo || 1)) * 22).toFixed(1)}`).join(" ");
          return (
            <div key={code} className="index-row">
              <span className="b">{st.scope.indexNames[code] ?? code}</span>
              <svg viewBox="0 0 100 26" preserveAspectRatio="none" aria-hidden="true" className={`spark ${tone(lastRow.change)}`}>
                <path d={path} />
              </svg>
              <span className="num r">{lastRow.close.toFixed(2)}</span>
              <span className={`num r b ${tone(lastRow.change)}`}>{pct(lastRow.change, 2, true)}</span>
            </div>
          );
        })}
    </section>
  );
}
