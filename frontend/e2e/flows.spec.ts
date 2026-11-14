// F8 end-to-end flows on the demo home. They assert rules, never the synthetic numbers.
import { basename, dirname, join } from "node:path";
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
  await expect(page).toHaveURL(/#\/backtest\?.*run=[0-9a-f]+/, { timeout: 90_000 });
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
  await expect(page).toHaveURL(/#\/backtest\?.*run=[0-9a-f]+/, { timeout: 90_000 });
  // The form is first and the run records are last, with the results between them.
  await expect(page.getByRole("region", { name: "回测结果" })).toBeVisible();
  const order = await page.locator("main > section").evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
  const blocks = order.filter((l) => l !== "数据状态");
  expect(blocks[0]).toBe("新建回测");
  expect(blocks.indexOf("回测结果")).toBeGreaterThan(0);
  expect(blocks[blocks.length - 1]).toBe("运行记录");
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
  // The backend's rerun details are technical English: not on the ordinary page.
  expect(await page.getByRole("region", { name: "重跑核对" }).innerText()).not.toMatch(/[A-Za-z]{3,}/);
});

test("data: the page opens with its sections", async ({ page }) => {
  await page.goto("#/data");
  await expect(page.getByRole("region", { name: "数据问题" })).toBeVisible();
  await expect(page.getByRole("region", { name: "存储位置" })).toContainText("交易日历");
});

