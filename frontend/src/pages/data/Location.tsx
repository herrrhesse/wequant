// 存储位置: the data folder's path and the free space on its disk; "更改位置…" browses for a folder, then copies (the
// default; the old folder stays as it is) or moves the data there (a background job with progress and cancel; the
// backend checks the folder and the space and switches only after the copy checks out) or switches to a WeQuant data
// folder already there. After a switch every page reads the new folder at once: all cached answers are dropped. The
// old folder of a copy is the user's to delete: no delete button here.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useCall, useCallMutation } from "../../api/client";
import { size } from "../../api/files";
import { cancelJob, finished, useJob } from "../../api/jobs";
import type { JobInfo, Relocated } from "../../api/types.gen";
import { FolderPicker, useFolder } from "../../components/FolderPicker";
import { join } from "../../components/paths";
import { Problem } from "../../components/Problem";
import { fillYears, JobBar } from "../../components/JobBar";
import { useLabels } from "../../labels";
import { href } from "../../router";

type Mode = "move" | "copy" | "use";
const MODES: { mode: Mode; label: string; desc: string; go: string }[] = [
  { mode: "copy", label: "复制过去，旧位置保留", desc: "核对无误后才切换；旧位置的数据原样留着，需要再占一份空间。", go: "开始复制" },
  { mode: "move", label: "把现有数据移过去", desc: "核对无误后才切换，旧位置随后清空。", go: "开始移动" },
  { mode: "use", label: "直接使用那里已有的数据", desc: "不动现在的数据，切换后读那个文件夹里的。", go: "切换过去" },
];
/** The folder made for the data when the chosen folder already has other folders in it. */
const SUB = "wequant-data";

export function Location({ calendar, first }: { calendar?: React.ReactNode; first?: string | null }) {
  const L = useLabels();
  const client = useQueryClient();
  const status = useCall("data_status", {});
  const home = status.data?.home ?? null;
  const here = useCall("fs_list", home ? { path: home } : null);
  const usage = useCall("storage_usage", {});
  const jobs = useCall("jobs_list", {}, { staleTime: 0 });
  const running = jobs.data?.jobs.find((j) => j.kind === "data_relocate" && !finished(j)) ?? null;
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId ?? running?.id ?? null);
  const result = useCall("jobs_result", job?.state === "succeeded" && job.kind === "data_relocate" ? { id: job.id } : null);
  const moved = result.data && "previous" in result.data ? (result.data as Relocated) : null;
  // After a copy, the old folder as the backend sees it now (its size, with the cache that was not copied).
  const old = useFolder(moved?.mode === "copy" ? moved.previous : null);
  const begin = useCallMutation("jobs_start", ["jobs_list"]);
  const useHome = useCallMutation("data_use_home");
  const [picking, setPicking] = useState(false);
  const [chosen, setChosen] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode | null>(null);
  const [done, setDone] = useState("");

  // A finished move or copy switched the folder: every page reads the new one.
  const switched = job?.state === "succeeded" && job.kind === "data_relocate";
  useEffect(() => {
    if (switched) void client.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "jobs_result" });
  }, [switched, client]);

  const at = here.data?.path ?? home ?? "";
  // What a move or copy takes: the cache is not copied (it is rebuilt when used).
  const total = usage.data ? usage.data.current + usage.data.versions + usage.data.runs : null;
  const active = !!job && !finished(job);
  const busy = active || begin.isPending || useHome.isPending;

  return (
    <section className="card" aria-label="存储位置">
      <div className="card-head wrap">
        <h2>存储位置</h2>
        <span className="loc-path path" translate="no">
          {at}
        </span>
        <span className="small muted">
          {usage.data ? `数据、版本和回测存档共 ${size(total)} · 缓存 ${size(usage.data.cache)}` : ""}
          {here.data ? ` · 所在磁盘还有 ${size(here.data.free_bytes)} 可用` : ""}
        </span>
        <button
          type="button"
          className="btn push"
          disabled={busy || !home || chosen !== null}
          onClick={() => {
            setDone("");
            setPicking(true);
          }}
        >
          更改位置…
        </button>
      </div>
      {chosen !== null && !active && (
        <Confirm
          chosen={chosen}
          current={at}
          total={total}
          mode={mode}
          setMode={setMode}
          busy={busy}
          error={begin.error ?? useHome.error}
          onBack={() => {
            setChosen(null);
            setPicking(true);
          }}
          onCancel={() => {
            setChosen(null);
            begin.reset();
            useHome.reset();
          }}
          onGo={async (m, to) => {
            if (m === "use") {
              const r = await useHome.mutateAsync({ path: to });
              setChosen(null);
              setDone(`已切换到 ${r.home}`);
              void client.invalidateQueries();
              return;
            }
            const j = await begin.mutateAsync({ kind: "data_relocate", request: { to, mode: m } });
            setChosen(null);
            setJobId(j.id);
          }}
        />
      )}
      {job && <JobLine job={job} moved={moved} home={at} left={old.entry?.size_bytes ?? null} />}
      {done && (
        <span role="status" className="small ok b">
          {done}
        </span>
      )}
      {job?.state === "failed" && job.error && (
        <div className="problem" role="alert">
          <strong>没有完成，数据仍在原来的位置</strong>
          <ul className="serrors">
            {job.error.detail?.length ? job.error.detail.map((d, i) => <li key={i}>{L.detail(d)}</li>) : <li>{L.stage(job.stage, job.params)}</li>}
          </ul>
        </div>
      )}
      <History first={first ?? null} />
      <div className="loc-foot">
        {calendar}
        <a className="small b push" href={href.setup()}>
          重新运行首次启动向导 ›
        </a>
      </div>
      {picking && home && (
        <FolderPicker
          title="选择数据存放的文件夹"
          start={at || home}
          openParent
          current={at}
          onClose={() => setPicking(false)}
          onChoose={(p) => {
            setPicking(false);
            setJobId(null);
            setDone("");
            setMode(null);
            begin.reset();
            useHome.reset();
            setChosen(p);
          }}
        />
      )}
    </section>
  );
}

