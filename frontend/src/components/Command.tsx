import { useState } from "react";
import { t } from "../i18n";

/** The equivalent wequant command of a result; click to copy. */
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
    <button type="button" className="cmd mono" onClick={copy} title={t.common.copy}>
      {copied ? t.common.copied : command}
    </button>
  );
}
