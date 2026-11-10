// Pieces the screen and backtest pages share: warnings (code + backend text) and anomaly uses.
import type { AnomalyUse, Warning } from "../../api/types.gen";
import { t } from "../../i18n";

const r = t.runs;

export function WarningList({ warnings, empty }: { warnings: Warning[]; empty: string }) {
  if (!warnings.length) return <span className="note">{empty}</span>;
  return (
    <>
      <ul className="warn-list">
        {warnings.map((w) => (
          <li key={w.code}>
            <strong>{t.warningLabels[w.code] ?? w.code}</strong> <span className="mono">{w.code}</span>
            <div className="note">{w.text}</div>
          </li>
        ))}
      </ul>
      <span className="note">{r.warningsTemp}</span>
    </>
  );
}

export function AnomalyTable({ rows }: { rows: AnomalyUse[] }) {
  return (
    <div className="tbl-wrap" style={{ maxHeight: 300 }}>
      <table className="tbl">
        <thead>
          <tr>
            <th>{r.acols.level}</th>
            <th>{r.acols.date}</th>
            <th>{r.acols.code}</th>
            <th>{r.acols.channel}</th>
            <th>{r.acols.anomaly}</th>
            <th>{r.acols.kind}</th>
            <th>{r.acols.root}</th>
            <th>{r.acols.decision}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a, i) => (
            <tr key={`${a.date}/${a.code}/${a.channel}/${a.seq}/${i}`} className={`level-${a.level}`}>
              <td>
                <span className={a.level === "error" ? "badge level-error" : a.level === "suspicious" ? "badge warn" : "badge"}>{r.levels[a.level]}</span>
              </td>
              <td className="num">{a.date}</td>
              <td>{a.code}</td>
              <td>{r.channels[a.channel]}</td>
              <td className="num">{a.anomaly_date}</td>
              <td>
                {t.data.kinds[a.kind] ?? a.kind} <span className="code-sub">{a.anomaly_class}</span>
              </td>
              <td>{a.root ?? t.common.none}</td>
              <td>{a.decision ? (r.actions[a.decision] ?? t.picks.roles[a.decision] ?? a.decision) : a.order_status ?? t.common.none}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
