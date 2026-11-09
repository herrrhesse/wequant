"""Rows the merge drops before writing: duplicates of another code's bar, and status before a code's first bar."""

from __future__ import annotations

import polars as pl

from wequant.data.issues import issue_rows

KEY = ["code", "date"]
VALUES = ("open", "high", "low", "close", "volume", "amount")


def drop_duplicate_codes(
    bars: pl.DataFrame, status: pl.DataFrame, stored: pl.DataFrame, source: str
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Drop a bar identical to another code's bar that day when only the other is in the store: (bars, status, issues)."""
    same_volume = bars.filter(pl.col("volume").is_duplicated().over("date"))  # a cheap first pass
    shared = same_volume.filter(pl.struct("date", *VALUES).is_duplicated())
    in_store = stored.select(*KEY, in_store=pl.lit(True))
    shared = shared.join(in_store, on=KEY, how="left").with_columns(pl.col("in_store").fill_null(False))
    same_as = pl.col("code").filter(pl.col("in_store")).min().over("date", *VALUES)
    dropped = shared.with_columns(same_as=same_as).filter(~pl.col("in_store") & pl.col("same_as").is_not_null())
    issues = issue_rows(dropped, "bars", "duplicate_code", "duplicate_dropped", ["same_as"], source)
    return _without(bars, dropped), _without(status, dropped), issues


def drop_status_before_first_bar(
    status: pl.DataFrame, spans: pl.DataFrame, source: str
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Drop daily_status rows dated before the code's first bar, all of them for a code without bars: (status, issues)."""
    joined = status.join(spans.select("code", "first_date"), on="code", how="left")
    early = joined.filter(pl.col("first_date").is_null() | (pl.col("date") < pl.col("first_date")))
    issues = issue_rows(
        early, "daily_status", "before_first_bar", "status_dropped", ["first_date", "tradestatus"], source
    )
    return _without(status, early), issues


def _without(frame: pl.DataFrame, rows: pl.DataFrame) -> pl.DataFrame:
    return frame.join(rows.select(KEY), on=KEY, how="anti") if rows.height else frame
