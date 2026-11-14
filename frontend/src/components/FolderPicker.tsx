// The folder browser ("选择文件夹"): the browser cannot see the computer's folders, so the backend lists them
// (fs_places, fs_list; folders only, never files) and makes new ones (fs_mkdir). Places on the left, the path as
// breadcrumbs (the path can also be typed), the subfolders with WeQuant data folders marked, a new-folder field and
// the free space on the disk. Used for the data folder (数据页) and by the first-run setup.
import { Fragment, useEffect, useState } from "react";
import { useCall, useCallMutation } from "../api/client";
import { size } from "../api/files";
import { crumbs } from "./paths";
import { Problem } from "./Problem";

interface Props {
  title: string;
  /** Where to open. */
  start: string;
  /** Open on start's parent, so start itself shows in the list (e.g. the current data folder). */
  openParent?: boolean;
  /** The current data folder, marked in the list. */
  current?: string | null;
  chooseLabel?: string;
  onChoose: (path: string) => void;
  onClose: () => void;
}

function FolderIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" aria-hidden="true">
      <path d="M2.5 5.5 H8 L9.5 7 H17.5 V15.5 H2.5 Z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

export function FolderPicker({ title, start, openParent = false, current = null, chooseLabel = "选择这个文件夹", onChoose, onClose }: Props) {
  const places = useCall("fs_places", {});
  const [path, setPath] = useState(start);
  const [jumped, setJumped] = useState(!openParent);
  const [typed, setTyped] = useState<string | null>(null);
  const [name, setName] = useState("");
  const list = useCall("fs_list", { path }, { staleTime: 0, placeholderData: (prev) => prev });
  const mkdir = useCallMutation("fs_mkdir", ["fs_list"]);
  const here = list.data && !list.isPlaceholderData && !list.isError ? list.data : undefined;

  useEffect(() => {
    if (jumped || !here) return;
    setJumped(true);
    if (here.parent) setPath(here.parent);
  }, [jumped, here]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const go = (p: string) => {
    setTyped(null);
    mkdir.reset();
    setPath(p);
  };
  const placeRows = places.data?.rows ?? [];
  const trail = crumbs(here?.path ?? path, placeRows);
  const inPlace = trail[0]?.path;
  const dirs = list.data?.dirs ?? [];
  // Up from a folder that could not be opened goes by the path itself.
  const up = here ? here.parent : list.isError ? (trail[trail.length - 2]?.path ?? null) : null;
  const make = async () => {
    const n = name.trim();
    if (!n || !here) return;
    const made = await mkdir.mutateAsync({ path: here.path, name: n });
    setName("");
    go(made.path);
  };

  return (
    <div className="modal-back" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <section className="modal folder-picker" role="dialog" aria-modal="true" aria-label={title}>
        <div className="card-head">
          <h2>{title}</h2>
          <button type="button" className="icon-btn push" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="row tight wrap">
          <button type="button" className="btn sm soft" aria-label="上一级" disabled={!up} onClick={() => up && go(up)}>
            ↑ 上一级
          </button>
          <nav className="crumbs" aria-label="路径">
            {trail.map((c, i) => (
              <Fragment key={c.path}>
                {i > 0 && (
                  <span className="crumb-sep" aria-hidden="true">
                    /
                  </span>
                )}
                <button type="button" className="crumb path" translate="no" aria-current={i === trail.length - 1 ? "location" : undefined} onClick={() => go(c.path)}>
                  {c.label}
                </button>
              </Fragment>
            ))}
          </nav>
        </div>
        <div className="fp-body">
          <div className="fp-places" aria-label="位置">
            <span className="tiny muted">位置</span>
            {placeRows.map((p) => (
              <button key={p.path} type="button" className="fp-place" aria-pressed={inPlace === p.path || here?.path === p.path} title={p.free_bytes !== null ? `还有 ${size(p.free_bytes)} 可用` : undefined} onClick={() => go(p.path)}>
                {p.label}
              </button>
            ))}
          </div>
          <div className="fp-list" role="list" aria-label="子文件夹" aria-busy={list.isFetching}>
            {list.isError ? (
              <Problem error={list.error} />
            ) : (
              <>
                {dirs.map((d) => (
                  <button key={d.path} type="button" role="listitem" className="fp-dir" onClick={() => go(d.path)}>
                    <FolderIcon />
                    <span className="grow path" translate="no">
                      {d.name}
                    </span>
                    {d.path === current ? (
                      <span className="tag acc">现在的数据目录</span>
                    ) : d.is_wequant_home ? (
                      <span className="tag">WeQuant 数据{d.size_bytes !== null ? ` · ${size(d.size_bytes)}` : ""}</span>
                    ) : null}
                  </button>
                ))}
                {here && dirs.length === 0 && <span className="fp-empty">这里没有子文件夹。</span>}
              </>
            )}
          </div>
        </div>
        <div className="row wrap">
          <input
            className="input"
            style={{ width: 180 }}
            aria-label="新文件夹名称"
            placeholder="新文件夹名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void make().catch(() => undefined)}
          />
          <button type="button" className="btn sm soft" disabled={!name.trim() || !here || mkdir.isPending} onClick={() => void make().catch(() => undefined)}>
            新建文件夹
          </button>
          {here && <span className="small muted">所在磁盘还有 {size(here.free_bytes)} 可用</span>}
        </div>
        {mkdir.isError && <Problem error={mkdir.error} />}
        <div className="fp-foot">
          <input
            className="input grow path"
            translate="no"
            aria-label="文件夹路径"
            title="可以直接输入或粘贴路径，按回车打开"
            value={typed ?? here?.path ?? path}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && typed?.trim()) go(typed.trim());
            }}
          />
          <button type="button" className="btn soft" onClick={onClose}>
            取消
          </button>
          <button type="button" className="btn" disabled={!here || typed !== null} onClick={() => here && onChoose(here.path)}>
            {chooseLabel}
          </button>
        </div>
      </section>
    </div>
  );
}
