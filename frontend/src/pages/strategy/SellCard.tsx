// 5 · 卖出规则 (S4.2): sell when no longer chosen at a rebalance (the default, as before), or buy on being chosen
// and sell by rules only; each rule a switch and a number, the sell and keep formulas in the formula editor. A
// strategy saved before S4.2 opens as the default with no rule, and an all-default section is not written.
import type { ConditionSpec, StrategySpec } from "../../api/types.gen";
import { MODEL_DEFAULTS } from "../../api/contract.gen";
import { useR4Labels } from "../../api/r4";
import { Help } from "../../components/Help";
import { NumInput } from "../../components/NumInput";
import { concerns, type Loc } from "../../strategy/locate";
import { editSell, formulaExpr, sellOf, type SellSpec } from "../../strategy/formula";
import { namesIn, uniqueName } from "../../strategy/model";
import { ExprFormula, useExample } from "./ExprFormula";
import type { EditorProps, Err } from "./Editor";
import { st } from "./text";

/** The backend takes a sell section (S4.2 in its schema): until then the section is not offered. */
export const SELL_RULES = "sell" in (MODEL_DEFAULTS.StrategySpec ?? {});

type Num = "stop_loss" | "take_profit" | "trailing_stop" | "max_hold_days" | "min_hold_days" | "cooldown_days";

/** Each number rule: its labels code, field, words around the number, the value it starts at, percent or days. */
const RULES: { code: keyof typeof st.sell.rules; field: Num; words: string[]; start: number; pct: boolean }[] = [
  { code: "stop_loss", field: "stop_loss", words: st.sell.stopLoss, start: 0.1, pct: true },
  { code: "take_profit", field: "take_profit", words: st.sell.takeProfit, start: 0.2, pct: true },
  { code: "trailing_stop", field: "trailing_stop", words: st.sell.trailing, start: 0.1, pct: true },
  { code: "max_hold", field: "max_hold_days", words: st.sell.maxHold, start: 20, pct: false },
];
const LIMITS: typeof RULES = [
  { code: "min_hold", field: "min_hold_days", words: st.sell.minHold, start: 5, pct: false },
  { code: "cooldown", field: "cooldown_days", words: st.sell.cooldown, start: 5, pct: false },
];

const errsAt = (errors: Err[], at: Loc) => errors.filter((e) => concerns(e.loc, at));

export function SellCard({ spec, edit, errors }: EditorProps) {
  const R = useR4Labels();
  const sell = sellOf(spec);
  const mode = sell.mode ?? "rebalance";
  const sellExample = useExample("sell");
  const keepExample = useExample("keep");
  const up = (fn: (s: SellSpec) => void) => edit((d) => editSell(d, fn));
  const name = (code: keyof typeof st.sell.rules) => R.sellRule(code).label || st.sell.rules[code];
  const on = (r: (typeof RULES)[number]) => (r.field === "cooldown_days" ? (sell.cooldown_days ?? 0) > 0 : sell[r.field] != null);
  const anySell = RULES.some(on) || (!!sell.sell_when && sell.sell_when.enabled !== false);
  const numRule = (r: (typeof RULES)[number]) => {
    const errs = errsAt(errors, ["sell", r.field]);
    return (
      <div key={r.field} id={`loc-sell-${r.field}`} className={`sell-row ${errs.length ? "has-err" : ""}`}>
        <label className="check sell-name">
          <input type="checkbox" checked={on(r)} onChange={() =>
              up((s) => {
                if (r.field === "cooldown_days") s.cooldown_days = on(r) ? 0 : r.start;
                else s[r.field] = on(r) ? null : r.start;
              })
            }
          />
          {name(r.code)}
        </label>
        <span className={`row small nowrap ${on(r) ? "" : "muted"}`}>
          {r.words[0]}
          <NumInput aria-label={name(r.code)} style={{ width: 64 }} disabled={!on(r)} value={on(r) ? (sell[r.field] ?? null) : null} scale={r.pct ? 0.01 : 1} onValue={(v) => v !== null && v > 0 && up((s) => void (s[r.field] = r.pct ? v : Math.round(v)))} />
          {r.words[1]}
        </span>
        {R.sellRule(r.code).help && <span className="tiny muted sell-help">{R.sellRule(r.code).help}</span>}
        {errs.map((e, i) => (
          <span key={i} className="err-text">
            {e.text}
          </span>
        ))}
      </div>
    );
  };
  const formulaRule = (key: "sell_when" | "keep_when", code: "sell_formula" | "keep_formula", words: string, example: ReturnType<typeof useExample>) => {
    const c = sell[key];
    const active = !!c && c.enabled !== false;
    const errs = errsAt(errors, ["sell", key]);
    const toggle = () =>
      edit((d) => {
        const taken = namesIn(d as StrategySpec);
        editSell(d, (s) => {
          const cur = s[key];
          if (cur) s[key] = { ...cur, enabled: cur.enabled === false };
          else s[key] = { name: uniqueName(name(code), taken), expr: formulaExpr(example?.text ?? "", example?.period ?? "day"), enabled: true } as ConditionSpec;
        });
      });
    return (
      <div key={key} id={`loc-sell-${key}`} className={`sell-row ${errs.length ? "has-err" : ""}`}>
        <label className="check sell-name">
          <input type="checkbox" checked={active} disabled={!c && !example} onChange={toggle} />
          {name(code)}
        </label>
        <span className={`small ${active ? "" : "muted"}`}>{words}</span>
        {active && c && (
          <div className="sell-formula">
            <ExprFormula expr={c.expr} role={key === "sell_when" ? "sell" : "keep"} label={name(code)} onCommit={(e) => up((s) => void (s[key] = { ...c, expr: e }))} />
          </div>
        )}
        {key === "keep_when" && active && (
          <label className="check small sell-sub">
            <input type="checkbox" checked={!!sell.keep_over_stops} onChange={() => up((s) => void (s.keep_over_stops = !sell.keep_over_stops))} />
            {st.sell.keepOverStops}
          </label>
        )}
        {errs.map((e, i) => (
          <span key={i} className="err-text">
            {e.text}
          </span>
        ))}
      </div>
    );
  };
  const modeErrs = errsAt(errors, ["sell"]).filter((e) => e.loc.length <= 2 && (e.loc[1] === undefined || e.loc[1] === "mode"));
  return (
    <section className="card" aria-label="卖出规则">
      <div className="card-head">
        <h2>{st.sell.title}</h2>
        <Help text={st.help.sell} />
      </div>
      <div id="loc-sell-mode" className="col tight">
        <div className="seg" role="group" aria-label={st.sell.mode}>
          {(["rebalance", "rules"] as const).map((m) => (
            <button key={m} type="button" aria-pressed={mode === m} onClick={() => up((s) => void (s.mode = m))}>
              {R.sellMode(m).label || st.sell.modes[m]}
            </button>
          ))}
        </div>
        <span className="small sub">{R.sellMode(mode).help || st.sell.modeHelp[mode]}</span>
        {mode === "rules" && !anySell && <span className="warnbox">{st.sell.needRule}</span>}
        {modeErrs.map((e, i) => (
          <span key={i} className="err-text">
            {e.text}
          </span>
        ))}
      </div>
      <div className="sell-rules">
        {RULES.map(numRule)}
        {formulaRule("sell_when", "sell_formula", st.sell.sellWhen, sellExample)}
        {formulaRule("keep_when", "keep_formula", st.sell.keepWhen, keepExample)}
        {LIMITS.map(numRule)}
      </div>
    </section>
  );
}
