// 首次启动向导: a data folder without data leads here from every page. Where the data goes and where it comes from
// (a BaoStock build of the last 1, 3 or 5 years with the earlier history filled in behind it, the user's own files,
// or a WeQuant data folder already on the computer), then the steps of that source, then ready (start from a
// template). Every step can go back; the steps done stay clickable at the top. Going back from a build pauses it
// (the backend keeps what it fetched and carries on when started again); going back from an import stops it with
// nothing written. The backend remembers the step, so a closed browser comes back where it was.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useCall, useCallMutation } from "../../api/client";
import { cancelJob, finished, useJob } from "../../api/jobs";
import type { BuildResult, JobInfo, OwnImported, SetupSaveRequest, SetupStatus } from "../../api/types.gen";
import { Problem } from "../../components/Problem";
import { useLabels } from "../../labels";
import { href } from "../../router";
import { DEFAULT_PICK, type Pick, pickOf, scopeOf } from "./Scope";
import { BuildStep, HistoryStep, ImportCheckStep, ImportFolderStep, ImportStep, ReadyStep, SourceStep } from "./steps";
import type { DataScope } from "../../api/types.gen";

export type Source = "baostock" | "import" | "existing";
export type Step = "source" | "build" | "history" | "import_folder" | "import_check" | "import" | "ready";
export type Years = 1 | 3 | 5;

const STEPS: Step[] = ["source", "build", "history", "import_folder", "import_check", "import", "ready"];
export const SOURCE_NAMES: Record<Source, string> = { baostock: "用 BaoStock 新建", import: "导入自己的数据", existing: "使用已有的数据" };

function flowOf(source: Source, backfill: boolean): Step[] {
  if (source === "baostock") return backfill ? ["source", "build", "history", "ready"] : ["source", "build", "ready"];
  if (source === "import") return ["source", "import_folder", "import_check", "import", "ready"];
  return ["source", "ready"];
}

function stepName(step: Step, years: Years): string {
  return {
    source: "位置和来源",
    build: `建最近 ${years} 年`,
    history: "补全更早的历史",
    import_folder: "选择文件夹",
    import_check: "检查",
    import: "导入",
    ready: "准备好了",
  }[step];
}

export function SetupPage() {
  const status = useCall("setup_status", {});
  return (
    <main className="page">
      {status.isError && <Problem error={status.error} />}
      {status.data && <Wizard status={status.data} />}
    </main>
  );
}

export interface Ctx {
  status: SetupStatus;
  source: Source;
  years: Years;
  backfill: boolean;
  folder: string;
  importPath: string;
  /** Which stocks a BaoStock build downloads. */
  pick: Pick;
  /** The build's request: the years and the stocks chosen. */
  buildRequest: () => Record<string, unknown>;
  job: JobInfo | null;
  go: (next: Step, patch?: SetupSaveRequest) => void;
  start: (kind: "data_build" | "data_backfill" | "data_own_import", request: Record<string, unknown>, step: Step, patch?: SetupSaveRequest) => Promise<void>;
  /** Pause or stop the job of this step, if it runs. */
  stop: () => void;
}

