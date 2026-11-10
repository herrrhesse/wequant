# WeQuant frontend

The browser UI for wequant: React, TypeScript (strict) and Vite, with TanStack Query for data and TanStack Table for
tables. It shows what the wequant service layer returns and computes nothing itself.

Node 22 and npm are required.

```bash
npm ci                  # install the locked dependencies
npm run dev             # dev server on http://127.0.0.1:5173
npm run typecheck       # tsc, strict; also checks every fixture against the generated types
npm run build           # typecheck, then a production build in dist/
npm run gen:fixtures    # write local fixtures/ from the real wequant CLI (read-only, about a minute; never committed, D20)
npm run check:fixtures  # typecheck the local fixtures against the generated types
npm run gen:types       # regenerate src/api/types.gen.ts from contract/service-models.schema.json
npm test                # unit tests (node --test with type stripping; no extra dependency)
```

## Data

- `src/api/` is the only code that reads data. `src/api/source.ts` picks the source: the local fixtures when
  `fixtures/index.ts` exists, else the synthetic data in `src/api/synthetic/`. Every page shows a banner while the data
  is synthetic. After S5.1a the same functions call the HTTP API, and no page changes.
- **No real market data in the repository (D20).** `fixtures/` is local only:
  - `scripts/gen-fixtures.sh` runs real `wequant … --json` commands in the repository root with
    `--home /home/sure/Projects/quant/wequant_frontend_dev/wq-home --cache /home/sure/Projects/quant/wequant_frontend_dev/cache` (override with `UV`,
    `WEQUANT_FIXTURE_HOME` and `WEQUANT_FIXTURE_CACHE`). It only reads: no `--commit`, no BaoStock.
  - `scripts/gen-fixture-index.mjs` then writes `fixtures/commands.json` (the command behind each fixture, shown on
    the page) and `fixtures/index.ts` (typed loaders). `npm run check:fixtures` typechecks them.
- **Synthetic data** in `src/api/synthetic/` is made up (codes like 999901.SH, dates in 2031) and small. It covers the
  branches the pages draw. Each file is imported with `satisfies <Model>`, so `npm run typecheck` is the contract test
  of the committed code: a renamed or retyped field fails it.
- `contract/service-models.schema.json` is written by the backend (`wequant schema --json`); the frontend never edits
  it. `npm run gen:types` turns it into `src/api/types.gen.ts`. It also lists which operations are available yet.
- A fresh clone needs no fixtures: `npm ci && npm run typecheck && npm run build` works on the synthetic data.

## Pages

Routes are hashes, so the built files work from any path: `#/data`, `#/data?code=C&date=D` (the issues of one code,
dated), `#/stock/600000.SH` and `#/stock/600000.SH?date=D` (scrolled to that day).

- Data page: store status, versions and data issues.
- Stock page: the daily K-line (klinecharts 10.1.0) with volume in lots, raw or back-adjusted prices, the backend's
  moving averages for each price mode (`expr eval` of `["MA","close",N]` and `["MA","adj_close",N]`), limit-up and
  limit-down marks (`limit_known = 0` shows as unknown), suspended days left blank, ST shading (`st_known = 0` shows
  as unknown) and data-issue marks that open the data page. Ex-rights marks wait for request F-20.

- Strategy page (`#/strategy`): the five steps edit one StrategySpec draft (kept in the browser until
  `strategy save` exists). The builder only writes the user's choices as expression forms (`src/strategy/expr.ts`);
  forms it did not write are edited as JSON. The summary sentence, factor weight shares, content hash and validation
  come from `strategy summary` and `strategy validate`; until the snapshot marks them available the page says
  "待后端提供". Backend errors are located by ErrorDetail.loc on the controls and by line in the code view; a synthetic
  ErrorBody (`src/api/synthetic/strategy-validate-error.json`) demonstrates it. Templates wait for S3.8.
  With local fixtures the backend panel can load two example strategies whose real `strategy validate` /
  `strategy summary --strategy - --json` output `gen-fixtures.sh` captured: a valid one (summary sentence, weight
  shares, hash) and a deliberately wrong one (a real ErrorBody, `complete=false`). The page shows those results only
  while the draft is exactly that example; any edit reads "pending" again. An ErrorBody with `complete=false` says that
  fixing it may reveal more errors (cross-field rules run only after every field passes).
