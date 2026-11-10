import { t } from "./i18n";

/** Display only: a missing value is shown as unknown, never as 0. */
export function show(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return t.common.none;
  return typeof value === "number" ? value.toLocaleString("zh-CN") : value;
}

/** An ISO timestamp in local time, to the minute. */
export function localTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** A JSON detail string as "key value · key value" when it is a flat object, else as stored. */
export function prettyDetail(detail: string | null): string {
  if (detail === null) return t.common.none;
  try {
    const value: unknown = JSON.parse(detail);
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return Object.entries(value)
        .map(([k, v]) => `${k} ${v === null ? t.common.none : typeof v === "object" ? JSON.stringify(v) : String(v)}`)
        .join(" · ");
    }
    return detail;
  } catch {
    return detail;
  }
}
