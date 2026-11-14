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
