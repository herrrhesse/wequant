"""The limit check: bars outside their price limits become data_issues rows that void that day's limits."""

from __future__ import annotations

import polars as pl

from wequant.data.issues import issue_rows
from wequant.rules.limits import LIMITED, UNKNOWN

KIND = "limit_breach"
ACTION = "limits_voided"
KEY = ["table", "code", "date", "kind"]
DETAIL = (
    "ex_date",
    "gap_days",
    "high",
    "is_st",
    "limit_down",
    "limit_rule",
    "limit_up",
    "low",
    "preclose",
    "preclose_derived",
)


def limit_issues(frame: pl.DataFrame, source: str) -> pl.DataFrame:
    """data_issues rows (via issue_rows) for LIMITED rows whose high or low lies outside the limits, measured values as detail."""
    outside = (pl.col("high") > pl.col("limit_up")) | (pl.col("low") < pl.col("limit_down"))
    breaches = frame.filter((pl.col("limit_state") == LIMITED) & outside)
    return issue_rows(breaches, "bars", KIND, ACTION, DETAIL, source)


def missing_issues(wanted: pl.DataFrame, recorded: pl.DataFrame) -> pl.DataFrame:
    """Rows of wanted whose data_issues key (table, code, date, kind) is not in recorded."""
    return wanted.join(recorded.select(KEY), on=KEY, how="anti", maintain_order="left")


def void(frame: pl.DataFrame, issues: pl.DataFrame) -> pl.DataFrame:
    """frame with limits nulled and limit_state UNKNOWN where issues hold a limits_voided row."""
    voided = issues.filter(pl.col("action") == ACTION).select("code", "date").unique().with_columns(_void=True)
    kept = pl.col("_void").is_null()
    return (
        frame.join(voided, on=["code", "date"], how="left", maintain_order="left")
        .with_columns(
            limit_up=pl.when(kept).then(pl.col("limit_up")),
            limit_down=pl.when(kept).then(pl.col("limit_down")),
            limit_state=pl.when(kept).then(pl.col("limit_state")).otherwise(pl.lit(UNKNOWN, pl.Int8)),
        )
        .drop("_void")
    )
