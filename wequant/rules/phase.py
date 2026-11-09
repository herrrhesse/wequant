"""Where each bar lies in its code's life: listing date, listing day and the gap since the previous bar."""

from __future__ import annotations

import polars as pl


def list_dates(securities: pl.DataFrame) -> pl.DataFrame:
    """code, list_date: first_date, or null when it is its exchange's earliest first_date (listed before the data starts)."""
    earliest = pl.col("first_date").min().over("exchange")
    return securities.select("code", list_date=pl.when(pl.col("first_date") > earliest).then(pl.col("first_date")))


def add_listing_day(frame: pl.DataFrame, calendar: pl.Series) -> pl.DataFrame:
    """listing_day (Int32): 1 on list_date, +1 per calendar trading day; null where list_date is null."""
    day = _index(calendar, frame["date"]) - _index(calendar, frame["list_date"]) + 1
    return frame.with_columns(listing_day=pl.when(pl.col("list_date").is_not_null()).then(day))


def add_gap_days(frame: pl.DataFrame, calendar: pl.Series) -> pl.DataFrame:
    """gap_days (Int32): trading days skipped since the code's previous row (0 when consecutive, null on its first row)."""
    index = pl.col("_index")
    gap = index - index.shift().over("code", order_by="date") - 1
    return frame.with_columns(_index=_index(calendar, frame["date"])).with_columns(gap_days=gap).drop("_index")


def _index(calendar: pl.Series, days: pl.Series) -> pl.Series:
    """Position of each day in the calendar (of the next trading day for a non-trading day)."""
    return calendar.sort().search_sorted(days).cast(pl.Int32)
