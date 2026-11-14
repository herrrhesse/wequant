// The right column of the strategy editor: every number about the draft lives here, not in the editing column.
// A status line says when the preview is being updated (from the moment of the edit), when it has been updated, or
// that the editing column has something to fix. Then the funnel of the latest trading day (each step's count, its
// cut and the cut it makes on its own), the picked stocks with their judgment chain, and "查股票".
import { useEffect, useRef, useState } from "react";
import { useCall } from "../../api/client";
import type { ScreenResult, StrategySpec } from "../../api/types.gen";
import { ChainView } from "../../components/ChainView";
import { Stock } from "../../components/Stock";
import { useLabels } from "../../labels";
import { href } from "../../router";
import { counts, cutAlone } from "../../strategy/model";
import { tx } from "../../text";
import { POOL_STEP_NAMES, st } from "./text";

const OPS: Record<string, string> = { gt: ">", ge: "≥", lt: "<", le: "≤", eq: "=", between: "介于" };
const ROLE_STEP = { target: "targets", backup: "backups", none: "scored" } as const;

export function stepName(step: string, kind: string, of: (t: "funnel_kinds" | "pool_items", c: string) => string): string {
  if (kind === "pool") return of("pool_items", step) || POOL_STEP_NAMES[step] || step;
  if (kind === "condition" || kind === "group") return step;
  return of("funnel_kinds", kind);
}

interface Props {
  spec: StrategySpec;
  screen: ScreenResult | undefined;
  /** An edit is waiting for its preview (from the moment of the edit until the newest answer arrives). */
  updating: boolean;
  /** How many places in the editing column need fixing. */
  errors: number;
  blocked: boolean;
  /** No enabled factor yet: the funnel shows the stages it can compute, and the picks how to go on. */
  noFactors: boolean;
  hl: string | null;
  setHl: (s: string | null) => void;
}

export function Preview(props: Props) {
  const fading = props.updating ? "pending strong" : "";
  return (
    <>
      <Funnel {...props} fading={fading} />
      <Picks {...props} fading={fading} />
      <Lookup {...props} />
    </>
  );
}

