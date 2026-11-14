// The setup's "下载哪些股票" for a BaoStock build: 沪深全部, 沪深300 and 中证500 (one or both: every stock that was a
// member in the years built), or the user's own list (pasted or from a file; what cannot be read is listed with the
// reason). Beside each choice the backend's stock count and time for the years chosen; the build's total under the
// years. The names are the strategy scope's (labels.scopes).
import { useState } from "react";
import { useCall } from "../../api/client";
import { call } from "../../api/http";
import type { BuildEstimate, DataScope, PoolStock, UnparsedItem } from "../../api/types.gen";
import { FileButton } from "../../components/FileButton";
import { useScopes } from "../../components/IndexSeg";
import { about } from "../../components/JobBar";
import { Problem } from "../../components/Problem";
import { readText, Unparsed } from "../../components/Unparsed";
import { show } from "../../format";
import type { Years } from "./SetupPage";

/** The indexes a build can download (their members over the years). */
export const SCOPE_INDEXES = ["000300.SH", "000905.SH"] as const;
type ScopeIndex = (typeof SCOPE_INDEXES)[number];

export type Choice = "all" | "indexes" | "stocks";
export interface Pick {
  choice: Choice;
  indexes: ScopeIndex[];
  list: PoolStock[];
  poolName: string;
}
export const DEFAULT_PICK: Pick = { choice: "indexes", indexes: [...SCOPE_INDEXES], list: [], poolName: "首次建库名单" };

/** The data scope of a pick; null when it has nothing in it yet. */
export function scopeOf(p: Pick): DataScope | null {
  if (p.choice === "all") return { all: true, indexes: [], stocks: [] };
  if (p.choice === "indexes") return p.indexes.length ? { all: false, indexes: SCOPE_INDEXES.filter((i) => p.indexes.includes(i)), stocks: [] } : null;
  return p.list.length ? { all: false, indexes: [], stocks: p.list.map((s) => s.code) } : null;
}

/** A pick back from a build's request (a paused build carried on after a reload). */
export function pickOf(scope: DataScope | undefined, poolName: string | null | undefined): Pick | null {
  if (!scope) return null;
  if (scope.all) return { ...DEFAULT_PICK, choice: "all" };
  if (scope.stocks?.length) return { ...DEFAULT_PICK, choice: "stocks", list: scope.stocks.map((code) => ({ code, name: null })), poolName: poolName ?? DEFAULT_PICK.poolName };
  return { ...DEFAULT_PICK, choice: "indexes", indexes: scope.indexes ?? [] };
}

/** The backend's estimate of a build of these stocks over these years. */
export function useEstimate(years: Years, scope: DataScope | null, on = true) {
  return useCall("data_build_estimate", on && scope ? { years, scope } : null);
}

const DESC: Record<string, string> = {
  all: "沪深两市的全部股票",
  "000300.SH": "这几年当过成分股的全部股票",
  "000905.SH": "这几年当过成分股的全部股票",
  stocks: "粘贴或导入一份名单，只下载这些股票",
};

function Cost({ est, label }: { est: BuildEstimate | undefined; label: string }) {
  return (
    <span className="small scope-cost" aria-label={label}>
      {est ? `约 ${show(est.stocks)} 只 · 预计${about(est.seconds)}` : " "}
    </span>
  );
}

