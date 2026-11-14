// The holdings a strategy with sell rules would have today, and what its rules do with each (S4.2). They come from
// running the strategy from a year back to today (the backend's simulation), never from the user's own account; the
// backend's sentence says so above the table.
import type { ScreenHoldings } from "../../api/r4";
import { Stock } from "../../components/Stock";
import { pct } from "../../format";

const ACTION = { sell: "卖出", blocked: "该卖，被挡住", keep: "继续持有" } as const;
const ORDER = { sell: 0, blocked: 1, keep: 2 } as const;

export function Holdings({ h, pick }: { h: ScreenHoldings; pick: (code: string) => void }) {
  const rows = [...h.rows].sort((a, b) => ORDER[a.action] - ORDER[b.action]);
  const sells = rows.filter((x) => x.action === "sell").length;
  return (
    <section className="card" aria-label="模拟持仓">
      <div className="card-head">
        <h2>{sells > 0 ? `今天要按规则卖出 ${sells} 只` : "今天没有要按规则卖出的持仓"}</h2>
        <span className="small muted">模拟持仓 {rows.length} 只</span>
      </div>
      <span className="note" role="note" aria-label="持仓来源">
        {h.note}
      </span>
      {rows.length > 0 && (
        <div className="tbl-wrap">
          <table className="tbl" aria-label="模拟持仓">
            <thead>
              <tr>
                <th>股票</th>
                <th>今天</th>
                <th>原因</th>
                <th className="r">持有天数</th>
                <th className="r">成本</th>
                <th className="r">收盘</th>
                <th className="r">盈亏</th>
                <th className="r">权重</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((x) => (
                <tr key={x.code} className="clickable" onClick={() => pick(x.code)}>
                  <td>
                    <Stock code={x.code} name={x.name} />
                  </td>
                  <td className={x.action === "sell" ? "bad b" : x.action === "blocked" ? "warn-text" : "muted"}>{ACTION[x.action]}</td>
                  <td className="small">{x.cause ?? x.rules.find((r) => r.blocked_by)?.text ?? ""}</td>
                  <td className="r num">{x.hold_days}</td>
                  <td className="r num">{x.cost === null ? "—" : x.cost.toFixed(2)}</td>
                  <td className="r num">{x.close === null ? "—" : x.close.toFixed(2)}</td>
                  <td className="r num">{pct(x.change, 1, true)}</td>
                  <td className="r num">{pct(x.weight, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
