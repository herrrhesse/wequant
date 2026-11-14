// Run with `npm test`. contract.gen.ts carries what the page reads from the backend's schema snapshot: it must be
// regenerated (npm run gen:types) whenever the snapshot changes, or this fails.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { contractFacts } from "../../scripts/contract-facts.mjs";
import * as generated from "./contract.gen.ts";

test("contract.gen.ts agrees with the schema snapshot (run npm run gen:types after the snapshot changes)", () => {
  const schema = JSON.parse(readFileSync(new URL("../../contract/service-models.schema.json", import.meta.url), "utf8"));
  assert.deepEqual({ ...generated }, contractFacts(schema));
});

test("the facts the pages use are there", () => {
  assert.ok(generated.MODEL_DEFAULTS.BacktestRequest && Object.keys(generated.MODEL_DEFAULTS.BacktestRequest).length > 0);
  assert.ok(generated.PANEL_FIELDS.includes("close"));
  assert.ok(generated.POOL_INDEX_CODES.includes("000300.SH"));
  assert.ok(generated.AVAILABLE_OPERATIONS.includes("screen"));
});
