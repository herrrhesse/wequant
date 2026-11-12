// Background jobs (wequant serve, round 2): start one, follow its progress over the event stream
// (GET /api/jobs/{id}/events, one `job` event per change, closed after the final state), cancel it.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { call } from "./http";
import type { JobInfo } from "./types.gen";

export const finished = (j: JobInfo | null | undefined) => !!j && (j.state === "succeeded" || j.state === "failed" || j.state === "cancelled");

/** The job's latest state: the stream while it runs, polling jobs_show if the stream cannot open. */
export function useJob(id: number | null): JobInfo | null {
  const [job, setJob] = useState<JobInfo | null>(null);
  const client = useQueryClient();
  useEffect(() => {
    setJob(null);
    if (id === null) return;
    let closed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const done = (j: JobInfo) => {
      if (finished(j)) {
        void client.invalidateQueries({ queryKey: ["runs_list"] });
        void client.invalidateQueries({ queryKey: ["jobs_list"] });
      }
    };
    const poll = () => {
      if (closed) return;
      call("jobs_show", { id }).then(
        (j) => {
          if (closed) return;
          setJob(j);
          done(j);
          if (!finished(j)) timer = setTimeout(poll, 800);
        },
        () => {
          if (!closed) timer = setTimeout(poll, 1500);
        },
      );
    };
    let source: EventSource | null = null;
    try {
      source = new EventSource(`/api/jobs/${id}/events`);
      source.addEventListener("job", (e) => {
        const j = JSON.parse((e as MessageEvent<string>).data) as JobInfo;
        setJob(j);
        done(j);
        if (finished(j)) source?.close();
      });
      source.onerror = () => {
        source?.close();
        poll();
      };
    } catch {
      poll();
    }
    return () => {
      closed = true;
      source?.close();
      clearTimeout(timer);
    };
  }, [id, client]);
  return job;
}

export const cancelJob = (id: number) => call("jobs_cancel", { id });
