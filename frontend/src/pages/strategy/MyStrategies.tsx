// "我的策略": every saved strategy with its summary, latest backtest and the "在今日显示" switch.
// Import and export arrive with the backend's storage tools (round 2, chunk 4).
import { useCall } from "../../api/client";
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
  const blankDraft = () => {
    try {
      localStorage.removeItem("wequant.strategy.draft");
    } catch {
      // storage unavailable
    }
  };
  return (
    <main className="page">
      <StatusBar />
      <section className="card" aria-label={st.list.title}>
        <div className="card-head">
          <h2>{st.list.title}</h2>
          <span className="small muted">{st.list.count(rows.length)}</span>
          <div className="row push">
            <a className="btn" href={href.newStrategy()} onClick={blankDraft}>
              {st.list.new}
            </a>
            <a className="btn soft" href={href.strategy()}>
              {st.list.back}
            </a>
          </div>
        </div>
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
        <a className="btn sm soft push" href={href.strategy(s.id)}>
          {st.list.edit}
        </a>
      </div>
    </div>
  );
}
