// 选股: one decision day of a saved strategy or of the strategy page's draft (wequant screen). Temporary changes to
// the scope and the factor weights make an "未保存的变体" that can be reset or saved as a new strategy. Every number
// is the backend's; the page selects and lays out.
import { Stock } from "../../components/Stock";
import { keepPreviousData } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { commandOf, useCall, useCallMutation } from "../../api/client";
import { IndexSeg, scopeOf } from "../../components/IndexSeg";
import { fromPool, PoolPicker } from "../strategy/PoolPicker";
import type { Reject, ScreenResult, ScreenRow, StrategySpec } from "../../api/types.gen";
import { Problem } from "../../components/Problem";
import { StatusBar } from "../../components/StatusBar";
import { WarningText } from "../../components/WarningText";
import { pct } from "../../format";
import { useLabels } from "../../labels";
import { href, type Route } from "../../router";
import { tx } from "../../text";
import { useUi } from "../../ui";
import { stepName } from "../strategy/Preview";
import { MiniK } from "./MiniK";
import { ChainView } from "../../components/ChainView";
import { boxes, readCondition, UNIT_TEXT } from "../../strategy/model";
import { draftGap, readPageDraft } from "../../strategy/pageDraft";
import { toDisplay, type Unit } from "../../strategy/fields";

/** The display unit of each value condition, by name (rank conditions keep the backend's number). */
function unitsOf(spec: StrategySpec | null): Map<string, Unit> {
  const m = new Map<string, Unit>();
  for (const b of spec ? boxes(spec) : []) for (const { c } of b.items) {
    const r = readCondition(c.expr);
    if (!r.custom && (r.mode === "gt" || r.mode === "lt")) m.set(c.name, r.unit);
  }
  return m;
}

type PicksRoute = Extract<Route, { page: "picks" }>;
const OPS: Record<string, string> = { gt: ">", ge: "≥", lt: "<", le: "≤", eq: "=", between: "介于" };
const ROLE_STEP = { target: "targets", backup: "backups", none: "scored" } as const;

