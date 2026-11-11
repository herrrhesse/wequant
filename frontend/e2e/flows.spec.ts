// F7 end-to-end flows on the demo home. They assert rules, never the synthetic numbers.
import { expect, test, withApi } from "./fixtures";

test.describe.configure({ mode: "serial" });
test.skip(!withApi, "needs wequant serve (npm run e2e starts it)");

const NAME = `e2e-${Date.now()}`;
const strategy = (field: string) =>
  JSON.stringify({
    // A rank filter always rejects about half of the pool, so the funnel has rejects to check.
    filters: [{ name: "成交额前一半", expr: ["GE", ["CS_PCT", ["MA", field, 5]], 0.5] }],
    factors: [{ name: "动量", expr: ["SUB", ["DIV", "adj_close", ["REF", "adj_close", 5]], 1] }],
    top_n: 5,
  });

test("strategy: a wrong field is located, the fix brings the summary, saving twice keeps one version", async ({ page }) => {
  await page.goto("#/strategy");
  await page.getByRole("button", { name: "新建", exact: true }).click();
  await page.getByRole("group", { name: "代码" }).getByRole("button", { name: "代码" }).click();
  const code = page.locator(".code-view textarea");
  await code.fill(strategy("amout"));
  await page.locator(".code-view").getByRole("button", { name: "可视化" }).click();
  // The backend's error names the field and points at the filter.
  await expect(page.getByTestId("errors")).toContainText("unknown_field");
  await expect(page.locator(".code-view .gutter .err")).toHaveCount(1);
  await page.getByRole("group", { name: "代码" }).getByRole("button", { name: "可视化" }).click();
  await expect(page.locator(".cond.has-error, .sfield.has-error").first()).toBeVisible();
  // Fixed: the error goes, the backend's summary sentence appears.
  await page.getByRole("group", { name: "代码" }).getByRole("button", { name: "代码" }).click();
  await code.fill(strategy("amount"));
  await page.locator(".code-view").getByRole("button", { name: "可视化" }).click();
  await expect(page.getByTestId("errors")).toHaveCount(0);
  await expect(page.getByTestId("validation")).toHaveText("OK");
  await expect(page.getByTestId("summary")).not.toBeEmpty();
  // Save: v1. Save again unchanged: still only v1 (D16).
  await page.getByLabel("策略名称").fill(NAME);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("v1");
  await page.getByRole("button", { name: "版本" }).click();
  await expect(page.getByTestId("versions").locator("tbody tr")).toHaveCount(1);
  await expect(page.getByTestId("dirty")).toHaveText("否");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("没有生成新版本");
  await expect(page.getByTestId("versions").locator("tbody tr")).toHaveCount(1);
});

test("screen: each level's reject list is that level's rejects; the explain role matches the table", async ({ page }) => {
  await page.goto("#/picks");
  await page.getByLabel("策略", { exact: true }).selectOption({ label: NAME });
  await page.getByLabel("决策日").fill("2025-06-30");
  await page.getByRole("button", { name: "选股", exact: true }).click();
  await expect(page.getByRole("table", { name: "有分股票" })).toBeVisible();
  const levels = page.locator(".funnel button:not([disabled])");
  const n = await levels.count();
  expect(n).toBeGreaterThan(0);
  for (let i = 0; i < n; i++) {
    const level = levels.nth(i);
    const label = (await level.getByTestId("funnel-rejected").textContent()) ?? "";
    await level.click();
    // The table of that level has exactly the rows the funnel counts (both are the backend's rejects of the step).
    await expect(page.locator("table[aria-rowcount]")).toHaveAttribute("aria-rowcount", label.replace(/\D/g, ""));
    await level.click();
  }
  const first = page.getByRole("table", { name: "有分股票" }).locator("tbody tr[data-index]").first();
  const role = (await first.getByTestId("row-role").textContent()) ?? "";
  await first.click();
  await expect(page.getByTestId("chain-role")).toHaveText(role);
});

test("backtest: default settings run; KPIs show a value or a reason; a picked day opens; rerun is identical", async ({ page }) => {
  await page.goto("#/backtest");
  await page.getByLabel("策略", { exact: true }).selectOption("demo-momentum");
  await page.getByRole("button", { name: "开始回测" }).click();
  await expect(page).toHaveURL(/#\/backtest\/[0-9a-f]+/, { timeout: 60_000 });
  const tiles = page.locator(".kpis").first().locator(".stat");
  await expect(tiles.first()).toBeVisible();
  expect(await tiles.count()).toBeGreaterThan(10);
  for (const tile of await tiles.all()) {
    const value = (await tile.locator(".stat-v").textContent()) ?? "";
    if (value.trim() === "—") await expect(tile.locator(".stat-s")).not.toBeEmpty();
  }
  const chart = page.getByRole("img", { name: "净值" });
  await chart.scrollIntoViewIfNeeded();
  const box = (await chart.boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.6, box.y + box.height * 0.5);
  await expect(page).toHaveURL(/day=\d{4}-\d{2}-\d{2}/);
  await expect(page.getByTestId("day")).toContainText("权益");
  await page.getByRole("button", { name: "重跑并逐字节比对" }).click();
  await expect(page.getByTestId("rerun")).toContainText("逐字节一致", { timeout: 60_000 });
});

test("data: the page opens and the delisting counts are the backend's", async ({ page, request }) => {
  const counts = (await (await request.post("api/data_delistings", { data: {} })).json()).counts;
  await page.goto("#/data");
  const section = page.getByRole("region", { name: "退市处理" });
  await expect(section.locator(".stat").first()).toContainText(String(counts.total));
});
