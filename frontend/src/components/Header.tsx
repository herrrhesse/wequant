import { useState } from "react";
import { useCall } from "../api/client";
import { useDebounced } from "../hooks";
import type { Conv, Theme } from "../prefs";
import { href, type Page } from "../router";
import { tx } from "../text";

const NAV: [Exclude<Page, "stock">, string, string][] = [
  ["today", tx.nav.today, href.today()],
  ["strategy", tx.nav.strategy, href.strategy()],
  ["backtest", tx.nav.backtest, href.backtest()],
  ["picks", tx.nav.picks, href.picks()],
  ["data", tx.nav.data, href.data()],
];

const ICONS: Record<Theme, string> = {
  light: "M10 6.5 A3.5 3.5 0 1 0 10 13.5 A3.5 3.5 0 1 0 10 6.5 Z M10 2 V3.5 M10 16.5 V18 M2 10 H3.5 M16.5 10 H18 M4.3 4.3 L5.4 5.4 M14.6 14.6 L15.7 15.7 M4.3 15.7 L5.4 14.6 M14.6 5.4 L15.7 4.3",
  dark: "M15.5 12.5 A6.5 6.5 0 1 1 7.5 4.5 A5 5 0 0 0 15.5 12.5 Z",
  system: "M3 4 H17 V13 H3 Z M7 16.5 H13 M10 13 V16.5",
};

interface Props {
  page: Page;
  theme: Theme;
  conv: Conv;
  dev: boolean;
  setTheme: (t: Theme) => void;
  setConv: (c: Conv) => void;
  setDev: (d: boolean) => void;
}

export function Header(props: Props) {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <a className="brand" href={href.today()}>
          <span className="brand-mark">W</span>
          {tx.app.brand}
        </a>
        <nav className="nav" aria-label={tx.nav.label}>
          {NAV.map(([key, label, to]) => (
            <a key={key} href={to} aria-current={props.page === key ? "page" : undefined}>
              {label}
            </a>
          ))}
        </nav>
        <Search from={props.page} />
        <div className="prefs">
          <button type="button" className="pref-btn" aria-label={tx.prefs.conv} onClick={() => props.setConv(props.conv === "cn" ? "intl" : "cn")}>
            <span className="conv-dot" />
            {props.conv === "cn" ? tx.prefs.cn : tx.prefs.intl}
          </button>
          <div className="seg icons" role="group" aria-label={tx.prefs.theme}>
            {(["light", "dark", "system"] as const).map((k) => (
              <button key={k} type="button" aria-pressed={props.theme === k} aria-label={tx.prefs[k]} title={tx.prefs[k]} onClick={() => props.setTheme(k)}>
                <svg width="16" height="16" viewBox="0 0 20 20" aria-hidden="true">
                  <path d={ICONS[k]} />
                </svg>
              </button>
            ))}
          </div>
          <button type="button" className="pref-btn dev" aria-pressed={props.dev} title={tx.prefs.devTitle} onClick={() => props.setDev(!props.dev)}>
            <svg width="14" height="14" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M7 6 L3 10 L7 14 M13 6 L17 10 L13 14" />
            </svg>
            {props.dev ? tx.prefs.devOn : tx.prefs.dev}
          </button>
        </div>
      </div>
    </header>
  );
}

/** Stock search by code (and by name once the backend has names): data securities, as the user types. */
function Search({ from }: { from: Page }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const query = useDebounced(q.trim(), 200);
  const found = useCall("data_securities", query ? { query, limit: 6 } : null);
  const rows = found.data?.rows ?? [];
  const go = (code: string) => {
    setOpen(false);
    setQ("");
    location.hash = href.stock(code, { from });
  };
  return (
    <div className="search">
      <label className="sr-only" htmlFor="q-top">
        {tx.search.label}
      </label>
      <input
        id="q-top"
        type="search"
        autoComplete="off"
        placeholder={tx.search.placeholder}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && rows[0]) go(rows[0].code);
          if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && query && found.data && (
        <div className="search-pop" role="listbox" aria-label={tx.search.label}>
          {rows.map((r) => (
            <button key={r.code} type="button" role="option" aria-selected={false} onMouseDown={(e) => e.preventDefault()} onClick={() => go(r.code)}>
              <span>
                {r.name && <b>{r.name} </b>}
                <span className="muted small">{r.code}</span>
              </span>
              <span className="muted small">{tx.boards[r.board] ?? ""}</span>
            </button>
          ))}
          {rows.length === 0 && <span className="search-none">{tx.search.none}</span>}
        </div>
      )}
    </div>
  );
}
