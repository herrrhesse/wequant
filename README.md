# WeQuant 微框

A local A-share stock selection and backtesting tool. The core is Python on numpy and polars; data lives in a
versioned Parquet store under `$WEQUANT_HOME` (default `~/.wequant`).

```bash
uv sync                      # core and tests
uv run pytest                # offline tests; network tests are skipped
uv sync --extra baostock     # BaoStock data source
```

## Quick start (快速开始)

Use an existing data root, or make the synthetic demo home (60 made-up stocks over 2023–2025, about two seconds; first
run setup on real data comes later, S2.9):

```bash
uv sync --extra server                                   # Python dependencies, the web server included
uv run python -m tools.demo_home --out ~/wequant-demo    # or skip this and use your own data root below
(cd frontend && npm ci && npm run build)                 # the browser app, into frontend/dist
uv run wequant serve --home ~/wequant-demo               # or --home /path/to/your/Database
```

Then open http://127.0.0.1:8731 . The demo home holds two saved strategies (`demo-momentum`, `demo-csi300`); the same
seed always writes the same bytes, so end-to-end tests run on it.

## Layout

- `wequant/data/`: table definitions (`schema.py`), the versioned store, data sources, the raw layer and the merge of
  raw BaoStock data into the store.
- `wequant/rules/`: dated A-share trading rules: price limits, lots, ticks, stamp tax and transfer fees.
- `wequant/panel/`: date × stock matrices of one store version, with day status and limit masks.
- `wequant/ops/`: TDX-style operators on those matrices.
- `wequant/expr/`: signal expressions: checked trees of panel fields and operators with one canonical JSON form,
  evaluated once per distinct subexpression and cached.
- `wequant/account/`: cash, positions and order execution on raw prices: lots, T+1, fees, corporate actions and one order
  validation, plus the batch protocol every engine trades by.
- `wequant/analytics/`: the metrics of a run, and its daily and period views from the same definitions.
- `wequant/strategy/`: strategy descriptions (universe, pool items, filters, ranked factors, top N, hold rank, schedule
  and weights) with one canonical form, the screen (a strategy's selection at one day's close), the saved-strategy
  library under `$WEQUANT_HOME/strategies/` and the Chinese summary sentence.
- `wequant/backtest/`: the rebalance engine: decides at each decision day's close, trades the next day through one
  account and returns the run with its metrics and warnings; it reports progress and can be cancelled.
- `wequant/runs/`: runs and the archive under `$WEQUANT_HOME/runs/`: each run with each decision day's selection, kept
  write-once and re-run byte for byte.
- `wequant/service/`: the operations: one function each, pydantic models in and out; `queue.py` and `worker.py` run
  long ones as background jobs under `wequant serve`.
- `wequant/cli/`: the `wequant` command; it only calls the service.
- `wequant/api/`: `wequant serve`, the operations over local HTTP; every route is built from the operation list and,
  like the command, only calls the service.
- `tools/`: command-line jobs: fetch, merge, data checks and reports. `tools/investment_data/` is a temporary seed
  import and not part of the package.

## The wequant command

```bash
uv sync                      # the dev group includes the cli extra
uv sync --no-dev --extra cli # the command without the test tools
uv run wequant --help
```

