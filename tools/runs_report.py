"""Run archive report on real data: one rebalance run saved, loaded, re-run here and in a fresh process, changed and
indexed.

Reads the store without the lock; writes only the panel cache (--cache, a fresh temporary directory by default), the
archive (--runs, a fresh temporary directory by default, never inside the store home) and the report.
Run: python -m tools.runs_report [--home H] [--cache DIR] [--runs DIR] [--years 10] [--top 50] [--window 20]
     [--benchmark 000300.SH|none] [--out FILE]
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import functools
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

from wequant.account import Execution
from wequant.backtest import Backtest, run_form
from wequant.canonical import canonical
from wequant.data.store import Store, wequant_home
from wequant.expr import REF, Field
from wequant.panel import load_panel
from wequant.runs import FILES, HEADLINE, Archive, Engine, Run, RunConflict, run_files
from wequant.strategy import Factor, Strategy
from wequant.version import code_version

REPO = Path(__file__).resolve().parent.parent
CASH = 10_000_000.0
MEMORY_LIMIT = 5 * 1024  # MB of peak RSS
RERUN_ENV = {"PYTHONHASHSEED": "123", "NUMBA_NUM_THREADS": "1", "POLARS_MAX_THREADS": "1"}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = Store(args.home)
    if store.manifest() is None:
        raise SystemExit(f"{store.home}: nothing committed")
    cache = args.cache or Path(tempfile.mkdtemp(prefix="wequant-runs-cache-"))
    archive = Archive(args.runs or Path(tempfile.mkdtemp(prefix="wequant-runs-")))
    engine = functools.partial(run_form, store=store, cache_dir=cache)
    if args.rerun:
        return rerun(archive, args.rerun, engine)
    text = report(store, engine, cache, archive, args)
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


def report(store: Store, engine: Engine, cache: Path, archive: Archive, args: argparse.Namespace) -> str:
    """The Markdown report: the run, then each archive check, under a header and above a peak RSS footer."""
    version = store.manifest()["version"]
    description = describe(load_panel(version=version, store=store, cache_dir=cache).dates, args)
    seconds, run = timed(engine, description, version)
    body = [
        *run_section(run, seconds),
        *save_section(archive, run),
        *rerun_section(archive, run.id, engine),
        *fresh_section(archive, run.id, store.home, cache),
        *changed_section(archive, run),
        *index_section(archive, run.id),
    ]
    return "\n".join([*header(store, cache, archive, run, args), *body, *footer()]) + "\n"


def describe(dates: np.ndarray, args: argparse.Namespace) -> dict:
    """The Backtest form: weekly top args.top by momentum over the last args.years of trading, without slippage."""
    start = dates[np.searchsorted(dates, np.datetime64(years_before(dates[-1].item(), args.years)))].item()
    adj = Field("adj_close")
    strategy = Strategy(factors=(Factor("momentum", adj / REF(adj, args.window) - 1),), top_n=args.top, period="week")
    benchmark = None if args.benchmark == "none" else args.benchmark
    return Backtest(strategy, start=start, cash=CASH, execution=Execution(slippage_bps=0.0), benchmark=benchmark).form


def years_before(day: dt.date, years: int) -> dt.date:
    """The same date years before day (28 February for 29 February)."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def header(store: Store, cache: Path, archive: Archive, run: Run, args: argparse.Namespace) -> list[str]:
    version, last = run.store_version, run.ledger.daily["date"][-1]
    return [
        f"# Run archive report: store v{version}",
        "",
        f"- **Store:** {store.home} (version {version}, last day {last}); read without the lock.",
        f"- **code_version:** {code_version()}",
        f"- **Cache:** {cache}",
        f"- **Archive:** {archive.root}",
        "- **Engine:** the rebalance engine, `functools.partial(wequant.backtest.run_form, store=store, "
        f"cache_dir=cache)`: weekly top {args.top} SH/SZ codes by adj_close / REF(adj_close, {args.window}) - 1, "
        "equal weights, no slippage.",
        f"- **Description:** `{canonical(run.description)}`",
        f"- **Versions:** `{canonical(run.versions)}`; run id {run.id}.",
        "",
    ]


def run_section(run: Run, seconds: float) -> list[str]:
    encode, files = timed(run_files, run)
    orders, days = run.ledger.orders, run.ledger.daily["date"]
    statuses = ", ".join(f"{s} {n:,}" for s, n in orders["status"].value_counts(sort=True).iter_rows())
    rows = [[name, f"{len(data):,}"] for name, data in files.items()]
    return [
        "## 1. The run",
        "",
        f"- **Engine:** {seconds:.2f} s for {days.len() - 1:,} run days from {days[0]}, on a panel from {run.anchor}.",
        f"- **Orders:** {orders.height:,} ({statuses or 'none'}); decisions {run.funnel['date'].n_unique():,}, "
        f"picks {run.picks.height:,}, detail rows {run.detail.height:,}.",
        f"- **Warnings:** {', '.join(run.warnings) or 'none'}.",
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
