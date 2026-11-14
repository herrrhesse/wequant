// Under the scope when it is 自定义名单: which stock pool the list was copied from, its size, 新建 and 编辑, and,
// when the pool has changed since (the backend's pool_drift), an offer to take its current list.
import { useState } from "react";
import { useCall } from "../../api/client";
import { call } from "../../api/http";
import type { PoolDrift, PoolOut, PoolSpec } from "../../api/types.gen";
import { Help } from "../../components/Help";
import { PoolEditor } from "../../components/PoolEditor";
import { st } from "./text";

/** The pool setting that takes a pool's current list (and leaves any index). */
export function fromPool(pool: PoolSpec | null | undefined, p: PoolOut): PoolSpec {
  return { ...(pool ?? {}), index: null, stocks: { codes: p.stocks.map((s) => s.code) as [string, ...string[]], pool_id: p.id, pool_name: p.name } };
}

export function PoolPicker({ pool, drift, apply, editable = true }: { pool: PoolSpec | null | undefined; drift?: PoolDrift | null; apply: (p: PoolOut) => void; editable?: boolean }) {
  const pools = useCall("pools_list", {});
  const [editing, setEditing] = useState<string | null | undefined>(undefined);
  const stocks = pool?.stocks;
  const pick = async (id: string) => apply(await call("pools_show", { id }));
  return (
    <div className="pool-picker col tight">
      <div className="row small">
        <select className="select" aria-label="股票池" value={stocks?.pool_id ?? ""} onChange={(e) => e.target.value && void pick(e.target.value)}>
          <option value="" disabled>
            {pools.data?.rows.length ? "选一个股票池" : "还没有股票池"}
          </option>
          {(pools.data?.rows ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}（{p.count} 只）
            </option>
          ))}
        </select>
        {editable && (
          <>
            <button type="button" className="btn sm soft" onClick={() => setEditing(null)}>
              新建
            </button>
            <button type="button" className="btn sm soft" disabled={!stocks?.pool_id} onClick={() => setEditing(stocks?.pool_id ?? null)}>
              编辑
            </button>
          </>
        )}
        <Help text={st.scope.poolHelp} />
      </div>
      {stocks && <span className="small sub">{st.scope.fromPool(stocks.pool_name ?? "", stocks.codes.length)}</span>}
      {drift && stocks?.pool_id && (
        <span className="row small warn-text">
          股票池已有改动（多了 {drift.added.length} 只，少了 {drift.removed.length} 只）
          <button type="button" className="btn sm" onClick={() => void pick(drift.pool_id)}>
            更新为最新名单
          </button>
        </span>
      )}
      {editing !== undefined && (
        <PoolEditor
          id={editing}
          onClose={() => setEditing(undefined)}
          onSaved={(p) => {
            setEditing(undefined);
            if (editing === null) apply(p);
          }}
        />
      )}
    </div>
  );
}
