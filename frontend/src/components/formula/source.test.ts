// Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { insertAt, lineCount, offsetOf, spanOf, toTsv } from "./source.ts";

const text = "N:=9;\nK:=SMA(RSV,M1,1);\nXG: K>D;";

test("offsetOf: 1-based line and column, clamped to the line", () => {
  assert.equal(offsetOf(text, 1, 1), 0);
  assert.equal(text.slice(offsetOf(text, 2, 4)), "SMA(RSV,M1,1);\nXG: K>D;");
  assert.equal(offsetOf(text, 2, 999), text.indexOf("\nXG"));
  assert.equal(offsetOf(text, 9, 1), text.length);
});

test("spanOf: the word given, stopping at the line's end; at least one character", () => {
  const [a, b] = spanOf(text, 2, 4, 3);
  assert.equal(text.slice(a, b), "SMA");
  const [c, d] = spanOf(text, 1, 5, 40);
  assert.equal(text.slice(c, d), ";");
  const [e, f] = spanOf(text, 3, 1, null);
  assert.equal(text.slice(e, f), "X");
});

test("lineCount counts a trailing empty line", () => {
  assert.equal(lineCount(""), 1);
  assert.equal(lineCount(text), 3);
  assert.equal(lineCount(`${text}\n`), 4);
});

test("toTsv: header first, one cell per value, empty for missing, no tabs or breaks inside a cell", () => {
  assert.equal(toTsv(["日期", "K"], [["2024-01-05", 48.2], ["2024-01-12", null]]), "日期\tK\n2024-01-05\t48.2\n2024-01-12\t");
  assert.equal(toTsv(["a"], [["x\ty\nz"]]), "a\nx y z");
});

test("insertAt replaces the selection and puts the caret after the piece", () => {
  assert.deepEqual(insertAt("A AND B", 2, 5, "OR"), { text: "A OR B", caret: 4 });
});
