// A formula wherever the strategy takes an expression: the box in place, checked as it is typed; opened large with
// the functions and fields (formula_vocab) and a trial on one stock. The text goes into the strategy when the box
// loses focus, the large editor closes, or the period changes; the backend compiles it.
import { useRef, useState } from "react";
import { useR4, useR4Labels, type FormulaPeriod, type FormulaRole } from "../../api/r4";
import { useLabels } from "../../labels";
import { Problem } from "../Problem";
import { StockPick } from "../StockPick";
import { FormulaBox, type Period } from "./FormulaBox";
import { FormulaDialog } from "./FormulaDialog";
import { HelpList, type HelpItem } from "./HelpList";
import { insertAt } from "./source";
import { TrialTable, type TrialColumn, type TrialRow } from "./TrialTable";
import { useFormula } from "./useFormula";

interface Props {
  label: string;
  text: string;
  period: FormulaPeriod;
  role: FormulaRole;
  onCommit: (text: string, period: FormulaPeriod) => void;
  /** Offer 日线 / 周线 / 月线 (default yes). */
  periods?: boolean;
  rows?: number;
  placeholder?: string;
}

export function FormulaField({ label, text, period, role, onCommit, periods = true, rows = 2, placeholder }: Props) {
  const f = useFormula(text, period, role);
  const [open, setOpen] = useState(false);
  const commit = (p: FormulaPeriod = period) => (f.draft !== text || p !== period) && onCommit(f.draft, p);
  const setPeriod = periods ? (p: Period) => commit(p) : undefined;
  const tools = (
    <button type="button" className="btn sm soft" onClick={() => setOpen(true)}>
      展开编辑、帮助和试算
    </button>
  );
  return (
    <>
      <FormulaBox label={label} text={f.draft} onText={f.setDraft} onBlur={() => commit()} period={period} onPeriod={setPeriod} status={f.status} minRows={rows} maxRows={20} placeholder={placeholder} tools={tools} />
      {open && (
        <Large
          label={label}
          draft={f.draft}
          setDraft={f.setDraft}
          status={f.status}
          period={period}
          onPeriod={setPeriod}
          onClose={() => {
            setOpen(false);
            commit();
          }}
        />
      )}
    </>
  );
}

function Large({ label, draft, setDraft, status, period, onPeriod, onClose }: { label: string; draft: string; setDraft: (t: string) => void; status: ReturnType<typeof useFormula>["status"]; period: FormulaPeriod; onPeriod?: (p: Period) => void; onClose: () => void }) {
  const box = useRef<HTMLTextAreaElement>(null);
  const vocab = useR4("formula_vocab", {});
  const R = useR4Labels();
  const items: HelpItem[] = [
    ...(vocab.data?.functions ?? []).map((x) => ({
      name: x.name,
      group: R.category(x.category),
      title: x.aliases.length ? `也可写 ${x.aliases.join("、")}` : "",
      desc: x.description,
      example: x.example,
      insert: `${x.name}(`,
    })),
    ...(vocab.data?.fields ?? []).map((x) => ({
      name: x.name,
      group: "字段",
      title: x.aliases.length ? `也可写 ${x.aliases.join("、")}` : "",
      desc: [x.description, x.unit ? `单位：${x.unit}` : ""].filter(Boolean).join("；"),
    })),
  ];
  const insert = (piece: string) => {
    const a = box.current;
    const at = a ? insertAt(draft, a.selectionStart, a.selectionEnd, piece) : { text: draft + piece, caret: draft.length + piece.length };
    setDraft(at.text);
    requestAnimationFrame(() => {
      box.current?.focus();
      box.current?.setSelectionRange(at.caret, at.caret);
    });
  };
  return (
    <FormulaDialog
      title={label}
      onClose={onClose}
      editor={<FormulaBox label={`${label}（展开）`} text={draft} onText={setDraft} period={period} onPeriod={onPeriod} status={status} minRows={20} maxRows={32} boxRef={box} />}
      help={
        vocab.isError ? (
          <Problem error={vocab.error} />
        ) : (
          <HelpList items={items} examples={(vocab.data?.examples ?? []).map((x) => ({ title: x.title, desc: x.description, text: x.text }))} onInsert={insert} onUse={(t) => setDraft(t)} />
        )
      }
      trial={<Trial text={draft} period={period} />}
    />
  );
}

/** The formula on one stock, bar by bar, to check against 通达信 line by line. */
function Trial({ text, period }: { text: string; period: FormulaPeriod }) {
  const L = useLabels();
  const [stock, setStock] = useState<{ code: string; name: string | null } | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [asked, setAsked] = useState<{ text: string; period: FormulaPeriod; code: string; start: string | null; end: string | null } | null>(null);
  const trial = useR4("formula_trial", asked ? { ...asked, max_bars: 1000 } : null);
  const r = trial.data;
  const bar = L.periodName(period) || "";
  const columns: TrialColumn[] = [
    { key: "$close", label: "C（后复权）" },
    ...(r?.rows.some((x) => x.tdx) ? [{ key: "$tdx", label: "收盘（前复权，对通达信）" }] : []),
    { key: "$raw", label: "收盘（不复权）" },
    ...(r?.columns ?? []).map((c) => ({ key: c.name, label: c.name, output: c.output })),
  ];
  const rows: TrialRow[] = (r?.rows ?? []).map((x) => ({
    date: x.date,
    span: x.start !== x.date ? `${x.start} 至 ${x.date}` : x.date,
    values: { ...x.values, $close: x.close, $tdx: x.tdx?.close ?? null, $raw: x.raw_close },
  }));
  return (
    <section className="col fx-trial-box" aria-label="试算">
      <div className="row">
        <h3 className="small b">试算</h3>
        <span className="tiny muted">选一只股票和一段日期，逐根 K 线列出每个变量的值，方便和通达信对数。</span>
      </div>
      <div className="row">
        <StockPick label="试算的股票" code={stock?.code ?? null} name={stock?.name} onPick={(code, name) => setStock({ code, name })} />
        <label className="row small nowrap">
          从
          <input className="input" type="date" aria-label="试算开始日" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="row small nowrap">
          到
          <input className="input" type="date" aria-label="试算结束日" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <button type="button" className="btn sm" disabled={!stock || !text.trim()} onClick={() => stock && setAsked({ text, period, code: stock.code, start: start || null, end: end || null })}>
          试算
        </button>
      </div>
      {trial.isError && <Problem error={trial.error} />}
      {trial.isFetching && <span className="note">正在试算…</span>}
      {r && !trial.isFetching && (
        <>
          <span className="small">
            {r.name ? `${r.name} ` : ""}
            {r.code} · {bar} · 数据版本 {r.data_version_name}
            {r.open_period ? ` · 本${period === "month" ? "月" : "周"}还没结束（目前到 ${r.open_period}），没有列出来` : ""}
          </span>
          <TrialTable columns={columns} rows={rows} dateLabel={period === "day" ? "日期" : `${period === "month" ? "月" : "周"}最后交易日`} spanLabel={period === "day" ? undefined : "起止"} />
        </>
      )}
    </section>
  );
}
