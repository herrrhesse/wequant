// F8 end-to-end flows on the demo home. They assert rules, never the synthetic numbers.
import type { Page } from "@playwright/test";
import { expect, test, withApi } from "./fixtures";

test.describe.configure({ mode: "serial" });
test.skip(!withApi, "needs wequant serve (npm run e2e starts it)");

const STAMP = Date.now();
const NAME = `e2e-${STAMP}`;
let preset = "";

const spec = {
  pool: { index: "000300.SH", exclude_st: true },
  filters: [{ name: "20 日平均成交额大于 0.3亿元", expr: ["GT", ["MA", "amount", 20], 30000000] }],
  factors: [{ name: "20 日涨跌幅", expr: ["SUB", ["DIV", "adj_close", ["REF", "adj_close", 20]], 1], prefer: "high", weight: 1 }],
  top_n: 5,
  backups: 2,
};

test.beforeAll(async ({ request }) => {
  const saved = await (await request.post("api/strategy_save", { data: { strategy: spec, name: `e2e-preset-${STAMP}` } })).json();
  preset = saved.id;
});

const blur = (page: Page) => page.keyboard.press("Tab");

test("strategy: a wrong field is located on its row, the fix brings the summary, saving twice keeps one history entry", async ({ page }) => {
  await page.goto("#/strategy?new=1");
  await page.getByLabel("策略名称").fill(NAME);
  await page.getByRole("button", { name: "＋ 添加因子" }).click();
  await page.getByRole("button", { name: "＋ 添加条件", exact: true }).click();
  await page.getByRole("button", { name: /自定义表达式/ }).click();
  await page.getByLabel("表达式").fill('["GT", ["MA", "amout", 5], ["MA", "amount", 20]]');
  await blur(page);
  // The backend's error points at that condition; the row is marked and says what is wrong in Chinese.
  await expect(page.locator(".cond.has-err")).toHaveCount(1);
  await expect(page.locator(".cond.has-err .err-text")).not.toBeEmpty();
  await expect(page.locator(".state.errors")).toBeVisible();
  await page.getByLabel("表达式").fill('["GT", ["MA", "amount", 5], ["MA", "amount", 20]]');
  await blur(page);
  await expect(page.locator(".has-err")).toHaveCount(0);
  await expect(page.locator(".strat-bar .summary")).not.toBeEmpty();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page).toHaveURL(/#\/strategy\?id=/);
  await page.getByRole("button", { name: "修改历史" }).click();
  await expect(page.locator(".hist-list button")).toHaveCount(1);
  // Unchanged content cannot make a second entry.
  await expect(page.getByRole("button", { name: "保存", exact: true })).toBeDisabled();
  await expect(page.locator(".hist-list button")).toHaveCount(1);
});

test("strategy preview: changing a threshold changes the row's 剩余 and the funnel together, and they agree", async ({ page }) => {
  await page.goto(`#/strategy?id=${preset}`);
  const row = page.locator("#loc-filters-0");
  const left = row.locator(".cond-count span").first();
  const funnelN = () => page.locator(".strat-aside .funnel button", { hasText: "20 日平均成交额" }).locator(".num.b");
  await expect(left).toHaveText(/剩余 \d+ 只/);
  const before = (await left.textContent()) ?? "";
  await expect(funnelN()).toHaveText(before.replace(/\D/g, ""));
  await row.getByLabel("数值").fill("3");
  await expect(left).not.toHaveText(before);
  await expect(left).toHaveText(/剩余 \d+ 只/);
  const after = (await left.textContent()) ?? "";
  await expect(funnelN()).toHaveText(after.replace(/\D/g, ""));
});

test("today: a strategy shown on today appears there; hiding a block keeps it hidden after a reload", async ({ page }) => {
  await page.goto(`#/strategy?id=${preset}`);
  await page.locator(".strat-bar label.switch").click();
  await expect(page.getByRole("switch", { name: "在今日显示" })).toBeChecked();
  await page.goto("#/");
  await expect(page.locator(".follow-card", { hasText: `e2e-preset-${STAMP}` })).toBeVisible();
  await expect(page.getByRole("region", { name: "最近的回测" })).toBeVisible();
  await page.getByRole("button", { name: "自定义" }).click();
  await page.getByRole("dialog", { name: "自定义今日页" }).getByLabel("最近的回测").uncheck();
  await expect(page.getByRole("region", { name: "最近的回测" })).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".follow-card").first()).toBeVisible();
  await expect(page.getByRole("region", { name: "最近的回测" })).toHaveCount(0);
});

