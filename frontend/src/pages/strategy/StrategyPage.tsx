// The strategy page: a one-page editor (scope, filters, ranking, trading on the left; a live preview on the right),
// the change history drawer and "我的策略". Every edit re-checks the draft (strategy validate) and re-screens it on the
// latest trading day (about 300 ms after the last change); an older answer never replaces a newer one.
import { keepPreviousData } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { commandOf, useCall, useCallMutation } from "../../api/client";
import { ApiError } from "../../api/http";
import type { StrategySpec } from "../../api/types.gen";
import { Problem } from "../../components/Problem";
import { StatusBar } from "../../components/StatusBar";
import { SCOPE_WARNINGS, WarningText } from "../../components/WarningText";
import { useDebounced } from "../../hooks";
import { useLabels } from "../../labels";
import { follows, setFollow } from "../../local";
import { href, type Route } from "../../router";
import { strategyLoc } from "../../strategy/locate";
import { PAGE_DRAFT as DRAFT, writePageDraft } from "../../strategy/pageDraft";
import { blank } from "../../strategy/model";
import { useUi } from "../../ui";
import { FactorsCard, FiltersCard, ScopeCard, TradeCard, type Err } from "./Editor";
import { History } from "./History";
import { MyStrategies } from "./MyStrategies";
import { leftOver, NewStrategy } from "./NewStrategy";
import { Preview } from "./Preview";
import { st } from "./text";

const LAST = "wequant.strategy.last";

interface Origin {
  id: string;
  name: string;
}

function readLast(): string | null {
  try {
    return localStorage.getItem(LAST);
  } catch {
    return null;
  }
}

export function StrategyPage({ route }: { route: Extract<Route, { page: "strategy" }> }) {
  const list = useCall("strategy_list", {});
  if (route.list) return <MyStrategies />;
  if (route.pick) return <NewStrategy />;
  if (route.fresh) return <Loader key="new" id={null} />;
  // The strategy opened last, unless the list (e.g. after switching the data folder) no longer has it.
  const stored = readLast();
  const last = stored && (!list.data || list.data.rows.some((r) => r.id === stored)) ? stored : null;
  const id = route.id || last || list.data?.rows[0]?.id || null;
  if (!route.id && !last && list.isPending) return <main className="page"><StatusBar /></main>;
  // Nothing saved and nothing begun: start by choosing a blank strategy or a template.
  if (!route.id && !last && list.data?.rows.length === 0 && !leftOver()) return <NewStrategy />;
  return <Loader key={id ?? "new"} id={id} />;
}

function Loader({ id }: { id: string | null }) {
  const saved = useCall("strategy_show", id ? { id } : null);
  if (id && saved.isPending) return <main className="page"><StatusBar /></main>;
  if (id && saved.isError) return <main className="page"><StatusBar /><Problem error={saved.error} /></main>;
  const origin = saved.data ? { id: saved.data.id, name: saved.data.name } : null;
  return <Editor key={saved.data ? `${saved.data.id}/${saved.data.n}` : "new"} origin={origin} savedSpec={saved.data?.strategy ?? blank()} />;
}

/** A stored unsaved draft of this origin, if the page was left with one. */
function storedDraft(origin: Origin | null): { spec: StrategySpec; name: string } | null {
  try {
    const v = JSON.parse(localStorage.getItem(DRAFT) ?? "null") as { id: string | null; spec: StrategySpec; name: string } | null;
    return v && v.id === (origin?.id ?? null) ? { spec: v.spec, name: v.name } : null;
  } catch {
    return null;
  }
}

