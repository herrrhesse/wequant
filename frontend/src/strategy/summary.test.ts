// Run with `npm test`. Synthetic sample shaped like a real `strategy summary` (D20): parts cover only some of the
// text, the punctuation between them belongs to no part, and one part's words are not in the text.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { SummaryPart } from "../api/types.gen";
import { summarySegments } from "./summary.ts";

const part = (text: string, field: string): SummaryPart => ({ loc: [[field]], text, params: {} });

test("the shown summary is the backend text character for character; parts only mark clickable stretches", () => {
  const text = "在示例指数成分股中，剔除 ST 股，按动量（100%，越大越好）打分，持有前10只，每周调仓。";
  const parts = [part("在示例指数成分股中", "pool"), part("按动量（100%，越大越好）打分", "factors"), part("没有出现的片段", "band"), part("每周调仓", "period")];
  const segments = summarySegments(text, parts);
  assert.equal(segments.map((s) => s.text).join(""), text);
  assert.deepEqual(
    segments.filter((s) => s.part).map((s) => s.text),
    ["在示例指数成分股中", "按动量（100%，越大越好）打分", "每周调仓"],
  );
});
