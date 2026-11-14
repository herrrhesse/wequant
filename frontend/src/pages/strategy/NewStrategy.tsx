// "新建": start from a blank strategy or from a template. A template opens as an unsaved draft in the editor, to be
// changed and then saved; nothing is saved by choosing it.
import { useState } from "react";
import { useCall } from "../../api/client";
import type { StrategySpec } from "../../api/types.gen";
import { Problem } from "../../components/Problem";
import { StatusBar } from "../../components/StatusBar";
import { useScopes } from "../../components/IndexSeg";
import { href } from "../../router";
import { boxes } from "../../strategy/model";
import { st } from "./text";

const DRAFT = "wequant.strategy.draft";

/** An unsaved new strategy (not yet saved under any id) the page was left with, if one was begun. */
export function leftOver(): { spec: StrategySpec; name: string } | null {
  try {
    const v = JSON.parse(localStorage.getItem(DRAFT) ?? "null") as { id: string | null; spec: StrategySpec; name: string } | null;
    // An untouched blank strategy is nothing to continue.
    const begun = v && v.id === null && ((v.spec.filters ?? []).length > 0 || (v.spec.factors ?? []).length > 0);
    return begun ? { spec: v.spec, name: v.name } : null;
  } catch {
    return null;
  }
}

function open(spec: StrategySpec | null, name: string) {
  try {
    if (spec) localStorage.setItem(DRAFT, JSON.stringify({ id: null, spec, name }));
    else localStorage.removeItem(DRAFT);
  } catch {
    // storage unavailable: the editor starts blank
  }
  location.hash = href.draftStrategy();
}

/** The settings a template card lists: scope, conditions, factors, hold count, rebalancing. */
function facts(s: StrategySpec, scopeName: (code: string | null) => string): string[] {
  const conds = boxes(s).reduce((n, b) => n + b.items.length, 0);
  const factors = (s.factors ?? []).filter((f) => f.enabled !== false).map((f) => (f.prefer === "low" ? `${f.name}（越小越好）` : f.name));
  const every = s.every ?? 1;
  const period = st.trade.periods[s.period ?? "week"];
  return [
    `范围：${s.pool?.stocks ? "自定义名单" : scopeName(s.pool?.index ?? null)}`,
    conds ? `筛选条件 ${conds} 条` : "不设筛选条件",
    factors.length ? `打分：${factors.join("、")}` : "没有打分因子",
    s.top_n ? `持有 ${s.top_n} 只` : "持有全部",
    `每${every > 1 ? ` ${every} ` : ""}${period}调仓`,
  ];
}

export function NewStrategy() {
  const templates = useCall("strategy_templates", {});
  const { name } = useScopes();
  const [left] = useState(leftOver);
  return (
    <main className="page">
      <StatusBar />
      <section className="card" aria-label="新建策略">
        <div className="card-head">
          <h2>新建策略</h2>
          <span className="small muted">从空白开始，或者选一个模板改。选了模板会打开一份还没保存的草稿，改好再保存。</span>
          <a className="btn soft push" href={href.strategy(null, true)}>
            ← 我的策略
          </a>
        </div>
        <div className="list-grid">
          {left && (
            <button type="button" className="inner tpl-card" aria-label="继续没保存的新策略" onClick={() => (location.hash = href.draftStrategy())}>
              <span className="b big">继续：{left.name}</span>
              <span className="small sub">上次新建了还没保存的策略。点这里接着改；选下面的空白或模板会替换掉它。</span>
              <span className="tpl-facts">
                {facts(left.spec, name).map((f) => (
                  <span key={f} className="tag">
                    {f}
                  </span>
                ))}
              </span>
            </button>
          )}
          <button type="button" className="inner tpl-card blank" onClick={() => open(null, st.newName)}>
            <span className="b big">空白策略</span>
            <span className="small sub">什么都不设，自己从范围、条件、打分到交易一项项写。</span>
          </button>
          {(templates.data?.rows ?? []).map((t) => (
            <button key={t.id} type="button" className="inner tpl-card" aria-label={`模板：${t.name}`} onClick={() => open(t.strategy, t.name)}>
              <span className="b big">{t.name}</span>
              <span className="small sub">{t.description}</span>
              <span className="tpl-facts">
                {facts(t.strategy, name).map((f) => (
                  <span key={f} className="tag">
                    {f}
                  </span>
                ))}
              </span>
            </button>
          ))}
        </div>
        {templates.isError && <Problem error={templates.error} />}
      </section>
    </main>
  );
}