test("picks: each level's reject list has the funnel's count; a weight change makes a variant that resets", async ({ page }) => {
  await page.goto(`#/picks?id=${preset}`);
  const levels = page.locator(".picks-funnel .funnel button:not([disabled])");
  await expect(levels.first()).toBeVisible();
  const n = await levels.count();
  expect(n).toBeGreaterThan(0);
  for (let i = 0; i < n; i++) {
    const level = levels.nth(i);
    const out = ((await level.locator(".funnel-out").textContent()) ?? "").replace(/\D/g, "");
    await level.click();
    await expect(page.getByRole("table", { name: "被剔除的股票" }).locator("tbody tr")).toHaveCount(Number(out));
    await level.click();
  }
  await page.getByRole("button", { name: "临时调整" }).click();
  await page.getByLabel("20 日涨跌幅 权重").fill("4");
  await expect(page.getByText("未保存的变体")).toBeVisible();
  await page.getByRole("button", { name: "还原" }).click();
  await expect(page.getByText("未保存的变体")).toHaveCount(0);
});

test("backtest: default settings run; metrics show a value or a reason; a picked day opens; rerun is identical", async ({ page }) => {
  await page.goto("#/backtest");
  await page.getByLabel("策略", { exact: true }).selectOption(preset);
  await page.getByRole("button", { name: "开始回测" }).click();
  await expect(page).toHaveURL(/#\/backtest\?run=[0-9a-f]+/, { timeout: 90_000 });
  const tiles = page.locator(".kpis").first().locator(".stat");
  await expect(tiles.first()).toBeVisible();
  for (const tile of await tiles.all()) {
    const value = (await tile.locator(".stat-v").textContent()) ?? "";
    if (value.trim() === "—") await expect(tile.locator(".stat-s")).not.toBeEmpty();
  }
  const chart = page.getByRole("img", { name: "净值曲线" });
  await chart.scrollIntoViewIfNeeded();
  const box = (await chart.boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.6, box.y + 100);
  await expect(page).toHaveURL(/day=\d{4}-\d{2}-\d{2}/);
  await expect(page.getByTestId("day")).toBeVisible();
  await page.getByRole("button", { name: /运行信息/ }).click();
  await page.getByRole("button", { name: "重跑并逐字节比对" }).click();
  await expect(page.getByTestId("rerun")).toContainText("逐字节一致", { timeout: 90_000 });
});

test("data: the page opens with its sections", async ({ page }) => {
  await page.goto("#/data");
  await expect(page.getByRole("region", { name: "数据问题" })).toBeVisible();
  await expect(page.getByRole("region", { name: "交易日历" })).toBeVisible();
});

test("ordinary pages show no developer details: no curl, hashes, decision numbers, code names or English text", async ({ page }) => {
  const runs = await (await page.request.post("api/runs_list", { data: { limit: 1 } })).json();
  const run = runs.rows[0]?.id as string | undefined;
  const pages = ["#/", `#/strategy?id=${preset}`, `#/strategy?view=list`, `#/picks?id=${preset}`, "#/backtest", "#/data", "#/stock/600030.SH"];
  if (run) pages.push(`#/backtest?run=${run}`);
  for (const at of pages) {
    await page.goto(at);
    await page.reload();
    await page.waitForLoadState("networkidle");
    // Hash routes load their data after the page itself: give the requests time to finish.
    await page.waitForTimeout(1500);
    await page.waitForLoadState("networkidle");
    const text = await page.locator("body").innerText();
    expect(text, at).not.toMatch(/curl/);
    expect(text, at).not.toMatch(/\b[0-9a-f]{16}\b/);
    expect(text, at).not.toMatch(/[A-Za-z]{4,} [a-z]{3,} [a-z]{3,} [a-z]{3,}/);
    expect(text, at).not.toMatch(/\bD\d{2}\b/);
    expect(text, at).not.toMatch(/\b[a-z]+_[a-z_]+\b/);
  }
});
