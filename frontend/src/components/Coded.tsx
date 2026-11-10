/** A backend code shown by its Chinese name, with the code itself kept visible; unknown codes show as is. */
export function Coded({ code, names }: { code: string; names: Record<string, string> }) {
  const name = names[code];
  if (!name) return <span className="mono">{code}</span>;
  return (
    <span className="coded" title={code}>
      {name}
      <span className="mono code-sub">{code}</span>
    </span>
  );
}
