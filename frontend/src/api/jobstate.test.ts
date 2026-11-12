// Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { jobView } from "./jobstate.ts";

test("a cancelled job leaves the page ready to start again, with no progress shown", () => {
  assert.deepEqual(jobView({ state: "running" }), { active: true, canStart: false, cancelled: false });
  assert.deepEqual(jobView({ state: "cancelled" }), { active: false, canStart: true, cancelled: true });
});

test("queued counts as in progress; no job, success and failure allow a new start", () => {
  assert.equal(jobView({ state: "queued" }).canStart, false);
  for (const j of [null, { state: "succeeded" as const }, { state: "failed" as const }]) assert.equal(jobView(j).canStart, true);
});
