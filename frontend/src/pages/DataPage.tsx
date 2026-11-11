import { useState } from "react";
import { commandOf, useCall } from "../api/client";
import type { DataStatus } from "../api/types.gen";
import { Card } from "../components/Card";
import { Problem } from "../components/Problem";
import { localTime, show } from "../format";
import { t } from "../i18n";
import { DelistingsSection } from "./DelistingsSection";
import { IssuesSection } from "./IssuesSection";

/** The data page: store status, versions, data issues and the update and resolution actions to come. */
export function DataPage({ focusCode, focusDate }: { focusCode: string | null; focusDate: string | null }) {
  const [version, setVersion] = useState<number | null>(null);
  const statusRequest = { data_version: version };
  // data status is re-read on every visit: a new store version invalidates what was read before.
  const status = useCall("data_status", statusRequest, { staleTime: 0 });
  const allIssues = useCall("data_issues", { data_version: version, limit: 1 });

  if (status.isPending) return <main className="page">{t.app.loading}</main>;
  if (status.isError) return <main className="page"><Problem error={status.error} /></main>;
  const s = status.data;
  const command = commandOf("data_status", statusRequest);

  return (
    <main className="page">
      <h1>{t.data.title}</h1>
      <StatusCard status={s} command={command} />
      <div className="row2">
        <Card title={t.data.update.title} version={s.current_version}>
          <button type="button" className="btn" disabled>
            {t.data.update.button}
          </button>
          <span className="note">{t.data.update.pending}</span>
        </Card>
        <Card title={t.data.resolve.title} version={s.current_version}>
          <span className="note">{t.data.resolve.pending}</span>
        </Card>
      </div>
      {allIssues.data && <IssuesSection
          counts={allIssues.data.counts}
          total={allIssues.data.total}
          version={allIssues.data.data_version}
          focusCode={focusCode}
          focusDate={focusDate}
        />}
      <DelistingsSection />
      <VersionsCard status={s} command={command} onView={setVersion} />
    </main>
  );
}

function meta(status: DataStatus, key: string): string {
  const v = status.meta[key];
  return typeof v === "string" || typeof v === "number" ? String(v) : t.common.none;
}

function StatusCard({ status: s, command }: { status: DataStatus; command: string }) {
  const current = s.data_version === s.current_version;
  return (
    <Card
      title={t.data.status.title}
      version={s.data_version}
      command={command}
      extra={<span className={current ? "badge acc" : "badge warn"}>{current ? t.data.status.current : t.data.status.old}</span>}
    >
      <div className="grid">
        <Stat label={t.data.status.lastTradeDate} value={show(s.last_trade_date)} />
        <Stat label={t.data.status.createdAt} value={localTime(s.created_at)} sub={s.created_at} />
        <Stat
          label={t.data.status.limitCheck}
          value={s.limit_check_current ? t.common.yes : t.common.no}
          sub={s.limit_check_current ? t.data.status.limitCheckYes : t.data.status.limitCheckNo}
        />
        <Stat label={t.data.status.source} value={meta(s, "source")} sub={`${t.data.status.releaseTag} ${meta(s, "release_tag")}`} />
      </div>
      <table className="tbl">
        <thead>
          <tr>
            <th>{t.data.status.table}</th>
            <th className="r">{t.data.status.rows}</th>
            <th className="r">{t.data.status.files}</th>
          </tr>
        </thead>
        <tbody>
          {s.tables.map((tb) => (
            <tr key={tb.name}>
              <td className="mono">{tb.name}</td>
              <td className="r num">{show(tb.rows)}</td>
              <td className="r num">{show(tb.files)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="stat">
      <span className="stat-l">{label}</span>
      <span className="stat-v num">{value}</span>
      {sub && <span className="stat-s">{sub}</span>}
    </div>
  );
}

function VersionsCard({ status: s, command, onView }: { status: DataStatus; command: string; onView: (v: number | null) => void }) {
  return (
    <Card title={t.data.versions.title} version={s.data_version} command={command}>
      <p className="lead">{t.data.versions.lead}</p>
      <table className="tbl">
        <tbody>
          {[...s.versions].reverse().map((v) => (
            <tr key={v} aria-selected={v === s.data_version}>
              <td className="num">v{v}</td>
              <td>{v === s.current_version ? <span className="badge acc">{t.data.status.current}</span> : <span className="badge">{t.data.status.old}</span>}</td>
              <td className="r">
                {v === s.data_version ? (
                  <span className="note">{t.data.versions.viewing}</span>
                ) : (
                  <button type="button" className="cmd" onClick={() => onView(v === s.current_version ? null : v)}>
                    {t.data.versions.view}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
