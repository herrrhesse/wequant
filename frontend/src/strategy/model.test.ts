// Run with `npm test`. Synthetic strategies and funnels shaped like the backend's (D20).
import assert from "node:assert/strict";
import { test } from "node:test";
import type { FunnelStep, StrategySpec } from "../api/types.gen";
import { boxes, counts, cutAlone, deltas, readCondition, uniqueName, writeCondition } from "./model.ts";

const step = (name: string, kind: FunnelStep["kind"], passed: number, left: number | null): FunnelStep => ({ step: name, kind, chain: left !== null, passed, left, mode: null, members: null });

test("consecutive plain conditions share one box; each group is its own box, in filter order", () => {
  const c = (name: string) => ({ name, expr: ["GT", "close", 1] });
  const spec: StrategySpec = { filters: [c("a"), c("b"), { name: "g", mode: "any", conditions: [c("x"), c("y")] }, c("d")] };
  const b = boxes(spec);
  assert.deepEqual(b.map((x) => [x.group, x.mode, x.items.map((i) => i.c.name)]), [[null, "all", ["a", "b"]], [2, "any", ["x", "y"]], [null, "all", ["d"]]]);
  assert.deepEqual(b[1]?.items[1]?.path, ["filters", 2, "conditions", 1]);
});

test("a condition cuts alone the pool size minus what passes it on its own; a pool step, the scope minus its own pass", () => {
  const c = counts([step("universe", "universe", 59, 59), step("index", "pool", 30, 30), step("exclude_st", "pool", 58, 29), step("amt", "condition", 20, 18)]);
  assert.equal(c.pool, 29);
  assert.equal(cutAlone(c, "amt", "condition"), 9);
  assert.equal(cutAlone(c, "exclude_st", "pool"), 1);
  assert.equal(c.left.get("amt"), 18);
});

test("deltas show how each step's left count changed", () => {
  const a = counts([step("amt", "condition", 20, 18), step("hit", "condition", 7, null)]);
  const b = counts([step("amt", "condition", 25, 21), step("hit", "condition", 5, null)]);
  assert.deepEqual([...deltas(a, b)], [["amt", "+3"], ["hit", "−2"]]);
});

test("a value in display units is written in backend units and reads back the same", () => {
  const row = readCondition(["GT", ["MA", "amount", 20], 100000000]);
  assert.equal(row.custom, false);
  if (row.custom) return;
  assert.equal(row.value, "1");
  assert.deepEqual(writeCondition({ ...row, value: "2.5" }), ["GT", ["MA", "amount", 20], 250000000]);
  assert.equal(writeCondition({ ...row, value: "" }), null);
});

test("a taken name gets the next free number", () => {
  assert.equal(uniqueName("动量", new Set(["动量", "动量 2"])), "动量 3");
  assert.equal(uniqueName("新", new Set()), "新");
});
