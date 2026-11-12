import type { ReactNode } from "react";
import { useUi } from "../ui";
import { Command } from "./Command";

/** A page section: title, and in developer mode the data version it shows and the command that produced it. */
export function Card(props: { title: string; version?: number | string; command?: string; extra?: ReactNode; children: ReactNode }) {
  const { dev } = useUi();
  return (
    <section className="card" aria-label={props.title}>
      <div className="card-head">
        <h2>{props.title}</h2>
        <div className="row push small muted">
          {props.extra}
          {dev && props.version !== undefined && <span className="tag">数据版本 {props.version}</span>}
          {dev && props.command && <Command command={props.command} />}
        </div>
      </div>
      {props.children}
    </section>
  );
}