export function PicksPage({ route }: { route: PicksRoute }) {
  const ui = useUi();
  const L = useLabels();
  const list = useCall("strategy_list", {});
  const calendar = useCall("data_calendar", {});
  const stored = readPageDraft();
  // What is computed is what the selector shows: the draft when asked for, or when nothing is saved yet.
  const useDraft = !!stored && (route.draft || (!route.id && list.isSuccess && list.data.rows.length === 0));
  const d = useDraft ? stored : null;
  const id = useDraft ? null : (route.id ?? list.data?.rows[0]?.id ?? null);
  const nothing = list.isSuccess && list.data.rows.length === 0 && !stored && !route.id;
  const saved = useCall("strategy_show", id ? { id, n: route.n } : null);
  const base: StrategySpec | null = d?.spec ?? saved.data?.strategy ?? null;
  const baseName = d?.name ?? saved.data?.name ?? "";
  const [variant, setVariant] = useState<StrategySpec | null>(null);
  const [tweak, setTweak] = useState(false);
  const [custom, setCustom] = useState(false);
  const [date, setDate] = useState(route.date ?? "");
  const [step, setStep] = useState<string | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const spec = variant ?? base;
  // A draft not complete yet is not sent (the backend would refuse it): the page says what is missing instead.
  const gap = d ? draftGap(variant ? { ...d, spec: variant, errors: undefined } : d) : null;
  const subject = gap ? null : variant || d ? (spec ? { strategy: spec } : null) : id ? { saved: { id, n: route.n } } : null;
  const request = subject ? { ...subject, date: route.date } : null;
  const screen = useCall("screen", request, { placeholderData: keepPreviousData });
  const summary = useCall("strategy_summary", spec && !gap ? { strategy: spec } : null);
  const save = useCallMutation("strategy_save", ["strategy_list"]);
  const r = screen.data;
  const nonTrading = !!route.date && !!r && r.date !== route.date;

  const go = (opts: { id?: string | null; draft?: boolean; date?: string | null }) => {
    setVariant(null);
    setStep(null);
    setCode(null);
    location.hash = href.picks({ id: opts.id ?? null, draft: opts.draft, date: opts.date === undefined ? route.date : opts.date });
  };
  const edit = (fn: (s: StrategySpec) => void) => {
    if (!spec) return;
    const next = structuredClone(spec);
    fn(next);
    setVariant(next);
  };
  if (nothing)
    return (
      <main className="page">
        <StatusBar />
        <section className="card" aria-label="选股设置">
          <div className="empty">
            <span>还没有策略。先在策略页新建一个策略，这里就能看到它在某一天选出的股票。</span>
            <a className="btn push" href={href.newStrategy()}>
              去策略页新建
            </a>
          </div>
        </section>
      </main>
    );

  return (
    <main className="page">
      <StatusBar />
      <section className="card" aria-label="选股设置">
        <div className="row picks-bar">
          <label className="field">
            策略
            <select className="select" aria-label="策略" value={useDraft ? "__draft" : (id ?? "")} onChange={(e) => (e.target.value === "__draft" ? go({ draft: true }) : go({ id: e.target.value }))}>
              {stored && <option value="__draft">策略页的草稿：{stored.name}</option>}
              {(list.data?.rows ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            决策日
            <input
              className="input"
              type="date"
              aria-label="决策日"
              value={date || route.date || calendar.data?.last || ""}
              min={calendar.data?.first ?? undefined}
              max={calendar.data?.last ?? undefined}
              onChange={(e) => setDate(e.target.value)}
            />
          </label>
          <button type="button" className="btn acc" disabled={!!gap} onClick={() => go({ id, draft: useDraft, date: date || null })}>
            选股
          </button>
          <button type="button" className="btn soft" aria-pressed={tweak} disabled={!!gap} onClick={() => setTweak(!tweak)}>
            临时调整
          </button>
          {variant && (
            <>
              <span className="state dirty">未保存的变体</span>
              <button type="button" className="btn sm soft" onClick={() => setVariant(null)}>
                还原
              </button>
              <button
                type="button"
                className="btn sm"
                onClick={async () => {
                  const v = await save.mutateAsync({ strategy: variant, name: `${baseName}（变体）` });
                  location.hash = href.strategy(v.id);
                }}
              >
                另存为新策略
              </button>
            </>
          )}
          {nonTrading && r && <span className="warnbox">{route.date} 不是交易日，按之前最近的交易日 {r.date} 计算</span>}
        </div>
        {summary.data && <p className="summary">{summary.data.text}</p>}
        {tweak && spec && !gap && (
          <div className="inner tweak">
            <span className="small b">换个范围</span>
            <IndexSeg
              label="范围"
              value={custom && !spec.pool?.stocks ? "stocks" : scopeOf(spec.pool)}
              onChange={(c) => {
                setCustom(c === "stocks");
                if (c !== "stocks") edit((s) => void (s.pool = { ...(s.pool ?? {}), stocks: null, index: (c === "all" ? null : c) as never }));
              }}
            />
            {(custom || !!spec.pool?.stocks) && <PoolPicker pool={spec.pool} editable={false} apply={(p) => edit((s) => void (s.pool = fromPool(s.pool, p)))} />}
            <label className="row small">
              <input
                type="checkbox"
                checked={spec.pool?.exclude_delisting ?? false}
                onChange={(e) => edit((s) => void (s.pool = { ...(s.pool ?? {}), exclude_delisting: e.target.checked }))}
              />
              {L.of("pool_items", "exclude_delisting") || "剔除退市整理期"}：{L.help("pool_items", "exclude_delisting") || "退市前最后一段交易日（简称里带“退”）的股票不参与"}
            </label>
            <span className="small b">因子权重</span>
            {(spec.factors ?? []).map((f, i) => (
              <label key={f.name} className="row small">
                <span style={{ minWidth: 120 }}>{f.name}</span>
                <input type="range" min={0} max={10} step={1} value={f.weight ?? 1} aria-label={`${f.name} 权重`} onChange={(e) => edit((s) => void (s.factors![i]!.weight = Number(e.target.value)))} />
                <span className="num">{f.weight ?? 1}</span>
              </label>
            ))}
            <span className="note">临时调整只影响这次选股；可以另存为新策略。</span>
          </div>
        )}
        {save.isError && <Problem error={save.error} />}
        {ui.dev && r && (
          <div className="dev-row">
            <span>
              数据版本 {r.data_version_name} · 内容哈希 {r.strategy_hash}
            </span>
            <button type="button" className="dev-btn" onClick={() => void navigator.clipboard?.writeText(commandOf("screen", request))}>
              复制选股命令
            </button>
          </div>
        )}
      </section>
      {gap && (
        <section className="card" aria-label="结果">
          <div className="empty">
            <span className="col tight">
              <span className="b">{gap.title}</span>
              <span className="small sub">{gap.text}</span>
            </span>
            <a className="btn push" href={gap.fix}>
              去策略页
            </a>
          </div>
        </section>
      )}
      {screen.isError && <Problem error={screen.error} />}
      {r && r.warnings.length > 0 && (
        <div className="notes-line">
          {r.warnings.map((w, i) => (
            <span key={i} className="warnbox" title={L.warningName(w.code)}>
              <WarningText w={w} />
            </span>
          ))}
        </div>
      )}
      {r && (
        <div className={`picks-layout ${screen.isFetching ? "pending" : ""}`}>
          <Funnel r={r} step={step} setStep={(s) => setStep(s === step ? null : s)} />
          <section className="card picks-main" aria-label="结果">
            {step ? <Rejects r={r} step={step} back={() => setStep(null)} pick={setCode} units={unitsOf(spec)} /> : <Scored r={r} pick={setCode} />}
          </section>
          {code && subject && <Side code={code} date={r.date} subject={subject} spec={spec} row={r.rows.find((x) => x.code === code) ?? null} close={() => setCode(null)} />}
        </div>
      )}
    </main>
  );
}

function Funnel({ r, step, setStep }: { r: ScreenResult; step: string | null; setStep: (s: string) => void }) {
  const L = useLabels();
  const top = r.funnel[0]?.left || 1;
  const outs = useMemo(() => {
    const m = new Map<string, number>();
    for (const x of r.rejects) m.set(x.step, (m.get(x.step) ?? 0) + 1);
    return m;
  }, [r.rejects]);
  return (
    <section className="card picks-funnel" aria-label="漏斗">
      <div className="card-head">
        <h2>漏斗 · {r.date}</h2>
        <span className="push small muted">剩余 · 剔除</span>
      </div>
      <span className="note">点一级，看这一级剔除了哪些股票</span>
      <ol className="funnel">
        {r.funnel.map((s, i) => {
          const n = s.left ?? s.passed;
          const out = outs.get(s.step) ?? 0;
          return (
            <li key={`${s.step}/${i}`} className={s.chain ? "" : "off-chain"}>
              <button type="button" aria-pressed={step === s.step} disabled={out === 0} onClick={() => setStep(s.step)}>
                <span className="funnel-name">
                  {s.chain ? "" : "└ "}
                  {stepName(s.step, s.kind, L.of)}
                </span>
                <span className="funnel-bar">
                  <span style={{ width: `${Math.max(1, (n / top) * 100)}%` }} />
                </span>
                <span className="num b">{n}</span>
                <span className="num funnel-out">{out ? `−${out}` : ""}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Scored({ r, pick }: { r: ScreenResult; pick: (c: string) => void }) {
  const L = useLabels();
  const heads = r.rows[0]?.factors.map((f) => f.name) ?? [];
  const capped = r.rows.some((x) => x.capped);
  return (
    <>
      <div className="card-head">
        <h2>有分股票 {r.rows.length} 只</h2>
        <span className="push small muted">按综合分排序；点一行看因子明细和判断链</span>
      </div>
      {r.rows.length === 0 && (
        <div className="empty">
          <span>这一天没有股票通过全部条件。看看左边漏斗里哪一级剔除得最多，再去策略页放宽那一条。</span>
          <a className="btn push" href={href.strategy()}>
            去策略页调整
          </a>
        </div>
      )}
      {r.rows.length > 0 && (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>名次</th>
                <th>股票</th>
                <th>角色</th>
                <th className="r">综合分</th>
                {heads.map((h) => (
                  <th key={h} className="r">
                    {h} 贡献
                  </th>
                ))}
                <th className="r">目标权重</th>
              </tr>
            </thead>
            <tbody>
              {r.rows.map((x: ScreenRow) => (
                <tr key={x.code} className="clickable" onClick={() => pick(x.code)}>
                  <td className="num muted">{x.rank}</td>
                  <td><Stock code={x.code} name={x.name} /></td>
                  <td>
                    <span className={`role ${x.role}`}>{x.role === "none" ? "有分未入选" : L.of("funnel_kinds", ROLE_STEP[x.role])}</span>
                  </td>
                  <td className="num r">{(x.score * 100).toFixed(1)}</td>
                  {x.factors.map((f) => (
                    <td key={f.name} className="num r muted">
                      {(f.contribution * 100).toFixed(1)}
                    </td>
                  ))}
                  <td className="num r">
                    {x.weight === null ? "—" : pct(x.weight, 1)}
                    {x.capped && (
                      <span className="tag" title="被单股上限截过">
                        触顶
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {capped && <span className="note">标“触顶”的股票被单股上限截过，截下的部分留作现金。</span>}
    </>
  );
}

function why(x: Reject, units: Map<string, Unit>): string {
  if (x.missing) return "缺数据";
  const u = units.get(x.step);
  const v = (n: number) => (u ? `${Number(toDisplay(n, u).toPrecision(4))}${UNIT_TEXT[u]}` : String(Number(n.toPrecision(6))));
  if (x.members) return x.members.map((m) => `${m.name} ${m.passed ? "✓" : "✗"}`).join("；");
  if (x.value !== null && x.threshold !== null)
    return `${v(x.value)} / 要求 ${x.op ? OPS[x.op] : ""} ${v(x.threshold)}${x.threshold_hi !== null ? ` 至 ${v(x.threshold_hi)}` : ""}`;
  return "";
}

function Rejects({ r, step, back, pick, units }: { r: ScreenResult; step: string; back: () => void; pick: (c: string) => void; units: Map<string, Unit> }) {
  const L = useLabels();
  const rows = r.rejects.filter((x) => x.step === step);
  const kind = rows[0]?.kind ?? "condition";
  return (
    <>
      <div className="card-head">
        <h2>
          被“{stepName(step, kind, L.of)}”剔除 {rows.length} 只
        </h2>
        <button type="button" className="btn sm soft push" onClick={back}>
          ← 回到有分股票
        </button>
      </div>
      <div className="tbl-wrap">
        <table className="tbl" aria-label="被剔除的股票">
          <thead>
            <tr>
              <th>股票</th>
              <th>被剔除在</th>
              <th>实际值和要求</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((x) => (
              <tr key={x.code} className="clickable" onClick={() => pick(x.code)}>
                <td><Stock code={x.code} name={x.name} /></td>
                <td>{stepName(x.step, x.kind, L.of)}</td>
                <td className="sub num">{why(x, units)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Side({ code, date, subject, row, close, spec }: { code: string; date: string; subject: object; row: ScreenRow | null; close: () => void; spec: StrategySpec | null }) {
  const ui = useUi();
  const L = useLabels();
  const request = { ...subject, code, date };
  const ex = useCall("explain", request);
  const e = ex.data;
  return (
    <aside className="card picks-side" aria-label={code}>
      <div className="row">
        <span className="big"><Stock code={code} name={row?.name ?? ex.data?.name} /></span>
        {row && (
          <span className="small sub">
            {row.role === "none" ? "有分未入选" : L.of("funnel_kinds", ROLE_STEP[row.role])} · 第 {row.rank} 名
          </span>
        )}
        <button type="button" className="icon-btn push" aria-label={tx.common.close} onClick={close}>
          ×
        </button>
      </div>
      <MiniK code={code} end={date} />
      <div className="row small">
        <span className="muted">近 60 个交易日</span>
        <a className="link push" href={href.stock(code, { date, from: "picks" })}>
          打开个股页 ›
        </a>
      </div>
      {row && row.factors.length > 0 && (
        <div className="col tight">
          <span className="small b">因子</span>
          {row.factors.map((f) => (
            <div key={f.name} className="row small">
              <span>{f.name}</span>
              <span className="push muted num">
                值 {Number(f.value.toPrecision(4))} · 百分位 {(f.pct * 100).toFixed(0)} · 贡献 {(f.contribution * 100).toFixed(1)}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="col tight">
        <span className="small b">判断链</span>
        {ex.isPending && <span className="note">{tx.app.loading}</span>}
        {ex.isError && <Problem error={ex.error} />}
        {e && <ChainView e={e} spec={spec} />}
      </div>
      {ui.dev && (
        <button type="button" className="dev-btn" onClick={() => void navigator.clipboard?.writeText(commandOf("explain", request as never))}>
          复制判断链命令
        </button>
      )}
    </aside>
  );
}
