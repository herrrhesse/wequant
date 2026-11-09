"""Panel report on one store version: load time, adjusted continuity, day status, limit masks and invariants.

Reads the store without the lock and raw BaoStock daily snapshots for the tradestatus comparison; writes only the
panel cache (--cache, a fresh temporary directory by default) and the report.
Run: python -m tools.panel_report [--home H] [--cache DIR] [--years 20] [--out FILE]
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from wequant.data.raw import BaoStockRaw
from wequant.data.store import Store
from wequant.panel import MISSING, NOT_LISTED, RAW_FIELDS, SUSPENDED, TRADED, Panel, load_panel
from wequant.panel.cache import cache_key
from wequant.panel.grid import cells, fill_forward, scatter
from wequant.rules.tables import limit_check_current
from wequant.version import code_version

STATUS_NAMES = {NOT_LISTED: "NOT_LISTED", TRADED: "TRADED", SUSPENDED: "SUSPENDED", MISSING: "MISSING"}
WARM_RUNS = 3
WARM_LIMIT = 1.0  # seconds
HALF_FEN = 0.005  # yuan: the exchange rounds preclose to the fen
FLOAT_SLACK = 1e-9
LISTED_ROWS = 50
FLAGGED = ("event_missing", "event_unimplied", "event_step_off")  # the merge's adjustment check on adj_factor
BREAK_COLUMNS = ["kind", "code", "date", "preclose", "previous bar", "previous close", "factor step", "half fens off"]


@dataclass(frozen=True)
class Run:
    store: Store
    cache: Path
    version: int
    years: int

    def panel(self, start: dt.date | None = None, end: dt.date | None = None) -> Panel:
        return load_panel(start, end, version=self.version, store=self.store, cache_dir=self.cache)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = Store(args.home)
    manifest = store.manifest()
    if manifest is None:
        raise SystemExit(f"{store.home}: nothing committed")
    cache = args.cache or Path(tempfile.mkdtemp(prefix="wequant-panel-"))
    text = report(Run(store, cache, manifest["version"], args.years))
    if args.out:
        args.out.write_text(text, "utf-8")
    else:
        print(text, end="")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.panel_report", description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, help="store home (default: WEQUANT_HOME)")
    parser.add_argument("--cache", type=Path, help="panel cache directory (default: a fresh temporary directory)")
    parser.add_argument("--years", type=int, default=20, help="years in the timed slice")
    parser.add_argument("--out", type=Path, help="write the Markdown here instead of stdout")
    return parser.parse_args(argv)


def report(run: Run) -> str:
    """The Markdown report: a header, then every section of SECTIONS."""
    manifest = run.store.manifest(run.version)
    lines = [
        f"# Panel report: store v{run.version}",
        "",
        f"- **Store:** {run.store.home} (version {run.version}, last trade date {manifest.get('last_trade_date')})",
        f"- **Panel key:** {cache_key(manifest)}",
        f"- **Rules key:** {run.panel().rules_key}",
        f"- **code_version:** {code_version()}",
        f"- **Cache:** {run.cache}",
        "",
    ]
    for section in SECTIONS:
        lines += section(run)
    return "\n".join(lines) + "\n"


def table(header: list[str], rows) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    return lines + ["| " + " | ".join(str(x) for x in row) + " |" for row in rows] + [""]


def count(n) -> str:
    return f"{int(n):,}"


# ---------------------------------------------------------------- 1. timing


def timing_section(run: Run) -> list[str]:
    cached = (run.cache / "panel" / cache_key(run.store.manifest(run.version)) / "close.npy").exists()
    started = time.perf_counter()
    full = run.panel()
    full.raw("close")
    first = time.perf_counter() - started
    last = full.dates[-1].item()
    start = years_before(last, run.years)
    warm = [timed_close(run, start, last) for _ in range(WARM_RUNS)]
    median = statistics.median(warm)
    shape = run.panel(start, last).raw("close").shape
    state = "already cached" if cached else "cold: dates, codes and close built"
    return [
        "## 1. Timing",
        "",
        f"- **First load** of the full close panel ({state}): {first:.2f} s.",
        f"- **Warm** `load_panel({start}, {last}).raw('close')` + `np.nansum`, {shape[0]:,} × {shape[1]:,}: "
        f"{', '.join(f'{t:.3f}' for t in warm)} s; median {median:.3f} s.",
        f"- **Result:** {'PASS' if median < WARM_LIMIT else 'FAIL'} (limit {WARM_LIMIT:.0f} s).",
        "",
    ]


def timed_close(run: Run, start: dt.date, end: dt.date) -> float:
    started = time.perf_counter()
    np.nansum(run.panel(start, end).raw("close"))
    return time.perf_counter() - started


def years_before(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


# ---------------------------------------------------------------- 2. continuity


def continuity_section(run: Run) -> list[str]:
    panel = run.panel()
    checked, broken = continuity(panel)
    derived = bars_grid(run, panel, "preclose_derived", pl.col("preclose_derived"))
    flagged, after = flagged_grid(run, panel), after_missing(panel)
    rows, listed = [], []
    for name, group in (("exchange preclose", checked & ~derived), ("derived preclose", checked & derived)):
        found = broken & group
        rows.append((name, *map(count, (group.sum(), found.sum(), (found & after).sum(), (found & flagged).sum()))))
        listed += [(name, *break_row(panel, t, j)) for t, j in zip(*np.nonzero(found & ~after & ~flagged))]
    note = f" (first {LISTED_ROWS} listed)" if len(listed) > LISTED_ROWS else ""
    return [
        "## 2. Continuity of adjusted prices",
        "",
        "Each bar's preclose against the one the factor implies: the previous bar's close × its factor / today's "
        "factor. A violation is more than half a fen away; an exact half-fen tie is the exchange's rounding. "
        "Exchange-preclose cells are the evidence and should match the merge's flagged adj_factor rows "
        f"({' / '.join(FLAGGED)}); derived cells were computed from the same factor and only check the build. "
        "After a MISSING day the preclose may refer to a close the store does not hold.",
        "",
        *table(["preclose", "checked", "violations", "after a MISSING day", "flagged by the merge"], rows),
        f"Flagged cells without a violation: {count((flagged & ~broken).sum())}.",
        "",
        f"Violations neither after a MISSING day nor flagged: {len(listed):,}{note}.",
        "",
        *(table(BREAK_COLUMNS, listed[:LISTED_ROWS]) if listed else []),
    ]


def continuity(panel: Panel) -> tuple[np.ndarray, np.ndarray]:
    """(checked, broken): bars with a preclose and an earlier bar, and those over half a fen from the implied one."""
    implied = previous_bar(panel.adjusted("close")) / panel.factor
    pre = panel.raw("preclose")
    checked = ~np.isnan(pre) & ~np.isnan(implied)
    return checked, checked & (np.abs(pre - implied) > HALF_FEN + FLOAT_SLACK)


def previous_bar(grid: np.ndarray) -> np.ndarray:
    """Each cell's value on the code's previous bar; NaN before its first."""
    filled = fill_forward(grid, np.nan)
    return np.vstack([np.full((1, grid.shape[1]), np.nan), filled[:-1]])


def after_missing(panel: Panel) -> np.ndarray:
    """Cells with a MISSING day since the code's previous bar."""
    seen = np.cumsum(panel.missing, axis=0, dtype=np.float64)
    return seen > previous_bar(np.where(panel.traded, seen, np.nan))


