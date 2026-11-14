// What the backend could not read in a pasted or imported list of stocks (pools_parse): header lines skipped apart, in
// grey; every other item with its reason and, when the item could be one of several stocks, a button for each. Used by
// the stock pool editor and by the first-run setup's own list.
import type { PoolStock, UnparsedItem } from "../api/types.gen";
import { useLabels } from "../labels";

/** A text file chosen by the user, read in the page. */
export function readText(file: File): Promise<string> {
  return new Promise((ok, fail) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result ?? ""));
    r.onerror = () => fail(r.error);
    r.readAsText(file);
  });
}

export function Unparsed({ items, onAdd, onDismiss, what = "名单" }: { items: UnparsedItem[]; onAdd: (s: PoolStock) => void; onDismiss: () => void; what?: string }) {
  const L = useLabels();
  const skipped = items.filter((u) => u.reason === "header");
  const failed = items.filter((u) => u.reason !== "header");
  return (
    <>
      {skipped.length > 0 && <span className="small muted">跳过了表头：{skipped.map((u) => u.item).join("、")}</span>}
      {failed.length > 0 && (
        <div className="inner unparsed" aria-label="没能识别的项">
          <div className="row small">
            <span className="b warn-text">
              有 {failed.length} 项没能识别，没有加入{what}
            </span>
            <button type="button" className="btn sm ghost push" onClick={onDismiss}>
              知道了
            </button>
          </div>
          {failed.map((u, i) => (
            <div key={`${u.item}/${i}`} className="row small">
              <b>{u.item}</b>
              <span className="sub">
                {L.of("parse_reasons", u.reason)}：{L.help("parse_reasons", u.reason)}
              </span>
              {u.candidates.map((c) => (
                <button key={c.code} type="button" className="btn sm soft" onClick={() => onAdd(c)}>
                  加入 {c.name ?? ""} {c.code}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
