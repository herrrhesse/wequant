// 版本历史 and 存储 (round 2, chunk 4): versions with their reasons, pin, export and "use this version"; import a
// version file; storage usage by part, cleanup, the run archive limit and how many versions to keep.
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useCall, useCallMutation } from "../../api/client";
import { exportFile, importFile, size } from "../../api/files";
import { FileButton } from "../../components/FileButton";
import { NumInput } from "../../components/NumInput";
import { Problem } from "../../components/Problem";
import { localTime } from "../../format";
import { useLabels } from "../../labels";

export function Versions() {
  const L = useLabels();
  const client = useQueryClient();
  const status = useCall("data_status", {});
  const settings = useCall("settings_show", {});
  const pin = useCallMutation("data_pin_version", ["settings_show", "data_status"]);
  const use = useCallMutation("data_use_version", []);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const s = status.data;
  const pinned = new Set(settings.data?.pinned_versions ?? []);
  const run = async (fn: () => Promise<unknown>, done: string) => {
    setErr(null);
    try {
      await fn();
      setNote(done);
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <section className="card versions" aria-label="版本历史">
      <div className="card-head">
        <h2>版本历史</h2>
        <span className="push" />
        <FileButton
          accept=".wqver"
          onFile={(f) =>
            void run(async () => {
              const r = await importFile("data_import", f);
              await client.invalidateQueries({ queryKey: ["data_status"] });
              setNote(`已导入版本 ${r.data_version_name}`);
            }, "")
          }
        >
          从文件导入版本
        </FileButton>
      </div>
      <span className="small muted">
        版本号是数据的最新交易日加当天第几版，例如 20261107.2 是数据到 2026 年 11 月 7 日的第 2 版。自动保留最近 {settings.data?.keep_versions ?? "—"} 个版本，另外保留固定的版本。切换到旧版本后，选股、回测和个股都按它计算，顶部会一直提示。
      </span>
      {s?.version_list.map((v) => {
        const active = v.id === s.version_in_use;
        const newest = v.id === s.current_version;
        const isPinned = pinned.has(v.id) || v.pinned;
        return (
          <div key={v.id} className="inner version-row">
            <div className="row">
              <span className="num b big">{v.name}</span>
              {newest && <span className="tag acc">最新</span>}
              {active && <span className="tag">正在使用</span>}
              {isPinned && <span className="tag">已固定</span>}
            </div>
            <span className="small sub">
              {L.reason(v.reason_code, v.reason_params) || v.reason} · 生成于 {localTime(v.created_at)}
            </span>
            <div className="row">
              <button type="button" className="btn sm soft" disabled={pin.isPending} onClick={() => void run(() => pin.mutateAsync({ version: v.id, pinned: !isPinned }), isPinned ? "已取消固定" : "已固定，不会被自动清理")}>
                {isPinned ? "取消固定" : "固定"}
              </button>
              <button type="button" className="btn sm soft" onClick={() => void run(() => exportFile("data_export", { data_version: v.id }), "已导出，浏览器开始下载")}>
                导出
              </button>
              <button
                type="button"
                className="btn sm"
                disabled={active || use.isPending}
                onClick={() =>
                  void run(async () => {
                    await use.mutateAsync({ version: newest ? null : v.id });
                    await client.invalidateQueries();
                  }, newest ? "已回到最新版本" : `已切换到 ${v.name}`)
                }
              >
                切换到这个版本使用
              </button>
            </div>
          </div>
        );
      })}
      {note && (
        <span role="status" className="small ok b">
          {note}
        </span>
      )}
      {err !== null && <Problem error={err} />}
    </section>
  );
}

export function Storage() {
  const usage = useCall("storage_usage", {}, { staleTime: 0 });
  const settings = useCall("settings_show", {});
  const update = useCallMutation("settings_update", ["settings_show", "storage_usage"]);
  const cleanup = useCallMutation("storage_cleanup", ["storage_usage", "runs_list", "data_status"]);
  const preview = useCall("storage_cleanup", { dry_run: true }, { staleTime: 0 });
  const u = usage.data;
  const total = u ? u.current + u.versions + u.cache + u.runs : null;
  const parts = u
    ? [
        { label: "当前数据", n: u.current, note: "", color: "var(--inv)" },
        { label: "旧版本", n: u.versions, note: `可清理 ${size(u.free_versions)}`, color: "var(--acc)" },
        { label: "缓存", n: u.cache, note: `可清理 ${size(u.free_cache)}`, color: "var(--warn)" },
        { label: "回测存档", n: u.runs, note: `上限 ${size(u.runs_limit)} · 可清理 ${size(u.free_runs)}`, color: "var(--track)" },
      ]
    : [];
  return (
    <section className="card storage" aria-label="存储">
      <div className="card-head">
        <h2>存储</h2>
        {/* Until the usage is counted there is no total to show (not 0). */}
        <span className="small muted">{usage.isPending ? "正在统计" : `共 ${size(total)}`}</span>
      </div>
      {u && (
        <>
          <div className="stack-bar" aria-hidden="true">
            {parts.map((p) => (
              <span key={p.label} style={{ flex: Math.max(p.n, 1), background: p.color }} title={`${p.label} ${size(p.n)}`} />
            ))}
          </div>
          {parts.map((p) => (
            <div key={p.label} className="row small">
              <span className="swatch" style={{ background: p.color }} />
              <span>{p.label}</span>
              <span className="tiny muted">{p.note}</span>
              <span className="push num">{size(p.n)}</span>
            </div>
          ))}
          <div className="row">
            <button type="button" className="btn sm soft" disabled={cleanup.isPending || !preview.data || preview.data.before.free === 0} onClick={() => cleanup.mutate({ dry_run: false })}>
              清理可清理的部分{preview.data ? `（约 ${size(preview.data.before.free)}）` : ""}
            </button>
            {cleanup.data && (
              <span role="status" className="small ok b">
                已清理：删除 {cleanup.data.files_removed} 个文件、{cleanup.data.runs_removed.length} 个回测存档
              </span>
            )}
          </div>
        </>
      )}
      {settings.data && (
        <div className="row">
          <label className="row small">
            回测存档上限（GB）
            <NumInput aria-label="回测存档上限" style={{ width: 70 }} value={settings.data.runs_limit_gb} onValue={(v) => v !== null && v > 0 && update.mutate({ runs_limit_gb: v })} />
          </label>
          <label className="row small">
            数据版本保留个数
            <NumInput aria-label="数据版本保留个数" style={{ width: 60 }} value={settings.data.keep_versions} onValue={(v) => v !== null && v >= 1 && update.mutate({ keep_versions: Math.round(v) })} />
          </label>
        </div>
      )}
      <span className="note">回测存档超过上限时，自动删除最旧的未保留存档；保留的存档和固定的版本不会被自动删除。</span>
      {(usage.isError || update.isError || cleanup.isError) && <Problem error={usage.error ?? update.error ?? cleanup.error} />}
    </section>
  );
}
