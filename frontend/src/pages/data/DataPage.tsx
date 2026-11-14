// 数据: data problems with their proposed handling (approve or reject, view on the K-line), what the import changed,
// version history and storage, delisting handling (folded), and where the data is kept (存储位置, with the trading
// calendar's span). The first-run setup is round 3 phase E.
import { useState } from "react";
import { commandOf, useCall, useCallMutation } from "../../api/client";
import type { Resolution } from "../../api/types.gen";
import { Problem } from "../../components/Problem";
import { StatusBar } from "../../components/StatusBar";
import { useLabels } from "../../labels";
import { href, type Route } from "../../router";
import { useUi } from "../../ui";
import { show } from "../../format";
import { DelistingsSection } from "./DelistingsSection";
import { Location } from "./Location";
import { ScopeCard } from "./ScopeCard";
import { Storage, Versions } from "./Versions";

type DataRoute = Extract<Route, { page: "data" }>;
type Tab = "proposed" | "approved" | "rejected";
const TABS: [Tab, string][] = [
  ["proposed", "待批准"],
  ["approved", "已批准"],
  ["rejected", "已驳回"],
];

export function DataPage({ route }: { route: DataRoute }) {
  const [tab, setTab] = useState<Tab>("proposed");
  const [fold, setFold] = useState(true);
  const res = useCall("data_resolve_list", { status: tab, limit: 200, code: route.code, date: route.date });
  const issues = useCall("data_issues", { limit: 200, codes: route.code ? [route.code] : null });
  const calendar = useCall("data_calendar", {});
  const L = useLabels();
  return (
    <main className="page">
      <StatusBar />
      <section className="card" aria-label="数据问题">
        <div className="card-head">
          <h2>数据问题</h2>
          <span className="small muted">两个来源对不上、或者数据自己有矛盾时，这里列出建议的处理；批准后下次生成数据版本时生效。</span>
        </div>
        <div className="row">
          <div className="seg" role="group" aria-label="状态">
            {TABS.map(([k, label]) => (
              <button key={k} type="button" aria-pressed={tab === k} onClick={() => setTab(k)}>
                {label}
                {res.data && tab === k ? ` ${res.data.total}` : ""}
              </button>
            ))}
          </div>
          {(route.code || route.date) && (
            <span className="tag acc">
              只看 {route.code} {route.date}{" "}
              <a className="link" href={href.data()} aria-label="看全部">
                ×
              </a>
            </span>
          )}
        </div>
        {res.isError && <Problem error={res.error} />}
        {res.data && res.data.rows.length === 0 && (
          <div className="empty">
            <span>
              {tab === "proposed"
                ? "现在没有要你决定的数据问题。更新数据时，如果两个来源对不上，这里会列出建议的处理，等你批准或驳回。"
                : tab === "approved"
                  ? "还没有批准过的处理。"
                  : "还没有驳回过的处理。"}
            </span>
          </div>
        )}
        {res.data?.rows.map((r) => <ResolutionRow key={`${r.code}/${r.date}/${r.kind}`} r={r} />)}
      </section>
      <div className="split">
        <div className="main">
          <Versions />
        </div>
        <div className="aside">
          <Storage />
        </div>
      </div>
      <section className="card" aria-label="导入时的改动">
        <button type="button" className="card-head fold" aria-expanded={!fold} onClick={() => setFold(!fold)}>
          <h2>导入时的改动</h2>
          <span className="small muted">{issues.data ? `${issues.data.total} 条` : ""}</span>
          <span className="push small muted">{fold ? "展开 ▸" : "收起 ▾"}</span>
        </button>
        {!fold && issues.data && (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>股票</th>
                  <th>日期</th>
                  <th>问题</th>
                  <th>处理</th>
                </tr>
              </thead>
              <tbody>
                {issues.data.rows.map((r, i) => (
                  <tr key={i}>
                    <td>
                      <a className="link" href={href.stock(r.code, { date: r.date, from: "data" })}>
                        {r.code}
                      </a>
                    </td>
                    <td className="num">{r.date}</td>
                    <td title={L.help("issue_kinds", r.kind)}>{L.of("issue_kinds", r.kind)}</td>
                    <td title={L.help("issue_actions", r.action)}>{L.of("issue_actions", r.action)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <details className="card fold-card">
        <summary className="card-head">
          <h2>退市处理</h2>
          <span className="push small muted">展开 ▸</span>
        </summary>
        <DelistingsSection />
      </details>
      <ScopeCard first={calendar.data?.first ?? null} open={route.extend} />
      <Location
        calendar={
          calendar.data && (
            <span className="small muted">
              交易日历 {calendar.data.first} 至 {calendar.data.last}，共 {show(calendar.data.dates.length)} 个交易日
            </span>
          )
        }
      />
    </main>
  );
}

function ResolutionRow({ r }: { r: Resolution }) {
  const L = useLabels();
  const ui = useUi();
  const [note, setNote] = useState("");
  const scope = { code: r.code, date: r.date, kind: r.kind };
  const approve = useCallMutation("data_resolve_approve", ["data_resolve_list", "data_status"]);
  const reject = useCallMutation("data_resolve_reject", ["data_resolve_list", "data_status"]);
  return (
    <div className="inner issue">
      <div className="row">
        <span className={`badge ${r.level === "error" ? "level-error" : r.level === "suspicious" ? "warn" : ""}`}>{L.of("anomaly_levels", r.level)}</span>
        <span className="b">{r.code}</span>
        <span className="num sub">{r.date}</span>
        <span className="b">{L.of("issue_kinds", r.kind)}</span>
        <a className="link small push" href={href.stock(r.code, { date: r.date, from: "data" })}>
          在 K 线上查看 ›
        </a>
      </div>
      <span className="small sub">{L.help("issue_kinds", r.kind)}</span>
      <span className="small">
        建议：采用 {r.take}
        {r.fields.length > 0 && `（${r.fields.join("、")}）`}
      </span>
      {r.status === "proposed" && (
        <div className="row">
          <input className="input grow" aria-label="批注" placeholder="批注（可选）" value={note} onChange={(e) => setNote(e.target.value)} />
          <button type="button" className="btn sm" disabled={approve.isPending} onClick={() => approve.mutate({ ...scope, note: note || "批准" })}>
            批准
          </button>
          <button type="button" className="btn sm soft" disabled={reject.isPending} onClick={() => reject.mutate({ ...scope, note: note || "驳回" })}>
            驳回
          </button>
        </div>
      )}
      {r.status !== "proposed" && r.approval && <span className="note">批注：{r.approval}</span>}
      {(approve.isError || reject.isError) && <Problem error={approve.error ?? reject.error} />}
      {ui.dev && (
        <div className="dev-row">
          <button type="button" className="dev-btn" onClick={() => void navigator.clipboard?.writeText(commandOf("data_resolve_approve", { ...scope, note: "…" }))}>
            复制批准命令
          </button>
        </div>
      )}
    </div>
  );
}
