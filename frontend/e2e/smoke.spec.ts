// Smoke: the built app loads and shows its navigation. Replaced by the F7 flows once the HTTP API is in main.
import { expect, test } from "./fixtures";

test("the app loads and shows its navigation", async ({ page }) => {
  await page.goto("./");
  await expect(page.getByRole("navigation")).toBeVisible();
});
