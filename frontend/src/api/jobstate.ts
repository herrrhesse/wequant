// What a background job's state means for the page: whether it is still going (progress and "取消" show) and
// whether a new one can be started (the start button is enabled again once the job has ended in any way).
import type { JobInfo } from "./types.gen";

export interface JobView {
  /** Queued or running: show progress and cancel. */
  active: boolean;
  /** Nothing in progress: the start button can be used. */
  canStart: boolean;
  /** Ended by the user's cancel. */
  cancelled: boolean;
}

export function jobView(job: Pick<JobInfo, "state"> | null | undefined): JobView {
  const active = !!job && (job.state === "queued" || job.state === "running");
  return { active, canStart: !active, cancelled: job?.state === "cancelled" };
}
