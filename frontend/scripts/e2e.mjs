// `npm run e2e`: build, make a demo home, start `wequant serve` on it, run Playwright, then stop everything.
// Two shapes (WQ_E2E_MODE):
//   release (default with an API): `wequant serve` hosts the built dist itself; the browser talks to serve only.
//   dev: `vite preview` serves dist and proxies /api to serve, as during development.
// Without WQ_SERVE_CMD only the smoke test runs, on vite preview. Nothing is hard-coded: commands, paths and ports come from the environment
// (or frontend/.env.e2e.local, KEY=VALUE lines), with the defaults below.
//   WQ_UV            uv binary                       (default: /home/sure/micromamba/envs/quant/bin/uv, else `uv`)
//   WQ_DEMO_HOME     demo home directory             (default: ../../wequant_frontend_dev/demo-home, outside git)
//   WQ_DEMO_CMD      command that writes the demo home into a new directory; {home} and {uv} are replaced
//                    (default: {uv} run --frozen python -m tools.demo_home --out {home}; "none" keeps the home as is)
//   WQ_SERVE_CMD     command that serves the API; {home}, {port} and {uv} are replaced
//                    (default: {uv} run --frozen wequant serve --home {home} --port {port}; "none": smoke only)
//   WQ_API_PORT      API port                        (default: a free port, apart from a dev serve on 8731)
//   WQ_WEB_PORT      vite preview port, dev shape    (default: a free port)
//   WQ_E2E_MODE      release | dev                   (default: release)
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, openSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repo = resolve(root, "..");
const local = resolve(root, ".env.e2e.local");
const fileEnv = existsSync(local)
  ? Object.fromEntries(
      readFileSync(local, "utf8")
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l && !l.startsWith("#") && l.includes("="))
        .map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]),
    )
  : {};
const env = { ...fileEnv, ...process.env };
const uvDefault = "/home/sure/micromamba/envs/quant/bin/uv";

/** A port nothing listens on now. */
function freePort() {
  return new Promise((ok, fail) => {
    const srv = createServer();
    srv.once("error", fail);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => ok(String(port)));
    });
  });
}
const cfg = {
  uv: env.WQ_UV ?? (existsSync(uvDefault) ? uvDefault : "uv"),
  home: resolve(root, env.WQ_DEMO_HOME ?? "../../wequant_frontend_dev/demo-home"),
  demo: env.WQ_DEMO_CMD === "none" ? "" : (env.WQ_DEMO_CMD ?? "{uv} run --frozen python -m tools.demo_home --out {home}"),
  serve: env.WQ_SERVE_CMD === "none" ? "" : (env.WQ_SERVE_CMD ?? "{uv} run --frozen wequant serve --home {home} --port {port}"),
  apiPort: env.WQ_API_PORT ?? (await freePort()),
  webPort: env.WQ_WEB_PORT ?? (await freePort()),
  mode: env.WQ_E2E_MODE ?? "release",
};
const fill = (cmd) => cmd.replaceAll("{home}", cfg.home).replaceAll("{port}", cfg.apiPort).replaceAll("{uv}", cfg.uv);
const children = [];
const started = Date.now();
const log = (m) => console.log(`[e2e ${((Date.now() - started) / 1000).toFixed(1)}s] ${m}`);

function run(cmd, cwd) {
  log(`$ ${cmd}`);
  const r = spawnSync(cmd, { cwd, shell: true, stdio: "inherit", env });
  if (r.status !== 0) throw new Error(`failed (${r.status}): ${cmd}`);
}

/** Background process; its output goes to test-results/<name>.log, not the test report. */
function start(name, cmd, cwd, extra = {}) {
  log(`& ${cmd}  (log: test-results/${name}.log)`);
  mkdirSync(resolve(root, "test-results"), { recursive: true });
  const out = openSync(resolve(root, `test-results/${name}.log`), "w");
  const child = spawn(cmd, { cwd, shell: true, stdio: ["ignore", out, out], env: { ...env, ...extra }, detached: true });
  children.push(child);
  return child;
}

async function waitFor(url, ms = 60_000) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    try {
      const r = await fetch(url);
      if (r.status < 500) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`timed out waiting for ${url}`);
}

function stopAll() {
  for (const c of children) {
    try {
      process.kill(-c.pid, "SIGTERM");
    } catch {
      // already gone
    }
  }
}
process.on("SIGINT", () => {
  stopAll();
  process.exit(130);
});

let code = 1;
try {
  run("npm run build --silent -- --logLevel warn", root);
  if (cfg.demo) {
    // The generator wants a new directory; the demo home is disposable test data outside git.
    rmSync(cfg.home, { recursive: true, force: true });
    run(fill(cfg.demo), repo);
  }
  if (cfg.serve) {
    start("serve", fill(cfg.serve), repo);
    await waitFor(`http://127.0.0.1:${cfg.apiPort}/`);
  }
  let web = `http://127.0.0.1:${cfg.apiPort}/`;
  if (!cfg.serve || cfg.mode === "dev") {
    start("preview", `npx vite preview --strictPort --port ${cfg.webPort}`, root, cfg.serve ? { WQ_API_PORT: cfg.apiPort } : { WQ_API_PORT: "" });
    web = `http://127.0.0.1:${cfg.webPort}/`;
  }
  await waitFor(web);
  // The same build with no backend behind it, for the "backend not running" test.
  const noApiPort = await freePort();
  start("preview-noapi", `npx vite preview --strictPort --port ${noApiPort}`, root, { WQ_API_PORT: "" });
  const noApi = `http://127.0.0.1:${noApiPort}/`;
  await waitFor(noApi);
  log(`testing ${web} (${cfg.serve ? cfg.mode : "smoke"})`);
  const args = process.argv.slice(2).join(" ");
  const r = spawnSync(`npx playwright test ${args}`, {
    cwd: root,
    shell: true,
    stdio: "inherit",
    env: { ...env, WQ_WEB_URL: web, WQ_API_PORT: cfg.apiPort, WQ_API: cfg.serve ? "1" : "", WQ_NOAPI_URL: noApi, WQ_E2E_MODE: cfg.serve ? cfg.mode : "smoke", WQ_DEMO_HOME: cfg.home, WQ_SERVE_CMD: fill(cfg.serve) },
  });
  code = r.status ?? 1;
} catch (e) {
  console.error(String(e));
} finally {
  stopAll();
  log(`done, exit ${code}`);
  process.exit(code);
}
