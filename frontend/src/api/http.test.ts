// Run with `npm test`. Synthetic bodies only (D20).
import assert from "node:assert/strict";
import { test } from "node:test";
import { classify } from "./http.ts";

test("a reply is a result on 2xx JSON, the ErrorBody otherwise, and 'unreachable' when no backend answered", () => {
  const body = JSON.stringify({ exit: 3, name: "NOT_FOUND", message: "no run x", detail: [], complete: true });
  assert.deepEqual(classify(200, '{"a":1}'), { kind: "ok", value: { a: 1 } });
  assert.equal(classify(404, body).kind, "error");
  assert.equal(classify(404, "<!doctype html><title>Not found</title>").kind, "unreachable");
  assert.equal(classify(500, '{"detail":"Internal Server Error"}').kind, "unreachable");
});
