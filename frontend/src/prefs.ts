import { useEffect, useState } from "react";

export type Theme = "light" | "dark" | "system";
export type Conv = "cn" | "intl";

const KEY = "wequant.prefs";

const THEMES: readonly string[] = ["light", "dark", "system"];

/** Saved preferences; ?theme=light|dark|system in the URL wins, e.g. for screenshots. */
function load(): { theme: Theme; conv: Conv } {
  let saved: { theme?: Theme; conv?: Conv } = {};
  try {
    saved = JSON.parse(localStorage.getItem(KEY) ?? "{}");
  } catch {
    // storage unavailable: defaults
  }
  const asked = new URLSearchParams(location.search).get("theme");
  const theme = asked && THEMES.includes(asked) ? (asked as Theme) : (saved.theme ?? "system");
  return { theme, conv: saved.conv ?? "cn" };
}

/** Theme and up/down colours, kept per browser; the page works without storage. */
export function usePrefs() {
  const [prefs, setPrefs] = useState(load);
  useEffect(() => {
    document.documentElement.dataset.theme = prefs.theme;
    document.documentElement.dataset.conv = prefs.conv;
    try {
      localStorage.setItem(KEY, JSON.stringify(prefs));
    } catch {
      // storage unavailable: preferences last for this visit only
    }
  }, [prefs]);
  return {
    ...prefs,
    setTheme: (theme: Theme) => setPrefs((p) => ({ ...p, theme })),
    setConv: (conv: Conv) => setPrefs((p) => ({ ...p, conv })),
  };
}
