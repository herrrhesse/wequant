// Choose one stock by code or name (data securities, as the user types); Enter takes the first match.
import { useState } from "react";
import { useCall } from "../api/client";
import { useDebounced } from "../hooks";

export function StockPick({ label, code, name, onPick }: { label: string; code: string | null; name?: string | null; onPick: (code: string, name: string | null) => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const query = useDebounced(q.trim(), 200);
  const found = useCall("data_securities", query ? { query, limit: 6 } : null);
  const rows = found.data?.rows ?? [];
  const pick = (c: string, n: string | null) => {
    onPick(c, n);
    setQ("");
    setOpen(false);
  };
  return (
    <div className="search stock-pick">
      <input
        className="input"
        type="search"
        aria-label={label}
        autoComplete="off"
        placeholder={code ? `${name ? `${name} ` : ""}${code}` : "输入代码或名称"}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && rows[0]) pick(rows[0].code, rows[0].name ?? null);
          if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && query && found.data && (
        <div className="search-pop" role="listbox" aria-label={label}>
          {rows.map((r) => (
            <button key={r.code} type="button" role="option" aria-selected={r.code === code} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(r.code, r.name ?? null)}>
              <span>
                {r.name && <b>{r.name} </b>}
                <span className="muted small">{r.code}</span>
              </span>
            </button>
          ))}
          {rows.length === 0 && <span className="search-none">没有找到</span>}
        </div>
      )}
    </div>
  );
}
