// 回测, top to bottom: "新建回测" (strategy, dates, cash and benchmark filled with what will be used; the strategy
// page's "回测" button opens it with that strategy chosen), the card of a running backtest (read from the backend's
// job list, so it survives leaving the page or reloading), the comparison of ticked runs, the shown run's results,
// and the run records at the bottom.
import { useEffect, useMemo, useRef, useState } from "react";
import { commandOf, modelDefaults, useCall, useCallMutation } from "../../api/client";
import { INDEX_CHOICES, indexName } from "../../components/IndexSeg";
import { cancelJob, finished, useJob } from "../../api/jobs";
import { jobView } from "../../api/jobstate";
import type { DataSpec, ErrorBody, ExecutionSpec, JobInfo, RunRow } from "../../api/types.gen";
import { Problem } from "../../components/Problem";
import { StatusBar } from "../../components/StatusBar";
import { useElapsed } from "../../hooks";
import { useLabels, type LabelBook } from "../../labels";
import { href, type Route } from "../../router";
import { draftGap, readPageDraft } from "../../strategy/pageDraft";
import { useUi } from "../../ui";
import { ResultView } from "./ResultView";
import { Compare, RunList, type NameOf } from "./RunList";

type BacktestRoute = Extract<Route, { page: "backtest" }>;
const DRAFT_NAME = "策略页的草稿";
const DRAFT_KEY = "__draft";

export function BacktestPage({ route }: { route: BacktestRoute }) {
  const runs = useCall("runs_list", { limit: 100 });
  const list = useCall("strategy_list", {});
  const names = useMemo(() => new Map((list.data?.rows ?? []).map((s) => [s.id, s.name])), [list.data]);
  const nameOf: NameOf = (r) => (r.saved_as[0] && names.get(r.saved_as[0].id)) || "未保存的策略";
  const [started, setStarted] = useState<number | null>(null);
  // The draft's content hash tells which runs were made from it (runs record the hash of what they ran).
  const draft = readPageDraft();
  // A draft not complete yet is not sent (the backend would refuse it); it has no runs either.
  const draftCheck = useCall("strategy_validate", draft && !draftGap(draft) ? { strategy: draft.spec } : null);
  const draftHash = draftCheck.data?.strategy_hash ?? null;
  const rows = runs.data?.rows ?? [];
  const keyOf = (r: RunRow): string | null => r.saved_as[0]?.id ?? (draftHash && r.strategy_hash === draftHash ? DRAFT_KEY : null);
  // The chosen strategy: as opened (?use), else the strategy of the run shown, else of the newest run.
  const shownRun = route.run ? rows.find((r) => r.id === route.run) : rows[0];
  const chosen =
    (route.use === "draft" ? (draft ? DRAFT_KEY : null) : route.use) ?? (shownRun ? keyOf(shownRun) : null) ?? list.data?.rows[0]?.id ?? (draft ? DRAFT_KEY : null);
  // The result shown: the run asked for, else the chosen strategy's latest run.
  const latest = chosen ? rows.find((r) => (chosen === DRAFT_KEY ? !!draftHash && r.strategy_hash === draftHash : r.saved_as.some((x) => x.id === chosen))) : undefined;
  const id = route.run ?? latest?.id ?? null;
  const choose = (key: string) => (location.hash = href.backtest({ use: key === DRAFT_KEY ? "draft" : key }));
  return (
    <main className="page">
      <StatusBar />
      <NewBacktest use={route.use} chosen={chosen ?? ""} onChoose={choose} onStarted={setStarted} />
      <Running started={started} names={names} />
      {runs.isError && <Problem error={runs.error} />}
      {route.cmp.length >= 2 && <Compare ids={route.cmp} nameOf={nameOf} />}
      {id && <ResultView key={id} id={id} day={route.day} nameOf={nameOf} />}
      {!id && runs.isSuccess && (
        <section className="card" aria-label="回测结果">
          <div className="empty">{chosen ? "这个策略还没有回测过，设好日期和资金后点开始回测。" : "还没有策略。先在策略页写一个策略，再回来回测。"}</div>
          {!chosen && (
            <a className="btn" style={{ alignSelf: "flex-start" }} href={href.newStrategy()}>
              去策略页新建
            </a>
          )}
        </section>
      )}
      {runs.data && <RunList rows={runs.data.rows} selected={id} cmp={route.cmp} nameOf={nameOf} />}
    </main>
  );
}

