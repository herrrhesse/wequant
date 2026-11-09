"""Conflicts the merge cannot settle by itself, and the exchange's next-day view of a disputed close."""

from __future__ import annotations

import polars as pl

from wequant.data.merge.audit import VOLUME_RTOL
from wequant.data.merge.daily import paid
from wequant.data.raw import BaoStockRaw
from wequant.data.sources.baostock import parse_daily

KEY = ["code", "date"]
OHLC = ("open", "high", "low", "close")
SEEDED = (*OHLC, "volume", "amount")
CONFLICT = (*KEY, "kind", *SEEDED, *(f"seed_{c}" for c in SEEDED), "seed_paid", "same_volume")
REFERENCE = (*KEY, "next_date", "next_preclose", "ex_event")


def find_conflicts(bars: pl.DataFrame, old: pl.DataFrame) -> pl.DataFrame:
    """CONFLICT rows, the store's bar that day as seed_*: price_mismatch where open, high, low or close differ from it,
    average_price where the amount fails paid()."""
    store = old.select(*KEY, *(pl.col(c).alias(f"seed_{c}") for c in SEEDED))
    joined = bars.join(store, on=KEY, how="left").with_columns(
        seed_paid=paid("seed_").fill_null(False),
        same_volume=(pl.col("volume") - pl.col("seed_volume")).abs() <= VOLUME_RTOL * pl.col("seed_volume"),
    )
    price = pl.any_horizontal(pl.col(c) != pl.col(f"seed_{c}") for c in OHLC).fill_null(False)
    found = [
        joined.filter(price).with_columns(kind=pl.lit("price_mismatch")),
        joined.filter(~paid().fill_null(False)).with_columns(kind=pl.lit("average_price")),
    ]
    return pl.concat(found).select(CONFLICT).sort(*KEY, "kind")


def next_precloses(raw: BaoStockRaw, rows: pl.DataFrame, calendar: pl.Series, adj: pl.DataFrame) -> pl.DataFrame:
    """REFERENCE rows for rows' (code, date): next_date is the next trading day (store calendar or raw layer),
    next_preclose the code's exchange preclose that day in the raw layer (null without one), and ex_event whether
    adj_factor has a step for the code in (date, next_date], which spoils the comparison."""
    on_disk = pl.Series("next_date", sorted(raw.daily_days()), dtype=pl.Date)
    days = pl.DataFrame(pl.concat([calendar.rename("next_date"), on_disk]).unique().sort())
    keyed = rows.select(KEY).unique().sort("date")
    keyed = keyed.join_asof(days, left_on="date", right_on="next_date", strategy="forward", allow_exact_matches=False)
    between = (pl.col("ex_date") > pl.col("date")) & (pl.col("ex_date") <= pl.col("next_date"))
    spoiled = keyed.join(_steps(adj), on="code").filter(between).select(*KEY, ex_event=pl.lit(True)).unique()
    return (
        keyed.join(_precloses(raw, keyed["next_date"]), on=["code", "next_date"], how="left")
        .join(spoiled, on=KEY, how="left")
        .with_columns(pl.col("ex_event").fill_null(False))
        .select(REFERENCE)
        .sort(KEY)
    )


def _steps(adj: pl.DataFrame) -> pl.DataFrame:
    """code, ex_date of each adj_factor row whose factor differs from the code's previous row."""
    previous = pl.col("factor").shift(1).over("code")
    return adj.sort("code", "ex_date").filter(pl.col("factor") != previous).select("code", "ex_date")


def _precloses(raw: BaoStockRaw, days: pl.Series) -> pl.DataFrame:
    """code, next_date, next_preclose of every row in the raw snapshots of days."""
    paths = [str(raw.daily(day)) for day in days.drop_nulls().unique().sort() if raw.daily(day).exists()]
    if not paths:
        return pl.DataFrame(schema={"code": pl.String, "next_date": pl.Date, "next_preclose": pl.Int32})
    daily = parse_daily(pl.scan_parquet(paths))
    return daily.select("code", next_date="date", next_preclose="preclose").collect()
