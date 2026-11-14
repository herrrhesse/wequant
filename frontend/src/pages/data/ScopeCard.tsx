// 数据范围: which stocks the data holds and from when (the backend's layers, e.g. 沪深全部 from 2023-11, 沪深300 +
// 中证500 from 2005-04) and how many have daily bars. Widening it ("扩大数据范围…") picks what to add, with the
// backend's count and time for each choice, and runs in the background (status bar and here; pause and carry on:
// what was committed stays). Going further back in time is the history backfill, which stops, with the backend's
// reason, where an index's member list ends.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useCall, useCallMutation } from "../../api/client";
import { cancelJob, finished, useJob } from "../../api/jobs";
import { callS211, type DataScope, type ExtendEstimate, SCOPE_INDEXES, scopeStatus } from "../../api/scope";
import type { JobInfo } from "../../api/types.gen";
import { useScopes } from "../../components/IndexSeg";
import { about, fillYears, JobBar } from "../../components/JobBar";
import { Problem } from "../../components/Problem";
import { show } from "../../format";
import { useLabels } from "../../labels";
import { DEFAULT_PICK, OwnList, type Pick, scopeOf } from "../setup/Scope";

/** The label of a running data_extend or data_backfill job: what it fills in and the years it is at now. */
export function fillLabel(job: JobInfo): string {
  const years = fillYears(job);
  if (job.kind === "data_extend") return `扩大数据范围${job.params.name ? `：${job.params.name}` : ""}${years ? ` · 正在补 ${years}` : ""}`;
  return `后台补齐更早的历史（到 2000 年）${years ? ` · 正在补 ${years}` : ""}`;
}

const month = (d: string) => d.slice(0, 7);

function useExtendEstimate(scope: DataScope | null) {
  return useQuery({
    queryKey: ["data_extend_estimate", scope] as const,
    queryFn: ({ signal }) => callS211<ExtendEstimate>("data_extend_estimate", { scope }, signal),
    enabled: scope !== null,
  });
}

export function ScopeCard({ first, open }: { first: string | null; open?: boolean }) {
  const status = useCall("data_status", {});
  const scope = scopeStatus(status.data);
  const jobs = useCall("jobs_list", {}, { staleTime: 0 });
  const running = jobs.data?.jobs.find((j) => (j.kind as string) === "data_extend" && !finished(j)) ?? null;
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId ?? running?.id ?? null);
  const [choosing, setChoosing] = useState(!!open);
  const begin = useCallMutation("jobs_start", ["jobs_list"]);
  const ref = useRef<HTMLElement>(null);
  const client = useQueryClient();
  const active = !!job && !finished(job);
  // Each committed segment is a new data version: when the job ends, every page reads the newest.
  const ended = job?.state === "succeeded" || job?.state === "cancelled";
  useEffect(() => {
    if (ended) void client.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "jobs_result" });
  }, [ended, client]);
  useEffect(() => {
    if (open) ref.current?.scrollIntoView({ block: "start" });
  }, [open]);
  // An extension to carry on: the one paused here (its own request), or a wider layer that does not reach back to the
  // first day yet (paused earlier, or stopped).
  const layers = scope?.layers ?? [];
  const paused = job?.state === "cancelled" ? (job.request.scope as DataScope | undefined) : undefined;
  const wider = layers.length > 1 ? layers[0]! : null;
  const resume: DataScope | null = active ? null : (paused ?? (wider ? { all: !!wider.all, indexes: wider.indexes ?? [], stocks: wider.stocks ?? [] } : null));
  const start = async (s: DataScope, poolName: string | null) => {
    const j = await begin.mutateAsync({ kind: "data_extend" as JobInfo["kind"], request: { scope: s, pool_name: poolName } } as never);
    setChoosing(false);
    setJobId(j.id);
  };

  return (
    <section className="card" aria-label="数据范围" ref={ref}>
      <div className="card-head wrap">
        <h2>数据范围</h2>
        {scope && (
          <span className="small sub">
            {layers.length === 1 ? `${layers[0]!.label} · ${month(layers[0]!.since)} 起` : ""}
            {scope.stock_count > 0 ? `${layers.length === 1 ? " · " : ""}有日线的股票 ${show(scope.stock_count)} 只` : ""}
          </span>
        )}
        <button type="button" className="btn push" disabled={!scope || active || choosing || begin.isPending} onClick={() => setChoosing(true)}>
          扩大数据范围…
        </button>
      </div>
      {layers.length > 1 && (
        <div className="scope-layers" aria-label="各部分的起始日">
          {layers.map((l) => (
            <span key={l.label + l.since} className="row tight small">
              <span className="b">{l.label}</span>
              <span className="sub">{l.since} 起</span>
            </span>
          ))}
        </div>
      )}
      {status.isError && <Problem error={status.error} />}
      {choosing && scope && <Extend since={scope.first_date} busy={begin.isPending} error={begin.error} onStart={start} onCancel={() => { setChoosing(false); begin.reset(); }} />}
      {job && active && (
        <JobBar job={job} label={fillLabel(job)}>
          <button type="button" className="btn sm soft" onClick={() => void cancelJob(job.id)}>
            暂停
          </button>
          <span className="small muted">暂停后已补上的部分保留，随时可以接着补。</span>
        </JobBar>
      )}
      {resume && !choosing && (
        <div className="row">
          <span className="small sub">
            {paused ? "扩大数据范围已暂停，已经补上的部分保留。" : ""}
            {wider ? `${wider.label}已补到 ${wider.since}，更早的部分还没补。` : ""}
          </span>
          <button type="button" className="btn sm soft" disabled={begin.isPending} onClick={() => void start(resume, (job?.request.pool_name as string | null | undefined) ?? null).catch(() => undefined)}>
            继续扩大
          </button>
        </div>
      )}
      {job?.state === "succeeded" && !wider && <span className="small ok b">数据范围已经扩大，所有页面已经换成新的数据。</span>}
      {job?.state === "failed" && <JobProblem job={job} />}
      <History first={scope?.first_date ?? first} floor={scope?.backfill_floor ?? null} />
    </section>
  );
}