def bars_grid(run: Run, panel: Panel, column: str, flag: pl.Expr) -> np.ndarray:
    """flag, a bool expression of one bars column, on the full grid at the panel's version; False without a bar."""
    frame = run.store.scan("bars", columns=["code", "date", column], version=run.version).select(
        "code", "date", flag.alias("flag")
    )
    return on_grid(panel, frame.collect())


def flagged_grid(run: Run, panel: Panel) -> np.ndarray:
    """Cells with a flagged adj_factor row of the merge's adjustment check at the panel's version."""
    frame = (
        run.store.scan("data_issues", columns=["table", "code", "date", "kind"], version=run.version)
        .filter(pl.col("table") == "adj_factor", pl.col("kind").is_in(FLAGGED))
        .filter(pl.col("code").is_in(panel.codes.tolist()))
        .select("code", "date", flag=pl.lit(True))
    )
    return on_grid(panel, frame.collect())


def on_grid(panel: Panel, frame: pl.DataFrame) -> np.ndarray:
    """frame's bool flag column on the panel's grid; False on cells without a row."""
    rows, cols = cells(frame, panel.dates, panel.codes)
    return scatter(panel.traded.shape, rows, cols, frame["flag"].to_numpy(), False)


def break_row(panel: Panel, t: int, j: int) -> tuple:
    """One continuity violation: code, dates, prices, factor step and its distance in half fens."""
    p = np.flatnonzero(panel.traded[:t, j])[-1]
    pre, close = panel.raw("preclose")[t, j], panel.raw("close")[p, j]
    step = panel.factor[t, j] / panel.factor[p, j]
    gap = abs(pre - close / step) / HALF_FEN
    return panel.codes[j], panel.dates[t], f"{pre:.2f}", panel.dates[p], f"{close:.2f}", f"{step:.6g}", f"{gap:.6f}"


