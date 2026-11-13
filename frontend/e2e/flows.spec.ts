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
  // A new strategy opens without errors: no marks, and the preview says how to go on.
  await expect(page.locator(".strat-aside .funnel")).toBeVisible();
  await expect(page.locator(".has-err")).toHaveCount(0);
  await expect(page.locator(".strat-aside").getByText("添加一个打分因子")).toBeVisible();
  await page.getByLabel("策略名称").fill(NAME);
  await page.getByRole("button", { name: "＋ 添加因子" }).click();
  await expect(page.locator(".strat-aside table tbody tr").first()).toBeVisible();
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

test("strategy preview: an edit shows updating at once and updated after; results stay on the right; rows and funnel steps light each other", async ({ page }) => {
  await page.goto(`#/strategy?id=${preset}`);
  const row = page.locator("#loc-filters-0");
  const status = page.locator(".strat-aside .pv-status");
  await expect(page.locator(".strat-aside .funnel")).toBeVisible();
  await expect(status).not.toHaveText(/正在更新/);
  // The editing column carries no result numbers.
  await expect(page.locator(".strat-main")).not.toContainText(/剩余 \d+ 只|单独剔除|命中 \d+ 只|剩 \d+ 只/);
  await row.getByLabel("数值").fill("3");
  await expect(status).toHaveText("正在更新预览…");
  await expect(status).toHaveText(/已更新/);
  await expect(page.locator(".strat-main")).not.toContainText(/剩余 \d+ 只|单独剔除|命中 \d+ 只|剩 \d+ 只/);
  await row.hover();
  await expect(page.locator(".strat-aside .funnel button[aria-pressed=true]")).toContainText("20 日平均成交额");
});

test("strategy: the backend's no-ST warning shows a note beside 剔除 ST", async ({ page }) => {
  await page.route("**/api/screen", async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.warnings = [...(body.warnings ?? []), { code: "st_unknown", text: "", params: { known_share: 0 } }];
    await route.fulfill({ response: res, json: body });
  });
  await page.goto(`#/strategy?id=${preset}`);
  await expect(page.locator("[data-step=exclude_st]")).toContainText("这份数据没有 ST 记录");
});

