import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { dirtyState, summarySegments } from "../../strategy/summary";
import { exampleError, strategyExamples, summarizeStrategy, validateStrategy } from "../../api/strategy";
import type { ErrorBody, StrategySpec } from "../../api/types.gen";
import { Card } from "../../components/Card";
import { Command } from "../../components/Command";
import { t } from "../../i18n";
import { useDraft } from "../../strategy/draft";
import { jsonLines, strategyLoc } from "../../strategy/locate";
import { FiltersEditor } from "./filters";
import { ErrorsContext, Seg, StepCard, backendNote, locAttr } from "./shared";
import { FactorsStep, PoolStep, TradeStep } from "./steps";

/** The strategy editor: five steps over one StrategySpec draft; every judgement on it comes from the backend. */
export function StrategyPage() {
  const s = t.strategy;
  const [draft, setDraft] = useDraft();
  const [view, setView] = useState<"visual" | "code">("visual");
  const [name, setName] = useState("");
  const [shownError, setShownError] = useState<ErrorBody | null>(null);
  const validation = useQuery({ queryKey: ["strategy", "validate", draft], queryFn: () => validateStrategy(draft) });
  const summary = useQuery({ queryKey: ["strategy", "summary", draft], queryFn: () => summarizeStrategy(draft) });
  const examples = useQuery({ queryKey: ["strategy", "examples"], queryFn: strategyExamples }).data ?? [];
  const check = validation.data?.state === "ok" ? validation.data.data : null;
  // The backend's ErrorBody for this draft (a local example), else the synthetic demonstration when shown.
  const realError = validation.data?.state === "error" ? validation.data.error : null;
  const error = realError ?? shownError;
  const details = error?.detail ?? [];
  const command = `echo '${JSON.stringify(draft)}' | wequant strategy validate --strategy - --json`;

  const locate = (loc: (string | number)[]) => {
    const target = strategyLoc(loc);
    // The control registered at the longest prefix of the error's path.
    for (let n = target.length; n >= 0; n--) {
      const el = document.querySelector(`[data-loc="${CSS.escape(locAttr(target.slice(0, n)))}"]`);
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
          <span className="badge warn">{s.unsaved}</span>
          <button type="button" className="cmd" disabled title={s.saveWhy}>
            {s.save}
          </button>
          <button type="button" className="cmd" disabled title={s.saveWhy}>
            {s.versions}
          </button>
          <button type="button" className="cmd" disabled title={s.templateWhy}>
            {s.template}
          </button>
          <Seg label={s.code} value={view} options={[["visual", s.visual], ["code", s.code]]} onChange={setView} />
          <button type="button" className="cmd" onClick={() => setDraft({})}>
            {s.clear}
          </button>
        </div>
      </div>
      <p className="note">
        {s.versionRule} · {s.saveWhy} · {backendNote("strategy_save", "wequant strategy save / list / versions")}
      </p>

      <Card title={s.summaryTitle}>
        {summary.data?.state === "ok" ? (
          <p className="summary">
            {summarySegments(summary.data.data.text, summary.data.data.parts).map(({ text, part }, i) =>
              part?.loc[0] ? (
                <button key={i} type="button" className="summary-part" onClick={() => locate(["strategy", ...part.loc[0]!])}>
                  {text}
                </button>
              ) : (
                <span key={i}>{text}</span>
              ),
            )}
          </p>
        ) : (
          <span className="note">
            {backendNote("strategy_summary", "wequant strategy summary")} · {s.summaryWhy}
          </span>
        )}
      </Card>

      {error && (
        <Card title={s.errorsTitle} extra={<span className="badge warn">{realError ? s.fixtureBadge : t.synthetic.badge}</span>}>
          <span className="note">{realError ? s.fixtureNote : s.demoNote}</span>
          {error.complete === false && <p className="approx-note">{s.incomplete}</p>}
          <ul className="serrors">
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

      <div className="row2 strategy-row">
        <ErrorsContext.Provider value={details}>
          <div className="steps">{view === "visual" ? <Steps draft={draft} setDraft={setDraft} shares={check?.weight_shares ?? null} /> : <CodeView draft={draft} setDraft={setDraft} error={error} />}</div>
        </ErrorsContext.Provider>
        <aside className="side">
          <Card title={s.backendTitle}>
            <dl className="day">
              <div>
                <dt>{s.validation}</dt>
                <dd>{check ? "OK" : realError ? s.invalid : backendNote("strategy_validate", "wequant strategy validate")}</dd>
              </div>
              <div>
                <dt>{s.hash}</dt>
                <dd className="mono">{check ? check.strategy_hash : realError ? s.noHash : backendNote("strategy_validate", "wequant strategy validate")}</dd>
              </div>
              <div>
                <dt>{s.dirty}</dt>
                <dd>{check ? s.dirtyStates[dirtyState(check, null)] : realError ? s.noHash : backendNote("strategy_validate", "wequant strategy validate")}</dd>
              </div>
            </dl>
            <span className="note">{s.dirtyWhy}</span>
            {examples.length > 0 && (
              <>
                <span className="stat-l">{s.examples}</span>
                <div className="cond-row">
                  {examples.map((ex) => (
                    <button key={ex.name} type="button" className="cmd" onClick={() => setDraft(ex.strategy)}>
                      {s.exampleNames[ex.name] ?? ex.name}
                    </button>
                  ))}
                </div>
                <span className="note">{s.examplesNote}</span>
              </>
            )}
            {examples.length === 0 && (
              <button type="button" className="cmd" onClick={() => setShownError(shownError ? null : exampleError)}>
                {shownError ? s.hideError : s.demoError}
              </button>
            )}
          </Card>
          <Card title={s.commandTitle}>
            <span className="note">{s.commandNote}</span>
            <Command command={command} />
          </Card>
        </aside>
      </div>
    </main>
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