function Confirm(props: {
  chosen: string;
  current: string;
  total: number | null;
  mode: Mode | null;
  setMode: (m: Mode) => void;
  busy: boolean;
  error: unknown;
  onBack: () => void;
  onCancel: () => void;
  onGo: (m: Mode, to: string) => Promise<void>;
}) {
  const { chosen, current, total, busy } = props;
  const inside = useCall("fs_list", { path: chosen }, { staleTime: 0 });
  const parent = inside.data?.parent ?? null;
  const above = useCall("fs_list", parent ? { path: parent } : null, { staleTime: 0 });
  const entry = above.data?.dirs.find((d) => d.path === inside.data?.path);
  const ready = !!inside.data && (!parent || !!above.data);
  const isCurrent = inside.data?.path === current;
  const isHome = !!entry?.is_wequant_home;
  // Moving or copying needs an empty or new folder: a folder with other folders in it gets one made for the data.
  const dest = inside.data && inside.data.dirs.length > 0 ? join(chosen, SUB) : chosen;
  const allowed = (m: Mode) => ready && !isCurrent && (m === "use" ? isHome : !isHome);
  const mode = props.mode && allowed(props.mode) ? props.mode : isHome ? "use" : "copy";
  const to = mode === "use" ? chosen : dest;
  const note = !ready
    ? "正在查看这个文件夹…"
    : isCurrent
      ? "这就是现在的数据目录。"
      : isHome
        ? `这个文件夹里已经有一份 WeQuant 数据${entry?.size_bytes != null ? `（${size(entry.size_bytes)}）` : ""}，可以直接改用它。`
        : `需要约 ${size(total)}（缓存不复制，用到时会重算），这个位置所在的磁盘还有 ${size(inside.data!.free_bytes)} 可用。${dest !== chosen ? `这个文件夹里已有别的文件夹，数据会放进里面新建的 ${SUB} 文件夹。` : ""}`;
  return (
    <div className="inner loc-confirm">
      <span className="b">
        把数据放到{" "}
        <span className="path" translate="no">
          {to}
        </span>
      </span>
      <span className="small sub">{note}</span>
      {inside.isError && <Problem error={inside.error} />}
      <div role="radiogroup" aria-label="怎么处理现有数据" className="col tight">
        {MODES.map((m) => (
          <label key={m.mode} className={`loc-mode${mode === m.mode ? " on" : ""}${allowed(m.mode) ? "" : " off"}`}>
            <input type="radio" name="loc-mode" checked={mode === m.mode} disabled={!allowed(m.mode)} onChange={() => props.setMode(m.mode)} />
            <span className="col">
              <span className="small b">{m.label}</span>
              <span className="tiny muted">{ready && !isCurrent && !allowed(m.mode) ? (m.mode === "use" ? "这个文件夹里没有 WeQuant 数据。" : "那里已经有一份数据，只能直接改用。") : m.desc}</span>
            </span>
          </label>
        ))}
      </div>
      {props.error !== null && props.error !== undefined && <Problem error={props.error} />}
      <div className="row">
        <button type="button" className="btn soft" onClick={props.onBack}>
          ‹ 重新选位置
        </button>
        <button type="button" className="btn soft" onClick={props.onCancel}>
          取消
        </button>
        <button type="button" className="btn acc" disabled={busy || !ready || isCurrent} onClick={() => void props.onGo(mode, to).catch(() => undefined)}>
          {MODES.find((m) => m.mode === mode)!.go}
        </button>
      </div>
    </div>
  );
}

