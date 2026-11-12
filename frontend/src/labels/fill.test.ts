import { strict as assert } from "node:assert";
import { test } from "node:test";
import { fill, formatValue } from "./fill.ts";

test("format specs write values as Python's format does", () => {
  assert.equal(formatValue(0.1234, ".1%"), "12.3%");
  assert.equal(formatValue(1234567.891, ",.2f"), "1,234,567.89");
  assert.equal(formatValue(3, ""), "3");
  assert.equal(formatValue("2025-01-02", ""), "2025-01-02");
  assert.equal(formatValue(null, ".1%"), "—");
});

test("optional clauses appear only for params that are set", () => {
  const label = { template: "{a} 个", params: ["a", "b"], optional: { b: "（从 {b} 开始）" } };
  assert.equal(fill(label, { a: 2 }), "2 个");
  assert.equal(fill(label, { a: 2, b: "2024-01-02" }), "2 个（从 2024-01-02 开始）");
});

test("repeated items take the suffixed params in order and are joined by the separator", () => {
  const label = { template: "{index}：{days} 天", params: ["index", "days"], repeat: "；" };
  assert.equal(fill(label, { index: "A", days: 1, index_3: "C", days_3: 3, index_2: "B", days_2: 2 }), "A：1 天；B：2 天；C：3 天");
});
