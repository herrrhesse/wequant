// A background job's progress as one block: what it is, its stage in the backend's words, a bar and the percentage,
// with the buttons the caller puts under it (pause, cancel, back).
import type { ReactNode } from "react";
import type { JobInfo } from "../api/types.gen";
import { useLabels } from "../labels";

/** A duration as people say it: 约 40 分钟, 约 2.5 小时. */
export function about(seconds: number): string {
  if (seconds < 3600) return `约 ${Math.max(1, Math.round(seconds / 60))} 分钟`;
  return `约 ${Math.round((seconds / 3600) * 2) / 2} 小时`;
}

/** The years a history backfill job is filling in now, e.g. "2017–2019 年"; "" when it has not said. */
export function fillYears(job: JobInfo): string {
  const { first_year, last_year } = job.params;
  return first_year && last_year ? `${first_year}–${last_year} 年` : "";
}

export function JobBar({ job, label, children }: { job: JobInfo; label: string; children?: ReactNode }) {
  const L = useLabels();
  const pct = Math.round(job.fraction * 100);
  return (
    <div className="inner job-bar" role="status" aria-label={label}>
      <div className="row">
        <span className="b">{label}</span>
        <span className="num b push">{pct}%</span>
      </div>
      <span className="bar" aria-hidden="true">
        <span style={{ width: `${pct}%` }} />
      </span>
      <span className="small sub">
        {L.stage(job.stage, job.params) || job.text}
        {job.eta_seconds != null && job.eta_seconds > 0 ? `，预计还要${about(job.eta_seconds)}` : ""}
      </span>
      {children && <div className="row">{children}</div>}
    </div>
  );
}
