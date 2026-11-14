// "我的策略": every saved strategy with its summary, latest backtest and the "在今日显示" switch.
// Each strategy exports with its change history; a .wqstrategy file imports as a new strategy.
import { useState } from "react";
import { useCall } from "../../api/client";
import { exportFile, importFile } from "../../api/files";
import { FileButton } from "../../components/FileButton";
import { Problem } from "../../components/Problem";
import type { RunRow, StrategyInfo } from "../../api/types.gen";
import { StatusBar } from "../../components/StatusBar";
import { localTime, pct } from "../../format";
import { follows, setFollow } from "../../local";
import { href } from "../../router";
import { st } from "./text";

export function MyStrategies() {
  const list = useCall("strategy_list", {});
  const runs = useCall("runs_list", { limit: 100 });
  const followed = follows.use();
  const rows = list.data?.rows ?? [];
  const [err, setErr] = useState<unknown>(null);
  return (
    <main className="page">
      <StatusBar />
      <section className="card" aria-label={st.list.title}>
        <div className="card-head">
          <h2>{st.list.title}</h2>
          <span className="small muted">{st.list.count(rows.length)}</span>
          <div className="row push">
            <a className="btn" href={href.newStrategy()}>
              {st.list.new}
            </a>
            <FileButton
              accept=".wqstrategy"
              onFile={(f) => {
                setErr(null);
                importFile("strategy_import", f).then(
                  (r) => (location.hash = href.strategy(r.id)),
                  (e) => setErr(e),
                );
              }}
            >
              导入策略
            </FileButton>
            <a className="btn soft" href={href.strategy()}>
              {st.list.back}
            </a>
          </div>
        </div>
        {err !== null && <Problem error={err} />}
        {list.isSuccess && rows.length === 0 && (
          <div className="empty">
            <span>还没有保存过策略。新建一个（可以从模板开始），或者导入别人给你的策略文件。</span>
            <a className="btn push" href={href.newStrategy()}>
              {st.list.new}
            </a>
          </div>
        )}
        <div className="list-grid">
          {rows.map((s) => (
            <Item key={s.id} s={s} run={runs.data?.rows.find((r) => r.saved_as.some((x) => x.id === s.id)) ?? null} on={followed.includes(s.id)} />
          ))}
        </div>
      </section>
    </main>
  );
}

function Item({ s, run, on }: { s: StrategyInfo; run: RunRow | null; on: boolean }) {
  const show = useCall("strategy_show", { id: s.id });
  return (
    <div className="inner">
      <div className="row">
        <a className="b big" href={href.strategy(s.id)}>
          {s.name}
        </a>
        <span className="push tiny muted">{st.list.savedAt(localTime(s.current_saved_at))}</span>
      </div>
      {show.data && <span className="small sub clamp2">{show.data.summary}</span>}
      <span className="small muted">{run ? `最近回测：年化 ${pct(run.annual_return, 1, true)} · 最大回撤 ${pct(run.max_drawdown, 1)}` : st.preview.noRun}</span>
      <div className="row">
        <label className="switch card-switch" title={st.followTitle}>
          <input type="checkbox" role="switch" checked={on} onChange={() => setFollow(s.id, !on)} />
          <span className="knob" aria-hidden="true" />
          {st.follow}
        </label>
        <button type="button" className="btn sm soft push" onClick={() => void exportFile("strategy_export", { id: s.id, history: true }).catch(() => window.alert("导出失败，请稍后再试"))}>
          导出
        </button>
        <a className="btn sm soft" href={href.strategy(s.id)}>
          {st.list.edit}
        </a>
      </div>
    </div>
  );
}
