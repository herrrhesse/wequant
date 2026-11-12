/** A stock as the pages show it: its name (when the backend has one) and its code. */
export function Stock({ code, name, link = false }: { code: string; name?: string | null; link?: boolean }) {
  const body = (
    <>
      {name ? <b>{name}</b> : <b>{code}</b>}
      {name && <span className="tiny muted"> {code}</span>}
    </>
  );
  return link ? (
    <a className="stock-ref" href={`#/stock/${encodeURIComponent(code)}`} onClick={(e) => e.stopPropagation()}>
      {body}
    </a>
  ) : (
    <span className="stock-ref">{body}</span>
  );
}