function NewBacktest({ use, chosen, onChoose, onStarted }: { use: string | null; chosen: string; onChoose: (key: string) => void; onStarted: (id: number) => void }) {
  const L = useLabels();
  const ui = useUi();
  const defaults = modelDefaults("BacktestRequest");
  const list = useCall("strategy_list", {});
  const calendar = useCall("data_calendar", {});
  const draft = readPageDraft();
  const gap = chosen === "__draft" ? draftGap(draft) : null;
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [cash, setCash] = useState(String(defaults.cash ?? ""));
  const [benchmark, setBenchmark] = useState(typeof defaults.benchmark === "string" ? defaults.benchmark : "");
  const [execution, setExecution] = useState<ExecutionSpec>({ ...(defaults.execution as ExecutionSpec) });
  const [data] = useState<DataSpec>({ ...(defaults.data as DataSpec) });
  const [adv, setAdv] = useState(false);
  const begin = useCallMutation("jobs_start", ["jobs_list"]);
  const box = useRef<HTMLElement>(null);
  const go = useRef<HTMLButtonElement>(null);
  // Opened from the strategy page: bring the form into view with the start button focused (once, on opening).
  const opened = useRef(use);
  useEffect(() => {
    if (!opened.current) return;
    box.current?.scrollIntoView({ block: "start" });
    go.current?.focus({ preventScroll: true });
  }, []);
  const first = calendar.data?.first ?? "";
  const last = calendar.data?.last ?? "";
  const from = start || first;
  const to = end || last;
  const subject = chosen === "__draft" ? (draft && !gap ? { strategy: draft.spec } : null) : chosen ? { saved: { id: chosen } } : null;
  const request = subject && from && to ? { ...subject, start: from, end: to, cash: Number(cash), execution, data, benchmark: benchmark || null, save: true } : null;
  const ex = (k: keyof ExecutionSpec, v: unknown) => setExecution({ ...execution, [k]: v });
  return (
    <section ref={box} className="card new-backtest" aria-label="新建回测">
      <div className="card-head">
        <h2>新建回测</h2>
      </div>
      <div className="row picks-bar">
        <label className="field">
          策略
          <select className="select" aria-label="策略" value={chosen} onChange={(e) => onChoose(e.target.value)}>
            {draft && (
              <option value="__draft">
                {DRAFT_NAME}：{draft.name}
              </option>
            )}
            {(list.data?.rows ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          开始日
          <input className="input" type="date" aria-label="开始日" value={from} min={first || undefined} max={to || undefined} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="field">
          结束日
          <input className="input" type="date" aria-label="结束日" value={to} min={from || undefined} max={last || undefined} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <label className="field">
          初始资金（元）
          <input className="input num" inputMode="decimal" aria-label="初始资金" value={cash} onChange={(e) => setCash(e.target.value)} />
        </label>
        <label className="field">
          基准
          <select className="select" aria-label="基准" value={benchmark} onChange={(e) => setBenchmark(e.target.value)}>
            <option value="">不设基准</option>
            {[...INDEX_CHOICES.filter((c): c is string => c !== null), ...(benchmark && !INDEX_CHOICES.includes(benchmark) ? [benchmark] : [])].map((c) => (
              <option key={c} value={c}>
                {indexName(c)}
              </option>
            ))}
          </select>
        </label>
        <button
          ref={go}
          type="button"
          className="btn acc push"
          disabled={!request || begin.isPending}
          onClick={async () => {
            if (!request) return;
            const j = await begin.mutateAsync({ kind: "backtest", request: request as never });
            onStarted(j.id);
          }}
        >
          开始回测
        </button>
      </div>
      {gap ? (
        <span className="row small" role="note" aria-label="为什么不能回测">
          <span className="warn-text">
            <b>{gap.title}</b>：{gap.text}
          </span>
          <a className="b" href={gap.fix}>
            去策略页 ›
          </a>
        </span>
      ) : (
        chosen === "__draft" && <span className="small warn-text">未保存的改动也会用于这次回测。</span>
      )}
      <span className="small sub">
        默认用全部数据（{first || "—"} 至 {last || "—"}）。决策日收盘后选股，下一交易日按{execution.price === "close" ? "收盘价" : "开盘价"}成交；涨跌停、停牌、不足一手的订单不成交。
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

/** The name of the strategy a backtest job runs. */
function jobName(job: JobInfo, names: Map<string, string>): string {
  const saved = (job.request as { saved?: { id?: string } }).saved;
  if (saved?.id) return names.get(saved.id) ?? "已保存的策略";
  return DRAFT_NAME;
}

/** Why a job failed, in words a user reads: the error's Chinese labels, else the stage sentence. */
function failure(L: LabelBook, job: JobInfo): string {
  const e = job.error as ErrorBody | null;
  const details = (e?.detail ?? []).map((d) => L.detail(d)).filter(Boolean);
  if (details.length) return details.join("；");
  return (e && L.help("exits", e.name)) || L.stage(job.stage, job.params) || "回测没有完成";
}

/** The backtest job in progress (or just ended): progress, time used and cancel; when it succeeds, its result opens. */
function Running({ started, names }: { started: number | null; names: Map<string, string> }) {
  const L = useLabels();
  const jobs = useCall("jobs_list", {}, { staleTime: 0 });
  const live = jobs.data?.jobs.find((j) => j.kind === "backtest" && !finished(j)) ?? null;
  // Keep following the job seen running here, so its end (cancelled, failed) stays on the card.
  const [followed, setFollowed] = useState<number | null>(null);
  useEffect(() => {
    if (live) setFollowed(live.id);
  }, [live]);
  const id = live?.id ?? started ?? followed;
  const job = useJob(id);
  const view = jobView(job);
  const since = job?.started_at ? Date.parse(job.started_at) : job?.created_at ? Date.parse(job.created_at) : null;
  const seconds = useElapsed(view.active ? since : null);
  const result = useCall("jobs_result", job?.state === "succeeded" && job.kind === "backtest" ? { id: job.id } : null);
  const opened = useRef<number | null>(null);
  useEffect(() => {
    const out = result.data;
    if (!out || !("run_id" in out) || !job || opened.current === job.id) return;
    opened.current = job.id;
    const saved = (job.request as { saved?: { id?: string } }).saved?.id;
    location.hash = href.backtest({ run: out.run_id, use: saved ?? "draft" });
  }, [result.data, job]);
  if (!job || job.kind !== "backtest" || job.state === "succeeded") return null;
  const name = jobName(job, names);
  return (
    <section className={`card running ${job.state}`} aria-label="正在回测">
      {view.active ? (
        <>
          <div className="row">
            <span className="b big">正在回测：{name}</span>
            <span className="small sub">{L.stage(job.stage, job.params)}</span>
            <span className="push small muted num">已用 {seconds} 秒</span>
            <button type="button" className="btn sm ghost" onClick={() => void cancelJob(job.id)}>
              取消
            </button>
          </div>
          <div className="progress">
            <span style={{ width: `${Math.round(job.fraction * 100)}%` }} />
          </div>
        </>
      ) : job.state === "cancelled" ? (
        <span className="b">已取消：{name}。没有留下结果。</span>
      ) : (
        <div className="col tight">
          <span className="b">回测没有完成：{name}</span>
          <span className="small">{failure(L, job)}</span>
        </div>
      )}
    </section>
  );
}
