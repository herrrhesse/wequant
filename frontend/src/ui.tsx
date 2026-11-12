// Page-wide display settings every component may read: developer mode, the up/down colour convention and a "look"
// key that changes with the theme (charts re-read their colours when it changes).
import { createContext, useContext } from "react";
import type { Conv } from "./prefs";

export interface Ui {
  /** Developer mode: copy commands, hashes, version numbers and JSON views. Off by default. */
  dev: boolean;
  conv: Conv;
  look: string;
}

export const UiContext = createContext<Ui>({ dev: false, conv: "cn", look: "system/cn" });

export function useUi(): Ui {
  return useContext(UiContext);
}
