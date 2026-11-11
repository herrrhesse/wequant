import { useEffect, useMemo, useState } from "react";
import { commandOf, useCall, useCallMutation, type Request } from "../../api/client";
import { ApiError } from "../../api/http";
import type { ErrorBody, SavedVersion, StrategySpec } from "../../api/types.gen";
import { Card } from "../../components/Card";
import { Command } from "../../components/Command";
import { Problem } from "../../components/Problem";
import { localTime } from "../../format";
import { useDebounced } from "../../hooks";
import { t } from "../../i18n";
import { href } from "../../router";
import { useDraft, type Origin } from "../../strategy/draft";
import { jsonLines, strategyLoc } from "../../strategy/locate";
import { dirtyState, summarySegments } from "../../strategy/summary";
import { FiltersEditor } from "./filters";
import { ErrorsContext, Seg, StepCard, locAttr } from "./shared";
import { FactorsStep, PoolStep, TradeStep } from "./steps";

/** The last save, kept across the remount that opening the saved version causes, so its message stays. */
let lastSave: SavedVersion | null = null;

/** Wait after the last edit before asking the backend (strategy validate and summary). */
const DEBOUNCE_MS = 300;

/** The strategy editor: five steps over one StrategySpec draft; every judgement on it comes from the backend, live. */
export function StrategyPage({ id, n }: { id: string | null; n: number | null }) {
  const s = t.strategy;
  const origin: Origin | null = useMemo(() => (id ? { id, n } : null), [id, n]);
  const { draft, setDraft, restored } = useDraft(origin);
  const [view, setView] = useState<"visual" | "code">("visual");
  const [showVersions, setShowVersions] = useState(false);

  // A saved version: loaded into the draft unless this browser holds edits made from it.
  const shown = useCall("strategy_show", id ? { id, n } : null);
  const [name, setName] = useState("");
  useEffect(() => {
    if (!shown.data) return;
    setName(shown.data.name);
    if (!restored) setDraft(shown.data.strategy);
    // Load once per opened version.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown.data]);

  const settled = useDebounced(draft, DEBOUNCE_MS);
  const request: Request<"strategy_validate"> = { strategy: settled };
  const keep = { placeholderData: <T,>(prev: T | undefined) => prev };
  const validation = useCall("strategy_validate", request, keep);
  const summary = useCall("strategy_summary", request, keep);
  // A result belongs to the request it answered; while the draft moved on it is shown as outdated, never as current.
  const current = JSON.stringify(settled) === JSON.stringify(draft) && !validation.isPlaceholderData;
  const usage = validation.error instanceof ApiError && validation.error.status === 422 ? validation.error.body : null;
  const error: ErrorBody | null = current ? usage : null;
  const details = error?.detail ?? [];
  const check = current && !validation.error ? (validation.data ?? null) : null;

  const save = useCallMutation("strategy_save", ["strategy_list", "strategy_versions", "strategy_validate"]);
  const archive = useCallMutation("strategy_archive", ["strategy_list", "strategy_versions"]);
  const saveRequest: Request<"strategy_save"> = { strategy: draft, id: id, name: name.trim() || null };
  const doSave = () =>
    save.mutate(saveRequest, {
      onSuccess: (v) => {
        lastSave = v;
        if (v.id !== id || v.n !== n) location.hash = href.strategy(v.id, v.n);
      },
    });
  const saved = save.data ?? (lastSave && lastSave.id === id && lastSave.n === n ? lastSave : null);

  const locate = (loc: (string | number)[]) => {
    const target = strategyLoc(loc);
    for (let k = target.length; k >= 0; k--) {
      const el = document.querySelector(`[data-loc="${CSS.escape(locAttr(target.slice(0, k)))}"]`);
      if (el) {
        el.scrollIntoView({ block: "center", behavior: "smooth" });
        return;
      }
    }
  };

  return (
    <main className="page">
      <div className="card-head">
        <h1>{s.title}</h1>
        <div className="card-meta">
          <input className="name-input" aria-label={s.name} placeholder={s.namePlaceholder} value={name} onChange={(e) => setName(e.target.value)} />
          {id && <span className="badge">{s.opened(id, shown.data?.n ?? n)}</span>}
          <button type="button" className="btn small" onClick={doSave} disabled={save.isPending || !!usage}>
            {save.isPending ? s.saving : s.save}
          </button>
          <button type="button" className="cmd" disabled={!id} onClick={() => setShowVersions(!showVersions)} aria-pressed={showVersions}>
            {s.versions}
          </button>
          <OpenSaved current={id} />
          <button type="button" className="cmd" disabled title={s.templateWhy}>
            {s.template}
          </button>
          <Seg label={s.code} value={view} options={[["visual", s.visual], ["code", s.code]]} onChange={setView} />
          <button
            type="button"
            className="cmd"
            onClick={() => {
              setDraft({});
              location.hash = href.strategy();
            }}
          >
            {s.newStrategy}
          </button>
        </div>
      </div>
      <p className="note">{s.versionRule}</p>
      {saved && (
        <p className="lead" role="status">
          {saved.created ? s.savedAs(saved.id, saved.n) : s.unchanged(saved.id, saved.n)}
        </p>
      )}
      {save.error && !(save.error instanceof ApiError && save.error.status === 422) && <Problem error={save.error} />}
      {shown.error && <Problem error={shown.error} />}
      {showVersions && id && <Versions id={id} onArchived={() => (location.hash = href.strategy())} archive={archive} />}

      <Card title={s.summaryTitle} command={commandOf("strategy_summary", request)}>
        {summary.data && !summary.error ? (
          <p className={current ? "summary" : "summary pending"} data-testid="summary">
            {summarySegments(summary.data.text, summary.data.parts).map(({ text, part }, i) =>
              part?.loc[0] ? (
                <button key={i} type="button" className="summary-part" onClick={() => locate(["strategy", ...part.loc[0]!])}>
                  {text}
                </button>
              ) : (
                <span key={i}>{text}</span>
              ),
            )}
          </p>
        ) : summary.error && !(summary.error instanceof ApiError && summary.error.status === 422) ? (
          <Problem error={summary.error} />
        ) : (
          <span className="note">{summary.isFetching || !current ? s.checking : s.summaryNone}</span>
        )}
      </Card>

      {error && (
        <Card title={s.errorsTitle}>
          {error.complete === false && <p className="approx-note">{s.incomplete}</p>}
          <ul className="serrors" data-testid="errors">
            {details.map((d, i) => (
              <li key={i}>
                <strong>{s.codes[d.code] ?? d.code}</strong> <span className="mono">{d.code}</span> · {s.where(strategyLoc(d.loc ?? []).join("."))}
                <span className="note"> · {d.message}</span>{" "}
                <button type="button" className="cmd" onClick={() => locate(d.loc ?? [])}>
                  {s.locate}
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {validation.error && !usage && <Problem error={validation.error} />}

      <div className="row2 strategy-row">
        <ErrorsContext.Provider value={details}>
          <div className="steps">{view === "visual" ? <Steps draft={draft} setDraft={setDraft} shares={check?.weight_shares ?? null} /> : <CodeView draft={draft} setDraft={setDraft} error={error} />}</div>
        </ErrorsContext.Provider>
        <aside className="side">
          <Card title={s.backendTitle}>
            <dl className="day">
              <div>
                <dt>{s.validation}</dt>
                <dd data-testid="validation">{!current ? s.checking : check ? "OK" : error ? s.invalid : t.common.none}</dd>
              </div>
              <div>
                <dt>{s.hash}</dt>
                <dd className="mono">{check ? check.strategy_hash : error ? s.noHash : t.common.none}</dd>
              </div>
              <div>
                <dt>{s.dirty}</dt>
                <dd data-testid="dirty">{check ? s.dirtyStates[dirtyState(check, origin)] : error ? s.noHash : t.common.none}</dd>
              </div>
            </dl>
            <span className="note">{s.dirtyWhy}</span>
          </Card>
          <Card title={s.commandTitle}>
            <span className="note">{s.commandNote}</span>
            <Command command={commandOf("strategy_validate", request)} />
          </Card>
        </aside>
      </div>
    </main>
  );
}

/** Open a saved strategy (strategy list). */
function OpenSaved({ current }: { current: string | null }) {
  const s = t.strategy;
  const list = useCall("strategy_list", {});
  return (
    <select
      aria-label={s.open}
      className="name-input"
      value={current ?? ""}
      onChange={(e) => (location.hash = e.target.value ? href.strategy(e.target.value) : href.strategy())}
    >
      <option value="">{list.data?.rows.length ? s.open : s.noSaved}</option>
      {(list.data?.rows ?? []).map((r) => (
        <option key={r.id} value={r.id}>
          {r.name} · v{r.current_n}
        </option>
      ))}
    </select>
  );
}

/** strategy versions: every version of the opened strategy; restoring opens that version as the draft. */
function Versions({ id, onArchived, archive }: { id: string; onArchived: () => void; archive: ReturnType<typeof useCallMutation<"strategy_archive">> }) {
  const s = t.strategy;
  const versions = useCall("strategy_versions", { id });
  return (
    <Card title={s.versionsTitle(versions.data?.name ?? id)} command={commandOf("strategy_versions", { id })}>
      {versions.error && <Problem error={versions.error} />}
      <table className="tbl" data-testid="versions">
        <thead>
          <tr>
            <th>{s.versionCol}</th>
            <th>{s.hash}</th>
            <th>{s.savedAt}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(versions.data?.rows ?? []).map((v) => (
            <tr key={v.n}>
              <td className="num">
                v{v.n} {v.current && <span className="badge acc">{s.currentVersion}</span>}
              </td>
              <td className="mono">{v.strategy_hash}</td>
              <td className="num">{localTime(v.saved_at)}</td>
              <td>
                <a className="link" href={href.strategy(id, v.n)}>
                  {s.restore}
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <span className="note">{s.restoreNote}</span>
      <div className="cond-row">
        <button type="button" className="cmd" disabled={archive.isPending} onClick={() => archive.mutate({ id }, { onSuccess: onArchived })}>
          {s.archive}
        </button>
        {archive.error && <Problem error={archive.error} />}
      </div>
    </Card>
  );
}

function Steps({ draft, setDraft, shares }: { draft: StrategySpec; setDraft: (s: StrategySpec) => void; shares: Record<string, number> | null }) {
  const s = t.strategy;
  const set = <K extends keyof StrategySpec>(key: K, value: StrategySpec[K]) => {
    const next = { ...draft };
    const empty = value === undefined || (Array.isArray(value) && value.length === 0) || (key === "pool" && value && !Object.keys(value).length);
    if (empty) delete next[key];
    else next[key] = value;
    setDraft(next);
  };
  return (
    <>
      <StepCard n={1} title={s.steps[0]!}>
        <PoolStep pool={draft.pool ?? {}} onChange={(p) => set("pool", p)} />
      </StepCard>
      <StepCard n={2} title={s.steps[1]!}>
        <FiltersEditor filters={draft.filters ?? []} onChange={(f) => set("filters", f)} />
      </StepCard>
      <StepCard n={3} title={s.steps[2]!}>
        <FactorsStep factors={draft.factors ?? []} shares={shares} onChange={(f) => set("factors", f)} />
      </StepCard>
      <StepCard n={4} title={s.steps[3]!}>
        <TradeStep spec={draft} onChange={setDraft} />
      </StepCard>
      <StepCard n={5} title={s.steps[4]!} disabled>
        <p className="note">{s.risk.note}</p>
      </StepCard>
    </>
  );
}

/** The draft as JSON; error lines are marked from ErrorDetail.loc. Applying parses the text; validity is the backend's call. */
function CodeView({ draft, setDraft, error }: { draft: StrategySpec; setDraft: (s: StrategySpec) => void; error: ErrorBody | null }) {
  const { text, lines } = useMemo(() => jsonLines(draft), [draft]);
  const [edited, setEdited] = useState<string | null>(null);
  const [bad, setBad] = useState(false);
  const marked = new Set(
    (error?.detail ?? []).map((d) => {
      const loc = strategyLoc(d.loc ?? []);
      for (let n = loc.length; n >= 0; n--) {
        const line = lines.get(loc.slice(0, n).join("."));
        if (line) return line;
      }
      return 1;
    }),
  );
  const shown = edited ?? text;
  return (
    <section className="card code-view">
      <div className="code-wrap">
        <pre className="gutter mono" aria-hidden>
          {shown.split("\n").map((_, i) => (
            <span key={i} className={edited === null && marked.has(i + 1) ? "err" : undefined}>
              {i + 1}
              {"\n"}
            </span>
          ))}
        </pre>
        <textarea className="mono" spellCheck={false} value={shown} rows={Math.max(12, shown.split("\n").length)} onChange={(e) => setEdited(e.target.value)} />
      </div>
      <div className="cond-row">
        <button
          type="button"
          className="cmd"
          disabled={edited === null}
          onClick={() => {
            try {
              const v: unknown = JSON.parse(edited ?? "");
              if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("not an object");
              setDraft(v as StrategySpec);
              setEdited(null);
              setBad(false);
            } catch {
              setBad(true);
            }
          }}
        >
          {t.strategy.visual}
        </button>
        {bad && <span className="sfield-hint">{t.strategy.filters.badJson}</span>}
      </div>
    </section>
  );
}
