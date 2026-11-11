// End-to-end tests against a running `wequant serve` on a demo home; `npm run e2e` (scripts/e2e.mjs) starts and stops
// everything and passes the URL in WQ_WEB_URL. The browser is the machine's Chrome (channel "chrome"): no download.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.WQ_WEB_URL ?? "http://127.0.0.1:4173",
    channel: "chrome",
    headless: true,
    trace: "off",
    deviceScaleFactor: 1,
    viewport: { width: 1440, height: 1000 },
  },
});
