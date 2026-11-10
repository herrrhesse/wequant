import type { ReactNode } from "react";
import { t } from "../i18n";
import { Command } from "./Command";

/** A page section: title, the data version it shows and the command that produced it. */
export function Card(props: { title: string; version?: number; command?: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <section className="card" aria-label={props.title}>
      <div className="card-head">
        <h2>{props.title}</h2>
        <div className="card-meta">
          {props.extra}
          {props.version !== undefined && <span className="badge">{t.common.dataVersion(props.version)}</span>}
          {props.command && <Command command={props.command} />}
        </div>
      </div>
      {props.children}
    </section>
  );
}