# ---------------------------------------------------------------- 3. status


def status_section(run: Run) -> list[str]:
    panel = run.panel()
    counts = np.bincount(panel.status.ravel(), minlength=len(STATUS_NAMES))
    return [
        "## 3. Day status",
        "",
        *table(["status", "cells"], [(STATUS_NAMES[s], count(counts[s])) for s in STATUS_NAMES]),
        *tradestatus_lines(run, panel),
        *suspension_lines(panel),
        "NOT_LISTED after a code's last bar and SUSPENDED between bars are inferred from data spans on days without "
        "daily_status: they are descriptive, not point-in-time (a later bar turns NOT_LISTED days into SUSPENDED).",
        "",
    ]


def tradestatus_lines(run: Run, panel: Panel) -> list[str]:
    raw = BaoStockRaw.at(run.store.home)
    on_disk = raw.daily_days()
    calendar = set(panel.dates.tolist())
    days = sorted(on_disk & calendar)
    if not days:
        return ["No raw BaoStock daily snapshots on calendar days.", ""]
    # One year of snapshots at a time keeps the raw string columns small.
    found = pl.concat(raw_status(raw, [d for d in days if d.year == y], panel) for y in sorted({d.year for d in days}))
    known = found.filter(pl.col("status").is_not_null())
    crosstab = known.group_by("status", "tradestatus").len().sort("status", "tradestatus")
    agree = ((pl.col("status") == TRADED) & (pl.col("tradestatus") == "1")) | (
        pl.col("status").is_in([SUSPENDED, NOT_LISTED]) & (pl.col("tradestatus") == "0")
    )
    odd = known.filter(~agree)
    outside = found.filter(pl.col("status").is_null())
    return [
        f"Raw BaoStock tradestatus on the {len(days):,} calendar days with a snapshot "
        f"(snapshot days off the calendar: {len(on_disk - calendar):,}):",
        "",
        *table(["status", "tradestatus", "cells"], [(STATUS_NAMES[s], ts, count(n)) for s, ts, n in crosstab.rows()]),
        f"Snapshot rows for codes the store does not hold: {count(outside.height)} "
        f"from {count(outside['code'].n_unique())} codes.",
        "",
        "NOT_LISTED with tradestatus 0 is expected: BaoStock lists a suspended stock until it delists. "
        "Disagreements, by code (top 5 per pair):",
        "",
        *table(["status", "tradestatus", "code", "cells", "first", "last"], disagreements(odd)),
    ]


def raw_status(raw: BaoStockRaw, days: list[dt.date], panel: Panel) -> pl.DataFrame:
    """date, code, tradestatus of these snapshots, with the panel status of each cell (null for codes it lacks)."""
    frame = (
        pl.scan_parquet([str(raw.daily(d)) for d in days])
        .select(
            pl.col("date").str.to_date(),
            (pl.col("code").str.slice(3) + "." + pl.col("code").str.slice(0, 2).str.to_uppercase()).alias("code"),
            "tradestatus",
        )
        .collect()
    )
    held = pl.col("code").is_in(panel.codes.tolist())
    inside = frame.filter(held)
    rows, cols = cells(inside, panel.dates, panel.codes)
    inside = inside.with_columns(status=pl.Series(panel.status[rows, cols]))
    return pl.concat([inside, frame.filter(~held).with_columns(status=pl.lit(None, dtype=pl.Int8))])


def disagreements(odd: pl.DataFrame) -> list[tuple]:
    by_code = (
        odd.group_by("status", "tradestatus", "code")
        .agg(cells=pl.len(), first=pl.col("date").min(), last=pl.col("date").max())
        .sort(["status", "tradestatus", "cells", "code"], descending=[False, False, True, False])
    )
    top = by_code.group_by("status", "tradestatus", maintain_order=True).head(5)
    return [(STATUS_NAMES[s], ts, code, count(n), a, b) for s, ts, code, n, a, b in top.rows()]