function Wizard({ status }: { status: SetupStatus }) {
  const client = useQueryClient();
  const saved = status.setup;
  const savedStep = STEPS.includes(saved.step as Step) ? (saved.step as Step) : "source";
  const [source, setSource] = useState<Source>(saved.source ?? (status.has_data ? "existing" : "baostock"));
  const [years, setYears] = useState<Years>(saved.years ?? 3);
  const [backfill, setBackfill] = useState(saved.backfill ?? true);
  const [folder, setFolder] = useState(saved.home ?? status.home);
  const [importPath, setImportPath] = useState(saved.import_path ?? "");
  const [step, setStep] = useState<Step>(savedStep);
  const [jobId, setJobId] = useState<number | null>(saved.job_id ?? null);
  const [pick, setPick] = useState<Pick>(DEFAULT_PICK);
  const flow = flowOf(source, backfill);
  const [reached, setReached] = useState(Math.max(0, flow.indexOf(savedStep)));
  const save = useCallMutation("setup_save");
  const begin = useCallMutation("jobs_start", ["jobs_list"]);
  const job = useJob(jobId);
  const at = Math.max(0, flow.indexOf(step));
  // After a reload the stocks chosen come back from the build's own request (the backend remembers the job).
  const restored = useRef(false);
  useEffect(() => {
    if (restored.current || !job) return;
    restored.current = true;
    const back = job.kind === "data_build" ? pickOf(job.request.scope as DataScope | undefined, job.request.pool_name as string | null | undefined) : null;
    if (back) setPick(back);
  }, [job]);
  const buildRequest = () => ({ years, scope: scopeOf(pick), pool_name: pick.choice === "stocks" ? pick.poolName.trim() : null });

  const go = (next: Step, patch: SetupSaveRequest = {}) => {
    setStep(next);
    setReached((r) => Math.max(r, flow.indexOf(next)));
    save.mutate({ step: next, ...patch });
  };
  const start: Ctx["start"] = async (kind, request, next, patch = {}) => {
    const j = await begin.mutateAsync({ kind, request });
    setJobId(j.id);
    go(next, { ...patch, job_id: j.id });
  };
  const running = !!job && !finished(job);
  const stop = () => {
    if (job && running) void cancelJob(job.id);
  };
  // Leaving a step: a build pauses, an import stops; the history keeps running behind.
  const leave = (to: Step) => {
    if (step === "build" || step === "import") stop();
    go(to);
  };

  // A finished build or import: everything reads the new data; a build goes on to the history unless turned off.
  const handled = useRef<number | null>(null);
  const result = useCall("jobs_result", job?.state === "succeeded" ? { id: job.id } : null);
  useEffect(() => {
    if (!job || job.state !== "succeeded" || !result.data || handled.current === job.id) return;
    handled.current = job.id;
    void client.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "jobs_result" && q.queryKey[0] !== "setup_status" });
    void client.invalidateQueries({ queryKey: ["setup_status"] });
    if (job.kind === "data_build" && step === "build") {
      const built = result.data as BuildResult;
      if (built.status !== "built" && built.status !== "exists") return;
      if (backfill) void start("data_backfill", {}, "history");
      else go("ready");
    }
  }, [job, result.data, step, backfill, client]); // eslint-disable-line react-hooks/exhaustive-deps

  const ctx: Ctx = { status, source, years, backfill, folder, importPath, pick, buildRequest, job, go, start, stop };
  const back = () => leave(flow[Math.max(0, at - 1)]!);

  return (
    <section className="card setup" aria-label="首次启动">
      <div className="col tight">
        <span className="small muted">首次启动 · {SOURCE_NAMES[source]}</span>
        <h1 className="setup-title">先把本地数据准备好</h1>
        <span className="small sub">数据存在你自己的电脑上。可以用 BaoStock 新建，也可以导入你已有的数据，或者直接用电脑上已有的 WeQuant 数据；任何一步都可以回到前面改。</span>
        {status.has_data && step !== "ready" && (
          <span className="small note">
            现在的数据文件夹里已经有数据，重新运行向导不会删除它。<a href={href.data()}>回到数据页 ›</a>
          </span>
        )}
      </div>
      <ol className="setup-steps" aria-label="步骤">
        {flow.map((s, i) => (
          <li key={s}>
            <button type="button" disabled={i > reached || i === at} aria-current={i === at ? "step" : undefined} className={i < at ? "done" : ""} onClick={() => leave(s)}>
              <span className="setup-dot">{i < at ? "✓" : i + 1}</span>
              {stepName(s, years)}
            </button>
          </li>
        ))}
      </ol>
      {begin.isError && <Problem error={begin.error} />}
      {step === "source" && (
        <SourceStep
          ctx={ctx}
          setSource={(s) => {
            setSource(s);
            setReached(0);
          }}
          setYears={setYears}
          setBackfill={setBackfill}
          setFolder={setFolder}
          setPick={setPick}
          busy={begin.isPending}
        />
      )}
      {step === "build" && <BuildStep ctx={ctx} back={back} result={(result.data as BuildResult | undefined) ?? null} />}
      {step === "history" && <HistoryStep ctx={ctx} back={back} />}
      {step === "import_folder" && <ImportFolderStep ctx={ctx} back={back} setImportPath={setImportPath} />}
      {step === "import_check" && <ImportCheckStep ctx={ctx} back={back} busy={begin.isPending} />}
      {step === "import" && <ImportStep ctx={ctx} back={back} result={(result.data as OwnImported | undefined) ?? null} />}
      {step === "ready" && <ReadyStep ctx={ctx} back={back} />}
    </section>
  );
}

/** A job's failure in the backend's Chinese words. */
export function JobFailure({ job }: { job: JobInfo }) {
  const L = useLabels();
  return (
    <div className="problem" role="alert">
      <strong>没有完成</strong>
      <ul className="serrors">{job.error?.detail?.length ? job.error.detail.map((d, i) => <li key={i}>{L.detail(d)}</li>) : <li>{L.stage(job.stage, job.params) || job.text}</li>}</ul>
    </div>
  );
}
