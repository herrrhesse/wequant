import { useEffect, useState } from "react";

/** The value after it stopped changing for `ms` (requests while typing). */
export function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return settled;
}

/** Seconds since `since` (null: not running), ticking once a second. */
export function useElapsed(since: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since === null) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [since]);
  return since === null ? 0 : Math.max(0, Math.floor((now - since) / 1000));
}

/** Seconds since `running` last became true; 0 while not running. */
export function useRunningSeconds(running: boolean): number {
  const [since, setSince] = useState<number | null>(null);
  useEffect(() => {
    setSince(running ? Date.now() : null);
  }, [running]);
  return useElapsed(since);
}
