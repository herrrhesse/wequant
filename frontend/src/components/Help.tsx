// A "?" beside a heading: its explanation in a small popover, closed by clicking anywhere else or Escape.
import { useEffect, useRef, useState, type ReactNode } from "react";

export function Help({ text, label = "这是什么" }: { text: ReactNode; label?: string }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  return (
    <span className="help" ref={box}>
      <button type="button" className="help-btn" aria-label={label} aria-expanded={open} onClick={() => setOpen(!open)}>
        ?
      </button>
      {open && (
        <span className="help-pop" role="tooltip">
          {text}
        </span>
      )}
    </span>
  );
}
