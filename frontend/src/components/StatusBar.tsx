// The data status strip under the top bar, on every page: the local latest trading day, the data version's display
// name, how many data problems wait for a decision and "更新到今天" (a background job with progress and cancel).
// The button shows while the latest local day is before today and the last update did not say "已是最新".
// The old-version notice arrives with version switching (round 2, chunk 4).
import { jobView } from "../api/jobstate";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useCall, useCallMutation } from "../api/client";
import { cancelJob, finished, useJob } from "../api/jobs";
import type { UpdateResult } from "../api/types.gen";
import { useLabels, type LabelBook } from "../labels";
import { href } from "../router";
import { tx, weekday } from "../text";

/** Today's date where the market is (Asia/Shanghai), as YYYY-MM-DD. */
function today(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date());
}

/** The data version an update last found up to date (kept for this visit). */
let upToDateAt: number | null = null;

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
  const job = useJob(jobId ?? running?.id ?? null);
  const result = useCall("jobs_result", job?.state === "succeeded" && job.kind === "data_update" ? { id: job.id } : null);
  const out = result.data && "status" in result.data ? (result.data as UpdateResult) : null;
  const begin = useCallMutation("jobs_start", ["jobs_list"]);
  const [, rerender] = useState(0);
  useEffect(() => {
    if (!out) return;
    if (out.status === "up_to_date") upToDateAt = out.data_version;
    void client.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "jobs_result" });
    rerender((n) => n + 1);
  }, [out, client]);
  const s = status.data;
  const active = jobView(job).active;
  const canUpdate = !!s && !active && !!s.last_trade_date && s.last_trade_date < today() && upToDateAt !== s.data_version;
  return (
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
        {!active && out && <span className={out.ok ? "small ok" : "small warn-text"}>{outcome(L, out)}</span>}
        {!active && job && job.kind === "data_update" && job.state === "failed" && <span className="small warn-text">{L.stage(job.stage, job.params)}</span>}
        {canUpdate && (
          <button
            type="button"
            className="btn acc"
            disabled={begin.isPending}
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
  );
}
