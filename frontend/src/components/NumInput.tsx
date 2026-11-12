import { useEffect, useState, type InputHTMLAttributes } from "react";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange"> & {
  value: number | null | undefined;
  /** Called with the number typed, or null when the box is emptied; not called while the text is not a number. */
  onValue: (v: number | null) => void;
  /** Display factor: shown = value / scale. */
  scale?: number;
};

/** A number box that keeps what the user is typing ("1.", "-") and only reports numbers. */
export function NumInput({ value, onValue, scale = 1, className, ...rest }: Props) {
  const shown = value === null || value === undefined ? "" : String(Number((value / scale).toPrecision(12)));
  const [text, setText] = useState(shown);
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    if (!focused) setText(shown);
  }, [shown, focused]);
  return (
    <input
      {...rest}
      className={`input num ${className ?? ""}`}
      inputMode="decimal"
      value={focused ? text : shown}
      onFocus={() => {
        setText(shown);
        setFocused(true);
      }}
      onBlur={() => setFocused(false)}
      onChange={(e) => {
        const t = e.target.value;
        setText(t);
        if (t.trim() === "") onValue(null);
        else if (Number.isFinite(Number(t))) onValue(Number((Number(t) * scale).toPrecision(12)));
      }}
    />
  );
}
