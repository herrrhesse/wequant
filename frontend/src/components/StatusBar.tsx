// The data status strip under the top bar, on every page: the local latest trading day, the data version's display
// name, how many data problems wait for a decision and "更新到今天" (a background job with progress and cancel).
// The button shows only when the backend says an update is due (data status update_due). While an older data
// version is in use, a notice above the strip says so and goes back to the newest in one click.
import { jobView } from "../api/jobstate";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useCall, useCallMutation } from "../api/client";
import { cancelJob, finished, useJob } from "../api/jobs";
import { fillYears } from "./JobBar";
import type { UpdateResult } from "../api/types.gen";
import { useLabels, type LabelBook } from "../labels";
import { href } from "../router";
import { tx, weekday } from "../text";

function outcome(L: LabelBook, r: UpdateResult): string {
  return L.updateStatus(r.status, { days: r.days.length, last_day: r.days[r.days.length - 1] ?? null, waiting: r.waiting, proposed: r.proposed, unresolved: r.unresolved });
}

export function StatusBar({ right }: { right?: ReactNode }) {
  const L = useLabels();
  const client = useQueryClient();
  const status = useCall("data_status", {});
  const pending = useCall("data_resolve_list", { status: "proposed", limit: 1 });
  const jobs = useCall("jobs_list", {}, { staleTime: 0 });
  const running = jobs.data?.jobs.find((j) => j.kind === "data_update" && !finished(j)) ?? null;
  const [jobId, setJobId] = useState<number | null>(null);
  // The earlier history filling in behind (after a first build, or started on the data page).
  const filling = jobs.data?.jobs.find((j) => j.kind === "data_backfill" && !finished(j)) ?? null;
  const fill = useJob(filling?.id ?? null);
  const job = useJob(jobId ?? running?.id ?? null);
  const result = useCall("jobs_result", job?.state === "succeeded" && job.kind === "data_update" ? { id: job.id } : null);
  const out = result.data && "status" in result.data ? (result.data as UpdateResult) : null;
  const begin = useCallMutation("jobs_start", ["jobs_list"]);
  const [, rerender] = useState(0);
  useEffect(() => {
    if (!out) return;
    void client.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "jobs_result" });
    rerender((n) => n + 1);
  }, [out, client]);
  const s = status.data;
  const active = jobView(job).active;
  const canUpdate = !!s && !active && s.update_due === true;
  const back = useCallMutation("data_use_version", []);
  return (
    <>
    {s?.using_old_version && (
      <section className="old-version" aria-label="正在使用旧版本">
        <strong>正在使用旧的数据版本 {s.version_list.find((v) => v.id === s.version_in_use)?.name ?? s.data_version_name}</strong>
        <span className="small sub">选股、回测和个股都按这个版本计算。最新版本是 {s.version_list.find((v) => v.id === s.current_version)?.name ?? ""}。</span>
        <button
          type="button"
          className="btn push"
          disabled={back.isPending}
          onClick={async () => {
            await back.mutateAsync({ version: null });
            void client.invalidateQueries();
          }}
        >
          回到最新版本
        </button>
      </section>
    )}
    <section className="statusbar" aria-label={tx.status.label}>
      {s ? (
        <span className="status-day">
          <span className={active ? "dot warn" : "dot ok"} />
          {tx.status.latest} <b>{s.last_trade_date ?? tx.common.none}</b>
          {s.last_trade_date && `（${weekday(s.last_trade_date)}）`}
          <span className="tag">{tx.status.version(s.data_version_name)}</span>
        </span>
      ) : (
        <span className="status-day muted">{tx.app.loading}</span>
      )}
      {pending.data && (
        <a className={pending.data.total > 0 ? "link small" : "small sub"} href={href.data()}>
          {tx.status.pending(pending.data.total)}
        </a>
      )}
      <div className="status-right">
        {active && job && (
          <>
            <span className="small sub">正在更新 · {L.stage(job.stage, job.params)}</span>
            <span className="progress status-progress">
              <span style={{ width: `${Math.round(job.fraction * 100)}%` }} />
            </span>
            <span className="num small b">{Math.round(job.fraction * 100)}%</span>
            <button type="button" className="btn sm ghost" onClick={() => void cancelJob(job.id)}>
              取消
            </button>
          </>
        )}
        {fill && !finished(fill) && (
          <span className="row nowrap" role="status" aria-label="后台补齐历史">
            <span className="small sub">
              后台补齐历史{fillYears(fill) ? ` ${fillYears(fill)}` : ""} · {L.stage(fill.stage, fill.params) || fill.text}
            </span>
            <span className="progress status-progress">
              <span style={{ width: `${Math.round(fill.fraction * 100)}%` }} />
            </span>
            <span className="num small b">{Math.round(fill.fraction * 100)}%</span>
            <button type="button" className="btn sm ghost" onClick={() => void cancelJob(fill.id)}>
              暂停
            </button>
          </span>
        )}
        {!active && out && <span className={out.ok ? "small ok" : "small warn-text"}>{outcome(L, out)}</span>}
        {!active && job && job.kind === "data_update" && job.state === "failed" && <span className="small warn-text">{L.stage(job.stage, job.params)}</span>}
        {canUpdate && (
          <button
            type="button"
            className="btn acc"
            disabled={begin.isPending || (!!fill && !finished(fill))}
            title={fill && !finished(fill) ? "后台正在补齐历史，BaoStock 一次只允许一个连接；暂停补齐后再更新" : undefined}
            onClick={async () => {
              const j = await begin.mutateAsync({ kind: "data_update", request: {} });
              setJobId(j.id);
            }}
          >
            更新到今天
          </button>
        )}
        {right}
      </div>
    </section>
    </>
  );
}