test("ordinary pages show no developer details: no curl, hashes, decision numbers, code names or English text", async ({ page }) => {
  const runs = await (await page.request.post("api/runs_list", { data: { limit: 1 } })).json();
  const run = runs.rows[0]?.id as string | undefined;
  const pages = ["#/", `#/strategy?id=${preset}`, `#/strategy?view=list`, "#/strategy?new=pick", `#/picks?id=${preset}`, "#/backtest", "#/data", "#/stock/600030.SH"];
  if (run) pages.push(`#/backtest?run=${run}`);
  for (const at of pages) {
    await page.goto(at);
    await page.reload();
    await page.waitForLoadState("networkidle");
    // Hash routes load their data after the page itself: give the requests time to finish.
    await page.waitForTimeout(1500);
    await page.waitForLoadState("networkidle");
    // Folder paths are the user's own names, not page text.
    await page.evaluate(() => document.querySelectorAll(".path").forEach((e) => e.remove()));
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
  // Five stock pages in a row; on a cold machine each may build its panels first.
  test.setTimeout(120_000);
  for (const at of ["#/", `#/strategy?id=${preset}`, "#/backtest", `#/picks?id=${preset}`, "#/data"]) {
    await page.goto(at);
    await page.getByLabel("搜索股票").fill(name!.slice(0, -1));
    // Every wait has its own limit, so a failure names the step and the page it started from.
    await expect(page.getByRole("listbox", { name: "搜索股票" }), `search results from ${at}`).toBeVisible({ timeout: 20_000 });
    await page.getByRole("option", { name: new RegExp(name!) }).click({ timeout: 20_000 });
    await expect(page, `stock page from ${at}`).toHaveURL(/#\/stock\/600030\.SH/, { timeout: 20_000 });
    await expect(page.locator(".stock-title"), `stock title from ${at}`).toContainText(name!, { timeout: 30_000 });
    await expect(page.locator("body"), `source from ${at}`).not.toContainText(/从[a-z]+打开/);
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

test("a strategy saved with an index no longer offered opens and shows it as an old option", async ({ page, request }) => {
  const old = await (await request.post("api/strategy_save", { data: { strategy: { ...spec, pool: { index: "000906.SH", exclude_st: true } }, name: `e2e-old-index-${STAMP}` } })).json();
  await page.goto(`#/strategy?id=${old.id}`);
  const scope = page.locator("[data-step=index]");
  await expect(scope).toContainText("旧选项");
  await expect(scope.getByRole("button", { pressed: true })).toHaveCount(1);
  // Warnings name the index, never its code.
  await expect(page.locator(".strat-bar .warn-list")).toBeVisible();
  await expect(page.locator("main")).not.toContainText("000906");
  await expect(page.locator(".strat-aside .funnel")).toBeVisible();
});

test("backtest results follow the chosen strategy: its latest run, or an empty state when it has none", async ({ page, request }) => {
  // Different content from the preset: runs belong to every saved strategy with the same content.
  const fresh = await (await request.post("api/strategy_save", { data: { strategy: { ...spec, top_n: 4 }, name: `e2e-unrun-${STAMP}` } })).json();
  await page.goto(`#/backtest?use=${fresh.id}`);
  const result = page.getByRole("region", { name: "回测结果" });
  await expect(result).toContainText("这个策略还没有回测过");
  await page.getByLabel("策略", { exact: true }).selectOption(preset);
  await expect(result.locator(".card-head h2").first()).toHaveText(`e2e-preset-${STAMP}`);
  const rows = page.getByRole("region", { name: "运行记录" }).locator("tbody tr");
  await rows.last().click();
  await expect(rows.last().locator(".current-tag")).toBeVisible();
});

test("empty pages say what to do next, and their buttons go there", async ({ page }) => {
  await page.route("**/api/runs_list", (r) => r.fulfill({ json: { total: 0, offset: 0, limit: 100, rows: [] } }));
  await page.goto("#/");
  await page.getByRole("region", { name: "最近的回测" }).getByRole("link", { name: "去回测" }).click();
  await expect(page).toHaveURL(/#\/backtest/);
  await page.unroute("**/api/runs_list");
  await page.route("**/api/strategy_list", (r) => r.fulfill({ json: { rows: [] } }));
  await page.goto("#/strategy?view=list");
  await page.getByRole("region", { name: "我的策略" }).getByRole("link", { name: /新建策略/ }).click();
  await expect(page).toHaveURL(/#\/strategy\?new=pick/);
  await page.getByRole("button", { name: /^空白策略/ }).click();
  await expect(page).toHaveURL(/#\/strategy\?new=1/);
  await page.unroute("**/api/strategy_list");
  await page.route("**/api/screen", async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    await route.fulfill({ response: res, json: { ...body, rows: [] } });
  });
  await page.goto(`#/picks?id=${preset}`);
  await page.getByRole("link", { name: "去策略页调整" }).click();
  await expect(page).toHaveURL(/#\/strategy/);
  await page.goto("#/data");
  await expect(page.getByRole("region", { name: "数据问题" }).locator(".empty")).toBeVisible();
});

test("help: each strategy section explains itself on request; backtest metrics explain themselves on hover", async ({ page }) => {
  await page.goto(`#/strategy?id=${preset}`);
  const buttons = page.locator(".strat-main .what-btn");
  await expect(buttons).toHaveCount(4);
  for (const b of await buttons.all()) await b.click();
  await expect(page.locator(".strat-main .what-text")).toHaveCount(4);
  await page.goto("#/backtest");
  const tile = page.locator(".kpis").first().locator(".stat").first();
  await expect(tile).toHaveAttribute("data-tip", /\S+：\S+/);
});

test("stock pool: built by search, paste, file and index, with unreadable items listed; a strategy previews and backtests on it; a changed pool is taken on request", async ({ page, request }) => {
  test.setTimeout(120_000);
  const names = await (await request.post("api/data_securities", { data: { query: "6000", limit: 3 } })).json();
  const [a, b, c] = names.rows as { code: string; name: string | null }[];
  await page.goto(`#/strategy?id=${preset}`);
  await expect(page.locator(".strat-aside .funnel")).toBeVisible();
  await page.locator("[data-step=index]").getByRole("button", { name: "自定义名单" }).click();
  await page.getByRole("button", { name: "新建", exact: true }).click();
  const d = page.getByRole("dialog", { name: "编辑股票池" });
  await d.getByLabel("股票池名称").fill(`e2e-pool-${STAMP}`);
  // Search
  await d.getByLabel("添加股票").fill(a!.code.slice(0, 6));
  await d.getByRole("option").first().click();
  // Paste, with two items that cannot be read
  await d.getByRole("button", { name: "粘贴一批" }).click();
  await d.getByLabel("粘贴股票清单").fill(`${b!.code}\nXYZ999\n不存在的股票`);
  await d.getByRole("button", { name: "加入名单" }).click();
  await expect(d.getByLabel("没能识别的项")).toContainText("XYZ999");
  await expect(d.getByLabel("没能识别的项")).toContainText("不存在的股票");
  // File
  await d.locator('input[type=file]').setInputFiles({ name: "pool.csv", mimeType: "text/csv", buffer: Buffer.from(`代码\n${c!.code}\n`) });
  await expect(d.getByRole("table", { name: "股票池名单" }).locator("tbody tr")).toHaveCount(3);
  // The file's header line is skipped, not counted among the items that could not be read.
  await expect(d.getByText("跳过了表头：代码")).toBeVisible();
  await expect(d.getByLabel("没能识别的项")).toHaveCount(0);
  // Index
  await d.getByRole("button", { name: /从沪深300添加/ }).click();
  await expect(d.getByRole("status")).toContainText("成分日期");
  const total = Number(((await d.getByText(/共 \d+ 只/).textContent()) ?? "").replace(/\D/g, ""));
  expect(total).toBeGreaterThan(3);
  await d.getByRole("button", { name: "保存" }).click();
  await expect(page.locator(".pool-picker")).toContainText(`（${total} 只）`);
  await expect(page.locator(".strat-aside .pv-status")).toHaveText(/已更新/);
  // The list is the scope itself: the funnel has no separate step for it.
  await expect(page.locator(".strat-aside .funnel")).not.toContainText("自定义名单");
  // Change the pool: one stock less; the strategy keeps its list until asked.
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const e = page.getByRole("dialog", { name: "编辑股票池" });
  await e.locator("tbody tr").first().click();
  await e.getByRole("button", { name: "删除选中" }).click();
  await e.getByRole("button", { name: "保存" }).click();
  await expect(page.locator(".pool-picker")).toContainText("股票池已有改动");
  await expect(page.locator(".pool-picker")).toContainText(`（${total} 只）`);
  await page.getByRole("button", { name: "更新为最新名单" }).click();
  await expect(page.locator(".pool-picker")).toContainText(`来自股票池“e2e-pool-${STAMP}”（${total - 1} 只）`);
  await expect(page.locator(".pool-picker")).not.toContainText("股票池已有改动");
  // Backtest on the pool (the unsaved draft)
  await expect(page.locator(".strat-aside .pv-status")).toHaveText(/已更新/);
  await page.locator(".strat-bar").getByRole("link", { name: "回测" }).click();
  await expect(page.getByLabel("策略", { exact: true })).toHaveValue("__draft");
  await page.getByRole("button", { name: "开始回测" }).click();
  await expect(page).toHaveURL(/run=[0-9a-f]+/, { timeout: 90_000 });
  await expect(page.getByRole("region", { name: "回测结果" }).locator(".summary")).toContainText("自定义股票池");
});

interface Template {
  id: string;
  name: string;
  description: string;
  strategy: { filters: unknown[]; factors: { name: string }[]; top_n: number };
}
const strategyCount = async (page: Page) => ((await (await page.request.post("api/strategy_list", { data: {} })).json()).rows as unknown[]).length;

test("new strategy: a template opens as an unsaved draft with its settings; nothing is saved by choosing; with nothing saved the page starts with the choice", async ({ page }) => {
  const templates = (await (await page.request.post("api/strategy_templates", { data: {} })).json()).rows as Template[];
  expect(templates.length).toBeGreaterThan(0);
  const before = await strategyCount(page);
  await page.goto("#/strategy?view=list");
  await page.getByRole("region", { name: "我的策略" }).getByRole("link", { name: "＋ 新建策略" }).click();
  await expect(page).toHaveURL(/new=pick/);
  const chooser = page.getByRole("region", { name: "新建策略" });
  // Every template the backend offers is a card with its description and key settings.
  await expect(chooser.getByRole("button", { name: /^模板：/ })).toHaveCount(templates.length);
  const t = templates.find((x) => x.strategy.filters.length > 0) ?? templates[0]!;
  const card = chooser.getByRole("button", { name: `模板：${t.name}` });
  await expect(card).toContainText(t.description);
  await expect(card).toContainText(`持有 ${t.strategy.top_n} 只`);
  await expect(card).toContainText(t.strategy.filters.length ? `筛选条件 ${t.strategy.filters.length} 条` : "不设筛选条件");
  await card.click();
  // An unsaved draft with the template's settings, previewed at once.
  await expect(page).toHaveURL(/new=1/);
  await expect(page.getByLabel("策略名称")).toHaveValue(t.name);
  await expect(page.locator(".state.never")).toBeVisible();
  await expect(page.locator(".strat-bar .summary")).toContainText(t.strategy.factors[0]!.name);
  await expect(page.locator(".strat-aside .pv-status")).toHaveText(/已更新/);
  await expect(page.locator(".strat-aside table tbody tr").first()).toBeVisible();
  expect(await strategyCount(page)).toBe(before);
  // Coming back offers to continue it; 空白策略 replaces it with an empty one.
  await page.locator(".strat-bar").getByRole("link", { name: "新建", exact: true }).click();
  await expect(chooser.getByRole("button", { name: "继续没保存的新策略" })).toContainText(t.name);
  await chooser.getByRole("button", { name: /^空白策略/ }).click();
  await expect(page.getByLabel("策略名称")).toHaveValue("新策略");
  await expect(page.locator(".strat-aside").getByText("添加一个打分因子")).toBeVisible();
  // With no saved strategy and nothing begun, the strategy page starts with the choice.
  await page.route("**/api/strategy_list", (r) => r.fulfill({ json: { rows: [] } }));
  await page.goto("#/data");
  await page.evaluate(() => {
    localStorage.removeItem("wequant.strategy.draft");
    localStorage.removeItem("wequant.strategy.last");
  });
  await page.reload();
  await page.goto("#/strategy");
  await expect(page.getByRole("region", { name: "新建策略" })).toBeVisible();
  await page.unroute("**/api/strategy_list");
});

interface Indicator {
  form: unknown;
  label: string;
  help: string;
  category: string;
  kind: string;
  available: boolean;
  needs_merge: boolean;
  legacy: boolean;
}

test("indicators: the condition picker shows each one's name, category and explanation, browses by category, searches by name; the row keeps the explanation", async ({ page }) => {
  const rows = (await (await page.request.post("api/expr_indicators", { data: {} })).json()).rows as Indicator[];
  const cats = (await (await page.request.post("api/labels", { data: {} })).json()).indicator_categories as { code: string; label: string }[];
  const target = rows.find((r) => r.kind === "num" && r.available && !r.legacy && Array.isArray(r.form) && r.form[0] === "STD") ?? rows.find((r) => r.kind === "num" && r.available && Array.isArray(r.form))!;
  const catLabel = cats.find((c) => c.code === target.category)!.label;
  await page.goto("#/strategy?new=pick");
  await page.getByRole("button", { name: /^空白策略/ }).click();
  await page.getByRole("button", { name: "＋ 添加条件", exact: true }).click();
  const panel = page.locator(".add-panel");
  const card = panel.locator(".add-grid button").filter({ hasText: target.label });
  await expect(card.first()).toContainText(target.help);
  await expect(card.first().locator(".ind-cat")).toHaveText(catLabel);
  // No English reaches the page, also for indicators that cannot be used yet.
  expect(await panel.innerText()).not.toMatch(/[A-Za-z]{4,} [a-z]{3,} [a-z]{3,}/);
  const off = rows.find((r) => r.kind === "num" && !r.available && r.needs_merge && !r.legacy);
  if (off) {
    const offCard = panel.locator(".add-grid button").filter({ hasText: off.label });
    await expect(offCard).toBeDisabled();
    await expect(offCard).toContainText("BaoStock");
  }
  // Browse by category: only that category's indicators are listed.
  await panel.getByRole("group", { name: "指标分类" }).getByRole("button", { name: catLabel }).click();
  await expect.poll(async () => [...new Set(await panel.locator(".ind-cat").allTextContents())]).toEqual([catLabel]);
  // Search by name, across categories.
  await panel.getByRole("group", { name: "指标分类" }).getByRole("button", { name: "全部" }).click();
  const all = await panel.locator(".ind-cat").count();
  await panel.getByRole("textbox").fill(target.label);
  await expect(card.first()).toBeVisible();
  expect(await panel.locator(".ind-cat").count()).toBeLessThan(all);
  await card.first().click();
  // The new condition explains its indicator, and its menu is grouped by category.
  await expect(page.locator(".cond .cond-meta").first()).toContainText(`${target.label}：${target.help}`);
  await expect(page.locator(`.cond select optgroup[label="${catLabel}"]`).first()).toBeAttached();
});

// The data folder (存储位置). npm run e2e gives the serve its own settings folder and an empty scratch folder; the
// tests end on the demo home again.
const SCRATCH = process.env.WQ_E2E_SCRATCH ?? "";
const DEMO = process.env.WQ_DEMO_HOME ?? "";

test.describe("data folder", () => {
  test.skip(!SCRATCH || !DEMO, "needs npm run e2e's scratch folder");
  // Refusals answer 404, 409 or 422, which Chrome logs as failed resources.
  test.use({ allowConsole: [/status of 4(04|09|22)/] });

  const location = (page: Page) => page.getByRole("region", { name: "存储位置" });
  const dialog = (page: Page) => page.getByRole("dialog", { name: "选择数据存放的文件夹" });
  const open = async (page: Page, path: string) => {
    const d = dialog(page);
    await d.getByLabel("文件夹路径").fill(path);
    await d.getByLabel("文件夹路径").press("Enter");
    await expect(d.getByLabel("文件夹路径")).toHaveValue(path);
    await expect(d.getByRole("button", { name: "选择这个文件夹" })).toBeEnabled();
  };
  const choose = async (page: Page, path: string) => {
    await location(page).getByRole("button", { name: "更改位置…" }).click();
    await open(page, path);
    await dialog(page).getByRole("button", { name: "选择这个文件夹" }).click();
    await expect(location(page).locator(".loc-confirm")).not.toContainText("正在查看这个文件夹");
  };

  test("folder browser: opens beside the data folder, follows a typed path, up and breadcrumbs, makes a folder, explains refusals", async ({ page }) => {
    await page.goto("#/data");
    await expect(location(page).locator(".loc-path")).toHaveText(DEMO);
    await location(page).getByRole("button", { name: "更改位置…" }).click();
    const d = dialog(page);
    await expect(d.getByRole("listitem").filter({ hasText: basename(DEMO) }).first()).toContainText("现在的数据目录");
    await open(page, SCRATCH);
    await expect(d.getByText("这里没有子文件夹。")).toBeVisible();
    await d.getByLabel("新文件夹名称").fill("新的数据");
    await d.getByRole("button", { name: "新建文件夹" }).click();
    await expect(d.getByLabel("文件夹路径")).toHaveValue(join(SCRATCH, "新的数据"));
    await d.getByLabel("新文件夹名称").fill("a/b");
    await d.getByRole("button", { name: "新建文件夹" }).click();
    await expect(d.getByRole("alert")).toContainText("名称不能用");
    await d.getByRole("button", { name: "上一级" }).click();
    await expect(d.getByLabel("文件夹路径")).toHaveValue(SCRATCH);
    await expect(d.getByRole("listitem").filter({ hasText: "新的数据" })).toBeVisible();
    await d.getByRole("navigation", { name: "路径" }).getByRole("button", { name: basename(dirname(SCRATCH)), exact: true }).click();
    await expect(d.getByLabel("文件夹路径")).toHaveValue(dirname(SCRATCH));
    await d.getByLabel("文件夹路径").fill(join(SCRATCH, "没有这个"));
    await d.getByLabel("文件夹路径").press("Enter");
    await expect(d.getByRole("alert")).toContainText("没有这个文件夹");
    await expect(d.getByRole("button", { name: "选择这个文件夹" })).toBeDisabled();
    await d.getByRole("button", { name: "上一级" }).click();
    await expect(d.getByLabel("文件夹路径")).toHaveValue(SCRATCH);
    const places = (await (await page.request.post("api/fs_places", { data: {} })).json()).rows as { label: string; path: string }[];
    await d.getByLabel("位置").getByRole("button", { name: places[0]!.label, exact: true }).click();
    await expect(d.getByLabel("文件夹路径")).toHaveValue(places[0]!.path);
    await d.getByRole("button", { name: "取消" }).click();
    await expect(d).toBeHidden();
  });

  test("copy, then move, then back to an existing data folder: each switch shows the new folder at once", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("#/data");
    const loc = location(page);
    const modes = loc.getByRole("radiogroup", { name: "怎么处理现有数据" });
    // Copy into the empty folder made above; switching to it is not offered, it holds no data.
    await choose(page, join(SCRATCH, "新的数据"));
    await expect(modes.getByRole("radio", { name: /直接使用那里已有的数据/ })).toBeDisabled();
    await modes.getByRole("radio", { name: /复制过去/ }).check();
    await loc.getByRole("button", { name: "开始复制" }).click();
    await expect(loc.getByRole("status")).toContainText("数据已复制到", { timeout: 60_000 });
    await expect(loc.locator(".loc-path")).toHaveText(join(SCRATCH, "新的数据"));
    await expect(loc).toContainText("交易日历");
    // A folder with other folders in it gets one made for the data; moving is the default.
    await choose(page, SCRATCH);
    await expect(loc.locator(".loc-confirm")).toContainText(join(SCRATCH, "wequant-data"));
    await loc.getByRole("button", { name: "开始移动" }).click();
    await expect(loc.getByRole("status")).toContainText("数据已移动到", { timeout: 60_000 });
    await expect(loc.locator(".loc-path")).toHaveText(join(SCRATCH, "wequant-data"));
    // Other pages read the new folder without a reload.
    await page.getByRole("link", { name: "策略", exact: true }).click();
    await expect(page.locator(".strat-aside .pv-status, .tpl-card").first()).toBeVisible();
    await page.getByRole("link", { name: "数据", exact: true }).click();
    // A WeQuant data folder can only be used as it is.
    await choose(page, DEMO);
    await expect(modes.getByRole("radio", { name: /直接使用那里已有的数据/ })).toBeChecked();
    await expect(modes.getByRole("radio", { name: /把现有数据移过去/ })).toBeDisabled();
    await loc.getByRole("button", { name: "切换过去" }).click();
    await expect(loc.getByRole("status")).toContainText("已切换到");
    await expect(loc.locator(".loc-path")).toHaveText(DEMO);
  });

  test("a move shows its stage and progress and can be cancelled; busy and no-space refusals are explained in Chinese", async ({ page }) => {
    // The backend's job is replayed in the browser: a copy takes longer than a test should wait.
    let state = "running";
    let error: unknown = null;
    const job = () => ({
      id: 990001, kind: "data_relocate", state, request: { to: SCRATCH, mode: "move" }, data_version: null, data_version_name: null, position: null,
      fraction: state === "running" ? 0.4 : 1, stage: state === "failed" ? "relocate_check" : "relocate_copy", params: { done_mb: 2, total_mb: 5 }, text: "", error,
      created_at: "2026-11-14T10:00:00", started_at: "2026-11-14T10:00:01", ended_at: null, seq: 1,
    });
    await page.route("**/api/jobs_start", (r) => r.fulfill({ json: job() }));
    await page.route("**/api/jobs/990001/events", (r) => r.fulfill({ headers: { "content-type": "text/event-stream" }, body: `event: job\ndata: ${JSON.stringify(job())}\n\n` }));
    await page.route("**/api/jobs_show", (r) => r.fulfill({ json: job() }));
    await page.route("**/api/jobs_cancel", (r) => {
      state = "cancelled";
      return r.fulfill({ json: job() });
    });
    await page.goto("#/data");
    const loc = location(page);
    await choose(page, SCRATCH);
    await loc.getByRole("button", { name: "开始移动" }).click();
    const progress = loc.getByRole("status", { name: "正在移动数据" });
    await expect(progress).toContainText("已复制 2/5 MB");
    await expect(progress).toContainText("40%");
    await expect(loc.getByRole("button", { name: "更改位置…" })).toBeDisabled();
    await progress.getByRole("button", { name: "取消" }).click();
    await expect(loc.getByRole("status")).toContainText("已取消，数据仍在");
    await expect(loc.locator(".loc-path")).toHaveText(DEMO);
    // Busy: the backend refuses to start.
    await page.route("**/api/jobs_start", (r) =>
      r.fulfill({ status: 409, json: { exit: 5, name: "LOCKED", message: "busy", detail: [{ code: "store_busy", message: "busy", loc: [], path: [], hint: null }], complete: true } }),
    );
    await choose(page, SCRATCH);
    await loc.getByRole("button", { name: "开始移动" }).click();
    await expect(loc.getByRole("alert")).toContainText("另一个写入");
    // No space: the job stops at its check.
    state = "failed";
    error = { exit: 2, name: "USAGE", message: "no space", detail: [{ code: "no_space", message: "no space", loc: [], path: [], hint: null }], complete: true };
    await page.route("**/api/jobs_start", (r) => r.fulfill({ json: job() }));
    await loc.getByRole("button", { name: "取消" }).click();
    await choose(page, SCRATCH);
    await loc.getByRole("button", { name: "开始移动" }).click();
    await expect(loc.getByRole("alert")).toContainText("空间不够");
    await expect(loc.getByRole("alert")).toContainText("数据仍在原来的位置");
    expect(await loc.innerText()).not.toMatch(/no space|busy/);
  });
});
