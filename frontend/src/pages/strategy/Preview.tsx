// The right column of the strategy editor: the funnel of the draft on the latest trading day, the picked stocks with
// their judgment chain, "查股票" and the latest backtest of this strategy. Everything shown comes from screen,
// explain and the run records; the panel fades while a newer preview is on its way instead of jumping.
import { Stock } from "../../components/Stock";
import { useState } from "react";
import { useCall } from "../../api/client";
import { cancelJob } from "../../api/jobs";
import type { JobInfo, ScreenResult, StrategySpec } from "../../api/types.gen";
import { fixed, pct, tone } from "../../format";
import { useLabels } from "../../labels";
import { href } from "../../router";
import { tx } from "../../text";
import { POOL_STEP_NAMES, st } from "./text";

const OPS: Record<string, string> = { gt: ">", ge: "≥", lt: "<", le: "≤", eq: "=", between: "介于" };
const ROLE_STEP = { target: "targets", backup: "backups", none: "scored" } as const;

export function stepName(step: string, kind: string, of: (t: "funnel_kinds", c: string) => string): string {
  if (kind === "pool") return POOL_STEP_NAMES[step] ?? step;
  if (kind === "condition" || kind === "group") return step;
  return of("funnel_kinds", kind);
}

interface Props {
  spec: StrategySpec;
  screen: ScreenResult | undefined;
  fading: boolean;
  blocked: boolean;
  hl: string | null;
  setHl: (s: string | null) => void;
  strategyId: string | null;
  draftHash: string | null;
  job: JobInfo | null;
}

export function Preview(props: Props) {
  return (
    <>
      <Funnel {...props} />
      <Picks {...props} />
      <Lookup {...props} />
      <LastRun {...props} />
    </>
  );
}

