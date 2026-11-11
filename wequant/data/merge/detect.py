"""Detections across BaoStock rows that no one-row check sees: a snapshot cut before the close, an amount carried over
from the previous row, a suspended row's stale volume, and closes the next day's exchange preclose does not repeat."""

from __future__ import annotations

import polars as pl

from wequant.data.issues import issue_rows

KEY = ["code", "date"]
EXCHANGE = pl.col("code").str.slice(-2)
SHORT_RTOL = 1e-3  # a volume this far below the seed's is short, well beyond the seed's float rounding
# A day's snapshot looks cut when both its share and its count of telling rows pass these; history: on sound days at
# most 0.8% (4 rows) short of the seed and 1.3% (9 rows) of closes off; 2003-11-17 SH 95.8% (723) short and 25.8%
# (195) off, 2008-10-24 SZ 4.9% (34) short.
TRUNCATED = {"share": 0.03, "rows": 10}  # volumes short of the seed's, among traded rows with a seed bar
CLOSE_OFF = {"share": 0.05, "rows": 20}  # closes the next exchange preclose does not repeat, among checked rows
FLAGS = ("close_preclose_off", "snapshot_truncated")  # state kinds: every merge recomputes them over all bars
MARKS = ("stale", "truncated", "carried")


def marks(bars: pl.DataFrame, status: pl.DataFrame, previous: pl.DataFrame, old: pl.DataFrame) -> pl.DataFrame:
    """KEY and MARKS of each bar: a suspended row (tradestatus 0, amount 0) with a volume, a volume short of the seed's
    on a truncated day, an amount repeating the code's previous bar to the fen at another volume."""
    rows = bars.select(*KEY, "volume", "amount").join(status.select(*KEY, "tradestatus"), on=KEY, how="left")
    rows = _short(rows, old).with_columns(stale=(pl.col("tradestatus") == 0) & (pl.col("amount") == 0))
    days = truncated_days(bars, old).select("date", "exchange", alarm=pl.lit(True))
    rows = rows.with_columns(exchange=EXCHANGE).join(days, on=["date", "exchange"], how="left")
    rows = rows.with_columns(truncated=pl.col("short") & pl.col("alarm").fill_null(False))
    rows = rows.join(_carried(bars, previous), on=KEY, how="left")
    return rows.select(*KEY, *(pl.col(c).fill_null(False) for c in MARKS)).sort(KEY)


def truncated_days(bars: pl.DataFrame, old: pl.DataFrame) -> pl.DataFrame:
    """date, exchange, rows (traded with a seed bar), short, share of each exchange-day whose volumes short of the
    seed's reach TRUNCATED."""
    rows = _short(bars.select(*KEY, "volume"), old).filter(pl.col("seed_volume").is_not_null())
    days = rows.group_by("date", exchange=EXCHANGE).agg(rows=pl.len(), short=pl.col("short").sum())
    days = days.with_columns(share=pl.col("short") / pl.col("rows"))
    return days.filter(_reaches("short", TRUNCATED)).sort("date", "exchange")


def close_flags(checked: pl.DataFrame, after: pl.DataFrame, source: str) -> pl.DataFrame:
    """close_preclose_off on each bar whose close the next bar's exchange preclose does not repeat without a factor
    step between, and snapshot_truncated on every bar of an exchange-day where such closes reach CLOSE_OFF."""
    rows = checked.filter(pl.col("factor_step") == 1).select(
        "code",
        date=pl.col("prev_date"),
        close=pl.col("prev_close"),
        next_date=pl.col("date"),
        next_preclose=pl.col("preclose"),
        off=pl.col("preclose") != pl.col("prev_close"),
    )
    days = rows.group_by("date", exchange=EXCHANGE).agg(checked=pl.len(), off=pl.col("off").sum())
    days = days.with_columns(share=pl.col("off") / pl.col("checked")).filter(_reaches("off", CLOSE_OFF))
    cut = after.select(KEY).with_columns(exchange=EXCHANGE).join(days, on=["date", "exchange"])
    off = rows.filter("off")
    return pl.concat(
        [
            issue_rows(off, "bars", FLAGS[0], "flagged", ["close", "next_date", "next_preclose"], source),
            issue_rows(cut, "bars", FLAGS[1], "flagged", ["exchange", "checked", "off"], source),
        ]
    )


def _reaches(count: str, limits: dict) -> pl.Expr:
    return (pl.col("share") >= limits["share"]) & (pl.col(count) >= limits["rows"])


def _short(rows: pl.DataFrame, old: pl.DataFrame) -> pl.DataFrame:
    """rows plus seed_volume and short: the volume below the seed's by more than SHORT_RTOL."""
    rows = rows.join(old.select(*KEY, seed_volume="volume"), on=KEY, how="left")
    short = (pl.col("volume") < pl.col("seed_volume") * (1 - SHORT_RTOL)).fill_null(False)
    return rows.with_columns(short=short)


def _carried(bars: pl.DataFrame, previous: pl.DataFrame) -> pl.DataFrame:
    """KEY, carried of each bar: its amount repeats the code's previous bar (previous before the first) to the fen while
    the volume differs."""
    columns = [*KEY, "volume", "amount"]
    rows = pl.concat([previous.select(columns), bars.select(columns)]).sort(KEY)
    fen = (pl.col("amount") * 100).round(0)
    same = (fen == fen.shift(1).over("code")) & (pl.col("volume") != pl.col("volume").shift(1).over("code"))
    return rows.select(*KEY, carried=same.fill_null(False)).join(bars.select(KEY), on=KEY, how="semi")