```text
wequant data status [--data-version N]
wequant data calendar [--start D] [--end D] [--data-version N]
wequant data securities [--query Q] [--limit N] [--data-version N]
wequant data bars --codes C... [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--fields F...] [--data-version N]
wequant data issues [--table T] [--kind K] [--codes C...] [--start D] [--end D] [--limit N] [--data-version N]
wequant data check [--commit]
wequant data delistings [--delisting RULE] [--failure-recovery X] [--data-version N]
wequant data index-check [--data-version N]
wequant data verify submit FILE|-
wequant data verify list [--id V] [--table T] [--code C] [--start D] [--end D] [--source-type T] [--source S]
                         [--verifier NAME] [--limit N] [--offset N]
wequant data resolve list [--code C] [--date D] [--kind K] [--category G] [--start D] [--end D] [--status S]
                          [--limit N] [--offset N]
wequant data resolve approve|reject --note NOTE [--code C] [--date D] [--kind K] [--category G] [--start D] [--end D]
wequant expr check --form JSON
wequant expr eval --form JSON --codes C... [--start D] [--end D] [--data-version N]
wequant strategy validate --strategy S
wequant strategy summary --strategy S
wequant strategy schema
wequant strategy save FILE [--id ID] [--name NAME] [--note NOTE]
wequant strategy list [--archived]
wequant strategy versions ID
wequant strategy show ID[@N]
wequant strategy archive ID [--restore]
wequant screen --strategy S [--date D] [--data-version N] [--anomalies MODE] [--exclude-suspicious]
wequant backtest --strategy S [--start D] [--end D] [--cash X] [--benchmark CODE | --no-benchmark]
                 [--price open|close] [--commission-rate X] [--min-commission X] [--slippage-bps X]
                 [--delisting RULE] [--failure-recovery X] [--anomalies MODE] [--exclude-suspicious]
                 [--data-version N] [--no-save] [--report]
wequant explain (--strategy S | --run ID) --code C --date D [--data-version N] [--anomalies MODE]
wequant runs list [--strategy-hash H] [--limit N] [--offset N]
wequant runs show ID | report ID | rerun ID | diff A B | day ID DATE
wequant runs orders ID [--code C] [--status S] [--side S] [--start D] [--end D] [--limit N] [--offset N]
wequant runs anomalies ID [--channel C] [--code C] [--limit N] [--offset N]
wequant schema
wequant labels
```

```bash
uv run wequant data bars --codes 600000.SH 000001.SZ --start 2024-01-02 --fields close adj_close
uv run wequant expr eval --form '["MA","adj_close",20]' --codes 600000.SH --start 2024-01-02 --json
uv run wequant strategy save my.json --id momentum --note "first version"
uv run wequant screen --strategy momentum --date 2024-06-28
echo '{"factors":[{"name":"mom","expr":["DIV","adj_close",["REF","adj_close",20]]}],"top_n":20}' \
  | uv run wequant backtest --strategy - --start 2016-11-07 --report
uv run wequant explain --run 0123456789abcdef --code 600000.SH --date 2024-06-28
```

Every command also takes `--json`, `-v`/`--verbose` (`-vv` for debug), `--profile`, `--home PATH` (the data root,
default `$WEQUANT_HOME`) and `--cache PATH` (panels go under `PATH/panel/`, default `HOME/cache`), after the command
name.

- **Output:** stdout carries the result. `--json` prints the result model exactly as `model_dump_json()` gives it, the
  same JSON the HTTP API returns; NaN and infinity are `null`. Logs and errors go to stderr. When a command fails,
  stdout is empty, or with `--json` holds its `ErrorBody` (below); Ctrl-C included (`INTERRUPTED`, exit 130).
- **`--profile`:** times each stage (opening the store and the panel, each expression, the cross-section, the day
  loop, metrics, frames, archiving, rendering) and writes the table to stderr, or one `ProfileReport` JSON line with
  `--json`; the result on stdout is unchanged.
