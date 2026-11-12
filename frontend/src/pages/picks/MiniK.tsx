// A small daily K-line of the last 60 trading days up to a date, drawn from data bars (prices as the backend gave).
import { useCall } from "../../api/client";

export function MiniK({ code, end }: { code: string; end: string }) {
  const start = new Date(Date.parse(`${end}T00:00:00Z`) - 100 * 864e5).toISOString().slice(0, 10);
  const bars = useCall("data_bars", { codes: [code], start, end, fields: ["open", "high", "low", "close"] });
  const rows = (bars.data?.rows ?? []).filter((r) => r.status === "traded").slice(-60);
  if (rows.length < 2) return <div className="mini-k" />;
  const v = (r: (typeof rows)[number], k: string) => r.values[k] ?? Number.NaN;
  const lo = Math.min(...rows.map((r) => v(r, "low")));
  const hi = Math.max(...rows.map((r) => v(r, "high")));
  const y = (p: number) => 106 - ((p - lo) / (hi - lo || 1)) * 102;
  const w = 360 / rows.length;
  return (
    <svg className="mini-k" viewBox="0 0 360 110" preserveAspectRatio="none" role="img" aria-label="近 60 日 K 线">
      {rows.map((r, i) => {
        const o = v(r, "open");
        const c = v(r, "close");
        const up = c >= o;
        const x = i * w;
        return (
          <g key={r.date} className={up ? "k-up" : "k-down"}>
            <path d={`M${(x + w / 2).toFixed(2)} ${y(v(r, "high")).toFixed(1)} V${y(v(r, "low")).toFixed(1)}`} />
            <rect x={(x + w * 0.15).toFixed(2)} y={Math.min(y(o), y(c)).toFixed(1)} width={Math.max(0.8, w * 0.7).toFixed(2)} height={Math.max(0.8, Math.abs(y(o) - y(c))).toFixed(1)} />
          </g>
        );
      })}
    </svg>
  );
}
