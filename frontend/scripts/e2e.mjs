// `npm run e2e`: build, make a demo home, start `wequant serve` on it, serve dist with `vite preview` (proxying /api),
// run Playwright, then stop everything. Nothing is hard-coded: commands, paths and ports come from the environment
// (or frontend/.env.e2e.local, KEY=VALUE lines), with the defaults below.
//   WQ_UV            uv binary                       (default: /home/sure/micromamba/envs/quant/bin/uv, else `uv`)
//   WQ_DEMO_HOME     demo home directory             (default: ../../wequant_frontend_dev/demo-home, outside git)
//   WQ_DEMO_CMD      command that writes the demo home; {home} is replaced. Unset: use the home as it is.
//   WQ_SERVE_CMD     command that serves the API; {home} and {port} are replaced. Unset: no API (smoke only).
//   WQ_API_PORT      API port                        (default: 8765)
//   WQ_WEB_PORT      vite preview port               (default: 4179)
import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
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
const cfg = {
  uv: env.WQ_UV ?? (existsSync(uvDefault) ? uvDefault : "uv"),
  home: resolve(root, env.WQ_DEMO_HOME ?? "../../wequant_frontend_dev/demo-home"),
  demo: env.WQ_DEMO_CMD ?? "",
  serve: env.WQ_SERVE_CMD ?? "",
  apiPort: env.WQ_API_PORT ?? "8765",
  webPort: env.WQ_WEB_PORT ?? "4179",
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

function start(cmd, cwd, extra = {}) {
  log(`& ${cmd}`);
  const child = spawn(cmd, { cwd, shell: true, stdio: ["ignore", "inherit", "inherit"], env: { ...env, ...extra }, detached: true });
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
  run("npm run build --silent", root);
  if (cfg.demo) run(fill(cfg.demo), repo);
  if (cfg.serve) {
    start(fill(cfg.serve), repo);
    await waitFor(`http://127.0.0.1:${cfg.apiPort}/`);
  }
  start(`npx vite preview --strictPort --port ${cfg.webPort}`, root, cfg.serve ? { WQ_API_PORT: cfg.apiPort } : { WQ_API_PORT: "" });
  const web = `http://127.0.0.1:${cfg.webPort}/`;
  await waitFor(web);
  const args = process.argv.slice(2).join(" ");
  const r = spawnSync(`npx playwright test ${args}`, {
    cwd: root,
    shell: true,
    stdio: "inherit",
    env: { ...env, WQ_WEB_URL: web, WQ_API_PORT: cfg.apiPort, WQ_API: cfg.serve ? "1" : "", WQ_DEMO_HOME: cfg.home, WQ_SERVE_CMD: fill(cfg.serve) },
  });
  code = r.status ?? 1;
} catch (e) {
  console.error(String(e));
} finally {
  stopAll();
  log(`done, exit ${code}`);
  process.exit(code);
}
