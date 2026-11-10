// Run with `npm test` (node --test, types stripped). No real data (D20).
import assert from "node:assert/strict";
import { test } from "node:test";
import { conditionForm, parseCondition, type Compare, type Metric } from "./expr.ts";

const change20: Metric = { kind: "change", n: 20 };

test("top N by highest value is CS_RANK of the negated metric at most N (field map 1.3, checked with expr check)", () => {
  assert.deepEqual(conditionForm(change20, { op: "top_n", n: 20, dir: "high" }), [
    "LE",
    ["CS_RANK", ["MUL", ["SUB", ["DIV", "adj_close", ["REF", "adj_close", 20]], 1], -1]],
    20,
  ]);
});

test("top x% by highest value is CS_PCT above 1 - x/100 (field map 1.3)", () => {
  assert.deepEqual(conditionForm({ kind: "field", field: "amount" }, { op: "top_pct", pct: 30, dir: "high" }), ["GT", ["CS_PCT", "amount"], 0.7]);
});

test("every comparison the builder writes reads back as the same controls", () => {
  const compares: Compare[] = [
    { op: "gt", value: 5e8 },
    { op: "lt", value: -0.05 },
    { op: "top_n", n: 10, dir: "high" },
    { op: "top_n", n: 10, dir: "low" },
    { op: "top_pct", pct: 30, dir: "high" },
    { op: "top_pct", pct: 30, dir: "low" },
  ];
  for (const compare of compares) assert.deepEqual(parseCondition(conditionForm(change20, compare)), { metric: change20, compare });
});

test("a form the builder did not write is left to the code view, not guessed", () => {
  assert.equal(parseCondition(["AND", ["GT", "close", 5], ["NOT", "st"]]), null);
  assert.equal(parseCondition(["GT", ["MA", "adj_close", 20], ["MA", "adj_close", 60]]), null);
});