function JobLine({ job, moved, home, left }: { job: JobInfo; moved: Relocated | null; home: string; left: number | null }) {
  const L = useLabels();
  const verb = (job.request.mode ?? moved?.mode) === "copy" ? "复制" : "移动";
  if (!finished(job))
    return (
      <div className="inner loc-progress" role="status" aria-label={`正在${verb}数据`}>
        <span className="bar" aria-hidden="true">
          <span style={{ width: `${Math.round(job.fraction * 100)}%` }} />
        </span>
        <span className="small sub">
          {L.stage(job.stage, job.params)} · {Math.round(job.fraction * 100)}%。期间不能更新数据和回测。
        </span>
        <button type="button" className="btn sm soft push" onClick={() => void cancelJob(job.id)}>
          取消
        </button>
      </div>
    );
  if (job.state === "cancelled")
    return (
      <span role="status" className="small b">
        已取消，数据仍在 <span className="path">{home}</span>
      </span>
    );
  if (job.state === "succeeded" && moved)
    return (
      <div role="status" className="col tight">
        <span className="small ok b">
          数据已{verb}到 <span className="path">{moved.home}</span>（{moved.files} 个文件，{size(moved.bytes)}
          {moved.renamed ? "，同一块磁盘上直接改了位置" : ""}）。所有页面已经换成这里的数据。
        </span>
        {moved.mode === "copy" && (
          <span className="small sub">
            旧位置 <span className="path" translate="no">{moved.previous}</span> 的数据还在，没有动过
            {left !== null ? `，约占 ${size(left)}` : ""}。确认新位置一切正常后，可以自己删掉旧文件夹。
          </span>
        )}
        {moved.left_behind.length > 0 && (
          <span className="small warn-text">
            旧位置还有 {moved.left_behind.length} 个文件没能删除，可以之后手动删除：
            <span className="path">{moved.left_behind.slice(0, 5).join("、")}</span>
          </span>
        )}
      </div>
    );
  return null;
}

/** The earlier history (back to 2000) filling in behind: its progress and pause, or a button to start or carry on. */
function History({ first }: { first: string | null }) {
  const jobs = useCall("jobs_list", {}, { staleTime: 0 });
  const running = jobs.data?.jobs.find((j) => j.kind === "data_backfill" && !finished(j)) ?? null;
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId ?? running?.id ?? null);
  const begin = useCallMutation("jobs_start", ["jobs_list"]);
  const active = !!job && !finished(job);
  const missing = !!first && first.slice(0, 4) > "2000";
  // Each segment is a new data version: when the job ends, every page reads the newest.
  const client = useQueryClient();
  const done = job?.state === "succeeded" || job?.state === "cancelled";
  useEffect(() => {
    if (done) void client.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "jobs_result" });
  }, [done, client]);
  if (!active && !missing && job?.state !== "succeeded") return null;
  return (
    <div className="col tight">
      {job && active ? (
        <JobBar job={job} label={`后台补齐更早的历史（到 2000 年）${fillYears(job) ? ` · 正在补 ${fillYears(job)}` : ""}`}>
          <button type="button" className="btn sm soft" onClick={() => void cancelJob(job.id)}>
            暂停
          </button>
        </JobBar>
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
