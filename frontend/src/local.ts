// Settings kept in this browser only: which strategies the today page follows, and which today blocks it shows.
// Every component reading one stays in step when another changes it.
import { useSyncExternalStore } from "react";

function store<T>(key: string, fallback: T, valid: (v: unknown) => v is T) {
  const listeners = new Set<() => void>();
  let cache: { raw: string | null; value: T } | null = null;
  const read = (): T => {
    let raw: string | null = null;
    try {
      raw = localStorage.getItem(key);
    } catch {
      // storage unavailable
    }
    if (cache && cache.raw === raw) return cache.value;
    let value = fallback;
    try {
      const v: unknown = raw === null ? null : JSON.parse(raw);
      if (valid(v)) value = v;
    } catch {
      // unreadable: the default
    }
    cache = { raw, value };
    return value;
  };
  const write = (value: T) => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // storage unavailable: kept for this visit only
      cache = { raw: JSON.stringify(value), value };
    }
    for (const l of listeners) l();
  };
  const subscribe = (l: () => void) => {
    listeners.add(l);
    const onStorage = (e: StorageEvent) => e.key === key && l();
    window.addEventListener("storage", onStorage);
    return () => {
      listeners.delete(l);
      window.removeEventListener("storage", onStorage);
    };
  };
  return { read, write, subscribe, use: () => useSyncExternalStore(subscribe, read) };
}

const isIds = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === "string");

/** Strategy ids shown on the today page ("在今日显示"). */
export const follows = store<string[]>("wequant.follow", [], isIds);

export function setFollow(id: string, on: boolean) {
  const rest = follows.read().filter((x) => x !== id);
  follows.write(on ? [...rest, id] : rest);
}

export interface TodayShow {
  follow: boolean;
  runs: boolean;
  index: boolean;
  issues: boolean;
}
const isShow = (v: unknown): v is TodayShow => typeof v === "object" && v !== null && ["follow", "runs", "index", "issues"].every((k) => typeof (v as Record<string, unknown>)[k] === "boolean");

/** Which blocks the today page shows (its "自定义"). */
export const todayShow = store<TodayShow>("wequant.today", { follow: true, runs: true, index: true, issues: true }, isShow);