- Picks page (`#/picks[?screen=NAME]`, 选股): one `wequant screen` result: the funnel (chain levels and the off-chain
  items apart; a level's rejects are the backend's reject rows of that step), the scored stocks and the rejects of a
  level in virtualized TanStack tables, warnings and anomalies, CSV export of the backend rows, and a side panel with
  the stock's factors, its anomalies, `explain` (when the fixtures have it) and a mini K-line (when they have the stock).
- Backtest page (`#/backtest`, 回测): `runs list`, and `runs diff` for two ticked runs. Run detail
  (`#/backtest/ID[?day=D]`): `runs show` (information, execution and data options, fees, KPIs by their own unit,
  ECharts curves of the daily columns, the year table and month heatmap of periods), orders (`runs orders`, filterable;
  a null `requested` is a full exit), delisted holdings, anomalies (`runs anomalies`), warnings, one day (`runs day`
  plus `explain --run`), `runs rerun` and the `--report` Markdown shown as text. Until S5.1a the choices are the
  fixtures'.
- Data page, 退市处理 (F-22): every delisted code with its approximate or official date, kind, basis and treatment;
  synthetic example data until `wequant data delistings` exists.

### Screen and runs fixtures

`gen-fixtures.sh` uses a frontend-only home, `wequant_frontend_dev/wq-home/`: symlinks to the Database tables
(adj_factor, bars, calendar, data_issues, index_bars, index_members, securities, manifests, manifest.json, raw) plus its
own `runs/` and `strategies/`, so backtest archives never land in Database. The panel cache is
`wequant_frontend_dev/cache/`. It writes two example strategies (s1: CSI 800, liquidity and low-volatility filters,
momentum and low-volatility factors, top 20 + 5; s2: the same with an "any" group, hold_rank, score weighting and a
5.5% cap) to `fixtures/runs-input/`, then records `screen` (last day for both, a past day for s1), `backtest` (s1 from
2023, s2 from 2021-11-08, s1 again with `--report --no-save`), an invalid backtest's ErrorBody, `runs list`,
`runs show`/`anomalies` of both runs, all `runs orders` pages, the rejected orders, `runs rerun`, three decision days
and a quiet day of `runs day`, `runs diff`, `explain` for a target, a backup and a rejected stock of the s1 screen and
one `explain --run`, and `data delistings`. `fixtures/runs.jsonl` keeps the run log, so
`node scripts/gen-fixture-index.mjs fixtures/runs.jsonl` rewrites the index without the CLI.

### Stock fixtures

Local only (D20). `scripts/gen-fixtures.sh` fetches `data bars` (about 500 trading days from 2024-10-17),
`data issues --codes` and the eight moving averages for each of these stocks; the store's real data never enters the
repository:

| Code | Case |
| --- | --- |
| 600000.SH | ordinary main-board stock, traded every day |
| 600863.SH | a suspension: 8 trading days from 2025-02-11 to 2025-02-20 |
| 000725.SZ | limit up and limit down, one of them locked all day |
| 300418.SZ | ChiNext, 20% limits |
| 000059.SZ | a data issue: adj_blip on 2025-08-26 |
| 600036.SH | long range: about 2,500 trading days from 2016-07-20, for render speed |

`scripts/gen-fixture-index.mjs` then writes `fixtures/index.ts`, whose loaders check each fixture against the
generated types (`src/api/contract.ts` widens string enums for JSON imports and checks their values on load).

Without them the page shows the synthetic stocks 999901.SH (a suspension, limit up, locked limit down, unknown
limits, a null amount, a data issue) and 999902.SZ (20% band, a known ST run).

### Render time

Measured on the production build (`vite preview`), headless Chrome, 1440 × 1300, this machine, 5 runs, for 600036.SH
(2,500 bars): the chart takes 68–81 ms from `init` to its first frame, and the page 200–230 ms from navigation start to
that frame, loading the fixtures included. Target: under 1 s. The page exposes both as `data-render-ms` and
`data-page-ms` on `<main>`.

## Conventions

- Codes carry their exchange suffix (`600000.SH`). Prices and amounts are in yuan, volume in shares; a missing value is
  shown as "—", never as 0.
- Every section shows the data version it reads and the equivalent `wequant` command.
- All user-facing text lives in `src/i18n/zh.ts`.
- Colours are CSS variables in `src/styles/tokens.css`: light, dark or follow the system, and red-up/green-down or the
  reverse. `?theme=light|dark|system` in the URL overrides the saved theme.
