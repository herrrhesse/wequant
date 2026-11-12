import { useState } from "react";

/** The equivalent request as a command (developer mode); click to copy. */
export function Command({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard unavailable: the command stays visible to copy by hand
    }
  };
  return (
    <button type="button" className="dev-btn" onClick={copy} title={command}>
      {copied ? "已复制 ✓" : "复制命令"}
    </button>
  );
}
