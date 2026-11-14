// Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { crumbs, join } from "./paths.ts";

test("breadcrumbs go from the root down, and every crumb opens its own folder", () => {
  assert.deepEqual(crumbs("/data/a b/c"), [
    { label: "/", path: "/" },
    { label: "data", path: "/data" },
    { label: "a b", path: "/data/a b" },
    { label: "c", path: "/data/a b/c" },
  ]);
  assert.deepEqual(crumbs("C:\\Users\\王"), [
    { label: "C:\\", path: "C:\\" },
    { label: "Users", path: "C:\\Users" },
    { label: "王", path: "C:\\Users\\王" },
  ]);
});

test("inside a place, the place's name stands for the folders above it; the deepest place wins", () => {
  const places = [
    { label: "根目录", path: "/" },
    { label: "主目录", path: "/home/me/" },
  ];
  assert.deepEqual(crumbs("/home/me/文档", places), [
    { label: "主目录", path: "/home/me" },
    { label: "文档", path: "/home/me/文档" },
  ]);
  assert.deepEqual(crumbs("/mnt/x", places)[0], { label: "根目录", path: "/" });
});

test("a new folder joins with the separator its parent uses", () => {
  assert.equal(join("/home/me", "新建"), "/home/me/新建");
  assert.equal(join("/", "a"), "/a");
  assert.equal(join("D:\\数据", "wequant-data"), "D:\\数据\\wequant-data");
  assert.equal(join("D:\\", "a"), "D:\\a");
});
