"""Run archive report on real data: one run saved, loaded, re-run here and in a fresh process, changed and indexed.

Reads the store without the lock; writes only the panel cache (--cache, a fresh temporary directory by default), the
archive (--runs, a fresh temporary directory by default, never inside the store home) and the report.
Run: python -m tools.runs_report [--home H] [--cache DIR] [--runs DIR] [--years 10] [--top 50] [--window 20]
     [--benchmark 000300.SH|none] [--out FILE]
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import os
import resource
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import polars as pl

from wequant.account import EXECUTION_VERSION, Account, Execution, Ledger, Target, trade_target
from wequant.analytics import METRICS_VERSION, MetricSpec, compute_metrics
from wequant.canonical import canonical
from wequant.data.store import Store, wequant_home
from wequant.expr import EXPR_VERSION, IF, NOT, REF, Field, evaluate, panel_key
from wequant.panel import Panel, load_panel
from wequant.runs import FILES, HEADLINE, Archive, Engine, Run, RunConflict, run_files
from wequant.version import code_version

REPO = Path(__file__).resolve().parent.parent
CASH = 10_000_000.0
EVERY = 5  # rows from one decision to the next
MEMORY_LIMIT = 5 * 1024  # MB of peak RSS
RERUN_ENV = {"PYTHONHASHSEED": "123", "NUMBA_NUM_THREADS": "1", "POLARS_MAX_THREADS": "1"}
PICKS = {"date": pl.Date, "code": pl.String, "rank": pl.Int64}
FUNNEL = {"date": pl.Date, "step": pl.String, "passed": pl.Int64, "left": pl.Int64}
DETAIL = {"date": pl.Date, "code": pl.String}


def standin(store: Store, cache_dir: Path) -> Engine:
    """Weekly top-N momentum through trade_target: the report's engine until the rebalance engine replaces it."""

    def engine(description: dict, version: int) -> Run:
        panel = load_panel(description["start"], version=version, store=store, cache_dir=cache_dir)
        close, top = Field("adj_close"), description["top"]
        score = evaluate(IF(NOT(Field("exchange@BJ")), close / REF(close, description["window"]), None), panel)
        account = Account(panel, description["cash"], panel.dates[0], Execution(**description["execution"]))
        decisions = []
        for t in range(1, len(panel.dates)):
            account.begin(t)
            if (t - 1) % EVERY == 0:
                targets = best(score[t - 1], top)
                weights = np.where(account.shares > 0, 0.0, np.nan)
                weights[targets] = 1 / top
                trade_target(account, Target(weights, score[t - 1], np.array([], dtype=np.int64)))
                decisions.append((t - 1, targets))
            account.end()
        return standin_run(description, panel, account.ledger(), decisions)

    return engine


def standin_run(description: dict, panel: Panel, ledger: Ledger, decisions: list[tuple[int, np.ndarray]]) -> Run:
    """The stand-in's Run: its versions, metrics and picks; an empty funnel and detail, no warnings."""
    calendar = pl.Series("date", panel.dates)
    metrics = compute_metrics(ledger.daily, calendar, positions=ledger.positions, orders=ledger.orders)
    versions = {"standin": 1, "expr": EXPR_VERSION, "execution": EXECUTION_VERSION, "metrics": METRICS_VERSION}
    frames = (picks_frame(panel, decisions), pl.DataFrame(schema=FUNNEL), pl.DataFrame(schema=DETAIL))
    key, anchor = panel_key(panel), panel.dates[0].item()
    return Run("standin", description, versions, key, anchor, ledger, metrics, MetricSpec(), *frames, {})


def best(scores: np.ndarray, n: int) -> np.ndarray:
    """The n columns with the highest finite scores, ties by column."""
    finite = np.flatnonzero(np.isfinite(scores))
    return finite[np.argsort(-scores[finite], kind="stable")][:n]


