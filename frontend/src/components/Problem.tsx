// How a failed request reads on the page: the backend's ErrorBody explained by its status, or "backend not running".
import { useState } from "react";
import { ApiError, Unreachable } from "../api/http";
import { t } from "../i18n";
import { useLabels } from "../labels";
import { useUi } from "../ui";

const p = t.problem;

/** One error, explained. 422 errors are usually located on controls instead; this is the fallback. */
export function Problem({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { dev } = useUi();
  const L = useLabels();
  if (error instanceof Unreachable) return <p className="problem">{p.unreachable}</p>;
  if (!(error instanceof ApiError)) return <p className="problem">{String(error)}</p>;
  const b = error.body;
  const head =
    error.status === 404 ? p.notFound : error.status === 409 ? p.locked : error.status === 503 ? p.unavailable : error.status >= 500 ? p.internal : p.usage;
  return (
    <div className="problem" role="alert">
      <strong>{head}</strong>
      {dev && <div className="mono small">{b.message}</div>}
      {b.detail && b.detail.length > 0 && (
        <ul className="serrors">
          {b.detail.map((d, i) => (
            <li key={i}>
              {L.detail(d)}
              {dev && <span className="mono"> · {d.code} · {(d.loc ?? []).join(".")} · {d.message}</span>}
            </li>
          ))}
        </ul>
      )}
      {error.status === 422 && b.complete === false && <div className="note">{p.incomplete}</div>}
      {dev && error.status >= 500 && error.status !== 503 && <CopyReport text={`${error.operation} ${error.status} ${b.name}: ${b.message}`} />}
      {(error.status === 409 || error.status === 503) && onRetry && (
        <button type="button" className="cmd" onClick={onRetry}>
          {p.retry}
        </button>
      )}
    </div>
  );
}

function CopyReport({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="cond-row">
      <span className="mono note">{text}</span>
      <button
        type="button"
        className="cmd"
        onClick={() =>
          navigator.clipboard.writeText(text).then(
            () => setCopied(true),
            () => {},
          )
        }
      >
        {copied ? t.common.copied : p.copyReport}
      </button>
    </div>
  );
}

/** Full page: no `wequant serve` answered. Never falls back to sample data. */
export function BackendDown({ onRetry }: { onRetry: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <main className="page">
      <section className="card backend-down" role="alert" aria-label={p.downTitle}>
        <h1>{p.downTitle}</h1>
        <p className="lead">{p.downLead}</p>
        <div className="cond-row">
          <code className="mono down-cmd">{p.serveCommand}</code>
          <button
            type="button"
            className="cmd"
            onClick={() =>
              navigator.clipboard.writeText(p.serveCommand).then(
                () => setCopied(true),
                () => {},
              )
            }
          >
            {copied ? t.common.copied : t.common.copy}
          </button>
          <button type="button" className="btn" onClick={onRetry}>
            {p.retry}
          </button>
        </div>
        <span className="note">{p.downNote}</span>
      </section>
    </main>
  );
}