function JobProblem({ job }: { job: JobInfo }) {
  const L = useLabels();
  return (
    <div className="problem" role="alert">
      <strong>没有完成，已经补上的部分保留</strong>
      <ul className="serrors">{job.error?.detail?.length ? job.error.detail.map((d, i) => <li key={i}>{L.detail(d)}</li>) : <li>{L.stage(job.stage, job.params) || job.text}</li>}</ul>
    </div>
  );
}

/** Choosing what to add: each choice with the backend's count of new stocks and time, or 已在数据里. */
function Extend({ since, busy, error, onStart, onCancel }: { since: string | null; busy: boolean; error: unknown; onStart: (s: DataScope, poolName: string | null) => Promise<void>; onCancel: () => void }) {
  const { name } = useScopes();
  const [pick, setPick] = useState<Pick>({ ...DEFAULT_PICK, choice: "all", poolName: "扩大范围名单" });
  const all = useExtendEstimate({ all: true, indexes: [], stocks: [] });
  const hs300 = useExtendEstimate({ all: false, indexes: ["000300.SH"], stocks: [] });
  const zz500 = useExtendEstimate({ all: false, indexes: ["000905.SH"], stocks: [] });
  const byIndex: Record<string, ExtendEstimate | undefined> = { "000300.SH": hs300.data, "000905.SH": zz500.data };
  const scope = scopeOf(pick);
  const total = useExtendEstimate(scope);
  const cost = (e: ExtendEstimate | undefined) => (!e ? " " : e.covered ? "已在数据里" : `新增约 ${show(e.stocks)} 只 · 预计${about(e.seconds)}`);
  const toggle = (ix: string) => {
    const on = pick.choice === "indexes" ? pick.indexes : [];
    setPick({ ...pick, choice: "indexes", indexes: on.includes(ix) ? on.filter((i) => i !== ix) : [...on, ix] });
  };
  const problem = all.error ?? hs300.error ?? zz500.error ?? total.error;
  return (
    <div className="inner col" aria-label="扩大数据范围">
      <span className="b">要补进来哪些股票</span>
      <div className="list-grid setup-sources scope-cards" role="group" aria-label="要补进来的股票">
        <button type="button" className="inner tpl-card" aria-pressed={pick.choice === "all"} disabled={all.data?.covered} onClick={() => setPick({ ...pick, choice: "all" })}>
          <span className="b">{name(null)}</span>
          <span className="small sub">沪深两市的全部股票</span>
          <span className="small scope-cost">{cost(all.data)}</span>
        </button>
        {SCOPE_INDEXES.map((ix) => (
          <button key={ix} type="button" className="inner tpl-card" aria-pressed={pick.choice === "indexes" && pick.indexes.includes(ix)} disabled={byIndex[ix]?.covered} onClick={() => toggle(ix)}>
            <span className="b">{name(ix)}</span>
            <span className="small sub">数据期间当过成分股的全部股票</span>
            <span className="small scope-cost">{cost(byIndex[ix])}</span>
          </button>
        ))}
        <button type="button" className="inner tpl-card" aria-pressed={pick.choice === "stocks"} onClick={() => setPick({ ...pick, choice: "stocks" })}>
          <span className="b">{name("stocks")}</span>
          <span className="small sub">粘贴或导入一份名单</span>
          <span className="small scope-cost">{pick.list.length ? cost(total.data) : "还没有名单"}</span>
        </button>
      </div>
      {pick.choice === "stocks" && <OwnList pick={pick} setPick={setPick} />}
      {problem && <Problem error={problem} />}
      {total.data && scope && (
        <span className="small sub" role="status" aria-label="扩大范围的预计耗时">
          {total.data.covered
            ? "选的股票都已经在数据里，不用再下载。"
            : `新增约 ${show(total.data.stocks)} 只股票，补到 ${total.data.since ?? since}，预计${about(total.data.seconds)}。从最近的年份往前补，每补完 3 年就能用上。`}
        </span>
      )}
      {error !== null && error !== undefined && <Problem error={error} />}
      <div className="row">
        <button type="button" className="btn soft" onClick={onCancel}>
          取消
        </button>
        <button
          type="button"
          className="btn acc"
          disabled={!scope || !total.data || total.data.covered || busy || (pick.choice === "stocks" && !pick.poolName.trim())}
          onClick={() => scope && void onStart(scope, pick.choice === "stocks" ? pick.poolName.trim() : null).catch(() => undefined)}
        >
          开始扩大
        </button>
      </div>
    </div>
  );
}