function Editor({ origin, savedSpec }: { origin: Origin | null; savedSpec: StrategySpec }) {
  const ui = useUi();
  const L = useLabels();
  const [initial] = useState(() => storedDraft(origin));
  const [spec, setSpec] = useState<StrategySpec>(initial?.spec ?? savedSpec);
  const [name, setName] = useState(initial?.name ?? origin?.name ?? st.newName);
  const [undo, setUndo] = useState<{ spec: StrategySpec; name: string }[]>([]);
  const [hl, setHl] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [showJson, setShowJson] = useState(false);
  const [narrow, setNarrow] = useState(() => window.innerWidth < 900);
  const [drawer, setDrawer] = useState(false);
  const followed = follows.use();

  useEffect(() => {
    if (origin) localStorage.setItem(LAST, origin.id);
  }, [origin]);
  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth < 900);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const edit = (fn: (d: StrategySpec) => void) => {
    setUndo((u) => [...u.slice(-49), { spec, name }]);
    setNote("");
    const next = structuredClone(spec);
    fn(next);
    setSpec(next);
  };

  // Check, summary and preview follow the draft about 300 ms after the last change. Until the draft has an enabled
  // factor there is nothing to rank: the preview asks for the stages it can compute (no hold count), and the page
  // shows how to go on instead of an error.
  const settled = useDebounced(spec, 300);
  const noFactors = !(settled.factors ?? []).some((f) => f.enabled !== false);
  const asked = useMemo(() => (noFactors ? { ...settled, top_n: null, backups: 0, hold_rank: null } : settled), [settled, noFactors]);
  const check = useCall("strategy_validate", { strategy: asked }, { placeholderData: keepPreviousData });
  const apiErr = check.error instanceof ApiError ? check.error : null;
  const errors: Err[] = (apiErr?.status === 422 ? (apiErr.body.detail ?? []) : []).map((d) => ({
    loc: strategyLoc(d.loc ?? []),
    text: L.detail(d),
  }));
  const screen = useCall("screen", check.isSuccess ? { strategy: asked } : null, { placeholderData: keepPreviousData });
  const schedule = useCall("strategy_schedule", check.isSuccess ? { strategy: asked, count: 3 } : null, { placeholderData: keepPreviousData });
  const blocked = errors.length > 0 || (check.isError && !apiErr);
  // The draft, kept for a reload and for 选股 and 回测, with the errors of the check of exactly this draft (else not
  // known yet): those pages do not send a draft the backend would refuse.
  const checked = spec === settled && !check.isFetching && !check.isPlaceholderData && (check.isSuccess || apiErr?.status === 422);
  useEffect(() => {
    writePageDraft({ id: origin?.id ?? null, spec, name, errors: checked ? errors.length : undefined });
  }, [spec, name, origin, checked, errors.length]);
  // Updating from the moment of the edit (not only once the debounce ends) until the newest preview has arrived.
  const updating = !blocked && (spec !== settled || check.isFetching || screen.isFetching || check.isPlaceholderData || screen.isPlaceholderData);
  const st0 = screen.data?.warnings.find((w) => w.code === "st_unknown");
  const stNone = !!st0 && Number(st0.params.known_share) === 0;
  // No stock names in the data: 剔除退市整理期 cannot tell the period (the backend's delisting_unknown), said on its row.
  const delistingNone = screen.data?.warnings.find((w) => w.code === "delisting_unknown");
  // The check's warnings, and the preview's data-scope warning for its day when the check has not given that one.
  const warnings = [...(check.data?.warnings ?? []), ...(screen.data?.warnings ?? []).filter((w) => SCOPE_WARNINGS.has(w.code) && !check.data?.warnings.some((c) => c.code === w.code))];

  const dirty = check.data ? !origin || !check.data.saved_as.some((s) => s.id === origin.id) || name !== origin.name : spec !== savedSpec;
  const state = errors.length ? "errors" : !origin ? "never" : dirty ? "dirty" : "saved";

  const save = useCallMutation("strategy_save", ["strategy_list", "strategy_versions", "strategy_show", "strategy_validate"]);
  const doSave = async (asNew: boolean) => {
    const n = asNew ? `${name}${st.copySuffix}` : name;
    const r = await save.mutateAsync({ strategy: spec, id: asNew ? null : (origin?.id ?? null), name: n });
    try {
      localStorage.removeItem(DRAFT);
      localStorage.setItem(LAST, r.id);
    } catch {
      // storage unavailable
    }
    if (asNew || !origin) {
      location.hash = href.strategy(r.id);
      return;
    }
    setNote(r.created ? st.saved : st.unchanged);
  };

  const nT = screen.data?.rows.filter((r) => r.role === "target").length ?? 0;
  const nB = screen.data?.rows.filter((r) => r.role === "backup").length ?? 0;
  const following = origin ? followed.includes(origin.id) : false;

  const props = { spec, edit, errors, hl, setHl, stNone, delistingNone: delistingNone ? L.warning(delistingNone) : null, drift: check.data?.pool_drift ?? null };
  const preview = <Preview spec={asked} screen={screen.data} updating={updating} errors={errors.length} blocked={blocked} noFactors={noFactors} hl={hl} setHl={setHl} />;

  return (
    <main className="page">
      <StatusBar />
      <section className="card strat-bar" aria-label="策略状态">
        <div className="row">
          <label htmlFor="s-name" className="sr-only">
            {st.nameLabel}
          </label>
          <input
            id="s-name"
            className="input name-big"
            value={name}
            onChange={(e) => {
              setUndo((u) => [...u.slice(-49), { spec, name }]);
              setName(e.target.value);
            }}
          />
          <span className={`state ${state}`}>
            {state === "errors" ? st.state.errors(errors.length) : state === "never" ? st.state.never : state === "dirty" ? st.state.dirty : st.state.saved}
          </span>
          {screen.data && <span className="small muted">{st.latest(screen.data.date)}</span>}
          <div className="row push">
            {origin && (
              <label className="switch" title={st.followTitle}>
                <input type="checkbox" role="switch" checked={following} onChange={() => setFollow(origin.id, !following)} />
                <span className="knob" aria-hidden="true" />
                {st.follow}
              </label>
            )}
            <button
              type="button"
              className="btn soft"
              disabled={undo.length === 0}
              onClick={() => {
                const last = undo[undo.length - 1];
                if (!last) return;
                setUndo(undo.slice(0, -1));
                setSpec(last.spec);
                setName(last.name);
              }}
            >
              {st.undo}
            </button>
            <button type="button" className="btn" title={noFactors ? st.factors.empty : undefined} disabled={blocked || noFactors || !name.trim() || (!dirty && !!origin) || save.isPending} onClick={() => void doSave(false)}>
              {st.save}
            </button>
            <button type="button" className="btn soft" title={noFactors ? st.factors.empty : undefined} disabled={blocked || noFactors || save.isPending} onClick={() => void doSave(true)}>
              {st.saveAs}
            </button>
            <button type="button" className="btn soft" aria-pressed={showHistory} disabled={!origin} onClick={() => setShowHistory(!showHistory)}>
              {st.historyBtn}
            </button>
            <a className="btn soft" href={href.newStrategy()}>
              {st.newOne}
            </a>
            <a className="btn soft" href={href.strategy(origin?.id, true)}>
              {st.mine}
            </a>
            {blocked || noFactors ? (
              <button type="button" className="btn acc" disabled title={noFactors ? st.factors.empty : undefined}>
                {st.backtest}
              </button>
            ) : (
              <a className="btn acc" href={href.backtest({ use: origin && !dirty ? origin.id : "draft" })} title={st.backtestTitle}>
                {st.backtest}
              </a>
            )}
          </div>
        </div>
        {warnings.length > 0 && (
          <ul className="warn-list" aria-label="提示">
            {warnings.map((w, i) => (
              <li key={i}>
                <WarningText w={w} />
              </li>
            ))}
          </ul>
        )}
        {note && (
          <span role="status" className="ok small b">
            {note}
          </span>
        )}
        {save.isError && <Problem error={save.error} />}
        {check.isError && !apiErr && <Problem error={check.error} />}
        {ui.dev && (
          <div className="dev-row">
            {check.data && <span>{`内容哈希 ${check.data.strategy_hash}`}</span>}
            <button type="button" className="dev-btn" onClick={() => setShowJson(!showJson)}>
              {showJson ? st.dev.jsonHide : st.dev.json}
            </button>
            <button type="button" className="dev-btn" onClick={() => void navigator.clipboard?.writeText(commandOf("strategy_validate", { strategy: spec }))}>
              {st.dev.copy}
            </button>
          </div>
        )}
      </section>
      {ui.dev && showJson && <pre className="code-pre">{JSON.stringify(spec, null, 2)}</pre>}
      {showHistory && origin && (
        <History
          id={origin.id}
          spec={spec}
          onClose={() => setShowHistory(false)}
          onRestore={(s) => {
            edit((d) => {
              for (const k of Object.keys(d)) delete (d as Record<string, unknown>)[k];
              Object.assign(d, structuredClone(s));
            });
          }}
        />
      )}
      <div className="strat-layout">
        <div className="strat-main">
          <ScopeCard {...props} />
          <FiltersCard {...props} />
          <FactorsCard {...props} shares={check.data?.weight_shares ?? null} />
          <TradeCard {...props} schedule={schedule.data ?? null} />
        </div>
        {narrow ? (
          <aside className={`drawer ${drawer ? "open" : ""}`} aria-label={st.preview.title}>
            <button type="button" className="drawer-head" aria-expanded={drawer} onClick={() => setDrawer(!drawer)}>
              <span className="b">{blocked ? st.preview.drawerBlocked : st.preview.drawer(nT, nB)}</span>
              <span className="push small">{drawer ? st.preview.close : st.preview.open}</span>
            </button>
            {drawer && <div className="drawer-body">{preview}</div>}
          </aside>
        ) : (
          <aside className="strat-aside" aria-label={st.preview.title}>
            {preview}
          </aside>
        )}
      </div>
    </main>
  );
}
