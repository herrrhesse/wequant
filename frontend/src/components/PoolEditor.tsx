// The stock pool editor: a name and a fixed list of stocks. Stocks come in by search, by pasting a list, from a
// CSV/TXT file, or from an index's current members; the backend parses every list and the items it cannot read are
// listed with the reason (never dropped silently). Selected rows can be removed, the list cleared or exported.
import { useState } from "react";
import { useCall, useCallMutation } from "../api/client";
import { call } from "../api/http";
import type { PoolOut, PoolStock, UnparsedItem } from "../api/types.gen";
import { useDebounced } from "../hooks";
import { useLabels } from "../labels";
import { FileButton } from "./FileButton";
import { Problem } from "./Problem";

const INDEXES = ["000300.SH", "000905.SH"] as const;

/** A text file chosen by the user, read in the page. */
function readText(file: File): Promise<string> {
  return new Promise((ok, fail) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result ?? ""));
    r.onerror = () => fail(r.error);
    r.readAsText(file);
  });
}

/** A CSV the browser saves as a file. */
function save(fileName: string, csv: string) {
  const url = URL.createObjectURL(new Blob(["﻿", csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function PoolEditor({ id, onClose, onSaved }: { id: string | null; onClose: () => void; onSaved: (p: PoolOut) => void }) {
  const L = useLabels();
  const loaded = useCall("pools_show", id ? { id } : null);
  const scopes = useCall("labels", {}).data?.scopes ?? [];
  const [name, setName] = useState<string | null>(null);
  const [stocks, setStocks] = useState<PoolStock[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [unparsed, setUnparsed] = useState<UnparsedItem[]>([]);
  const [paste, setPaste] = useState("");
  const [pasting, setPasting] = useState(false);
  const [q, setQ] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const query = useDebounced(q.trim(), 200);
  const found = useCall("data_securities", query ? { query, limit: 8 } : null);
  const saveIt = useCallMutation("pools_save", ["pools_list", "pools_show", "strategy_validate"]);
  const remove = useCallMutation("pools_delete", ["pools_list"]);

  const list = stocks ?? loaded.data?.stocks ?? [];
  const title = name ?? loaded.data?.name ?? "";
  const add = (more: PoolStock[], what: string) => {
    const have = new Set(list.map((s) => s.code));
    const fresh = more.filter((s) => !have.has(s.code));
    setStocks([...list, ...fresh]);
    setNote(`${what}：加入 ${fresh.length} 只${more.length > fresh.length ? `，${more.length - fresh.length} 只已在名单里` : ""}`);
  };
  const parse = async (text: string, what: string) => {
    setErr(null);
    try {
      const r = await call("pools_parse", { text });
      add(r.stocks, what);
      setUnparsed(r.unparsed);
    } catch (e) {
      setErr(e);
    }
  };
  const fromIndex = async (index: (typeof INDEXES)[number]) => {
    setErr(null);
    try {
      const r = await call("pools_from_index", { index });
      add(r.stocks, `${scopes.find((s) => s.code === index)?.label ?? index}（成分日期 ${r.date}）`);
    } catch (e) {
      setErr(e);
    }
  };
  // Header lines (代码, 名称) are skipped, not failures: they are listed apart, in grey.
  const skipped = unparsed.filter((u) => u.reason === "header");
  const failed = unparsed.filter((u) => u.reason !== "header");
  const allPicked = list.length > 0 && list.every((s) => picked.has(s.code));
  const loading = !!id && loaded.isPending;

  return (
    <div className="modal-back" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <section className="modal pool-editor" role="dialog" aria-modal="true" aria-label="编辑股票池">
        <div className="card-head">
          <h2>{id ? "编辑股票池" : "新建股票池"}</h2>
          <button type="button" className="icon-btn push" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </div>
        {loading ? (
          <span className="note">正在读取…</span>
        ) : (
          <>
            <label className="row small">
              名称
              <input className="input grow" aria-label="股票池名称" placeholder="例如：我的自选" value={title} onChange={(e) => setName(e.target.value)} />
            </label>
            <div className="pool-tools">
              <div className="search pool-search">
                <input className="input" type="search" aria-label="添加股票" placeholder="添加：输入代码或名称" value={q} onChange={(e) => setQ(e.target.value)} />
                {query && found.data && (
                  <div className="search-pop" role="listbox" aria-label="添加股票">
                    {found.data.rows.map((r) => (
                      <button
                        key={r.code}
                        type="button"
                        role="option"
                        aria-selected={false}
                        onClick={() => {
                          add([{ code: r.code, name: r.name }], "添加");
                          setQ("");
                        }}
                      >
                        <span>
                          {r.name && <b>{r.name} </b>}
                          <span className="muted small">{r.code}</span>
                        </span>
                        {list.some((s) => s.code === r.code) && <span className="tiny muted">已在名单里</span>}
                      </button>
                    ))}
                    {found.data.rows.length === 0 && <span className="search-none">没有匹配的股票</span>}
                  </div>
                )}
              </div>
              <button type="button" className="btn soft" aria-expanded={pasting} onClick={() => setPasting(!pasting)}>
                粘贴一批
              </button>
              <FileButton accept=".csv,.txt,text/csv,text/plain" onFile={(f) => void readText(f).then((t) => parse(t, `导入 ${f.name}`), setErr)}>
                导入文件
              </FileButton>
              {INDEXES.map((ix) => (
                <button key={ix} type="button" className="btn soft" onClick={() => void fromIndex(ix)}>
                  从{scopes.find((s) => s.code === ix)?.label ?? ix}添加
                </button>
              ))}
            </div>
            {pasting && (
              <div className="col tight">
                <textarea className="input pool-paste" aria-label="粘贴股票清单" rows={4} placeholder="每行或用逗号、空格分开都可以，代码和名称混着写也行，例如：600519 招商银行 000001.SZ" value={paste} onChange={(e) => setPaste(e.target.value)} />
                <button
                  type="button"
                  className="btn sm"
                  style={{ alignSelf: "flex-start" }}
                  disabled={!paste.trim()}
                  onClick={() =>
                    void parse(paste, "粘贴").then(() => {
                      setPaste("");
                      setPasting(false);
                    })
                  }
                >
                  加入名单
                </button>
              </div>
            )}
            {note && (
              <span role="status" className="small ok b">
                {note}
              </span>
            )}
            {skipped.length > 0 && <span className="small muted">跳过了表头：{skipped.map((u) => u.item).join("、")}</span>}
            {failed.length > 0 && (
              <div className="inner unparsed" aria-label="没能识别的项">
                <div className="row small">
                  <span className="b warn-text">有 {failed.length} 项没能识别，没有加入名单</span>
                  <button type="button" className="btn sm ghost push" onClick={() => setUnparsed([])}>
                    知道了
                  </button>
                </div>
                {failed.map((u, i) => (
                  <div key={`${u.item}/${i}`} className="row small">
                    <b>{u.item}</b>
                    <span className="sub">
                      {L.of("parse_reasons", u.reason)}：{L.help("parse_reasons", u.reason)}
                    </span>
                    {u.candidates.map((c) => (
                      <button key={c.code} type="button" className="btn sm soft" onClick={() => add([c], "添加")}>
                        加入 {c.name ?? ""} {c.code}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            )}
            <div className="row small">
              <span className="b">共 {list.length} 只</span>
              {picked.size > 0 && <span className="muted">已选 {picked.size} 只</span>}
              <button
                type="button"
                className="btn sm soft push"
                disabled={picked.size === 0}
                onClick={() => {
                  setStocks(list.filter((s) => !picked.has(s.code)));
                  setNote(`删除 ${picked.size} 只`);
                  setPicked(new Set());
                }}
              >
                删除选中
              </button>
              <button
                type="button"
                className="btn sm soft"
                disabled={list.length === 0}
                onClick={() => {
                  setStocks([]);
                  setPicked(new Set());
                  setNote("已清空");
                }}
              >
                清空
              </button>
              {id && (
                <button
                  type="button"
                  className="btn sm soft"
                  onClick={() =>
                    void call("pools_export", { id }).then(
                      (r) => save(r.file_name, r.csv),
                      (e) => setErr(e),
                    )
                  }
                >
                  导出 CSV
                </button>
              )}
            </div>
            <div className="tbl-wrap pool-table">
              <table className="tbl" aria-label="股票池名单">
                <thead>
                  <tr>
                    <th>
                      <input type="checkbox" aria-label="全选" checked={allPicked} onChange={() => setPicked(allPicked ? new Set() : new Set(list.map((s) => s.code)))} />
                    </th>
                    <th>代码</th>
                    <th>名称</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((s) => (
                    <tr key={s.code} className="clickable" aria-selected={picked.has(s.code)} onClick={() => {
                      const next = new Set(picked);
                      if (next.has(s.code)) next.delete(s.code);
                      else next.add(s.code);
                      setPicked(next);
                    }}>
                      <td>
                        <input type="checkbox" aria-label={`选中 ${s.code}`} checked={picked.has(s.code)} readOnly />
                      </td>
                      <td className="num">{s.code}</td>
                      <td>{s.name ?? <span className="muted">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {list.length === 0 && <div className="empty">名单还是空的。用上面的搜索、粘贴、导入文件或从指数添加。</div>}
            </div>
            {loaded.data && loaded.data.missing.length > 0 && <span className="small warn-text">名单里有 {loaded.data.missing.length} 只股票在当前数据里找不到：{loaded.data.missing.slice(0, 8).join("、")}</span>}
            {(err !== null || saveIt.isError) && <Problem error={err ?? saveIt.error} />}
            <div className="row">
              {id && (
                <button
                  type="button"
                  className="btn ghost"
                  onClick={async () => {
                    if (!window.confirm(`删除股票池“${title}”？已经用了它的策略不受影响，它们保存的是一份名单。`)) return;
                    await remove.mutateAsync({ id });
                    onClose();
                  }}
                >
                  删除这个股票池
                </button>
              )}
              <button type="button" className="btn soft push" onClick={onClose}>
                取消
              </button>
              <button
                type="button"
                className="btn acc"
                disabled={!title.trim() || list.length === 0 || saveIt.isPending}
                onClick={async () => {
                  const p = await saveIt.mutateAsync({ id, name: title.trim(), codes: list.map((s) => s.code) });
                  onSaved(p);
                }}
              >
                保存
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
