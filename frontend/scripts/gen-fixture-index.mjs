// Write fixtures/commands.json and fixtures/index.ts from the runs gen-fixtures.sh recorded. Both stay local (D20).
// Usage: node scripts/gen-fixture-index.mjs RUNS (one JSON object {name, args} per line).
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const runs = readFileSync(process.argv[2], "utf8").trim().split("\n").map((line) => JSON.parse(line));

/** An argument as a POSIX shell would need it: bare when safe, else single-quoted. */
const quote = (arg) => (/^[\w@%+=:,./-]+$/.test(arg) ? arg : `'${arg.replaceAll("'", `'\\''`)}'`);
const commands = Object.fromEntries(runs.map(({ name, args }) => [name, `wequant ${[...args, "--json"].map(quote).join(" ")}`]));
writeFileSync(`${root}/fixtures/commands.json`, `${JSON.stringify(commands, null, 2)}\n`);

/** The value after a flag, or null. */
const flag = (args, name) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : null;
};
/** A Named loader of fixtures/<name>.json; check wraps the imported value (m.default) in its contract check. */
const named = (name, check) =>
  `{ name: ${JSON.stringify(name)}, load: () => import(${JSON.stringify(`./${name}.json`)}).then((m) => ${check(`m.default`, name)}) }`;
const asIs = (type) => (v) => `${v} satisfies ${type}`;
const checked = (fn, type) => (v, name) => `${fn}(${v} satisfies Fixture<${type}>, ${JSON.stringify(name)})`;

const stocks = new Map();
for (const { name } of runs) {
  const m = name.match(/^stock\/([^/]+)\/(.+)$/);
  if (!m) continue;
  if (!stocks.has(m[1])) stocks.set(m[1], []);
  stocks.get(m[1]).push(m[2]);
}
const WINDOWS = [5, 10, 20, 60];
const stockLines = [...stocks.keys()].map((code) => {
  const p = (part) => `stock/${code}/${part}`;
  const ma = (mode) => WINDOWS.map((n) => named(p(`ma-${mode}-${n}`), checked("evalResult", "EvalResult"))).join(", ");
  return [
    `    ${JSON.stringify(code)}: {`,
    `      bars: ${named(p("bars"), checked("bars", "Bars"))},`,
    `      issues: ${named(p("issues"), asIs("Issues"))},`,
    `      ma: { raw: [${ma("raw")}], adj: [${ma("adj")}] },`,
    "    },",
  ].join("\n");
});

// Strategy examples: strategy/<example>/{strategy, validate, summary}; validate is a StrategyCheck, or the ErrorBody
// an invalid strategy gets (it has "exit").
const examples = [...new Set(runs.map((r) => r.name.match(/^strategy\/([^/]+)\//)?.[1]).filter(Boolean))];
const exampleLines = examples.map((ex) => {
  const p = (part) => `strategy/${ex}/${part}`;
  const has = (part) => runs.some((r) => r.name === p(part));
  const validate = has("validate") ? JSON.parse(readFileSync(`${root}/fixtures/${p("validate")}.json`, "utf8")) : null;
  const parts = [
    `    {`,
    `      name: ${JSON.stringify(ex)},`,
    `      strategy: { name: ${JSON.stringify(p("strategy"))}, load: () => import(${JSON.stringify(`./${p("strategy")}.json`)}).then((m) => m.default satisfies Fixture<StrategySpec> as StrategySpec) },`,
  ];
  if (validate && "exit" in validate) parts.push(`      error: ${named(p("validate"), checked("errorBody", "ErrorBody"))},`);
  else if (validate) parts.push(`      validate: ${named(p("validate"), (v) => `${v} satisfies Fixture<StrategyCheck> as StrategyCheck`)},`);
  if (has("summary")) parts.push(`      summary: ${named(p("summary"), (v) => `${v} satisfies Fixture<StrategySummary> as StrategySummary`)},`);
  parts.push("    },");
  return parts.join("\n");
});

// Screens: screen/<name> with explain/<name>/<code>; <name> starts with the strategy's short name (s1-last).
// satisfies checks the shape against the widened type; the cast restores the enums (too deep for a plain `as`).
const typed = (type) => (v) => `${v} satisfies Fixture<${type}> as unknown as ${type}`;
const json = (name) => JSON.parse(readFileSync(`${root}/fixtures/${name}.json`, "utf8"));
const has = (name) => runs.some((r) => r.name === name);
const under = (prefix) => runs.map((r) => r.name).filter((n) => n.startsWith(prefix));
const screenLines = under("screen/").map((name) => {
  const id = name.slice("screen/".length);
  const explains = under(`explain/${id}/`).map((n) => `${JSON.stringify(n.split("/").pop())}: ${named(n, typed("Explain"))}`);
  return `    { name: ${JSON.stringify(id)}, strategy: ${JSON.stringify(id.split("-")[0])}, result: ${named(name, typed("ScreenResult"))}, explains: { ${explains.join(", ")} } },`;
});

// Runs: runs/<id>/{show, anomalies, orders/<offset>, orders-rejected, rerun, day/<date>}, runs/diff/<a>-<b>,
// explain/run/<id>/<date>/<code>, and the backtest/<name> outputs, matched to their run by run_id.
const runIds = [...new Set(under("runs/").map((n) => n.split("/")[1]).filter((id) => id !== "list" && id !== "diff"))];
const backtests = under("backtest/").filter((n) => n !== "backtest/error");
const runLines = runIds.map((id) => {
  const p = (part) => `runs/${id}/${part}`;
  const opt = (key, part, type) => (has(p(part)) ? `      ${key}: ${named(p(part), typed(type))},` : null);
  const orders = under(p("orders/")).sort((a, b) => Number(a.split("/").pop()) - Number(b.split("/").pop()));
  const days = under(p("day/")).map((n) => `${JSON.stringify(n.split("/").pop())}: ${named(n, typed("RunDay"))}`);
  const explains = under(`explain/run/${id}/`).map((n) => `${JSON.stringify(n.split("/").slice(-2).join("/"))}: ${named(n, typed("Explain"))}`);
  const mine = backtests.filter((n) => json(n).run_id === id);
  const plain = mine.find((n) => json(n).report === null);
  const report = mine.find((n) => json(n).report !== null);
  return [
    `    ${JSON.stringify(id)}: {`,
    `      show: ${named(p("show"), typed("RunDetail"))},`,
    opt("anomalies", "anomalies", "AnomaliesPage"),
    opt("rejected", "orders-rejected", "OrdersPage"),
    opt("rerun", "rerun", "RunComparison"),
    `      orders: [${orders.map((n) => named(n, typed("OrdersPage"))).join(", ")}],`,
    `      days: { ${days.join(", ")} },`,
    `      explains: { ${explains.join(", ")} },`,
    plain ? `      backtest: ${named(plain, typed("BacktestResult"))},` : null,
    report ? `      report: ${named(report, typed("BacktestResult"))},` : null,
    "    },",
  ]
    .filter(Boolean)
    .join("\n");
});
const diffLines = under("runs/diff/").map((n) => {
  const [a, b] = n.split("/").pop().split("-");
  return `    { ...${named(n, typed("RunDiff"))}, a: ${JSON.stringify(a)}, b: ${JSON.stringify(b)} },`;
});

const lines = [
  "/* Generated by scripts/gen-fixture-index.mjs from the runs of gen-fixtures.sh. Local only (D20); do not edit. */",
  'import { bars, delistings, errorBody, evalResult, type Fixture } from "../src/api/contract";',
  'import type { Source } from "../src/api/source";',
  'import type { AnomaliesPage, BacktestResult, Bars, DataStatus, Delistings, ErrorBody, EvalResult, Explain, Issues, OrdersPage, RunComparison, RunDay, RunDetail, RunDiff, RunsPage, ScreenResult, StrategyCheck, StrategySpec, StrategySummary } from "../src/api/types.gen";',
  'import commands from "./commands.json";',
  "",
  "export const source: Source = {",
  '  kind: "real",',
  "  commands,",
  "  statuses: [",
  ...runs.filter((r) => r.name.startsWith("data-status")).map((r) => `    ${named(r.name, asIs("DataStatus"))},`),
  "  ],",
  "  issues: [",
  ...runs
    .filter((r) => r.name.startsWith("data-issues"))
    .map((r) => {
      const n = named(r.name, asIs("Issues"));
      return `    { ...${n}, table: ${JSON.stringify(flag(r.args, "--table"))}, kind: ${JSON.stringify(flag(r.args, "--kind"))} },`;
    }),
  "  ],",
  "  stocks: {",
  ...stockLines,
  "  },",
  "  strategyExamples: [",
  ...exampleLines,
  "  ],",
  ...(has("data-delistings") ? [`  delistings: ${named("data-delistings", checked("delistings", "Delistings"))},`] : []),
  "  screens: [",
  ...screenLines,
  "  ],",
  ...(has("runs/list")
    ? [
        "  runs: {",
        `    list: ${named("runs/list", typed("RunsPage"))},`,
        ...(has("backtest/error") ? [`    error: ${named("backtest/error", checked("errorBody", "ErrorBody"))},`] : []),
        "    diffs: [",
        ...diffLines.map((l) => `  ${l}`),
        "    ],",
        "    runs: {",
        ...runLines.map((l) => l.replace(/^/gm, "  ")),
        "    },",
        "  },",
      ]
    : []),
  "};",
  "",
];
writeFileSync(`${root}/fixtures/index.ts`, lines.join("\n"));
console.log(
  `fixtures/commands.json: ${runs.length} commands; fixtures/index.ts: ${stocks.size} stocks, ${examples.length} strategy examples, ${screenLines.length} screens, ${runIds.length} runs`,
);
