import assert from "node:assert/strict";
import { test } from "node:test";
import { size } from "./files.ts";

test("size: unknown is a dash, nothing is 0 KB, a few bytes are 1 KB", () => {
  assert.equal(size(null), "—");
  assert.equal(size(undefined), "—");
  assert.equal(size(0), "0 KB");
  assert.equal(size(1), "1 KB");
  assert.equal(size(511), "1 KB");
  assert.equal(size(10 * 1024), "10 KB");
  assert.equal(size(3 * 1024 ** 2), "3.0 MB");
  assert.equal(size(2 * 1024 ** 3), "2.00 GB");
});
