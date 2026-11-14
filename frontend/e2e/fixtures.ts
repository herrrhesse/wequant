// Every e2e test fails when the browser console logged an error (F7 rule 6). Tests that need the API are skipped
// when `npm run e2e` runs without a backend (WQ_API unset).
import { expect, test as base } from "@playwright/test";

export const test = base.extend<{ allowConsole: RegExp[]; consoleErrors: string[] }>({
  // Nothing is allowed by default: a page sends nothing the backend refuses. A test that sends an invalid strategy on
  // purpose allows the 422 Chrome logs for it (the page locates it on the controls); the backend-down test allows the
  // 404 of /api.
  allowConsole: [[], { option: true }],
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