def picks_frame(panel: Panel, decisions: list[tuple[int, np.ndarray]]) -> pl.DataFrame:
    """PICKS: each decision row's targets by rank."""
    rows = [
        (panel.dates[row].item(), code, rank)
        for row, targets in decisions
        for rank, code in enumerate(panel.codes[targets].tolist(), 1)
    ]
    return pl.DataFrame(rows, schema=PICKS, orient="row")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = Store(args.home)
    if store.manifest() is None:
        raise SystemExit(f"{store.home}: nothing committed")
    cache = args.cache or Path(tempfile.mkdtemp(prefix="wequant-runs-cache-"))
    archive = Archive(args.runs or Path(tempfile.mkdtemp(prefix="wequant-runs-")))
    if args.rerun:
        return rerun(archive, args.rerun, standin(store, cache))
    text = report(store, cache, archive, args)
    if args.out:
        args.out.write_text(text, "utf-8")
    else:
        sys.stdout.write(text)
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.runs_report", description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, help="store home (default: WEQUANT_HOME)")
    parser.add_argument("--cache", type=Path, help="panel cache directory (default: a fresh temporary directory)")
    parser.add_argument("--runs", type=Path, help="archive root (default: a fresh temporary directory)")
    parser.add_argument("--years", type=int, default=10, help="years of trading up to the last day")
    parser.add_argument("--top", type=int, default=50, help="stocks held after each decision")
    parser.add_argument("--window", type=int, default=20, help="rows of the momentum score")
    parser.add_argument("--benchmark", default="000300.SH", help="index code recorded in the description, or none")
    parser.add_argument("--out", type=Path, help="write the Markdown here instead of stdout")
    parser.add_argument("--rerun", metavar="ID", help=argparse.SUPPRESS)  # one archived run in this fresh process
    args = parser.parse_args(argv)
    home = (args.home or wequant_home()).resolve()
    if args.runs and args.runs.resolve().is_relative_to(home):
        parser.error(f"--runs {args.runs} lies inside the store home {home}")
    return args


def rerun(archive: Archive, run_id: str, engine: Engine) -> int:
    """Print "identical" or the differences of the archived run_id re-run by engine; 0 when identical."""
    comparison = archive.rerun(run_id, engine)
    lines = ["identical"] if comparison.identical else [f"{n}: {t}" for n, t in comparison.differences.items()]
    print("\n".join([*lines, *(f"note: {note}" for note in comparison.notes)]))
    return 0 if comparison.identical else 1


def report(store: Store, cache: Path, archive: Archive, args: argparse.Namespace) -> str:
    """The Markdown report: the run, then each archive check, under a header and above a peak RSS footer."""
    version = store.manifest()["version"]
    description = describe(load_panel(version=version, store=store, cache_dir=cache).dates, args)
    engine = standin(store, cache)
    seconds, run = timed(engine, description, version)
    body = [
        *run_section(run, seconds),
        *save_section(archive, run),
        *rerun_section(archive, run.id, engine),
        *fresh_section(archive, run.id, store.home, cache),
        *changed_section(archive, run),
        *index_section(archive, run.id),
    ]
    return "\n".join([*header(store, version, cache, archive, run), *body, *footer()]) + "\n"


def describe(dates: np.ndarray, args: argparse.Namespace) -> dict:
    """The stand-in's description: the last args.years of trading, the options and fixed cash and costs."""
    start = dates[np.searchsorted(dates, np.datetime64(years_before(dates[-1].item(), args.years)))].item()
    benchmark = None if args.benchmark == "none" else args.benchmark
    execution = Execution(slippage_bps=0.0).to_dict()
    options = {"top": args.top, "window": args.window, "benchmark": benchmark}
    return {"start": start.isoformat(), **options, "cash": CASH, "execution": execution}


