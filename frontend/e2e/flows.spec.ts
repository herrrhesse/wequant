// F8 end-to-end flows on the demo home. They assert rules, never the synthetic numbers.
import { mkdirSync, writeFileSync } from "node:fs";
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

test.describe("an invalid strategy on purpose", () => {
  // The wrong field below is sent as typed: the backend refuses it (422, logged by Chrome) and the page locates it.
  test.use({ allowConsole: [/status of 422/] });

  test("strategy: a wrong field is located on its row, the fix brings the preview back, saving twice keeps one history entry", async ({ page }) => {
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
    await expect(page.locator(".strat-aside .pv-status")).toHaveText(/已更新/);
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page).toHaveURL(/#\/strategy\?id=/);
    await page.getByRole("button", { name: "修改历史" }).click();
    await expect(page.locator(".hist-list button")).toHaveCount(1);
    // Unchanged content cannot make a second entry.
    await expect(page.getByRole("button", { name: "保存", exact: true })).toBeDisabled();
    await expect(page.locator(".hist-list button")).toHaveCount(1);
  });
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
  // The demo home holds every stock from its first day: nothing to widen, said in small print instead of a button.
  const scope = page.getByRole("region", { name: "数据范围" });
  await expect(scope).toContainText("已包含沪深全部股票");
  await expect(scope.getByRole("button", { name: "扩大数据范围…" })).toHaveCount(0);
  await page.goto("#/data?extend=1");
  await expect(scope.getByRole("group", { name: "要补进来的股票" })).toHaveCount(0);
});