export function ScopeChoice({ years, pick, setPick }: { years: Years; pick: Pick; setPick: (p: Pick) => void }) {
  const { name } = useScopes();
  const all = useEstimate(years, { all: true, indexes: [], stocks: [] });
  const hs300 = useEstimate(years, { all: false, indexes: ["000300.SH"], stocks: [] });
  const zz500 = useEstimate(years, { all: false, indexes: ["000905.SH"], stocks: [] });
  const byIndex: Record<string, BuildEstimate | undefined> = { "000300.SH": hs300.data, "000905.SH": zz500.data };
  const own = useEstimate(years, pick.list.length ? { all: false, indexes: [], stocks: pick.list.map((s) => s.code) } : null);
  const problem = all.error ?? hs300.error ?? zz500.error;
  const toggle = (ix: ScopeIndex) => {
    const on = pick.choice === "indexes" ? pick.indexes : [];
    setPick({ ...pick, choice: "indexes", indexes: on.includes(ix) ? on.filter((i) => i !== ix) : [...on, ix] });
  };
  return (
    <div className="col tight">
      <div className="list-grid setup-sources scope-cards" role="group" aria-label="下载哪些股票">
        <button type="button" className="inner tpl-card" aria-pressed={pick.choice === "all"} onClick={() => setPick({ ...pick, choice: "all" })}>
          <span className="b">{name(null)}</span>
          <span className="small sub">{DESC.all}</span>
          <Cost est={all.data} label={`${name(null)}的股票数和耗时`} />
        </button>
        {SCOPE_INDEXES.map((ix) => (
          <button key={ix} type="button" className="inner tpl-card" aria-pressed={pick.choice === "indexes" && pick.indexes.includes(ix)} onClick={() => toggle(ix)}>
            <span className="row tight">
              <span className="b">{name(ix)}</span>
              <span className="tiny muted push">可和另一个指数同时选</span>
            </span>
            <span className="small sub">{DESC[ix]}</span>
            <Cost est={byIndex[ix]} label={`${name(ix)}的股票数和耗时`} />
          </button>
        ))}
        <button type="button" className="inner tpl-card" aria-pressed={pick.choice === "stocks"} onClick={() => setPick({ ...pick, choice: "stocks" })}>
          <span className="b">{name("stocks")}</span>
          <span className="small sub">{DESC.stocks}</span>
          {pick.list.length > 0 ? <Cost est={own.data} label={`${name("stocks")}的股票数和耗时`} /> : <span className="small scope-cost muted">还没有名单</span>}
        </button>
      </div>
      {problem && <Problem error={problem} />}
      {pick.choice === "indexes" && pick.indexes.length === 0 && <span className="small warn-text">至少选一项：沪深全部、一个指数或一份名单。</span>}
      {pick.choice === "stocks" && <OwnList pick={pick} setPick={setPick} />}
      <span className="small sub">先下载一部分股票会快很多，之后随时可以在数据页扩大范围。</span>
    </div>
  );
}

/** The user's own list: pasted or from a file, parsed by the backend (in an empty folder it reads BaoStock's list of
 * securities once, which takes a few seconds), unreadable items listed; saved as a stock pool after the build. */
export function OwnList({ pick, setPick }: { pick: Pick; setPick: (p: Pick) => void }) {
  const [paste, setPaste] = useState("");
  const [unparsed, setUnparsed] = useState<UnparsedItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [note, setNote] = useState("");
  const add = (more: PoolStock[], what: string) => {
    const have = new Set(pick.list.map((s) => s.code));
    const fresh = more.filter((s) => !have.has(s.code));
    setPick({ ...pick, list: [...pick.list, ...fresh] });
    setNote(`${what}：加入 ${fresh.length} 只${more.length > fresh.length ? `，${more.length - fresh.length} 只已在名单里` : ""}`);
  };
  const parse = async (text: string, what: string) => {
    setErr(null);
    setBusy(true);
    try {
      const r = await call("pools_parse", { text });
      add(r.stocks, what);
      setUnparsed(r.unparsed);
      return true;
    } catch (e) {
      setErr(e);
      return false;
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="inner col">
      <textarea
        className="input pool-paste"
        aria-label="粘贴股票清单"
        rows={3}
        placeholder="每行或用逗号、空格分开都可以，代码和名称混着写也行，例如：600519 招商银行 000001.SZ"
        value={paste}
        onChange={(e) => setPaste(e.target.value)}
      />
      <div className="row wrap">
        <button type="button" className="btn sm" disabled={!paste.trim() || busy} onClick={() => void parse(paste, "粘贴").then((ok) => ok && setPaste(""))}>
          加入名单
        </button>
        <FileButton accept=".csv,.txt,text/csv,text/plain" disabled={busy} onFile={(f) => void readText(f).then((t) => parse(t, `导入 ${f.name}`), setErr)}>
          导入文件
        </FileButton>
        <span className="small b">名单共 {pick.list.length} 只</span>
        <button type="button" className="btn sm soft" disabled={pick.list.length === 0} onClick={() => setPick({ ...pick, list: [] })}>
          清空
        </button>
      </div>
      {busy && (
        <span role="status" className="small sub">
          正在识别名单…还没有数据时要先从 BaoStock 读一次证券列表，需要几秒钟。
        </span>
      )}
      {note && !busy && (
        <span role="status" className="small ok b">
          {note}
        </span>
      )}
      <Unparsed items={unparsed} onAdd={(c) => add([c], "添加")} onDismiss={() => setUnparsed([])} />
      {pick.list.length > 0 && (
        <span className="small sub scope-list" translate="no">
          {pick.list
            .slice(0, 12)
            .map((s) => (s.name ? `${s.name} ${s.code}` : s.code))
            .join("、")}
          {pick.list.length > 12 ? ` 等 ${pick.list.length} 只` : ""}
        </span>
      )}
      <label className="row small">
        建好后存成股票池，名称
        <input className="input" style={{ width: 200 }} aria-label="股票池名称" value={pick.poolName} onChange={(e) => setPick({ ...pick, poolName: e.target.value })} />
      </label>
      {err !== null && <Problem error={err} />}
    </div>
  );
}
