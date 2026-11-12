"""BaoStock back-adjustment factors: read, normalized to the store's convention, and mapped to events."""

from __future__ import annotations

from collections.abc import Iterable

import polars as pl

from wequant.data.merge.daily import SAMPLES, RawDataError
from wequant.data.raw import BaoStockRaw
from wequant.data.schema import ADJ_FACTOR
from wequant.data.sources.baostock import parse_adjust_factor

COLUMNS = ("code", "dividOperateDate", "backAdjustFactor")


def read_factors(raw: BaoStockRaw, codes: Iterable[str]) -> pl.DataFrame:
    """adj_factor rows of these codes as published: each code's factor file, plus the events of the daily factor files
    fetched since; RawDataError on bad rows, a factor two files give differently included."""
    codes = sorted(codes)
    paths = [str(raw.adjust_factor(code)) for code in codes]
    if not paths:
        return pl.DataFrame(schema=ADJ_FACTOR.schema)
    scan = pl.scan_parquet(paths, include_file_paths="path")
    file_code = pl.col("path").str.extract(r"([^/\\]+)\.parquet$")
    factors = pl.concat([parse_adjust_factor(scan), scan.select(file=file_code)], how="horizontal").collect()
    if daily := raw.daily_factor_files():
        events = parse_adjust_factor(pl.scan_parquet([str(path) for path in daily])).filter(pl.col("code").is_in(codes))
        factors = pl.concat([factors, events.collect().with_columns(file=pl.col("code"))])
    problems = [
        f"{name}: {rows.height} rows, e.g. {rows.head(SAMPLES).rows()}"
        for name, rows in _bad_factor_rows(factors).items()
        if rows.height
    ]
    if problems:
        raise RawDataError(problems)
    return factors.drop("file").unique().sort("code", "ex_date")


def normalize(factors: pl.DataFrame, spans: pl.DataFrame) -> pl.DataFrame:
    """For each code in spans: one row at its first_date with the factor then in force (1.0 before any event), then each later event."""
    starts = spans.select("code", ex_date="first_date").sort("ex_date")
    # Both sides are sorted on the asof key; polars cannot verify that within by-groups.
    first = starts.join_asof(factors.sort("ex_date"), on="ex_date", by="code", check_sortedness=False)
    later = factors.join(starts, on="code", suffix="_first").filter(pl.col("ex_date") > pl.col("ex_date_first"))
    rows = [first.with_columns(pl.col("factor").fill_null(1.0)), later.select(list(ADJ_FACTOR.schema))]
    return pl.concat(rows).sort("code", "ex_date")


def events(adj: pl.DataFrame, bar_dates: pl.DataFrame) -> pl.DataFrame:
    """One row per (code, day) after each code's first row: day = first bar on or after ex_date, the last factor mapped there, step = factor / factor in force before day."""
    rows = adj.sort("code", "ex_date").with_columns(before=pl.col("factor").shift(1).over("code"))
    rows = rows.filter(pl.col("before").is_not_null()).sort("ex_date")
    days = bar_dates.select("code", day="date").sort("day")
    mapped = rows.join_asof(
        days, left_on="ex_date", right_on="day", by="code", strategy="forward", check_sortedness=False
    )
    grouped = mapped.sort("code", "ex_date").group_by("code", "day", maintain_order=True)
    found = grouped.agg(pl.col("ex_date").last(), pl.col("factor").last(), pl.col("before").first())
    step = pl.when(pl.col("day").is_not_null()).then(pl.col("factor") / pl.col("before"))
    return found.select("code", "day", "ex_date", "factor", step=step)


def _bad_factor_rows(factors: pl.DataFrame) -> dict[str, pl.DataFrame]:
    clashing = pl.col("factor").n_unique().over("code", "ex_date") > 1
    return {
        "code": factors.filter(pl.col("code") != pl.col("file")),
        "factor": factors.filter(pl.col("factor").is_null() | (pl.col("factor") <= 0)),
        "ex_date": factors.filter(pl.col("ex_date").is_null() | clashing),
    }
