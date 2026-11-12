import { useRef, type ReactNode } from "react";

/** A button that opens the file chooser for one file of the given kind. */
export function FileButton({ accept, onFile, children, disabled }: { accept: string; onFile: (f: File) => void; children: ReactNode; disabled?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <button type="button" className="btn soft" disabled={disabled} onClick={() => ref.current?.click()}>
        {children}
      </button>
      <input
        ref={ref}
        type="file"
        accept={accept}
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) onFile(f);
        }}
      />
    </>
  );
}
