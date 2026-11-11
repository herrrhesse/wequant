// With no backend behind the page, it says so (and never shows sample data).
import { expect, test } from "./fixtures";

// The no-API preview answers /api with its own 404 page (that is "unreachable"); Chrome logs that 404.
test.use({ allowConsole: [/status of 404/] });

test("without a backend the page says how to start it", async ({ page }) => {
  await page.goto(process.env.WQ_NOAPI_URL ?? "http://127.0.0.1:4180/");
  await expect(page.getByRole("alert", { name: "后端未启动" })).toBeVisible();
  await expect(page.getByText("wequant serve").first()).toBeVisible();
});