- **`--strategy S`:** a JSON file (a path with `/` or `\`, or ending in `.json`), `-` for JSON on stdin, or a saved
  version `ID` (the current one) or `ID@N`. `wequant strategy schema` gives the JSON Schema of a strategy and of the
  execution block.
- **Strategy files:** `strategy save` writes `$WEQUANT_HOME/strategies/<id>/<n>.json` once and never changes it;
  `meta.json` beside it indexes the versions (hash, time, note). Saving unchanged content adds no version; restoring old
  content adds a new version with the old hash; `archive` only hides a strategy from the list. A version file changed
  by hand is refused (`hash_mismatch`). The data root is not a git repository: back up `strategies/` yourself.
- **`screen`** is one decision day of the backtest's own path without holdings: the funnel (how many pass each level,
  alone and along the chain), every scored stock by rank with its factor percentiles, contributions and comparison
  values, and every other listed stock as a reject at the first level it fails. `explain` reads one stock from the
  same cross-section; with `--run` it adds the run's role, holding, action and next-day orders, and the date may be
  the decision day or its fill day.
- **`backtest`** archives the run under `$WEQUANT_HOME/runs/<id>/` unless `--no-save`; `--report` adds a Markdown
  report. The execution options are flat (`--price`, `--slippage-bps`, ...); only those given override the defaults.
  `runs` reads the archive: `show` adds daily rows (nav, benchmark, excess, drawdown, exposure) and returns by year and
  month, `report` gives the Markdown report `--report` would have attached, `rerun` repeats a run on its store version
  and compares the bytes, `day` shows one day's holdings, decisions and fills. A full exit's `requested` is null (the
  whole holding).
- **Version names:** each store version is named `YYYYMMDD.N` when it is made: its last trade date, then which version
  of that day (`20261111.2` is the second version whose data end on 2026-11-11), with a reason (a code with params, and
  its Chinese sentence: `每日更新`, `合并 BaoStock …`, `提交涨跌停检查`). The name never changes, whatever cleanup
  removes; a version written before names (store v1 to v3) reads as its day and its own number with the reason `早期版本`.
  `data status` keeps `versions` (the ids, oldest first) and lists them in `version_list`, newest first, as {id, name,
  reason, last trade date, created at, pinned};
  every result that reports a `data_version` also gives `data_version_name` (runs: null once their version is gone).
  Requests still take the integer `data_version`.
- **`labels`** gives the Chinese name and one sentence of every code the operations return: warnings (a template whose
  `{param}`, `{param:.1%}` or `{param:,.2f}` placeholders are the warning's params, with optional clauses), data_issues
  kinds and actions, metrics, metric reasons and units, funnel kinds, order statuses and reasons, sell reasons,
  delisting treatments and kinds, anomaly classes and levels, error codes, exit codes and version reasons. English
  stays in the `text` fields for logs; a test keeps the tables and the code base's codes one to one.
- **`data calendar`** lists a store version's trading days in a range, with its first and last; **`data securities`**
  finds codes by a code prefix in any case (`600`, `600000.sh`) or part of a name, in code order, `--limit` of them
  (20 by default, at most 100) with the total.
- **Anomalous data:** `--anomalies flag` (the default) uses every value and reports what the result read
  (`data_anomaly`, `runs anomalies`). `exclude_day` reads error-level anomalous days as missing, and suspicious ones too
  with `--exclude-suspicious`. Excluding is a robustness check of whether a result rests on disputed data, not a
  point-in-time rule: some anomaly labels rest on the next day's exchange data.
- **`data verify`** keeps verification records under `$WEQUANT_HOME/verifications/`: what each source (BaoStock, the
  seed, an exchange, a data vendor, an announcement, a rule) says about a cell, who checked it (a person, or an agent
  and its model), how, the conclusion, the verified values and the level. `submit` takes a JSON list or JSON Lines
  (values in yuan, shares and percent, as the exchanges publish them); each record's id is the digest of its content,
  so a resubmitted record is not added again, and records are never changed or removed. Resolutions and `data_issues`
  cite them by id. This is the entry point for an agent that checks data; the checking itself (fetching from outside
  sources) stays out of the repository.
- **`data resolve`** shows and records the user's decisions in `$WEQUANT_HOME/resolutions/baostock.toml`, the merge's
  resolutions file: `list` filters entries and counts them by category and status; `approve` and `reject` set the status
  of every entry all the given selectors match (at least one), with a note on who decided and on what basis. They change
  only that file, rewriting just the chosen entries, never the store; the next merge applies the approved entries. An
  entry that takes `undetermined` needs evidence first: approving a selection with one refuses the whole decision.
- **`data delistings`** lists every delisted code with its kind, what classified it (the rule, or an entry of
  `$WEQUANT_HOME/overrides/delisting.toml` with its evidence and source) and how a holding is treated, with counts.
  **`data index-check`** compares each index's daily member count, as `member@` reads it, with its nominal size and
  exits 1 when any run of days is off.
- **Python:** the commands are thin wrappers over `wequant.service`, e.g.
  `data_bars(BarsRequest(codes=["600000.SH"], start="2024-01-02")).rows`.
- **`data bars`** gives the panel's values as an expression reads them: prices and amount in yuan, volume in shares,
  `adj_*` back-adjusted, masks as 1.0/0.0. A limit price is null both where the bar has no limit and where it is
  unknown; `limit_known` tells them apart (1.0 and 0.0).
- **`expr eval`** always computes from the store's first trading day, so the value on a day never depends on `--start`.
  `CS_*` operators rank among the given codes only.
- **`data check`** is read-only. `--commit` records the breaches and the check in a new store version under the writer
  lock and writes its report to `reports/check_limits_<last trade date>_v<version>.md`.
- **`schema`** gives one JSON Schema (2020-12) document of every operation's request and result model, of `ErrorBody`
  (the error body of the HTTP API) and of `ProfileReport`. `$defs` holds each model by name in alphabetical order and
  `operations` names each operation's request and result; requests are described as the operations accept them,
  results as they print them. The text form lists the operations, then each model with its description's first line.
- **Contract snapshot:** `frontend/contract/service-models.schema.json` is the output of `wequant schema --json` and the
  only file the backend writes under `frontend/`; the frontend generates its TypeScript types from it. Whenever a
  service model changes, regenerate it in the same commit:
  `uv run wequant schema --json > frontend/contract/service-models.schema.json`. `tests/service/test_contract.py` fails
  until it matches.

Exit codes (`wequant.service.Exit`):

| Code | Name | Meaning |
| --- | --- | --- |
| 0 | `OK` | success |
| 1 | `PROBLEMS` | a check found something to act on: `data check` found breaches the store does not record, recorded breaches that are gone, or no current limit check; `data index-check` found days off an index's nominal size |
| 2 | `USAGE` | bad arguments or input: a refused expression or strategy, a malformed `ID@N`, a changed version file, a date that is not a run's decision or fill day, an invalid verification record (none of the submission is added), a malformed resolutions file, a decision without a selector or one that would approve an `undetermined` entry (nothing is changed) |
| 3 | `NOT_FOUND` | no store, store version, code, trading day, saved strategy or version, run, or resolutions file to decide in |
| 4 | `UNREACHABLE` | a data source cannot be reached |
| 5 | `LOCKED` | another writer holds the store lock, a strategy's lock, the verification records' lock or the resolutions file's lock; try again later |
| 70 | `INTERNAL` | a bug (the traceback is on stderr), or the cli extra is not installed |
| 130 | `INTERRUPTED` | Ctrl-C, or a cancelled run |

A failure's `ErrorBody` holds `exit`, `name`, `message`, `detail` and `complete`. Each detail has a stable `code`
(`unknown_key`, `missing_key`, `wrong_type`, `not_a_choice`, `out_of_range`, `requires`, `duplicate_name`,
`reserved_name`, `bad_ref`, `hash_mismatch`, `not_a_decision_day`, the expression codes such as `unknown_field` or
`future_function`, and the NOT_FOUND codes `no_store`, `version_not_kept`, `unknown_code`, `not_a_trading_day`,
`unknown_strategy`, `unknown_version`, `unknown_run`), its `loc` in the request (e.g. `["strategy", "filters", 2,
"expr"]`) and, inside an expression, its `path`. One failure reports every field error at once; `complete` is false
when rules that relate fields to each other could not run yet because a field failed.

## The HTTP API (`wequant serve`)

```bash
uv sync --extra server                       # fastapi and uvicorn (the dev group includes them)
uv run wequant serve --home /path/to/home    # http://127.0.0.1:8731, until Ctrl-C
```

- **One rule for every route:** operation `<name>` (as listed by `wequant schema`) is `POST /api/<name>`; the JSON body
  is its request model (`{}` or an empty body for all defaults) and the response is its result model, byte for byte
  what `wequant <command> --json` prints (without the final newline). The snapshot
  `frontend/contract/service-models.schema.json` carries each operation's `method` and `path`. Adding an operation needs
  no change in `wequant/api/`.
- **Errors:** the body is the ErrorBody the CLI prints, with the status: USAGE 422 (a malformed body or invalid fields
  included, located like pydantic), NOT_FOUND 404, UNREACHABLE 503, LOCKED 409, INTERNAL 500. A check that finds
  problems (`data check`, `data index-check`) is a 200 with `ok: false`.
- **One resident process:** evaluated expressions stay in memory between requests (`--cache-limit` GB, default 8);
  panels come from the on-disk panel cache (`--cache`). Each request runs in a worker thread, so a backtest does not hold up others. A client that
  disconnects cancels its backtest, which then archives nothing. Writes take the same locks as the CLI (409 when busy).
- **Background jobs (S5.2):** `jobs_start` with `{"kind": "backtest", "request": <BacktestRequest>}` queues a job and
  returns its JobInfo (id, state, place in the queue, fraction done, stage code and params, and the stage as one
  Chinese sentence). The request is checked at once (errors located under `request`), and a null `data_version` is
  pinned to the version current then, so a version committed while the job waits or runs changes nothing. Each job runs
  in a worker process of its own; one job runs at a time and at most one uses BaoStock, while the others wait in order.
  `GET /api/jobs/{id}/events` streams the job's JobInfo as server-sent events (`event: job`), one per change, and ends
  once the job has finished. `jobs_list`, `jobs_show` and `jobs_cancel` take `{"id": n}` (`jobs_list` none).
  `jobs_result` returns a succeeded job's result byte for byte as the operation run directly would, a failed or
  cancelled job's ErrorBody with its status, and NOT_FOUND `job_unfinished` before then. Cancel stops a queued job
  before it starts; a running one stops and leaves nothing behind, and its worker is killed if it is still running
  10 s later. A worker that dies fails its job with its exit code (stage `crashed`). Stopping the server fails the
  jobs it leaves unfinished (stage `interrupted`), and so does starting it after a crash. The CLI and the plain
  routes are unchanged. Job records live in `<home>/jobs/`, see `Database/README.md`.
- **Local only:** `--host` defaults to 127.0.0.1 and any other address prints a warning; there is no CORS. During
  frontend development Vite proxies `/api` to the server. `--port` defaults to 8731.
- **OpenAPI:** the generated document is at `/api/openapi.json`; there is no Swagger or ReDoc page, since those load
  their scripts from a CDN and stay blank offline. The schema snapshot remains the frontend's source of types.
- **The frontend at `/`:** the build in `--static DIR`, else `$WEQUANT_STATIC`, else the repository's `frontend/dist`.
  A file of the build is served as is and any other path outside `/api` gets its `index.html`. Without a build, `/`
  is a page saying how to build it; the API works either way. An unknown `/api/...` path is a 404 ErrorBody.
- **Demo home:** `uv run python -m tools.demo_home --out DIR [--seed N]` writes a synthetic data root into a new
  directory: every board, two ST stocks, a suspension, a delisting, a new listing, limit-up and limit-down streaks, a
  bonus issue and a dividend, 000300.SH with synthetic members, data issues (one at error level), its limit check
  recorded and two saved strategies. Nothing comes from a real market.

## Fetching BaoStock raw data

`tools/baostock_backfill.py` fetches BaoStock's raw data into `$WEQUANT_HOME/raw/baostock/`. It can be stopped and
resumed at any time and never writes the store. **While it runs, nothing else may use BaoStock, network tests
included:** BaoStock keeps one session per user, so another login ends the running one.

```bash
tmux new -s backfill
systemd-inhibit --what=sleep --why="baostock backfill" uv run --extra baostock python -m tools.baostock_backfill run
# detach: Ctrl-b d    reattach: tmux attach -t backfill
```

- **Progress:** `uv run python -m tools.baostock_backfill status`, or `tail -f $WEQUANT_HOME/logs/baostock_backfill.log`.
  The log has one line per file with its rows, the seconds spent waiting for BaoStock (server) and the rest (local).
- **Stop:** Ctrl-C in tmux, or `kill -TERM <pid>`. It finishes the current file, logs out and exits 0.
- **Resume:** run the same command again.
- **Report:** when it has finished, run `uv run python -m tools.baostock_backfill report` (local files only).
- **Refetch:** `uv run --extra baostock python -m tools.baostock_backfill refetch 2020-06-17 2020-07-21` fetches those
  days again into `raw/baostock/refetch/` and never touches `daily/` or `progress.json`. `run` and `refetch` hold
  `raw/baostock/.lock`, so neither starts while the other is running (exit code 5).
- **Compare:** `uv run python -m tools.baostock_backfill compare [--rows resolutions.toml] [--out FILE]` compares each
  refetched day with its `daily/` file row by row; `--rows` adds whether each listed row's problem reproduces.
- **Sleep:** `systemd-inhibit --what=sleep` needs an active desktop session. From a non-interactive shell, use `--what=idle`.

## Merging BaoStock into the store

`tools/baostock_merge.py` merges the raw days into the store as one version. These conflicts need your decision, each
an approved entry in `$WEQUANT_HOME/resolutions/baostock.toml` (its header explains the format); while one lacks it, the
merge writes nothing and its report lists the conflicts awaiting a decision, those without any entry first:

- `price_mismatch`: open, high, low or close differ from the store's bar; `preclose_odd`: BaoStock's exchange preclose
  is not whole fen.
- `stale_volume`: a suspended row (tradestatus 0 and amount 0; tradestatus 0 alone is not enough) carries a volume.
- `volume_truncated`: on a day whose snapshot looks cut (at least 3% and 10 of an exchange's traded rows short of the
  seed's volume by over 0.1%), a volume short of the seed's.
- `amount_carried`: the amount repeats the code's previous bar to the fen while the volume differs, unless the seed's
  amount agrees.
- `average_price`: amount / volume outside [low, high] of the bar as its price entry leaves it.

Each bar gets at most one of the last four, in that order. Volumes and amounts count auction trading only, as BaoStock
and the data vendors do: block and after-hours trades are excluded, so an exchange's all-trades total can be slightly
higher. On days the store already has, a BaoStock volume more than one lot (100 shares) away from the store's is
recorded as `volume_mismatch` (BaoStock's kept); within a lot the sources agree, since they round to it.

```bash
uv run python -m tools.baostock_merge propose        # add a proposed entry for each conflict; reads only
uv run wequant data resolve list                      # review, then approve or reject with a note
uv run wequant data resolve approve --category GROUP --note "who decided, and on what basis"
uv run python -m tools.baostock_merge run            # dry run with the approved entries applied
uv run python -m tools.baostock_merge run --commit   # one version, its report in $WEQUANT_HOME/reports/
```

- **Proposals:** a price mismatch takes the prices of the source whose close the next trading day's exchange preclose
  repeats; it is `undetermined` (outside evidence needed, never approved) when the closes are equal, neither repeats,
  the preclose is missing or an adj_factor step lies between, and gets no proposal until that day's snapshot is fetched.
  A stale volume builds no bar; a truncated volume takes the seed's volume and amount; a carried amount takes the seed's
  when its bar has the same volume and passes the check. An average-price conflict takes the store's amount, or its
  volume and amount, when the store's bar passes the check, and otherwise a null amount. A preclose off the fen is
  `undetermined`. After changing a price entry, run `propose` again: amounts are checked on the prices it takes.
- **Record:** each applied entry is one `data_issues` row (`resolved_baostock`, `_seed`, `_null`, `_no_bar` or
  `_corrected`, the last with values from verification records) whose detail holds the fields, the store's values before
  the cell's first resolution, BaoStock's values, the values after, the category, level, verification ids and evidence.
  Its kind follows the level: the conflict kind for an error (contradicted the exchange, corrected), `price_noted` /
  `volume_noted` for a notice, `price_unverified` / `volume_unverified` for a suspicious one. The report also lists
  approved entries that met no conflict.
- **Flags:** every merge recomputes, over all bars, `close_preclose_off` (a close the next bar's exchange preclose does
  not repeat with no factor step between; suspicious) and `snapshot_truncated` (every bar of an exchange-day where at
  least 5% and 20 of such closes are off: the source's snapshot looks cut and no second source decides).
- **Null amount:** unknown, never 0. The panel shows NaN and operators and expressions skip it.
- **turn:** BaoStock's turnover in percent, kept on its bars; seed and BJ bars have none.
- **Memory:** a merge whose bars gain a column rewrites every year of bars; on the full store its commit peaks near 8 GB.

## Data checks and reports

These tools read the store under `$WEQUANT_HOME` (or `--home`). Without `--commit` they write nothing there: the report
goes to stdout (redirect it to keep it), or to `--out FILE` for the `tools` jobs. `--commit` writes a new store version
under the writer lock and its report to `reports/<tool>_<last trade date>_v<version>.md`, so never run it while another
writer (an import or a merge) is running.

```bash
uv run python -m tools.baostock_merge copy-store /tmp/wequant-copy   # copy the current snapshot, no lock
uv run python -m tools.baostock_merge run --home /tmp/wequant-copy --raw $WEQUANT_HOME/raw/baostock   # dry run
uv run wequant data check                                            # price-limit check, dry run
uv run python -m tools.panel_report --cache /tmp/wequant-cache       # panel timings, day status, limit masks
uv run python -m tools.ops_report --cache /tmp/wequant-cache         # operator speed and TDX comparison values
uv run python -m tools.metrics_report                                # metrics of index buy-and-hold
uv run python -m tools.expr_report --cache /tmp/wequant-cache        # expression timings, cache hits, equality with operators
uv run python -m tools.account_report --cache /tmp/wequant-cache     # buy-and-hold through ex-dates, account invariants, speed
uv run python -m tools.backtest_report --cache /tmp/wequant-cache    # no signal, buy-and-hold, speed, typical strategies
uv run python -m tools.runs_report --cache /tmp/wequant-cache        # one run saved, loaded, re-run byte for byte, indexed
```

The backtest and run archive reports open with the store version, the code version, the start time (ISO 8601 with
its UTC offset) and a summary table of every check: PASS or FAIL and what it measured, such as seconds, bytes or
whether a re-run was byte-identical. The sections below give the details.

Run `wequant data check --commit` again after every import: the panel applies the voids it records. Panels are cached
by store version under `$WEQUANT_HOME/cache/panel/`. The cache is derived data and can be deleted at any time.

## Backtest assumptions

- **Fills:** an order fills at the next trading day's raw open (`Execution(price="open")`).
- **Limits:** a buy is refused when the fill price is at the up limit, a sell when it is at the down limit.
- **Costs:** commission of 0.03% per order (at least 5 yuan), stamp tax and transfer fee as dated in
  `wequant/rules/rules.toml`, and slippage of 0.1% per side by default (`Execution(slippage_bps=10)`). Slippage is a
  cost on the trade value; it moves neither the fill price nor the limit checks.
- **Volume:** no cap on a fill's share of the stock's traded amount that day; the `capacity_*` metrics report it.
- **Corporate actions:** simulated with the adjustment factors, i.e. dividends are reinvested and dividend tax is
  ignored.
- **ST stocks:** on the seed store ST status is unknown, so ST stocks get the normal price band.
- **Halts:** a stock without a bar stays valued at its last close; one held more than 20 trading days without a bar is
  reported (`stale_holdings`).
- **Delistings:** `--delisting auto` (the default) writes a forced delisting down to zero, cashes a merger or
  privatisation at its last close without costs, and holds a long suspension. Until official delisting dates are in
  the store, the day comes from the data's last date and every delisting is written down (`delisting_approximate`).
  `--failure-recovery` returns a share of the last close for sensitivity checks; `hold` treats delistings as before.
  The result lists the delisted holdings it handled.
- **Anomalous data:** used and reported by default; see `--anomalies` above.
