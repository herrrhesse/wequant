// Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { editSell, formulaOf, sellIsDefault } from "./formula.ts";

test("formulaOf tells a formula object from a tree or a string", () => {
  assert.deepEqual(formulaOf({ text: "C>MA(C,20)", period: "week" }), { text: "C>MA(C,20)", period: "week" });
  assert.equal(formulaOf(["GT", "close", 5]), null);
  assert.equal(formulaOf("st"), null);
  assert.equal(formulaOf(null), null);
});

test("a sell section back to all defaults is removed, so an older strategy keeps its hash", () => {
  const spec: { sell?: unknown } = {};
  editSell(spec, (s) => void (s.stop_loss = 0.1));
  assert.deepEqual(spec.sell, { stop_loss: 0.1 });
  editSell(spec, (s) => void (s.stop_loss = null));
  assert.equal("sell" in spec, false);
  assert.equal(sellIsDefault({ mode: "rebalance", cooldown_days: 0, keep_over_stops: false }), true);
  assert.equal(sellIsDefault({ mode: "rules" }), false);
});

test("formulaWords: the failed parts first, then the others, then the numbers rounded", async () => {
  const { formulaWords } = await import("./formula.ts");
  const w = formulaWords([
    { name: "A", kind: "bool", value: 1, direct: true },
    { name: "B", kind: "bool", value: 0, direct: true },
    { name: "C", kind: "bool", value: null, direct: true },
    { name: "J", kind: "num", value: 48.2345, direct: false },
    { name: "K", kind: "num", value: 45.1, direct: false },
    { name: "VOLSUM", kind: "num", value: 123456.7, direct: false },
  ]);
  assert.deepEqual(w.parts, ["B ✗", "A ✓", "C —"]);
  assert.deepEqual(w.numbers, ["J 48.23", "K 45.1", "VOLSUM 123457"]);
});

test("rejectFormulaValues pairs the screen's lines with a reject's numbers in order", async () => {
  const { rejectFormulaValues } = await import("./formula.ts");
  const screen = { formula_lines: { KDJ: [{ name: "A", kind: "bool", direct: true }, { name: "J", kind: "num", direct: false }] } };
  assert.deepEqual(rejectFormulaValues(screen, { step: "KDJ", formula_values: [0, 48.2] } as { step: string }), [
    { name: "A", kind: "bool", direct: true, value: 0 },
    { name: "J", kind: "num", direct: false, value: 48.2 },
  ]);
  assert.deepEqual(rejectFormulaValues(screen, { step: "other" }), []);
});

test("formulaTexts: a comment changes the formulas as written; a tree has none", async () => {
  const { formulaTexts } = await import("./formula.ts");
  const a = { filters: [{ name: "x", expr: { text: "C>MA(C,20)", period: "day", tree: ["GT"] } }], factors: [{ name: "f", expr: ["SUB", 0, "close"] }] };
  const b = { filters: [{ name: "x", expr: { text: "{均线}\nC>MA(C,20)", period: "day" } }], factors: [{ name: "f", expr: ["SUB", 0, "close"] }] };
  assert.notEqual(formulaTexts(a), formulaTexts(b));
  assert.equal(formulaTexts({ filters: [{ name: "x", expr: ["GT", "close", 5] }] }), "[]");
  assert.equal(formulaTexts(a), formulaTexts({ ...a, filters: [{ name: "x", expr: { text: "C>MA(C,20)" } }] }));
});
