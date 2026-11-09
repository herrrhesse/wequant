"""Rows the merge drops before writing: duplicates of another code's bar, and status before a code's first bar."""

from __future__ import annotations

import polars as pl

from wequant.data.issues import issue_rows
from wequant.data.merge.audit import AMOUNT_RTOL

KEY = ["code", "date"]
MATCHED = ["date", "open", "high", "low", "close", "volume"]
BEFORE_FIRST_BAR = pl.col("first_date").is_null() | (pl.col("date") < pl.col("first_date"))


def drop_duplicate_codes(
    bars: pl.DataFrame, status: pl.DataFrame, stored: pl.DataFrame, source: str
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Drop a bar equal to another code's bar that day (amount within AMOUNT_RTOL) when only the other is in the
    store: (bars, status, issues)."""
    candidates = bars.filter(pl.col("volume").is_duplicated().over("date"))  # a cheap first pass
    in_store = candidates.join(stored.select(KEY), on=KEY, how="semi")
    pairs = candidates.join(stored.select(KEY), on=KEY, how="anti").join(
        in_store.select(*MATCHED, same_as="code", kept_amount="amount"), on=MATCHED
    )
    near = (pl.col("amount") - pl.col("kept_amount")).abs() <= AMOUNT_RTOL * pl.col("kept_amount").abs()
    dropped = pairs.filter(near).group_by(KEY).agg(pl.col("same_as").min()).sort(KEY)
    issues = issue_rows(dropped, "bars", "duplicate_code", "duplicate_dropped", ["same_as"], source)
    return _without(bars, dropped), _without(status, dropped), issues


def drop_status_before_first_bar(
    status: pl.DataFrame, spans: pl.DataFrame, source: str
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Drop daily_status rows dated before the code's first bar, all of them for a code without bars: (status, issues)."""
    early = status.join(spans.select("code", "first_date"), on="code", how="left").filter(BEFORE_FIRST_BAR)
    issues = issue_rows(
        early, "daily_status", "before_first_bar", "status_dropped", ["first_date", "tradestatus"], source
    )
    return _without(status, early), issues


def _without(frame: pl.DataFrame, rows: pl.DataFrame) -> pl.DataFrame:
    return frame.join(rows.select(KEY), on=KEY, how="anti") if rows.height else frame
