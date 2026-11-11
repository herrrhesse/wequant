# WeQuant frontend

The browser UI for wequant: React, TypeScript (strict) and Vite, with TanStack Query for requests and TanStack Table
for tables. Every number on a page comes from the local backend, `wequant serve`; the UI converts units and lays out,
nothing more.

## 从零跑起来

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)，以及 Node 22 和 npm。在仓库根目录执行：

```bash
uv sync --extra server                       # 1. 后端依赖（含 HTTP 服务）
(cd frontend && npm ci && npm run build)     # 2. 前端依赖和构建，产物在 frontend/dist
uv run python -m tools.demo_home --out ~/wequant-demo   # 3.（可选）约 2 秒生成一个合成的演示数据目录
uv run wequant serve --home ~/wequant-demo   # 4. 启动服务；用自己的数据目录时换成它
```

然后在浏览器打开 <http://127.0.0.1:8731>。

- 服务只绑定本机 127.0.0.1，并托管 `frontend/dist`。前端没有构建时，打开的页面会说明怎样构建；接口照常可用。
- 服务没有启动时，页面整页提示"后端未启动"，并给出要运行的命令。页面不会显示样例数据。

## Development

```bash
npm run dev             # Vite on http://127.0.0.1:5173; /api goes to WQ_API_PORT (e.g. WQ_API_PORT=8731 npm run dev)
npm run typecheck       # tsc, strict
npm run build           # typecheck, then a production build in dist/
npm run gen:types       # src/api/types.gen.ts and operations.gen.ts from contract/service-models.schema.json
npm test                # unit tests (node --test with type stripping)
npm run e2e             # end-to-end tests on a fresh demo home (below)
```

- Put `WQ_API_PORT=8731` in `frontend/.env.local` to keep the dev proxy pointed at a running `wequant serve`.
- `contract/service-models.schema.json` is written by the backend (`wequant schema --json`); the frontend never edits
  it. Each operation has a request model, a result model, a method and a path (`POST /api/<operation>`).
  `npm run gen:types` turns them into `types.gen.ts` (models) and `operations.gen.ts` (routes and the request and
  result type of each operation).

## Data

- `src/api/http.ts` is the only code that talks to the backend: `call(operation, request)` sends the request to the
  operation's route. It returns the result, throws `ApiError` with the backend's ErrorBody (422 usage, 404, 409 locked,
  503, 500), or throws `Unreachable` when nothing that speaks the API answered. `Unreachable` turns the whole page into
  the "后端未启动" notice.
- `src/api/client.ts` wraps it for TanStack Query. A query's key is `[operation, request]`, so a request is fetched once,
  and a stale request is cancelled through its `AbortSignal` when the key changes. Mutations (save, archive, backtest,
  rerun) invalidate the operations they change.
- Request defaults, such as the backtest's cash, execution and data options, are read from the snapshot
  (`modelDefaults`). The UI never writes them in.
- Each result shows its data version and the equivalent request as a copyable `curl` command.
- No real market data in the repository (D20). Tests use made-up values; the end-to-end tests use the synthetic demo
  home.

## Pages

Routes are hashes that hold each page's state, so back and forward restore it:

| Route | Page |
| --- | --- |
| `#/data[?code&date]` | Data: `data status`, `data issues` (server-side filters; 200 more rows each time), delistings with the backend's counts, store versions. |
| `#/stock/CODE[?date&run&from]` | Stock: `data bars` for two years up to the focus day, four `expr eval` moving averages per price mode, `data issues`, and with `run` that run's orders of the code (`runs orders --code`) marked 买 and 卖. Code search uses `data securities`. |
| `#/strategy[?id&n]` | Strategy: five steps over one StrategySpec draft. `strategy validate` and `strategy summary` run 300 ms after each edit; a result is shown only for the draft it answered. Errors are located by `loc` on the controls and the code view. Save, versions, restore and archive follow D16: content that is unchanged makes no new version. "有未保存修改" compares content hashes (`saved_as`). |
| `#/picks[?id&n&draft&date]` | Picks (选股): a saved strategy and version, or the draft, on a trading day from `data calendar`. The screen runs on "选股". The funnel opens each level's rejects (the backend's rows of that step). Scored stocks and rejects are in virtualized tables, with CSV export of the backend rows. The side panel shows the row's `explain` and a mini K-line. |
| `#/backtest[?cmp=A,B]` | Backtest (回测): the settings form, with defaults from the snapshot; a run is synchronous and shows elapsed time. Also `runs list`, and `runs diff` of two ticked runs. |
| `#/backtest/ID[?day]` | Run: `runs show` (information, execution, fees, KPIs by their unit, ECharts curves of the daily columns, year table, month heatmap), paged `runs orders`, delisted holdings, `runs anomalies`, warnings, `runs day` for a day picked on a curve (with `explain --run` per decision), `runs rerun` and `runs report`. |

Pages load lazily: klinecharts loads with the stock page or the picks side panel, echarts with a run's page.

## End-to-end tests

`npm run e2e` (`scripts/e2e.mjs`) runs these steps, then stops every process:
1. Builds dist.
2. Writes a fresh demo home (`tools.demo_home`, fixed seed) into `../../wequant_frontend_dev/demo-home`.
3. Starts `wequant serve` on it, on a free port; serve hosts dist.
4. Runs Playwright on the machine's Chrome (`channel: "chrome"`; nothing is downloaded) against the serve address.

`WQ_E2E_MODE=dev` puts `vite preview` with the `/api` proxy in front instead. The tests assert rules, never the
synthetic numbers:
- strategy: a wrong field is located; the fix brings the summary; saving twice keeps one version;
- screen: each level's reject list is that level's rejects; the explain role matches the table;
- backtest: KPIs show a value or a reason; a picked day opens; rerun is byte-identical;
- data: the delisting counts are the backend's;
- backend down: the notice is shown.

Every test also fails on a browser console error. The one exception is the 422 that an invalid strategy gets by design.
Commands, paths and ports can be overridden: `WQ_UV`, `WQ_DEMO_HOME`, `WQ_DEMO_CMD`, `WQ_SERVE_CMD`, `WQ_API_PORT`,
`WQ_WEB_PORT`, or `frontend/.env.e2e.local`. Process logs go to `test-results/*.log`.

## Conventions

- Codes carry their exchange suffix (`600000.SH`). Prices and amounts are in yuan, volume in shares. A missing value is
  shown as "—", never as 0.
- All user-facing text lives in `src/i18n/` (Chinese).
- Colours are CSS variables in `src/styles/tokens.css`. Themes are light, dark or follow the system, and colours are
  red-up/green-down or the reverse. `?theme=light|dark|system` in the URL overrides the saved theme.
