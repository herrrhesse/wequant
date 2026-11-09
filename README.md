# WeQuant 微框

A local A-share stock selection and backtesting tool. The core is Python on numpy and polars; data lives in a
versioned Parquet store under `$WEQUANT_HOME` (default `~/.wequant`).

```bash
uv sync                      # core and tests
uv run pytest                # offline tests; network tests are skipped
uv sync --extra baostock     # BaoStock data source
```

## Layout

- `wequant/data/`: table definitions (`schema.py`), the versioned store, data sources, the raw layer and the merge of
  raw BaoStock data into the store.
- `wequant/rules/`: dated A-share trading rules: price limits, lots, ticks, stamp tax and transfer fees.
- `wequant/panel/`: date × stock matrices of one store version, with day status and limit masks.
- `wequant/ops/`: TDX-style operators on those matrices.
- `wequant/expr/`: signal expressions: checked trees of panel fields and operators with one canonical JSON form,
  evaluated once per distinct subexpression and cached.
- `wequant/account/`: cash, positions and order execution on raw prices: lots, T+1, fees, corporate actions and one order
  validation.
- `wequant/analytics/`: the metrics of a run.
- `tools/`: command-line jobs: fetch, merge, data checks and reports. `tools/investment_data/` is a temporary seed
  import and not part of the package.

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
- **Stop:** Ctrl-C in tmux, or `kill -TERM <pid>`. It finishes the current file, logs out and exits 0.
- **Resume:** run the same command again.
- **Report:** when it has finished, run `uv run python -m tools.baostock_backfill report` (local files only).
- **Sleep:** `systemd-inhibit --what=sleep` needs an active desktop session. From a non-interactive shell, use `--what=idle`.

## Data checks and reports

These tools read the store under `$WEQUANT_HOME` (or `--home`). Without `--commit` they write nothing there: the report
goes to stdout or `--out FILE`. `--commit` writes a new store version under the writer lock and its report to
`reports/<tool>_<last trade date>_v<version>.md`, so never run it while another writer (an import or a merge) is running.

```bash
uv run python -m tools.baostock_merge copy-store /tmp/wequant-copy   # copy the current snapshot, no lock
uv run python -m tools.baostock_merge run --home /tmp/wequant-copy --raw $WEQUANT_HOME/raw/baostock   # dry run
uv run python -m tools.check_limits                                  # price-limit check, dry run
uv run python -m tools.panel_report --cache /tmp/wequant-cache       # panel timings, day status, limit masks
uv run python -m tools.ops_report --cache /tmp/wequant-cache         # operator speed and TDX comparison values
uv run python -m tools.metrics_report                                # metrics of index buy-and-hold
uv run python -m tools.expr_report --cache /tmp/wequant-cache        # expression timings, cache hits, equality with operators
uv run python -m tools.account_report --cache /tmp/wequant-cache     # buy-and-hold through ex-dates, account invariants, speed
```

Run `check_limits --commit` again after every import: the panel applies the voids it records. Panels are cached by store
version under `$WEQUANT_HOME/cache/panel/`. The cache is derived data and can be deleted at any time.

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
- **Halts:** a stock without a bar stays valued at its last close.