def years_before(day: dt.date, years: int) -> dt.date:
    """The same date years before day (28 February for 29 February)."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def header(store: Store, version: int, cache: Path, archive: Archive, run: Run) -> list[str]:
    last = run.ledger.daily["date"][-1]
    return [
        f"# Run archive report: store v{version}",
        "",
        f"- **Store:** {store.home} (version {version}, last day {last}); read without the lock.",
        f"- **code_version:** {code_version()}",
        f"- **Cache:** {cache}",
        f"- **Archive:** {archive.root}",
        f"- **Engine:** the stand-in, weekly top {run.description['top']} by "
        f"adj_close / REF(adj_close, {run.description['window']}) over SH/SZ codes, through trade_target; its "
        "returns exercise the archive and say nothing about a strategy.",
        f"- **Description:** `{canonical(run.description)}`",
        f"- **Versions:** `{canonical(run.versions)}`; run id {run.id}.",
        "",
    ]


def run_section(run: Run, seconds: float) -> list[str]:
    encode, files = timed(run_files, run)
    orders = run.ledger.orders
    statuses = ", ".join(f"{s} {n:,}" for s, n in orders["status"].value_counts(sort=True).iter_rows())
    rows = [[name, f"{len(data):,}"] for name, data in files.items()]
    return [
        "## 1. The run",
        "",
        f"- **Engine:** {seconds:.2f} s for {run.ledger.daily.height - 1:,} days from {run.anchor}.",
        f"- **Orders:** {orders.height:,} ({statuses or 'none'}); picks {run.picks.height:,}.",
        f"- **run_files:** {encode * 1000:.0f} ms for {sum(map(len, files.values())):,} bytes.",
        "",
        *table(["file", "bytes"], rows),
    ]


def save_section(archive: Archive, run: Run) -> list[str]:
    seconds, run_id = timed(archive.save, run)
    before = mtimes(archive.path(run_id))
    kept = archive.save(run) == run_id and mtimes(archive.path(run_id)) == before
    load, loaded = timed(archive.load, run_id)
    same = run_files(loaded) == archived(archive, run_id)
    return [
        "## 2. Save and load",
        "",
        f"- **Save:** {seconds * 1000:.0f} ms into {archive.path(run_id)}.",
        f"- **Save again:** {verdict(kept)}: the same id, and no file's st_mtime_ns changed.",
        f"- **Load:** {load * 1000:.0f} ms; {verdict(same)}: run_files of the loaded run equal the archived bytes.",
        "",
    ]


def rerun_section(archive: Archive, run_id: str, engine: Engine) -> list[str]:
    seconds, comparison = timed(archive.rerun, run_id, engine)
    found = "; ".join(f"{n}: {t}" for n, t in comparison.differences.items())
    return [
        "## 3. Re-run in this process",
        "",
        f"- **Re-run:** {seconds:.2f} s; {verdict(comparison.identical)}: {found or 'every file byte-identical'}.",
        f"- **Notes:** {'; '.join(comparison.notes) or 'none'}.",
        "",
    ]


def fresh_section(archive: Archive, run_id: str, home: Path, cache: Path) -> list[str]:
    paths = ["--home", str(home), "--cache", str(cache), "--runs", str(archive.root)]
    command = [sys.executable, "-m", "tools.runs_report", "--rerun", run_id, *paths]
    seconds, done = timed(subprocess.run, command, cwd=REPO, env=os.environ | RERUN_ENV, capture_output=True, text=True)
    lines = done.stdout.splitlines()
    ok = done.returncode == 0 and lines[:1] == ["identical"]
    said = "; ".join(lines or done.stderr.strip().splitlines()[-1:])
    settings = ", ".join(f"{key}={value}" for key, value in RERUN_ENV.items())
    return [
        "## 4. Re-run in a fresh process",
        "",
        f"- **Command:** `python -m tools.runs_report --rerun {run_id} ...` with {settings}.",
        f"- **Result:** {seconds:.2f} s, exit {done.returncode}; {verdict(ok)}: {said}.",
        "",
    ]


def changed_section(archive: Archive, run: Run) -> list[str]:
    equity = run.ledger.daily["equity"].to_numpy().copy()
    equity[-1] += 0.01
    daily = run.ledger.daily.with_columns(pl.Series("equity", equity))
    changed = dataclasses.replace(run, ledger=dataclasses.replace(run.ledger, daily=daily))
    found = archive.compare(run.id, changed).differences
    listed = found.get("daily.parquet", "").startswith("equity row ") and "run.json" not in found
    before = archived(archive, run.id)
    try:
        archive.save(changed)
        refused = False
    except RunConflict:
        refused = archived(archive, run.id) == before
    return [
        "## 5. A changed result",
        "",
        f"- **Change:** the last day's equity +0.01 yuan, run id unchanged ({changed.id}); compared, never saved.",
        f"- **Compare:** {verdict(listed)}: {'; '.join(f'{n}: {t}' for n, t in found.items())}.",
        f"- **Save:** {verdict(refused)}: RunConflict, and the archived bytes are unchanged.",
        "",
    ]


def index_section(archive: Archive, run_id: str) -> list[str]:
    seconds, index = timed(archive.index)
    row = index.filter(pl.col("id") == run_id).row(0, named=True)
    rows = [[name, "null" if row[name] is None else f"{row[name]:.6f}"] for name in HEADLINE]
    return [
        "## 6. Index",
        "",
        f"- **Index:** rows {index.height:,}, read in {seconds * 1000:.0f} ms; created_at {row['created_at']}, "
        f"{row['start']} to {row['end']}.",
        "",
        *table(["metric", "value"], rows),
    ]


def footer() -> list[str]:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    child = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    return [
        f"- **Memory:** peak RSS: {peak:,.0f} MB ({verdict(peak <= MEMORY_LIMIT)}, at most {MEMORY_LIMIT:,} MB); "
        f"the fresh process: {child:,.0f} MB."
    ]


def archived(archive: Archive, run_id: str) -> dict[str, bytes]:
    return {name: (archive.path(run_id) / name).read_bytes() for name in FILES}


def mtimes(directory: Path) -> dict[str, int]:
    return {path.name: path.stat().st_mtime_ns for path in directory.iterdir()}


def timed(call: Callable, *args, **kwargs) -> tuple[float, object]:
    start = time.perf_counter()
    result = call(*args, **kwargs)
    return time.perf_counter() - start, result


def table(header: list[str], rows) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    return lines + ["| " + " | ".join(str(x) for x in row) + " |" for row in rows] + [""]


def verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


if __name__ == "__main__":
    raise SystemExit(main())
