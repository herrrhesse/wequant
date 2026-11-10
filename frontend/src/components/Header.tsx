import { useState, type FormEvent } from "react";
import { t } from "../i18n";
import type { Conv, Theme } from "../prefs";
import { href, type Route } from "../router";

const PAGES = [
  ["today", t.nav.today],
  ["strategy", t.nav.strategy],
  ["picks", t.nav.picks],
  ["backtest", t.nav.backtest],
  ["data", t.nav.data],
] as const;

interface Props {
  route: Route;
  theme: Theme;
  conv: Conv;
  setTheme: (t: Theme) => void;
  setConv: (c: Conv) => void;
}

export function Header(props: Props) {
  const [code, setCode] = useState("");
  const jump = (e: FormEvent) => {
    e.preventDefault();
    const c = code.trim().toUpperCase();
    if (c) location.hash = href.stock(c);
  };
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <a className="brand" href={href.data()}>
          <span className="brand-mark">W</span>
          {t.app.brand}
        </a>
        <nav className="nav" aria-label={t.nav.label}>
          {PAGES.map(([key, label]) =>
            key !== "today" ? (
              <a
                key={key}
                href={key === "data" ? href.data() : key === "strategy" ? href.strategy() : key === "picks" ? href.picks() : href.backtest()}
                aria-current={props.route.page === key || (key === "backtest" && props.route.page === "run") ? "page" : undefined}
              >
                {label}
              </a>
            ) : (
              <button key={key} type="button" disabled title={t.nav.notYet}>
                {label}
              </button>
            ),
          )}
        </nav>
        <form className="jump" onSubmit={jump} role="search">
          <label className="sr-only" htmlFor="jump-code">
            {t.nav.jump}
          </label>
          <input id="jump-code" value={code} onChange={(e) => setCode(e.target.value)} placeholder={t.nav.jumpPlaceholder} />
          <button type="submit" className="cmd">
            {t.nav.jumpGo}
          </button>
        </form>
        <div className="prefs">
          <button
            type="button"
            className="cmd"
            aria-label={t.prefs.conv}
            onClick={() => props.setConv(props.conv === "cn" ? "intl" : "cn")}
          >
            <span className="conv-dot" />
            {props.conv === "cn" ? t.prefs.cn : t.prefs.intl}
          </button>
          <div className="seg" role="group" aria-label={t.prefs.theme}>
            {(["light", "dark", "system"] as const).map((k) => (
              <button key={k} type="button" aria-pressed={props.theme === k} onClick={() => props.setTheme(k)}>
                {t.prefs[k]}
              </button>
            ))}
          </div>
        </div>
      </div>
    </header>
  );
}
