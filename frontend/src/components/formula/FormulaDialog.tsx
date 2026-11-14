// The formula opened large: the editor with room for a long formula (20 lines and more), the functions and fields
// beside it, and the trial under it. Closed with ×, Escape or a click outside; the text stays as written.
import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

export function FormulaDialog({ title, onClose, editor, help, trial }: { title: string; onClose: () => void; editor: ReactNode; help?: ReactNode; trial?: ReactNode }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  // On the body, above every card (a card can make its own stacking context).
  return createPortal(
    <div className="modal-back fx-modal-back" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <section className="modal fx-modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="card-head">
          <h2>{title}</h2>
          <button type="button" className="icon-btn push" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </div>
        <div className={help ? "fx-modal-body" : undefined}>
          <div className="col">{editor}</div>
          {help}
        </div>
        {trial}
      </section>
    </div>,
    document.body,
  );
}