/** Scroll the editing column to the row of a funnel step, or to the first marked error. */
function locate(step: string | null) {
  const el = step ? document.querySelector(`[data-step="${CSS.escape(step)}"]`) : document.querySelector(".has-err");
  el?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function Status({ updating, errors, blocked, screen }: Props) {
  const [fresh, setFresh] = useState(false);
  const was = useRef(updating);
  useEffect(() => {
    if (was.current && !updating) {
      setFresh(true);
      const t = setTimeout(() => setFresh(false), 2500);
      was.current = updating;
      return () => clearTimeout(t);
    }
    was.current = updating;
  }, [updating]);
  if (errors > 0 || blocked)
    return (
      <button type="button" className="pv-status err" onClick={() => locate(null)}>
        {st.preview.fix(errors)}
      </button>
    );
  if (updating)
    return (
      <span className="pv-status busy" role="status">
        <span className="spinner" aria-hidden="true" />
        {st.preview.updating}
      </span>
    );
  if (!screen) return null;
  return (
    <span className={`pv-status ${fresh ? "done" : "idle"}`} role="status">
      {fresh ? st.preview.updated(screen.date) : st.preview.date(screen.date)}
    </span>
  );
}

function Funnel(props: Props & { fading: string }) {
  const { screen, blocked, noFactors, hl, setHl, fading } = props;
  const L = useLabels();
  const steps = (screen?.funnel ?? []).filter((s) => !noFactors || (s.kind !== "scored" && s.kind !== "targets" && s.kind !== "backups"));
  const top = steps[0]?.left || 1;
  const c = counts(screen?.funnel);
  return (
    <section className="card" aria-label={st.preview.funnel}>
      <div className="card-head">
        <h2>{st.preview.title}</h2>
        <span className="push" />
        <Status {...props} />
      </div>
      {!blocked && screen && (
        <ol className={`funnel ${fading}`}>
          {steps.map((s, i) => {
            const n = s.left ?? s.passed;
            const prev = steps.slice(0, i).reverse().find((x) => x.chain && x.left !== null);
            const out = s.chain && s.left !== null && prev?.left != null && s.kind !== "targets" && s.kind !== "backups" ? prev.left - s.left : 0;
            const alone = s.kind === "pool" || s.kind === "condition" ? cutAlone(c, s.step, s.kind) : null;
            return (
              <li key={`${s.step}/${i}`} className={s.chain ? "" : "off-chain"}>
                <button
                  type="button"
                  aria-pressed={hl === s.step}
                  onMouseEnter={() => setHl(s.step)}
                  onMouseLeave={() => setHl(null)}
                  onClick={() => locate(s.step)}
                >
                  <span className="funnel-name">
                    {s.chain ? "" : "└ "}
                    {stepName(s.step, s.kind, L.of)}
                    {s.left === null ? "（命中）" : ""}
                    {alone !== null && s.left !== null && <span className="tiny muted funnel-alone">{st.filters.cut(alone)}</span>}
                  </span>
                  <span className="funnel-bar">
                    <span style={{ width: `${Math.max(1, (n / top) * 100)}%` }} />
                  </span>
                  <span className="num b">{n}</span>
                  <span className="num funnel-out">{out > 0 ? `−${out}` : ""}</span>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

function Picks({ spec, screen, fading, blocked, noFactors }: Props & { fading: string }) {
  const L = useLabels();
  const [open, setOpen] = useState<string | null>(null);
  if (noFactors)
    return (
      <section className="card" aria-label="入选名单">
        <div className="empty">{st.factors.empty}</div>
      </section>
    );
  if (blocked || !screen) return null;
  const rows = [...screen.rows.filter((r) => r.role === "target"), ...screen.rows.filter((r) => r.role === "backup")];
  const nT = screen.rows.filter((r) => r.role === "target").length;
  const factors = rows[0]?.factors.map((f) => f.name) ?? [];
  return (
    <section className={`card ${fading}`} aria-label="入选名单">
      <div className="card-head">
        <h2>{st.preview.picks(nT, rows.length - nT)}</h2>
        <a className="link small push" href={href.picks({ draft: true })}>
          选股页 ›
        </a>
      </div>
      {rows.length === 0 && <div className="empty">{st.preview.none}</div>}
      {rows.length > 0 && (
        <div className="tbl-wrap" style={{ maxHeight: 360 }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>{st.preview.rank}</th>
                <th>{st.preview.stock}</th>
                <th className="r">{st.preview.score}</th>
                {factors.map((f) => (
                  <th key={f} className="r">
                    {f}
                  </th>
                ))}
                <th className="r">{st.preview.role}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.code} className="clickable" aria-selected={open === r.code} onClick={() => setOpen(open === r.code ? null : r.code)}>
                  <td className="num muted">{r.rank}</td>
                  <td><Stock code={r.code} name={r.name} /></td>
                  <td className="num r">{(r.score * 100).toFixed(1)}</td>
                  {r.factors.map((f) => (
                    <td key={f.name} className="num r muted">
                      {(f.adjusted_pct * 100).toFixed(0)}
                    </td>
                  ))}
                  <td className="r">
                    <span className={`role ${r.role}`}>{L.of("funnel_kinds", ROLE_STEP[r.role])}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && <Chain spec={spec} code={open} date={screen.date} onClose={() => setOpen(null)} />}
    </section>
  );
}

function Chain({ spec, code, date, onClose }: { spec: StrategySpec; code: string; date: string; onClose: () => void }) {
  const ex = useCall("explain", { strategy: spec, code, date });
  const e = ex.data;
  return (
    <div className="inner chain">
      <div className="row">
        <span className="b">{st.preview.chain(code)}</span>
        <button type="button" className="btn sm soft push" onClick={onClose}>
          {tx.common.close}
        </button>
      </div>
      {ex.isPending && <span className="note">{tx.app.loading}</span>}
      {e && <ChainView e={e} spec={spec} />}
    </div>
  );
}

function Lookup({ screen, blocked }: Props) {
  const L = useLabels();
  const [q, setQ] = useState("");
  if (blocked || !screen) return null;
  const s = q.trim().toUpperCase();
  const row = s ? screen.rows.find((r) => r.code === s || r.code.startsWith(s)) : undefined;
  const rej = s && !row ? screen.rejects.find((r) => r.code === s || r.code.startsWith(s)) : undefined;
  return (
    <section className="card" aria-label={st.preview.lookup}>
      <div className="card-head">
        <h2>{st.preview.lookup}</h2>
      </div>
      <input className="input" aria-label={st.preview.lookup} placeholder={st.preview.lookupPlaceholder} value={q} onChange={(e) => setQ(e.target.value)} />
      {s && row && (
        <div className="inner lookup ok-bg">
          <span className="b">{st.preview.picked(row.name ?? row.code, L.of("funnel_kinds", ROLE_STEP[row.role]))}</span>
          <span className="small">{st.preview.pickedLine((row.score * 100).toFixed(1), row.rank)}</span>
        </div>
      )}
      {s && rej && (
        <div className="inner lookup bad-bg">
          <span className="b">{st.preview.stuck(rej.code)}</span>
          <span className="small">{st.preview.stuckAt(stepName(rej.step, rej.kind, L.of))}</span>
          {rej.value !== null && rej.threshold !== null && (
            <span className="small sub num">
              {Number(rej.value.toPrecision(6))} / 要求 {rej.op ? OPS[rej.op] : ""} {Number(rej.threshold.toPrecision(6))}
            </span>
          )}
        </div>
      )}
      {s && !row && !rej && (
        <div className="inner lookup">
          <span className="b">{st.preview.notFound(q.trim())}</span>
          <span className="small sub">{st.preview.notFoundLine}</span>
        </div>
      )}
    </section>
  );
}