test("ordinary pages show no developer details: no curl, hashes, decision numbers, code names or English text", async ({ page }) => {
  const runs = await (await page.request.post("api/runs_list", { data: { limit: 1 } })).json();
  const run = runs.rows[0]?.id as string | undefined;
  const pages = ["#/", "#/setup", `#/strategy?id=${preset}`, `#/strategy?view=list`, "#/strategy?new=pick", `#/picks?id=${preset}`, "#/backtest", "#/data", "#/stock/600030.SH"];
  if (run) pages.push(`#/backtest?run=${run}`);
  for (const at of pages) {
    await page.goto(at);
    await page.reload();
    await page.waitForLoadState("networkidle");
    // Hash routes load their data after the page itself: give the requests time to finish.
    await page.waitForTimeout(1500);
    await page.waitForLoadState("networkidle");
    // Folder paths and file column names are literal text, not page text.
    await page.evaluate(() => document.querySelectorAll('.path, [translate="no"]').forEach((e) => e.remove()));
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

test("the scrollbar's room is always kept: a page growing taller does not shift sideways", async ({ page }) => {
  // The test browser hides scrollbars, so the rule itself is checked: the page and the strategy preview keep the room.
  await page.goto("#/strategy?new=1");
  await expect(page.locator(".strat-aside")).toBeVisible();
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).scrollbarGutter)).toBe("stable");
  expect(await page.locator(".strat-aside").evaluate((e) => getComputedStyle(e).scrollbarGutter)).toBe("stable");
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

test("help: each strategy section explains itself in a small popover from its ?, closed by a click elsewhere or Escape; no one-line summary, no how-to hints; backtest metrics explain themselves on hover", async ({ page }) => {
  await page.goto(`#/strategy?id=${preset}`);
  const buttons = page.locator(".strat-main").getByRole("button", { name: "这是什么" });
  await expect(buttons).toHaveCount(4);
  await expect(buttons.first()).toHaveText("?");
  const pop = page.getByRole("tooltip");
  await buttons.nth(1).click();
  await expect(pop).toHaveCount(1);
  await expect(pop).toContainText("门槛");
  // A click elsewhere closes it; so does Escape.
  await page.getByRole("heading", { name: /筛选条件/ }).click();
  await expect(pop).toHaveCount(0);
  await buttons.nth(0).click();
  await expect(pop).toContainText("先圈定从哪些股票里挑");
  await page.keyboard.press("Escape");
  await expect(pop).toHaveCount(0);
  await expect(page.locator(".summary")).toHaveCount(0);
  await expect(page.locator(".strat-main")).not.toContainText(/右边|鼠标/);
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
  await expect(page.getByRole("region", { name: "回测结果" })).toBeVisible();
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

  test("copy (the default), then move, then back to an existing data folder: each switch shows the new folder at once", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("#/data");
    const loc = location(page);
    const modes = loc.getByRole("radiogroup", { name: "怎么处理现有数据" });
    // Copy into the empty folder made above: copying is the default; switching to it is not offered, it holds no data.
    await choose(page, join(SCRATCH, "新的数据"));
    await expect(modes.getByRole("radio", { name: /复制过去/ })).toBeChecked();
    await expect(modes.getByRole("radio", { name: /把现有数据移过去/ })).toBeEnabled();
    await expect(modes.getByRole("radio", { name: /直接使用那里已有的数据/ })).toBeDisabled();
    await loc.getByRole("button", { name: "开始复制" }).click();
    const copied = loc.getByRole("status").filter({ hasText: "数据已复制到" });
    await expect(copied).toBeVisible({ timeout: 60_000 });
    await expect(loc.locator(".loc-path")).toHaveText(join(SCRATCH, "新的数据"));
    await expect(loc).toContainText("交易日历");
    // The old folder is still there, with its size; deleting it is the user's to do, not a button here.
    await expect(copied).toContainText(`旧位置 ${DEMO} 的数据还在`);
    await expect(copied).toContainText(/约占 [\d.]+ (KB|MB|GB)。确认新位置一切正常后，可以自己删掉旧文件夹/);
    await expect(loc.getByRole("button", { name: /删除/ })).toHaveCount(0);
    // A folder with other folders in it gets one made for the data; moving is still there to choose.
    await choose(page, SCRATCH);
    await expect(loc.locator(".loc-confirm")).toContainText(join(SCRATCH, "wequant-data"));
    await expect(loc.getByRole("button", { name: "开始复制" })).toBeVisible();
    await modes.getByRole("radio", { name: /把现有数据移过去/ }).check();
    await loc.getByRole("button", { name: "开始移动" }).click();
    await expect(loc.getByRole("status")).toContainText("数据已移动到", { timeout: 60_000 });
    await expect(loc.locator(".loc-path")).toHaveText(join(SCRATCH, "wequant-data"));
    await expect(loc.getByRole("status")).not.toContainText("旧位置");
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
    await loc.getByRole("radiogroup", { name: "怎么处理现有数据" }).getByRole("radio", { name: /把现有数据移过去/ }).check();
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
    await loc.getByRole("button", { name: "开始复制" }).click();
    await expect(loc.getByRole("alert")).toContainText("另一个写入");
    // No space: the job stops at its check.
    state = "failed";
    error = { exit: 2, name: "USAGE", message: "no space", detail: [{ code: "no_space", message: "no space", loc: [], path: [], hint: null }], complete: true };
    await page.route("**/api/jobs_start", (r) => r.fulfill({ json: job() }));
    await loc.getByRole("button", { name: "取消" }).click();
    await choose(page, SCRATCH);
    await loc.getByRole("button", { name: "开始复制" }).click();
    await expect(loc.getByRole("alert")).toContainText("空间不够");
    await expect(loc.getByRole("alert")).toContainText("数据仍在原来的位置");
    expect(await loc.innerText()).not.toMatch(/no space|busy/);
  });
});

// The first-run setup. Builds and the history backfill would ask BaoStock: they are always replayed in the browser and
// never reach the backend. The user's own data imports for real, into the empty serve's folder.
const EMPTY = process.env.WQ_EMPTY_URL ?? "";
const EMPTY_HOME = process.env.WQ_EMPTY_HOME ?? "";

interface FakeJob {
  id: number;
  kind: string;
  state: string;
  fraction: number;
  stage: string;
  params: Record<string, string | number>;
  request?: Record<string, unknown>;
}

/** Replayed build, backfill and extend jobs: started, followed, cancelled and finished in the browser only. */
async function replayBuilds(page: Page) {
  const jobs = new Map<number, FakeJob>();
  const starts: { kind: string; request: Record<string, unknown> }[] = [];
  let next = 880001;
  const info = (j: FakeJob) => ({
    ...j, request: j.request ?? {}, data_version: null, data_version_name: null, position: null, text: "", error: null,
    created_at: "2026-11-14T10:00:00", started_at: "2026-11-14T10:00:01", ended_at: null, seq: 1,
  });
  const idOf = (r: { request: () => { postDataJSON: () => { id?: number } } }) => r.request().postDataJSON()?.id ?? 0;
  // Never let a build reach the backend.
  await page.route(/\/api\/data_(build|backfill|extend)$/, (r) => r.abort());
  await page.route("**/api/jobs_start", (r) => {
    const body = r.request().postDataJSON() as { kind: string; request: Record<string, unknown> };
    if (!["data_build", "data_backfill", "data_extend"].includes(body.kind)) return r.fallback();
    starts.push(body);
    const extra = body.kind === "data_extend" ? { name: "沪深全部", first_year: 2024, last_year: 2026 } : {};
    const j: FakeJob = { id: next++, kind: body.kind, state: "running", fraction: 0.3, stage: "build_stocks", params: { code: "600000.SH", done: 120, total: 5566, ...extra }, request: body.request };
    jobs.set(j.id, j);
    return r.fulfill({ json: info(j) });
  });
  await page.route(/\/api\/jobs\/88\d+\/events$/, (r) => {
    const j = jobs.get(Number(r.request().url().match(/jobs\/(\d+)\//)![1]))!;
    return r.fulfill({ headers: { "content-type": "text/event-stream" }, body: `event: job\ndata: ${JSON.stringify(info(j))}\n\n` });
  });
  await page.route("**/api/jobs_show", (r) => (jobs.has(idOf(r)) ? r.fulfill({ json: info(jobs.get(idOf(r))!) }) : r.fallback()));
  await page.route("**/api/jobs_cancel", (r) => {
    const j = jobs.get(idOf(r));
    if (!j) return r.fallback();
    j.state = "cancelled";
    return r.fulfill({ json: info(j) });
  });
  await page.route("**/api/jobs_result", (r) => {
    const j = jobs.get(idOf(r));
    if (!j) return r.fallback();
    const built = { status: "built", version: 1, version_name: "20261113.1", first: "2025-11-13", last: "2026-11-13", days: 243, requests: 11000, seconds: 3600 };
    const extended = { status: "built", segments: [built], layers: [], added: 3758, pool_id: null };
    return r.fulfill({ json: j.kind === "data_build" ? built : j.kind === "data_extend" ? extended : { segments: [], status: "complete" } });
  });
  await page.route("**/api/jobs_list", async (r) => {
    // Running first, then finished ones, newest first: as the backend lists them.
    const mine = [...jobs.values()].sort((a, b) => Number(a.state !== "running") - Number(b.state !== "running") || b.id - a.id).map(info);
    try {
      const res = await r.fetch();
      const body = await res.json();
      await r.fulfill({ response: res, json: { ...body, jobs: [...mine, ...body.jobs] } });
    } catch {
      // The page closed while the real list was on its way (the test is over): nothing left to answer.
    }
  });
  return { jobs, starts };
}

const RECONNECT = { stage: "build_reconnect", params: { seconds: 30, attempt: 2, of: 5 } };
const RECONNECT_TEXT = "BaoStock 连接中断，30 秒后重新连接（第 2/5 次），已下载的部分不会重下";

test.describe("first-run setup", () => {
  test.skip(!EMPTY, "needs npm run e2e's empty serve (release shape)");
  test.use({ baseURL: EMPTY, allowConsole: [/status of 4(04|09|22)/] });

  test.describe("an empty folder", () => {
    // Nothing allowed but the 422 of every test: no page asks the empty folder for data it does not have.
    test.use({ allowConsole: [/status of 422/] });

    test("every page leads to the setup without one 404 on the way", async ({ page }) => {
      await page.request.post("api/setup_save", { data: { clear: true } });
      const missing: string[] = [];
      page.on("response", (r) => r.status() === 404 && missing.push(r.url()));
      for (const at of ["#/", "#/data", "#/strategy", "#/picks", "#/backtest", "#/stock/600000.SH"]) {
        await page.goto(at);
        await expect(page).toHaveURL(/#\/setup$/);
        await expect(page.getByRole("heading", { name: "先把本地数据准备好" })).toBeVisible();
        await expect(page.getByRole("status", { name: "预计耗时" })).toBeVisible();
      }
      expect(missing).toEqual([]);
    });
  });

  test("a build waiting to reach BaoStock again (replayed) says so in Chinese, keeps its bar and still pauses", async ({ page }) => {
    await page.request.post("api/setup_save", { data: { clear: true } });
    const { jobs } = await replayBuilds(page);
    await page.goto("#/setup");
    await page.getByRole("group", { name: "建最近几年" }).getByRole("button", { name: "1 年" }).click();
    await page.getByRole("button", { name: "下一步" }).click();
    const build = page.getByRole("status", { name: "用 BaoStock 建最近 1 年的数据" });
    await expect(build).toContainText("30%");
    Object.assign([...jobs.values()].at(-1)!, RECONNECT);
    await expect(build).toContainText(RECONNECT_TEXT);
    // Still running: the same share done, no failure, pause at hand.
    await expect(build).toContainText("30%");
    await expect(build.locator(".bar > span")).toHaveAttribute("style", /width: 30%/);
    await expect(page.getByRole("alert")).toHaveCount(0);
    await build.getByRole("button", { name: "暂停" }).click();
    await expect(page.getByText("已暂停。已下载的部分保留")).toBeVisible();
    await page.request.post("api/setup_save", { data: { clear: true } });
  });

  test("an empty folder opens the setup; a BaoStock build (replayed) pauses, goes back, carries on, comes back after a reload and finishes into the history", async ({ page }) => {
    await page.request.post("api/setup_save", { data: { clear: true } });
    const { jobs, starts } = await replayBuilds(page);
    await page.goto("#/strategy");
    await expect(page).toHaveURL(/#\/setup$/);
    await expect(page.getByRole("heading", { name: "先把本地数据准备好" })).toBeVisible();
    await expect(page.getByLabel("数据文件夹")).toHaveText(EMPTY_HOME);
    await expect(page.getByRole("button", { name: /用 BaoStock 新建/ })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("group", { name: "建最近几年" }).getByRole("button", { name: "1 年" }).click();
    await expect(page.getByRole("status", { name: "预计耗时" })).toContainText(/合计约 [\d,]+ 只股票、[\d,]+ 个交易日，预计(约|不到)/);
    await page.getByRole("button", { name: "下一步" }).click();
    const build = page.getByRole("status", { name: "用 BaoStock 建最近 1 年的数据" });
    await expect(build).toContainText("正在下载 600000.SH 的行情（已完成 120/5566 只）");
    // The default stocks: 沪深300 and 中证500.
    expect(starts).toEqual([{ kind: "data_build", request: { years: 1, scope: { all: false, indexes: ["000300.SH", "000905.SH"], stocks: [] }, pool_name: null } }]);
    // The backend remembers the step: a reload comes back to the running build.
    await page.reload();
    await expect(build).toContainText("30%");
    await build.getByRole("button", { name: "暂停" }).click();
    await expect(page.getByText("已暂停。已下载的部分保留")).toBeVisible();
    await page.getByRole("button", { name: "‹ 上一步" }).click();
    await expect(page.getByRole("button", { name: "下一步" })).toBeVisible();
    // The build's step stays reachable at the top; going on starts it again, carrying on from what it fetched.
    await expect(page.getByRole("list", { name: "步骤" }).getByRole("button", { name: /建最近 1 年/ })).toBeEnabled();
    await page.getByRole("button", { name: "下一步" }).click();
    await expect(build).toBeVisible();
    expect(starts.map((x) => x.kind)).toEqual(["data_build", "data_build"]);
    const second = [...jobs.values()].at(-1)!;
    Object.assign(second, { state: "succeeded", fraction: 1, stage: "build_commit", params: {} });
    // Done: the history fills in behind (on by default), and the setup goes on.
    const history = page.getByRole("status", { name: /^后台补全更早的历史（到 \d{4} 年）/ });
    await expect(history).toBeVisible();
    expect(starts.at(-1)!.kind).toBe("data_backfill");
    await expect(page.getByText("最近 1 年的数据已经可用")).toBeVisible();
    await page.getByRole("button", { name: "先去用，让它在后台跑" }).click();
    await expect(page.getByRole("heading", { name: "准备好了" })).toBeVisible();
    const templates = (await (await page.request.post("api/strategy_templates", { data: {} })).json()).rows as unknown[];
    await expect(page.getByRole("button", { name: /^模板：/ })).toHaveCount(templates.length);
    await page.request.post("api/setup_save", { data: { clear: true } });
  });

  test("which stocks to download: each choice shows the backend's count and time, which follow the years; an own list lists what it cannot read; the build carries the choice, also after a reload", async ({ page }) => {
    await page.request.post("api/setup_save", { data: { clear: true } });
    const { starts } = await replayBuilds(page);
    // In an empty folder the list is read against BaoStock's list of securities: replayed, never asked.
    await page.route("**/api/pools_parse", (r) =>
      r.fulfill({
        json: {
          stocks: [{ code: "600519.SH", name: "贵州茅台" }, { code: "000001.SZ", name: "平安银行" }],
          unparsed: [
            { item: "招商", reason: "ambiguous", candidates: [{ code: "600036.SH", name: "招商银行" }, { code: "001979.SZ", name: "招商蛇口" }] },
            { item: "830799", reason: "beijing", candidates: [] },
          ],
        },
      }),
    );
    await page.goto("#/setup");
    const choices = page.getByRole("group", { name: "下载哪些股票" });
    const total = page.getByRole("status", { name: "预计耗时" });
    // 沪深300 + 中证500 by default; every choice says how many stocks and how long.
    await expect(choices.getByRole("button", { name: /^沪深300/ })).toHaveAttribute("aria-pressed", "true");
    await expect(choices.getByRole("button", { name: /^中证500/ })).toHaveAttribute("aria-pressed", "true");
    await expect(choices.getByRole("button", { name: /^沪深全部/ })).toHaveAttribute("aria-pressed", "false");
    const cost = page.getByLabel("沪深300的股票数和耗时");
    await expect(cost).toContainText(/约 [\d,]+ 只 · 预计(约|不到)/);
    await expect(page.getByLabel("沪深全部的股票数和耗时")).toContainText(/约 [\d,]+ 只 · 预计(约|不到)/);
    await expect(page.getByText("先下载一部分股票会快很多，之后随时可以在数据页扩大范围。")).toBeVisible();
    // The years change the times; numbers have thousands separators, the trading days too.
    const three = (await cost.innerText()).trim();
    const threeTotal = (await total.innerText()).trim();
    await page.getByRole("group", { name: "建最近几年" }).getByRole("button", { name: "5 年" }).click();
    await expect(cost).not.toHaveText(three);
    await expect(total).not.toHaveText(threeTotal);
    await expect(total).toContainText(/、\d{1,3}(,\d{3})+ 个交易日/);
    // One index alone: fewer stocks in the total; none at all cannot go on.
    const both = (await total.innerText()).trim();
    await choices.getByRole("button", { name: /^中证500/ }).click();
    await expect(total).not.toHaveText(both);
    await choices.getByRole("button", { name: /^沪深300/ }).click();
    await expect(page.getByText("至少选一项")).toBeVisible();
    await expect(page.getByRole("button", { name: "下一步" })).toBeDisabled();
    // An own list: what cannot be read is listed with the reason; a candidate goes in with a click.
    await choices.getByRole("button", { name: /^自定义名单/ }).click();
    await expect(page.getByRole("button", { name: "下一步" })).toBeDisabled();
    await page.getByLabel("粘贴股票清单").fill("600519 平安银行 招商 830799");
    await page.getByRole("button", { name: "加入名单" }).click();
    const unread = page.getByLabel("没能识别的项");
    await expect(unread).toContainText("有 2 项没能识别");
    await expect(unread).toContainText("招商");
    await expect(unread).toContainText("有歧义");
    await expect(unread).toContainText("北交所");
    await unread.getByRole("button", { name: "加入 招商银行 600036.SH" }).click();
    await expect(page.getByText("名单共 3 只")).toBeVisible();
    await expect(page.getByLabel("自定义名单的股票数和耗时")).toContainText("约 3 只");
    await page.getByRole("button", { name: "下一步" }).click();
    const build = page.getByRole("status", { name: "用 BaoStock 建最近 5 年的数据" });
    await expect(build).toBeVisible();
    expect(starts.at(-1)).toEqual({ kind: "data_build", request: { years: 5, scope: { all: false, indexes: [], stocks: ["600519.SH", "000001.SZ", "600036.SH"] }, pool_name: "首次建库名单" } });
    // After a reload, going back shows the same choice: it comes from the build's own request.
    await page.reload();
    await build.getByRole("button", { name: "‹ 上一步" }).click();
    await expect(choices.getByRole("button", { name: /^自定义名单/ }).first()).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("名单共 3 只")).toBeVisible();
    await page.request.post("api/setup_save", { data: { clear: true } });
  });

  test("own data into a new folder: the setup switches to it; the format is explained with example files; the check lists what it found and what is wrong; the import is real and leads to a template", async ({ page }) => {
    test.setTimeout(90_000);
    // Three stocks over 30 weekdays, one repeated row and one row whose prices do not fit; a list for two of them.
    const own = join(SCRATCH, "own-data");
    mkdirSync(own, { recursive: true });
    const days: string[] = [];
    for (let d = new Date("2025-01-02"); days.length < 30; d.setDate(d.getDate() + 1)) if (d.getDay() % 6 !== 0) days.push(d.toISOString().slice(0, 10));
    const lines = ["code,date,open,high,low,close,volume,amount"];
    ["600000.SH", "600001.SH", "000002.SZ"].forEach((code, k) =>
      days.forEach((day, i) => {
        const p = 10 + k + i * 0.05;
        lines.push(`${code},${day},${p.toFixed(2)},${(p + 0.2).toFixed(2)},${(p - 0.2).toFixed(2)},${(p + 0.1).toFixed(2)},100000,${(100000 * p).toFixed(0)}`);
      }),
    );
    lines.push(lines[1]!);
    lines.push(`600001.SH,2025-02-20,10,9,11,10,100,1000`);
    writeFileSync(join(own, "bars.csv"), lines.join("\n") + "\n");
    writeFileSync(join(own, "securities.csv"), "code,name,list_date,delist_date\n600000.SH,自备甲,2000-01-04,\n600001.SH,自备乙,2001-03-01,\n");
    await page.request.post("api/setup_save", { data: { clear: true } });
    await page.goto("#/");
    await expect(page).toHaveURL(/#\/setup$/);
    await page.getByRole("button", { name: /导入自己的数据/ }).click();
    // The data goes into a new folder made in the browser; the backend switches to it.
    await page.getByRole("button", { name: "浏览…" }).click();
    const d = page.getByRole("dialog", { name: "选择数据存放的文件夹" });
    await d.getByLabel("文件夹路径").fill(SCRATCH);
    await d.getByLabel("文件夹路径").press("Enter");
    await expect(d.getByLabel("文件夹路径")).toHaveValue(SCRATCH);
    await d.getByLabel("新文件夹名称").fill("向导新建");
    await d.getByRole("button", { name: "新建文件夹" }).click();
    await expect(d.getByLabel("文件夹路径")).toHaveValue(join(SCRATCH, "向导新建"));
    await d.getByRole("button", { name: "选择这个文件夹" }).click();
    await expect(page.getByLabel("数据文件夹")).toHaveText(join(SCRATCH, "向导新建"));
    await expect(page.getByText("下一步时改用这个文件夹")).toBeVisible();
    await page.getByRole("button", { name: "下一步" }).click();
    await expect(page.getByText("文件要求")).toBeVisible();
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载示例 bars.csv" }).click();
    expect((await download).suggestedFilename()).toBe("bars.csv");
    await page.getByLabel("要导入的文件夹").fill(own);
    await page.getByRole("button", { name: "检查这个文件夹" }).click();
    const found = page.getByLabel("检查结果");
    await expect(found).toContainText("3 只股票");
    await expect(found).toContainText("重复的行");
    await expect(found).toContainText("价格不合理");
    // A reload comes back to the check, with the folder.
    await page.reload();
    await expect(found).toContainText("3 只股票");
    await page.getByRole("button", { name: "开始导入" }).click();
    await expect(page.getByRole("status").filter({ hasText: "导入完成" })).toContainText("3 只股票", { timeout: 60_000 });
    await page.getByRole("button", { name: "下一步" }).click();
    await expect(page.getByRole("heading", { name: "准备好了" })).toBeVisible();
    const first = page.getByRole("button", { name: /^模板：/ }).first();
    const name = ((await first.getAttribute("aria-label")) ?? "").replace("模板：", "");
    await first.click();
    await expect(page).toHaveURL(/#\/strategy\?new=1/);
    await expect(page.getByLabel("策略名称")).toHaveValue(name);
    // The folder has data now: other pages open as usual.
    await page.goto("#/data");
    await expect(page.getByRole("region", { name: "存储位置" }).locator(".loc-path")).toHaveText(join(SCRATCH, "向导新建"));
  });
  test("own data without stock names: 剔除退市整理期 says on its row that it cannot work here", async ({ page }) => {
    test.setTimeout(90_000);
    const own = join(SCRATCH, "own-no-names");
    mkdirSync(own, { recursive: true });
    const days: string[] = [];
    for (let d = new Date("2025-01-02"); days.length < 30; d.setDate(d.getDate() + 1)) if (d.getDay() % 6 !== 0) days.push(d.toISOString().slice(0, 10));
    const lines = ["code,date,open,high,low,close,volume,amount"];
    ["600000.SH", "600001.SH", "000002.SZ"].forEach((code, k) =>
      days.forEach((day, i) => {
        const p = 10 + k + i * 0.05;
        lines.push(`${code},${day},${p.toFixed(2)},${(p + 0.2).toFixed(2)},${(p - 0.2).toFixed(2)},${(p + 0.1).toFixed(2)},100000,${(100000 * p).toFixed(0)}`);
      }),
    );
    writeFileSync(join(own, "bars.csv"), lines.join("\n") + "\n");
    await page.request.post("api/setup_save", { data: { clear: true } });
    await page.goto("#/setup");
    await page.getByRole("button", { name: /导入自己的数据/ }).click();
    await page.getByRole("button", { name: "浏览…" }).click();
    const d = page.getByRole("dialog", { name: "选择数据存放的文件夹" });
    await d.getByLabel("文件夹路径").fill(SCRATCH);
    await d.getByLabel("文件夹路径").press("Enter");
    await expect(d.getByLabel("文件夹路径")).toHaveValue(SCRATCH);
    await d.getByLabel("新文件夹名称").fill("没有名称");
    await d.getByRole("button", { name: "新建文件夹" }).click();
    await d.getByRole("button", { name: "选择这个文件夹" }).click();
    await page.getByRole("button", { name: "下一步" }).click();
    await page.getByLabel("要导入的文件夹").fill(own);
    await page.getByRole("button", { name: "检查这个文件夹" }).click();
    await expect(page.getByLabel("检查结果")).toContainText("3 只股票");
    await page.getByRole("button", { name: "开始导入" }).click();
    await expect(page.getByRole("status").filter({ hasText: "导入完成" })).toBeVisible({ timeout: 60_000 });
    await page.getByRole("button", { name: "下一步" }).click();
    await page.getByRole("button", { name: /^模板：/ }).first().click();
    await expect(page).toHaveURL(/#\/strategy\?new=1/);
    // The template has the rule on; the data has no names to tell the period by.
    const row = page.locator('[data-step="exclude_delisting"]');
    await expect(row.getByRole("checkbox")).toBeChecked();
    await expect(row).toContainText("这份数据没有股票名称，认不出退市整理期");
    await row.getByRole("checkbox").uncheck();
    await expect(row).not.toContainText("这份数据没有股票名称");
  });
});

test("the history backfill shows in the status bar and on the data page, waits for BaoStock in Chinese, pauses and carries on; the setup can be run again", async ({ page }) => {
  const { jobs } = await replayBuilds(page);
  await page.goto("#/data");
  const loc = page.getByRole("region", { name: "数据范围" });
  const first = (await (await page.request.post("api/data_calendar", { data: {} })).json()).first as string;
  test.skip(first.slice(0, 4) <= "2000", "the demo data already starts in 2000");
  await loc.getByRole("button", { name: "补齐更早的历史（到 2000 年）" }).click();
  const bar = page.getByRole("status", { name: "后台补齐历史" });
  await expect(bar).toContainText("30%");
  const fill = loc.getByRole("status", { name: "后台补齐更早的历史（到 2000 年）" });
  await expect(fill).toBeVisible();
  // Waiting to reach BaoStock again: both say so, the share done stays, pausing still works.
  Object.assign([...jobs.values()].at(-1)!, RECONNECT);
  await expect(bar).toContainText(RECONNECT_TEXT);
  await expect(fill).toContainText(RECONNECT_TEXT);
  await expect(bar).toContainText("30%");
  await expect(fill).toContainText("30%");
  await expect(bar.locator(".status-progress > span")).toHaveAttribute("style", /width: 30%/);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await bar.getByRole("button", { name: "暂停" }).click();
  await expect(bar).toBeHidden();
  await expect(loc).toContainText("补齐已暂停");
  await loc.getByRole("button", { name: "继续补齐" }).click();
  await expect(bar).toBeVisible();
  await page.getByRole("region", { name: "存储位置" }).getByRole("link", { name: "重新运行首次启动向导 ›" }).click();
  await expect(page).toHaveURL(/#\/setup$/);
  await expect(page.getByText("现在的数据文件夹里已经有数据，重新运行向导不会删除它")).toBeVisible();
  await page.getByRole("button", { name: /用 BaoStock 新建/ }).click();
  await expect(page.getByText("这个文件夹里已经有数据；新建或导入需要一个空的文件夹")).toBeVisible();
  await expect(page.getByRole("button", { name: "下一步" })).toBeDisabled();
});

// S2.11 data scope on the data and strategy pages, on demo homes holding part of the stocks (npm run e2e makes them
// with the demo generator's --scope). The layered one: 沪深全部 from 2024-09-23, 沪深300 from the first day, 2023-01-02;
// the other holds 沪深300 alone, from 2023-01-02.
// Widening would ask BaoStock: its jobs are replayed in the browser.
const LAYERED = process.env.WQ_LAYERED_URL ?? "";
const CSI300 = process.env.WQ_CSI300_URL ?? "";

test.describe("data scope, layered data", () => {
  test.skip(!LAYERED, "needs npm run e2e's layered demo home");
  test.use({ baseURL: LAYERED });

  test("the data page says which stocks and from when; a widening not finished is carried on from the data's own layers, with no job of it in the list", async ({ page }) => {
    const { starts } = await replayBuilds(page);
    await page.goto("#/data");
    const card = page.getByRole("region", { name: "数据范围" });
    const layers = card.getByLabel("各部分的起始日");
    await expect(layers).toContainText("沪深全部2024-09-23 起");
    await expect(layers).toContainText("沪深300");
    await expect(layers).toContainText("2023-01-02 起");
    await expect(card).toContainText(/有日线的股票 [\d,]+ 只/);
    // The queue keeps only the newest jobs and a restart forgets them: the layers alone say what is left.
    expect(((await (await page.request.post("api/jobs_list", { data: {} })).json()).jobs as { kind: string }[]).some((j) => j.kind === "data_extend")).toBe(false);
    await expect(card).toContainText("沪深全部已补到 2024-09-23，更早的部分还没补。");
    await card.getByRole("button", { name: "继续扩大（补到 2023-01-02）" }).click();
    expect(starts.at(-1)).toEqual({ kind: "data_extend", request: { scope: { all: true, indexes: [], stocks: [] }, pool_name: null } });
    await expect(page.getByRole("status", { name: "扩大数据范围", exact: true })).toBeVisible();
    await expect(page.locator(".statusbar")).not.toContainText("继续扩大");
  });

  test("while a wider layer is not complete, what the data holds from its first day still cannot be added, and the rest says what it costs", async ({ page }) => {
    // The link opens the choice also when the data page is showing already.
    await page.goto("#/data");
    await expect(page.getByRole("region", { name: "数据范围" })).toContainText("有日线的股票");
    await page.goto("#/data?extend=1");
    const choices = page.getByRole("region", { name: "数据范围" }).getByRole("group", { name: "要补进来的股票" });
    await expect(choices.getByRole("button", { name: /^沪深300/ })).toBeDisabled();
    await expect(choices.getByRole("button", { name: /^沪深300/ })).toContainText("已在数据里");
    // No stock to add (the demo has no 中证500 members) counts as held: not offered, and no time shown.
    await expect(choices.getByRole("button", { name: /^中证500/ })).toBeDisabled();
    await expect(choices.getByRole("button", { name: /^中证500/ })).toContainText("已在数据里");
    await expect(choices.getByRole("button", { name: /^中证500/ })).not.toContainText("预计");
    // 沪深全部 from 2024-09-23 only: its other stocks still need their earlier history.
    await expect(choices.getByRole("button", { name: /^沪深全部/ })).toBeEnabled();
    await expect(choices.getByRole("button", { name: /^沪深全部/ })).toContainText(/要补约 [1-9][\d,]* 只 · 预计(约|不到)/);
  });

  test("a scope complete only from a later day is marked so; a backtest starting before it carries the backend's warning, said on the result, with a way to widen the data", async ({ page, request }) => {
    const saved = await (await request.post("api/strategy_save", { data: { strategy: { ...spec, pool: { index: null, exclude_st: true } }, name: `e2e-all-${STAMP}` } })).json();
    await page.goto(`#/strategy?id=${saved.id}`);
    const scope = page.getByRole("group", { name: "股票范围" });
    await expect(scope.getByRole("button", { name: /^沪深全部/ })).toContainText("2024-09 起完整");
    await expect(scope.getByRole("button", { name: /^沪深300/ })).not.toContainText("起完整");
    await page.goto("#/backtest");
    await page.getByLabel("策略", { exact: true }).selectOption(saved.id);
    // The form starts at the data's first day, 2023-01-02: before 沪深全部 is complete.
    await expect(page.getByLabel("开始日")).toHaveValue("2023-01-02");
    await page.getByRole("button", { name: "开始回测" }).click();
    await expect(page).toHaveURL(/#\/backtest\?.*run=[0-9a-f]+/, { timeout: 90_000 });
    const note = page.getByRole("region", { name: "回测结果" }).getByRole("note", { name: "数据范围提示" });
    await expect(note).toContainText("2024-09-23 之前只有沪深300的数据");
    // The backend's sentence, its full stop, then the link.
    await expect(note).toContainText(/可以在数据页扩大数据范围。 ?去扩大数据范围 ›$/);
    await note.getByRole("link", { name: "去扩大数据范围 ›" }).click();
    await expect(page).toHaveURL(/#\/data\?extend=1$/);
    await expect(page.getByRole("region", { name: "数据范围" }).getByRole("group", { name: "要补进来的股票" })).toBeVisible();
  });
});

test.describe("data scope, 沪深300 only", () => {
  test.skip(!CSI300, "needs npm run e2e's 沪深300-only demo home");
  test.use({ baseURL: CSI300 });

  test("widening picks what to add with the backend's count and time, runs behind, pauses (the offer stays on the card only), carries on and finishes", async ({ page }) => {
    const { jobs, starts } = await replayBuilds(page);
    await page.goto("#/data");
    const card = page.getByRole("region", { name: "数据范围" });
    await card.getByRole("button", { name: "扩大数据范围…" }).click();
    const choices = card.getByRole("group", { name: "要补进来的股票" });
    // What the data holds cannot be added again.
    await expect(choices.getByRole("button", { name: /^沪深300/ })).toBeDisabled();
    await expect(choices.getByRole("button", { name: /^沪深300/ })).toContainText("已在数据里");
    await expect(choices.getByRole("button", { name: /^沪深全部/ })).toContainText(/要补约 [\d,]+ 只 · 预计(约|不到)/);
    await choices.getByRole("button", { name: /^沪深全部/ }).click();
    await expect(card.getByRole("status", { name: "扩大范围的预计耗时" })).toContainText("补到 2023-01-02");
    await card.getByRole("button", { name: "开始扩大" }).click();
    expect(starts.at(-1)).toEqual({ kind: "data_extend", request: { scope: { all: true, indexes: [], stocks: [] }, pool_name: null } });
    const bar = page.getByRole("status", { name: "扩大数据范围", exact: true });
    await expect(bar).toContainText("沪深全部 2024–2026 年");
    await expect(bar).toContainText("30%");
    await expect(card.getByRole("status", { name: "扩大数据范围：沪深全部 · 正在补 2024–2026 年" })).toBeVisible();
    await bar.getByRole("button", { name: "暂停" }).click();
    await expect(card).toContainText("扩大数据范围已暂停，已经补上的部分保留。");
    await expect(bar).toBeHidden();
    await page.getByRole("link", { name: "策略", exact: true }).click();
    await expect(page.locator(".statusbar")).not.toContainText(/扩大数据范围|继续扩大/);
    await page.getByRole("link", { name: "数据", exact: true }).click();
    await card.getByRole("button", { name: /^继续扩大/ }).click();
    expect(starts.at(-1)).toEqual(starts.at(-2));
    await expect(bar).toBeVisible();
    Object.assign([...jobs.values()].at(-1)!, { state: "succeeded", fraction: 1 });
    await expect(bar).toBeHidden();
    await expect(card.getByRole("status", { name: /^扩大数据范围：/ })).toHaveCount(0);
  });

  test("a scope beyond the data stays choosable, marked; a template's draft keeps it and shows the backend's warning with a way to widen the data", async ({ page }) => {
    await page.goto("#/data");
    await page.evaluate(() => localStorage.clear());
    await page.goto("#/strategy?new=pick");
    await page.getByRole("button", { name: /^模板：/ }).first().click();
    const scope = page.getByRole("group", { name: "股票范围" });
    await expect(scope.getByRole("button", { name: /^沪深全部/ })).toHaveAttribute("aria-pressed", "true");
    await expect(scope.getByRole("button", { name: /^沪深全部/ })).toContainText("数据只含部分股票");
    await expect(scope.getByRole("button", { name: /^沪深300/ })).not.toContainText("数据只含部分股票");
    const notes = page.getByRole("list", { name: "提示" });
    await expect(notes).toContainText("这份数据只下载了沪深300的股票");
    await scope.getByRole("button", { name: /^沪深300/ }).click();
    await expect(notes).toBeHidden();
    await scope.getByRole("button", { name: /^沪深全部/ }).click();
    await notes.getByRole("link", { name: "去扩大数据范围 ›" }).click();
    await expect(page).toHaveURL(/#\/data\?extend=1$/);
    await expect(page.getByRole("region", { name: "数据范围" }).getByRole("group", { name: "要补进来的股票" })).toBeVisible();
  });
});

test("data scope: where an index's member list ends, the history backfill says why instead of offering more", async ({ page }) => {
  // No demo home has a member list that ends: data_status is answered in the browser, the words are the backend's.
  await page.route("**/api/data_status", async (r) => {
    const res = await r.fetch();
    const body = await res.json();
    return r.fulfill({ response: res, json: { ...body, scope: { ...body.scope, first_date: "2005-04-08", backfill_floor: { index: "000300.SH", name: "沪深300", date: "2005-04-08" } } } });
  });
  await page.goto("#/data");
  const card = page.getByRole("region", { name: "数据范围" });
  await expect(card.getByRole("note", { name: "补齐历史停在这里" })).toContainText("沪深300的成分名单最早到 2005-04-08，更早的历史补不了");
  await expect(card.getByRole("button", { name: /补齐更早的历史/ })).toHaveCount(0);
});

test("a blank draft (no factor yet) is not sent from picks or backtest: they say what is missing, with the way back; no 4xx on the way", async ({ page }) => {
  const refused: string[] = [];
  page.on("response", (r) => r.status() >= 400 && refused.push(`${r.status()} ${r.url()}`));
  const cal = await (await page.request.post("api/data_calendar", { data: {} })).json();
  await page.goto("#/data");
  await page.evaluate(() => localStorage.clear());
  await page.goto("#/strategy?new=1");
  await expect(page.locator(".strat-aside").getByText("添加一个打分因子")).toBeVisible();
  // 选股: the decision day is the latest trading day; the draft is not screened; the strategy page is one click away.
  await page.goto("#/picks?draft=1");
  const result = page.getByRole("region", { name: "结果" });
  await expect(result).toContainText("这个草稿还没有打分因子");
  await expect(page.getByLabel("决策日")).toHaveValue(cal.last);
  await expect(page.getByRole("button", { name: "选股", exact: true })).toBeDisabled();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.locator("main")).not.toContainText("请求不合法");
  await expect(result.getByRole("link", { name: "去策略页" })).toHaveAttribute("href", "#/strategy?new=1");
  // 回测: the start button is off, the reason beside it.
  await page.goto("#/backtest?use=draft");
  await expect(page.getByLabel("策略", { exact: true })).toHaveValue("__draft");
  await expect(page.getByRole("button", { name: "开始回测" })).toBeDisabled();
  const why = page.getByRole("note", { name: "为什么不能回测" });
  await expect(why).toContainText("这个草稿还没有打分因子");
  await expect(page.locator("main")).not.toContainText("请求不合法");
  await why.getByRole("link", { name: "去策略页 ›" }).click();
  await expect(page).toHaveURL(/#\/strategy\?new=1$/);
  // With a factor, both run the draft.
  await page.getByRole("button", { name: "＋ 添加因子" }).click();
  await expect(page.locator(".strat-aside table tbody tr").first()).toBeVisible();
  await expect(page.locator(".strat-aside .pv-status")).toHaveText(/已更新/);
  await page.goto("#/picks?draft=1");
  await expect(page.locator(".picks-main")).toBeVisible();
  await expect(page.getByRole("region", { name: "结果" })).not.toContainText("还没有打分因子");
  await page.goto("#/backtest?use=draft");
  await expect(page.getByRole("button", { name: "开始回测" })).toBeEnabled();
  expect(refused).toEqual([]);
});

// S2.10a 剔除退市整理期: on by default for a new strategy; a strategy saved without it opens with it off and screens
// as before.
test("剔除退市整理期: a new strategy has it on, with its funnel step; off takes the step away; a strategy saved without it opens off and screens the same", async ({ page, request }) => {
  await page.goto("#/data");
  await page.evaluate(() => localStorage.clear());
  await page.goto("#/strategy?new=1");
  const box = page.getByRole("checkbox", { name: /退市前最后一段交易日/ });
  await expect(box).toBeChecked();
  const steps = page.locator(".strat-aside .funnel button");
  await expect(steps.filter({ hasText: "剔除退市整理期" })).toHaveCount(1);
  const withIt = await steps.count();
  await box.uncheck();
  await expect(steps.filter({ hasText: "剔除退市整理期" })).toHaveCount(0);
  await expect(steps).toHaveCount(withIt - 1);
  // `spec` (saved at the start) has no exclude_delisting: it opens off, and screens as with it off.
  await page.goto(`#/strategy?id=${preset}`);
  await expect(page.getByRole("checkbox", { name: /退市前最后一段交易日/ })).not.toBeChecked();
  await expect(page.locator(".strat-aside .funnel button").filter({ hasText: "剔除退市整理期" })).toHaveCount(0);
  const saved = await (await request.post("api/screen", { data: { saved: { id: preset } } })).json();
  const off = await (await request.post("api/screen", { data: { strategy: { ...spec, pool: { ...spec.pool, exclude_delisting: false } } } })).json();
  expect(saved.rows.map((r: { code: string }) => r.code)).toEqual(off.rows.map((r: { code: string }) => r.code));
  // 选股's temporary changes can turn it on.
  await page.goto(`#/picks?id=${preset}`);
  await page.getByRole("button", { name: "临时调整" }).click();
  await page.getByRole("checkbox", { name: /剔除退市整理期/ }).check();
  await expect(page.getByText("未保存的变体")).toBeVisible();
  await expect(page.locator(".funnel, .picks-layout").getByText("剔除退市整理期").first()).toBeVisible();
});

test("data downloaded by index says how far back the history goes before the backfill starts", async ({ page }) => {
  // The demo homes' member lists start on their first day: data_status is answered in the browser with the backend's
  // shape for a 沪深300 + 中证500 build of the last year.
  await page.route("**/api/data_status", async (r) => {
    const res = await r.fetch();
    const body = await res.json();
    const layer = { all: false, indexes: ["000300.SH", "000905.SH"], stocks: [], label: "沪深300 + 中证500", since: body.scope.first_date };
    const member_floors = [
      { index: "000300.SH", name: "沪深300", date: "2006-01-04" },
      { index: "000905.SH", name: "中证500", date: "2007-01-15" },
    ];
    return r.fulfill({ response: res, json: { ...body, scope: { ...body.scope, layers: [layer], covers: [], backfill_floor: null, member_floors } } });
  });
  await page.goto("#/data");
  const card = page.getByRole("region", { name: "数据范围" });
  await expect(card.getByRole("button", { name: "补齐更早的历史（到 2007 年）" })).toBeVisible();
  await expect(card.getByRole("note", { name: "补齐能补到哪天" })).toHaveText("按指数下载的数据最早补到 2007-01-15（中证500的成分名单从这天开始；沪深300从 2006-01-04 开始），更早需要扩大到沪深全部");
});

test("剔除退市整理期: a stock in its last trading days is left out by its own step, its chain says which day of the period; the day before, it is not", async ({ page, request }) => {
  // The demo's 600070.SH (演示07退) trades its last 15 days from 2024-12-10 to 2024-12-30.
  const strategy = { ...spec, pool: { index: null, exclude_st: true, exclude_delisting: true } };
  const saved = await (await request.post("api/strategy_save", { data: { strategy, name: `e2e-delisting-${STAMP}` } })).json();
  await page.goto(`#/picks?id=${saved.id}&date=2024-12-30`);
  const funnel = page.getByRole("region", { name: "漏斗" });
  await funnel.getByRole("button", { name: /剔除退市整理期/ }).click();
  const out = page.getByRole("table", { name: "被剔除的股票" });
  // The list says it too, in the chain's words.
  await expect(out.getByRole("row").filter({ hasText: "600070.SH" })).toContainText("处于退市整理期（第 15/15 天）");
  await out.getByRole("row").filter({ hasText: "600070.SH" }).click();
  await expect(page.getByRole("complementary", { name: "600070.SH" })).toContainText("处于退市整理期（第 15/15 天）");
  const before = await (await request.post("api/screen", { data: { strategy, date: "2024-12-09" } })).json();
  expect(before.rejects.some((x: { code: string; step: string }) => x.code === "600070.SH" && x.step === "exclude_delisting")).toBe(false);
});

test("a backtest whose benchmark starts later than the run says from which day its line is drawn; one starting on day one says nothing", async ({ page }) => {
  await page.goto("#/backtest");
  await page.getByLabel("策略", { exact: true }).selectOption(preset);
  await page.getByRole("button", { name: "开始回测" }).click();
  await expect(page).toHaveURL(/#\/backtest\?.*run=[0-9a-f]+/, { timeout: 90_000 });
  const result = page.getByRole("region", { name: "回测结果" });
  await expect(result.getByRole("img", { name: "净值曲线" })).toBeVisible();
  // The demo's indexes start on its first day: the line starts with the run.
  await expect(result).not.toContainText("基准 无");
  await expect(result.getByRole("note", { name: "基准线" })).toHaveCount(0);
  // Answered in the browser with the backend's shape for a run from before the benchmark's first close: no value
  // before that day, the strategy's nav on it, excess 0.
  let first = "";
  await page.route("**/api/runs_show", async (r) => {
    const res = await r.fetch();
    const body = await res.json();
    const daily = body.daily as { date: string; nav: number; benchmark_nav: number | null; excess: number | null }[];
    const k = Math.floor(daily.length / 3);
    const scale = daily[k].nav / (daily[k].benchmark_nav as number);
    first = daily[k].date;
    body.benchmark = "000300.SH";
    body.daily = daily.map((d, i) => ({ ...d, benchmark_nav: i < k ? null : (d.benchmark_nav as number) * scale, excess: i < k ? null : i === k ? 0 : d.excess }));
    return r.fulfill({ response: res, json: body });
  });
  await page.reload();
  await expect(result).toContainText("基准 沪深300");
  expect(first).not.toBe("");
  await expect(result.getByRole("note", { name: "基准线" })).toHaveText(`沪深300从 ${first} 起才有数据，基准线和超额从这天画起，起点对齐到当天的策略净值。`);
});

test("a backtest whose benchmark has no value on any day says there is no benchmark line, and names the index", async ({ page }) => {
  // Kept for a run the backend should not give (a benchmark with no close in the whole run is no_data): answered
  // in the browser.
  await page.route("**/api/runs_show", async (r) => {
    const res = await r.fetch();
    const body = await res.json();
    body.benchmark = "000300.SH";
    body.daily = body.daily.map((d: Record<string, unknown>) => ({ ...d, benchmark_nav: null, excess: null }));
    return r.fulfill({ response: res, json: body });
  });
  await page.goto("#/backtest");
  await page.getByLabel("策略", { exact: true }).selectOption(preset);
  await page.getByRole("button", { name: "开始回测" }).click();
  await expect(page).toHaveURL(/#\/backtest\?.*run=[0-9a-f]+/, { timeout: 90_000 });
  const result = page.getByRole("region", { name: "回测结果" });
  await expect(result).toContainText("基准 沪深300");
  await expect(result.getByRole("note", { name: "基准线" })).toHaveText("沪深300在回测期间没有数据，所以画不出基准线和超额。");
});
