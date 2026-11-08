# WeQuant 微框

A local A-share stock selection and backtesting tool. The core is Python on numpy and polars; data lives in a
versioned Parquet store under `$WEQUANT_HOME` (default `~/.wequant`).

```bash
uv sync                      # core and tests
uv run pytest                # offline tests; network tests are skipped
uv sync --extra baostock     # BaoStock data source
```

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