test("strategy to backtest: no run button or last run here; 回测 opens the form with this strategy and filled dates, and the run opens", async ({ page, request }) => {
  const cal = await (await request.post("api/data_calendar", { data: {} })).json();
  await page.goto(`#/strategy?id=${preset}`);
  await expect(page.locator(".strat-aside .funnel")).toBeVisible();
  await expect(page.getByRole("button", { name: "运行回测" })).toHaveCount(0);
  await expect(page.getByText("最近一次回测")).toHaveCount(0);
  // Unchanged: the saved strategy is chosen.
  await page.locator(".strat-bar").getByRole("link", { name: "回测" }).click();
  await expect(page.getByLabel("策略", { exact: true })).toHaveValue(preset);
  await page.goBack();
  // Changed and unsaved: the draft is chosen, with the note.
  await page.locator("#loc-filters-0").getByLabel("数值").fill("0.5");
  await expect(page.locator(".strat-aside .pv-status")).toHaveText(/已更新/);
  await page.locator(".strat-bar").getByRole("link", { name: "回测" }).click();
  await expect(page.getByLabel("策略", { exact: true })).toHaveValue("__draft");
  await expect(page.getByText("未保存的改动也会用于这次回测")).toBeVisible();
  await expect(page.getByLabel("开始日")).toHaveValue(cal.first);
  await expect(page.getByLabel("结束日")).toHaveValue(cal.last);
  await expect(page.getByLabel("初始资金")).not.toHaveValue("");
  await page.getByRole("button", { name: "开始回测" }).click();
  await expect(page).toHaveURL(/#\/backtest\?run=[0-9a-f]+/, { timeout: 90_000 });
  await expect(page.locator(".current-tag")).toHaveCount(1);
});

test("backtest: a running job's card stays across pages and shows 已取消 after cancel", async ({ page }) => {
  // The job's states come from the backend's job list; here they are played back so the run stays in progress.
  let state = "running";
  const job = () => ({
    id: 990001, kind: "backtest", state, request: { saved: { id: preset } }, data_version: 1, data_version_name: "", position: null,
    fraction: 0.4, stage: state === "running" ? "backtest" : "cancelled", params: { day: "2024-06-28", done: 120, total: 300, equity: 1000000 },
    text: "", error: null, created_at: new Date().toISOString(), started_at: new Date().toISOString(), ended_at: null, seq: 1,
  });
  await page.route("**/api/jobs_list", (r) => r.fulfill({ json: { jobs: [job()], limit: 1 } }));
  await page.route("**/api/jobs_show", (r) => r.fulfill({ json: job() }));
  await page.route("**/api/jobs/990001/events", (r) => r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: `event: job\ndata: ${JSON.stringify(job())}\n\n` }));
  await page.route("**/api/jobs_cancel", (r) => {
    state = "cancelled";
    return r.fulfill({ json: job() });
  });
  await page.goto("#/backtest");
  const card = page.getByRole("region", { name: "正在回测" });
  await expect(card).toContainText("正在回测");
  await page.goto("#/data");
  await page.goto("#/backtest");
  await expect(card).toContainText("正在回测");
  await expect(card.locator(".progress")).toBeVisible();
  await card.getByRole("button", { name: "取消" }).click();
  await expect(card).toContainText("已取消");
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
  // The form and the run records come before the results.
  await expect(page.getByRole("region", { name: "回测结果" })).toBeVisible();
  const order = await page.locator("main > section").evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
  expect(order.indexOf("新建回测")).toBeLessThan(order.indexOf("运行记录"));
  expect(order.indexOf("运行记录")).toBeLessThan(order.indexOf("回测结果"));
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
  await page.getByRole("tab", { name: "订单" }).click();
  await expect(page.getByRole("region", { name: "订单" })).toBeVisible();
  await page.getByRole("tab", { name: "运行信息" }).click();
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

test("search: from every page a stock found by part of its name opens with its name in the title and no English page name", async ({ page, request }) => {
  const found = await (await request.post("api/data_securities", { data: { query: "600030", limit: 1 } })).json();
  const name = found.rows[0]?.name as string | null;
  test.skip(!name, "the demo home has no names");
  for (const at of ["#/", `#/strategy?id=${preset}`, "#/backtest", `#/picks?id=${preset}`, "#/data"]) {
    await page.goto(at);
    await page.getByLabel("搜索股票").fill(name!.slice(0, -1));
    await page.getByRole("option", { name: new RegExp(name!) }).click();
    await expect(page).toHaveURL(/#\/stock\/600030\.SH/);
    await expect(page.locator(".stock-title")).toContainText(name!);
    await expect(page.locator("body")).not.toContainText(/从[a-z]+打开/);
  }
  await page.goto(`#/picks?id=${preset}`);
  await expect(page.locator(".picks-main .stock-ref b").first()).not.toHaveText(/^\d{6}\.(SH|SZ)$/);
});

test("picks with no saved strategy: the draft is computed; with no draft either, an empty state", async ({ page }) => {
  await page.route("**/api/strategy_list", (r) => r.fulfill({ json: { rows: [] } }));
  await page.addInitScript((s) => localStorage.setItem("wequant.strategy.draft", JSON.stringify({ id: null, name: "草稿", spec: s })), spec);
  await page.goto("#/picks");
  await expect(page.getByLabel("策略", { exact: true })).toHaveValue("__draft");
  await expect(page.locator(".picks-main tbody tr").first()).toBeVisible();
  await page.evaluate(() => localStorage.removeItem("wequant.strategy.draft"));
  await page.context().clearCookies();
  const fresh = await page.context().newPage();
  await fresh.route("**/api/strategy_list", (r) => r.fulfill({ json: { rows: [] } }));
  await fresh.goto("#/picks");
  await expect(fresh.getByText("还没有策略")).toBeVisible();
  await fresh.close();
});

test("navigation order: 今日, 策略, 回测, 选股, 数据", async ({ page }) => {
  await page.goto("#/");
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("link")).toHaveText(["今日", "策略", "回测", "选股", "数据"]);
});

test("storage: an old version in use is announced and one click goes back; a pinned version says so; a run exports and imports", async ({ page }) => {
  await page.goto("#/data");
  const versions = page.getByRole("region", { name: "版本历史" });
  const older = versions.locator(".version-row").nth(1);
  await older.getByRole("button", { name: "切换到这个版本使用" }).click();
  const notice = page.getByRole("region", { name: "正在使用旧版本" });
  await expect(notice).toBeVisible();
  await notice.getByRole("button", { name: "回到最新版本" }).click();
  await expect(notice).toHaveCount(0);
  await older.getByRole("button", { name: "固定" }).click();
  await expect(older.getByText("已固定")).toBeVisible();
  await older.getByRole("button", { name: "取消固定" }).click();
  await expect(older.getByText("已固定")).toHaveCount(0);

  await page.goto("#/backtest");
  const list = page.getByRole("region", { name: "运行记录" });
  const rows = list.locator("tbody tr");
  await expect(rows.first()).toBeVisible();
  const download = page.waitForEvent("download");
  await rows.first().getByRole("button", { name: "导出" }).click();
  const file = await (await download).path();
  const name = (await download).suggestedFilename();
  expect(name).toMatch(/\.wqrun$/);
  await rows.first().getByRole("button", { name: "保留" }).click();
  await expect(rows.first().getByRole("button", { name: "已保留" })).toBeVisible();
  await expect(rows.first().getByRole("button", { name: "删除" })).toBeDisabled();
  await page.locator('input[type=file][accept=".wqrun"]').setInputFiles({ name, mimeType: "application/octet-stream", buffer: (await import("node:fs")).readFileSync(file) });
  await expect(list.getByRole("status")).toBeVisible();
});