def suspension_lines(panel: Panel) -> list[str]:
    cols, starts, lengths = runs(panel.suspended & ~panel.st_known)
    longest = np.argsort(-lengths, kind="stable")[:20]
    rows = [
        (panel.codes[cols[i]], panel.dates[starts[i]], panel.dates[starts[i] + lengths[i] - 1], count(lengths[i]))
        for i in longest
    ]
    return [
        f"The 20 longest inferred suspensions (SUSPENDED without daily_status; {count(len(lengths))} runs in all):",
        "",
        *table(["code", "from", "to", "trading days"], rows),
    ]


def runs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(column, first row, length) of every run of True down the columns of mask."""
    padded = np.zeros((mask.shape[0] + 2, mask.shape[1]), dtype=np.int8)
    padded[1:-1] = mask
    edges = np.diff(padded, axis=0).T
    cols, starts = np.nonzero(edges == 1)
    _, ends = np.nonzero(edges == -1)
    return cols, starts, ends - starts


# ---------------------------------------------------------------- 4. limits


def limits_section(run: Run) -> list[str]:
    panel = run.panel()
    up, down, close = panel.limit_up_price, panel.limit_down_price, panel.raw("close")
    limited = np.isfinite(up)
    with np.errstate(invalid="ignore"):
        outside = limited & ((close > up) | (close < down))
    masks = {
        "traded": panel.traded,
        "LIMITED": limited,
        "FREE": np.isinf(up),
        "UNKNOWN": panel.traded & np.isnan(up),
        "limit_up": panel.limit_up,
        "limit_down": panel.limit_down,
        "limit_locked": panel.limit_locked,
        "out of band": outside,
    }
    years, totals = per_year(panel.dates, masks)
    current = limit_check_current(run.store.manifest(run.version))
    return [
        "## 4. Limits",
        "",
        f"- **Recorded limit check current:** {'yes' if current else 'no'}",
        f"- **Out of band** (close above the up limit or below the down limit on a LIMITED cell): "
        f"{count(totals['out of band'])}. It cannot exceed the limit check's high/low breach count, and is 0 "
        "once the check's voids are committed.",
        "",
        *table(
            ["year", *masks],
            [*((year, *map(count, values)) for year, values in years), ("all", *map(count, totals.values()))],
        ),
    ]


def per_year(dates: np.ndarray, masks: dict[str, np.ndarray]) -> tuple[list, dict[str, int]]:
    """Cell counts of each mask by calendar year, and over all years."""
    years, inverse = np.unique(dates.astype("datetime64[Y]").astype(int) + 1970, return_inverse=True)
    sums = {name: np.bincount(inverse, weights=mask.sum(axis=1), minlength=len(years)) for name, mask in masks.items()}
    rows = [(int(year), [sums[name][i] for name in masks]) for i, year in enumerate(years)]
    return rows, {name: int(s.sum()) for name, s in sums.items()}


# ---------------------------------------------------------------- 5. invariants


def invariants_section(run: Run) -> list[str]:
    panel = run.panel()
    traded, suspended, missing = panel.traded, panel.suspended, panel.missing
    no_pre = np.isnan(panel.raw("preclose"))
    null_pre = bars_grid(run, panel, "preclose", pl.col("preclose").is_null())
    checks = [
        (
            "listed is the union of traded, suspended and missing",
            (panel.listed != (traded | suspended | missing)).sum(),
        ),
        (
            "traded, suspended and missing are disjoint",
            (traded & suspended | traded & missing | suspended & missing).sum(),
        ),
        *((f"isnan({f}) == ~traded", (np.isnan(panel.raw(f)) != ~traded).sum()) for f in RAW_FIELDS if f != "preclose"),
        ("isnan(preclose) ⊇ ~traded", (~traded & ~no_pre).sum()),
        ("isnan(preclose) & traded == store preclose null", ((no_pre & traded) != null_pre).sum()),
        ("factor is finite", (~np.isfinite(panel.factor)).sum()),
        ("st implies st_known", (panel.st & ~panel.st_known).sum()),
    ]
    rows = [(name, count(bad), "PASS" if bad == 0 else "FAIL") for name, bad in checks]
    return [
        "## 5. Invariants",
        "",
        f"Traded cells without a preclose: {count((no_pre & traded).sum())} (each code's first bar in the store "
        "has a null preclose).",
        "",
        *table(["check", "cells violating", "result"], rows),
    ]


SECTIONS = (timing_section, continuity_section, status_section, limits_section, invariants_section)


if __name__ == "__main__":
    raise SystemExit(main())