function Funnel({ screen, fading, blocked, hl, setHl }: Props) {
  const L = useLabels();
  const steps = screen?.funnel ?? [];
  const top = steps[0]?.left || 1;
  return (
    <section className={`card ${fading ? "pending" : ""}`} aria-label={st.preview.funnel}>
      <div className="card-head">
        <h2>{st.preview.title}</h2>
        {screen && <span className="push small muted">{st.preview.date(screen.date)}</span>}
      </div>
      {blocked && <span className="note">{st.preview.blocked}</span>}
      {!blocked && screen && (
        <ol className="funnel">
          {steps.map((s, i) => {
            const n = s.left ?? s.passed;
            const prev = steps.slice(0, i).reverse().find((x) => x.chain && x.left !== null);
            const out = s.chain && s.left !== null && prev?.left != null && s.kind !== "targets" && s.kind !== "backups" ? prev.left - s.left : 0;
            return (
              <li key={`${s.step}/${i}`} className={s.chain ? "" : "off-chain"}>
                <button type="button" aria-pressed={hl === s.step} onClick={() => setHl(hl === s.step ? null : s.step)}>
                  <span className="funnel-name">
                    {s.chain ? "" : "└ "}
                    {stepName(s.step, s.kind, L.of)}
                    {s.left === null ? "（命中）" : ""}
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

function Picks({ spec, screen, fading, blocked }: Props) {
  const L = useLabels();
  const [open, setOpen] = useState<string | null>(null);
  if (blocked || !screen) return null;
  const rows = [...screen.rows.filter((r) => r.role === "target"), ...screen.rows.filter((r) => r.role === "backup")];
  const nT = screen.rows.filter((r) => r.role === "target").length;
  const factors = rows[0]?.factors.map((f) => f.name) ?? [];
  return (
    <section className={`card ${fading ? "pending" : ""}`} aria-label="入选名单">
      <div className="card-head">
        <h2>{st.preview.picks(nT, rows.length - nT)}</h2>
        <a className="link small push" href={href.picks({ draft: true })}>
          选股页 ›
        </a>
      </div>
      {rows.length === 0 && <span className="note">{st.preview.none}</span>}
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
  const L = useLabels();
  const ex = useCall("explain", { strategy: spec, code, date });
  const e = ex.data;
  const mark = (ok: boolean | null) => (ok === null ? <span className="muted">·</span> : ok ? <span className="ok">✓</span> : <span className="bad">✗</span>);
  return (
    <div className="inner chain">
      <div className="row">
        <span className="b">{st.preview.chain(code)}</span>
        <button type="button" className="btn sm soft push" onClick={onClose}>
          {tx.common.close}
        </button>
      </div>
      {ex.isPending && <span className="note">{tx.app.loading}</span>}
      {e && (
        <>
          {e.pool.map((p) => (
            <div key={p.name} className="chain-row">
              {mark(p.passed)}
              <span>{POOL_STEP_NAMES[p.name] ?? (L.of("funnel_kinds", p.name) || p.name)}</span>
            </div>
          ))}
          {e.steps.map((s) => (
            <div key={s.name} className={`chain-row ${s.chain ? "" : "sub-row"}`}>
              {mark(s.passed)}
              <span>{s.name}</span>
              {s.value !== null && <span className="muted small num">{Number(s.value.toPrecision(6))}</span>}
            </div>
          ))}
          {e.score && (
            <>
              {e.score.factors.map((f) => (
                <div key={f.name} className="chain-row">
                  <span className="muted">·</span>
                  <span>
                    {f.name} 排名分 {(f.adjusted_pct * 100).toFixed(1)} × {Math.round(f.weight_share * 100)}%
                  </span>
                  <span className="muted small num">= {(f.contribution * 100).toFixed(1)}</span>
                </div>
              ))}
              <span className="small b">{st.preview.total((e.score.score * 100).toFixed(1), e.score.rank, L.of("funnel_kinds", ROLE_STEP[e.role]), e.weight === null ? null : pct(e.weight, 1))}</span>
            </>
          )}
        </>
      )}
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

function LastRun({ spec, strategyId, draftHash, job }: Props) {
  const L = useLabels();
  const runs = useCall("runs_list", strategyId ? { limit: 100 } : null);
  const last = runs.data?.rows.find((r) => r.saved_as.some((s) => s.id === strategyId) || r.strategy_hash === draftHash) ?? null;
  const detail = useCall("runs_show", last ? { id: last.id } : null);
  const stale = !!last && !!draftHash && last.strategy_hash !== draftHash;
  const daily = detail.data?.daily ?? [];
  const curve = (key: "nav" | "benchmark_nav") => {
    const vs = daily.map((d) => d[key]).filter((v): v is number => v !== null);
    const all = daily.flatMap((d) => [d.nav, d.benchmark_nav]).filter((v): v is number => v !== null);
    const lo = Math.min(...all);
    const hi = Math.max(...all);
    return vs.map((v, i) => `${i ? "L" : "M"}${((i / Math.max(1, vs.length - 1)) * 300).toFixed(1)} ${(54 - ((v - lo) / (hi - lo || 1)) * 52).toFixed(1)}`).join(" ");
  };
  const years = last ? (Date.parse(last.end) - Date.parse(last.start)) / (365.25 * 864e5) : 0;
  const running = job && (job.state === "queued" || job.state === "running");
  return (
    <section className="card" aria-label={st.preview.lastRun}>
      <div className="card-head">
        <h2>{st.preview.lastRun}</h2>
        {last && (
          <a className="link small push" href={href.backtest({ run: last.id })}>
            {st.preview.full}
          </a>
        )}
      </div>
      {running && job && (
        <div className="inner">
          <div className="row small">
            <span className="b">{L.of("job_states", job.state)}</span>
            <span className="sub grow">{L.stage(job.stage, job.params)}</span>
            <button type="button" className="btn sm ghost" onClick={() => void cancelJob(job.id)}>
              {st.runs.cancel}
            </button>
          </div>
          <div className="progress">
            <span style={{ width: `${Math.round(job.fraction * 100)}%` }} />
          </div>
        </div>
      )}
      {job?.state === "failed" && <span className="problem">{L.stage(job.stage, job.params)}</span>}
      {!last && !running && <span className="note">{st.preview.noRun}</span>}
      {last && (
        <div className={stale ? "pending col" : "col"}>
          <span className="small sub">
            {last.start} 至 {last.end}
          </span>
          <div className="row">
            <span className="kv">
              {st.runs.ann} <b className={`num ${tone(last.annual_return)}`}>{pct(last.annual_return, 1, true)}</b>
            </span>
            <span className="kv">
              {st.runs.mdd} <b className="num">{pct(last.max_drawdown, 1)}</b>
            </span>
            <span className="kv">
              {st.runs.sharpe} <b className="num">{fixed(last.sharpe)}</b>
            </span>
          </div>
          {daily.length > 1 && (
            <svg viewBox="0 0 300 56" preserveAspectRatio="none" className="mini-curve" aria-hidden="true">
              <path d={curve("benchmark_nav")} className="bench" />
              <path d={curve("nav")} className="nav" />
            </svg>
          )}
        </div>
      )}
      {stale && <span className="warnbox">{st.preview.stale}</span>}
      {last && years < 5 && <span className="note">{st.preview.shortRange(years.toFixed(1))}</span>}
      {(spec.top_n ?? 0) > 0 && (spec.top_n ?? 0) < 5 && <span className="note">{st.preview.fewHold(spec.top_n ?? 0)}</span>}
    </section>
  );
}
