// The holdings a strategy with sell rules would have today, and what its rules do with each (S4.2). They come from
// running the strategy from a year back to today (the backend's simulation), never from the user's own account; the
// backend's sentence says so above the table.
import type { Holdings as Simulated, HoldingRow } from "../../api/types.gen";
import { Stock } from "../../components/Stock";
import { pct } from "../../format";
import { useLabels } from "../../labels";
import { ruleWords } from "../../strategy/formula";

export function Holdings({ h, pick }: { h: Simulated; pick: (code: string) => void }) {
  const L = useLabels();
  const rows = [...h.rows].sort((a, b) => (a.action === b.action ? 0 : a.action === "sell" ? -1 : 1));
  const sells = rows.filter((x) => x.action === "sell").length;
  // Why it is sold: the rule that marked it (its value against its setting), or the rebalance leaving it out; why a
  // kept one is not sold when a rule fired: the keep formula holding it back.
  const why = (x: HoldingRow) => {
    if (x.action === "sell") {
      if (x.cause === "rebalance") return L.of("sell_modes", "rebalance") || "调仓时不再入选";
      const r = x.rules.find((c) => c.rule === x.cause);
      return [L.of("sell_rules", x.cause), r ? ruleWords(r) : ""].filter(Boolean).join("：");
    }
    const held = x.rules.find((c) => c.blocked_by === "keep");
    return held ? `${L.of("sell_rules", held.rule)}：${ruleWords(held)}，被${L.of("sell_rules", "keep_formula") || "不卖公式"}挡住` : "";
  };
  return (
    <section className="card" aria-label="模拟持仓">
      <div className="card-head">
        <h2>{sells > 0 ? `今天要按规则卖出 ${sells} 只` : "今天没有要卖出的持仓"}</h2>
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
                  <td className={x.action === "sell" ? "bad b" : "muted"}>{x.action === "sell" ? "卖出" : "继续持有"}</td>
                  <td className="small">{why(x)}</td>
                  <td className="r num">{x.hold_days}</td>
                  <td className="r num">{x.cost.toFixed(2)}</td>
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
