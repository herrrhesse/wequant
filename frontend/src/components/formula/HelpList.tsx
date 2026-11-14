// Functions and fields a formula can use, searchable by name or by what they do; each with the backend's Chinese
// description and an example, and a button that puts the name at the caret. Whole examples (the weekly KDJ) go first.
import { useMemo, useState } from "react";

export interface HelpItem {
  name: string;
  group: string;
  title: string;
  desc?: string;
  example?: string;
  /** What goes into the text when inserted (defaults to the name, with "(" for a function). */
  insert?: string;
}

export interface HelpExample {
  title: string;
  desc?: string;
  text: string;
}

export function HelpList({ items, examples, onInsert, onUse }: { items: HelpItem[]; examples?: HelpExample[]; onInsert: (piece: string) => void; onUse?: (text: string) => void }) {
  const [q, setQ] = useState("");
  const shown = useMemo(() => {
    const k = q.trim().toLowerCase();
    if (!k) return items;
    return items.filter((x) => [x.name, x.title, x.desc ?? "", x.group].some((s) => s.toLowerCase().includes(k)));
  }, [items, q]);
  const groups = useMemo(() => {
    const m = new Map<string, HelpItem[]>();
    for (const x of shown) m.set(x.group, [...(m.get(x.group) ?? []), x]);
    return [...m];
  }, [shown]);
  return (
    <section className="fx-help" aria-label="函数和字段">
      <input className="input" type="search" aria-label="搜索函数和字段" placeholder="搜索：名字或用途，例如 均线、REF" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="fx-help-list">
        {!q &&
          examples?.map((x) => (
            <div key={x.title} className="fx-help-item example">
              <div className="row nowrap">
                <span className="b">{x.title}</span>
                {onUse && (
                  <button type="button" className="btn sm soft push" onClick={() => onUse(x.text)}>
                    放进编辑框
                  </button>
                )}
              </div>
              {x.desc && <span className="tiny muted">{x.desc}</span>}
              <pre className="mono fx-example">{x.text}</pre>
            </div>
          ))}
        {groups.map(([g, list]) => (
          <div key={g} className="col tight">
            <span className="tiny muted b">{g}</span>
            {list.map((x) => (
              <div key={x.name} className="fx-help-item">
                <div className="row nowrap">
                  <span className="mono b">{x.name}</span>
                  <span className="small">{x.title}</span>
                  <button type="button" className="btn sm soft push" aria-label={`插入 ${x.name}`} onClick={() => onInsert(x.insert ?? x.name)}>
                    插入
                  </button>
                </div>
                {x.desc && <span className="tiny muted">{x.desc}</span>}
                {x.example && <code className="mono tiny">{x.example}</code>}
              </div>
            ))}
          </div>
        ))}
        {shown.length === 0 && <span className="note">没有找到“{q}”。</span>}
      </div>
    </section>
  );
}
