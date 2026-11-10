// Run with `npm test`. Synthetic values only (D20).
import assert from "node:assert/strict";
import { test } from "node:test";
import type { MetricOut, Reject } from "../api/types.gen";
import { metricShown, rejectsAt } from "./display.ts";

const reject = (code: string, step: string): Reject => ({
  code, step, kind: "condition", passed: false, value: null, op: null, threshold: null, threshold_hi: null, members: null, missing: null,
});

test("a funnel level's reject list is exactly the backend's rejects whose step is that level, in their order", () => {
  const rows = [reject("999901.SH", "成交额"), reject("999902.SZ", "index"), reject("999903.SH", "成交额"), reject("999904.SZ", "成交额大于")];
  assert.deepEqual(rejectsAt(rows, "成交额").map((r) => r.code), ["999901.SH", "999903.SH"]);
});

test("a metric shows the backend value in its own unit, and the backend reason when the value is null", () => {
  const m = (value: MetricOut["value"], unit: string, reason: string | null = null): MetricOut => ({ value, unit, reason });
  assert.deepEqual(
    [m(0.1234, "fraction"), m(0.7643, "ratio"), m(216, "days"), m("2025-04-07", "date"), m(null, "fraction", "too few days")].map((x) => metricShown(x)),
    [
      { text: "12.34%", reason: null },
      { text: "0.76", reason: null },
      { text: "216 天", reason: null },
      { text: "2025-04-07", reason: null },
      { text: "—", reason: "too few days" },
    ],
  );
});
