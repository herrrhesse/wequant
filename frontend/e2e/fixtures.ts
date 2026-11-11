// Every e2e test fails when the browser console logged an error (F7 rule 6). Tests that need the API are skipped
// when `npm run e2e` runs without a backend (WQ_API unset).
import { expect, test as base } from "@playwright/test";

export const test = base.extend<{ allowConsole: RegExp[]; consoleErrors: string[] }>({
  // A 422 is the backend's answer to an invalid strategy or request, located on the controls by the page; Chrome
  // still logs it as a failed resource. A test may allow more (the backend-down test allows the 404 of /api).
  allowConsole: [[/status of 422/], { option: true }],
  consoleErrors: [
    async ({ page, allowConsole }, use) => {
      const errors: string[] = [];
      const allowed = (text: string) => allowConsole.some((re) => re.test(text));
      page.on("console", (m) => {
        if (m.type() === "error" && !allowed(m.text())) errors.push(m.text());
      });
      page.on("pageerror", (e) => errors.push(String(e)));
      await use(errors);
      expect(errors, "browser console errors").toEqual([]);
    },
    { auto: true },
  ],
});

export const withApi = !!process.env.WQ_API;

export { expect };