/** The earlier history (back to 2000) filling in behind: its progress and pause, or a button to start or carry on; where
 * an index's member list ends, the backend's reason instead. */
function History({ first, floor }: { first: string | null; floor: { index: string; name: string; date: string } | null }) {
  const L = useLabels();
  const jobs = useCall("jobs_list", {}, { staleTime: 0 });
  const running = jobs.data?.jobs.find((j) => j.kind === "data_backfill" && !finished(j)) ?? null;
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId ?? running?.id ?? null);
  const begin = useCallMutation("jobs_start", ["jobs_list"]);
  const active = !!job && !finished(job);
  const missing = !!first && first.slice(0, 4) > "2000" && !floor;
  const client = useQueryClient();
  const done = job?.state === "succeeded" || job?.state === "cancelled";
  useEffect(() => {
    if (done) void client.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "jobs_result" });
  }, [done, client]);
  if (!active && !missing && !floor && job?.state !== "succeeded") return null;
  return (
    <div className="col tight">
      {job && active ? (
        <JobBar job={job} label={fillLabel(job)}>
          <button type="button" className="btn sm soft" onClick={() => void cancelJob(job.id)}>
            暂停
          </button>
        </JobBar>
      ) : floor ? (
        <span className="small sub" role="note" aria-label="补齐历史停在这里">
          数据从 {first} 开始。{L.stage("backfill_floor", floor)}
        </span>
      ) : (
        <div className="row">
          <span className="small sub">
            {job?.state === "succeeded" ? "更早的历史已经补齐。" : `数据从 ${first} 开始。${job?.state === "cancelled" ? "补齐已暂停，已经补上的部分保留。" : ""}`}
          </span>
          {missing && (
            <button
              type="button"
              className="btn sm soft"
              disabled={begin.isPending}
              onClick={async () => {
                const j = await begin.mutateAsync({ kind: "data_backfill", request: {} });
                setJobId(j.id);
              }}
            >
              {job?.state === "cancelled" ? "继续补齐" : "补齐更早的历史（到 2000 年）"}
            </button>
          )}
        </div>
      )}
      {begin.isError && <Problem error={begin.error} />}
    </div>
  );
}
