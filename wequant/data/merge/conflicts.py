"""Conflicts the merge cannot settle by itself, and the exchange's next-day view of a disputed close."""

from __future__ import annotations

import polars as pl

from wequant.data.merge.audit import AMOUNT_RTOL, OHLC, VOLUME_RTOL
from wequant.data.merge.daily import PRECLOSE_TEXT, check, paid
from wequant.data.raw import BaoStockRaw
from wequant.data.sources.baostock import parse_daily

KEY = ["code", "date"]
SEEDED = (*OHLC, "volume", "amount")
CONFLICT = (*KEY, "kind", *SEEDED, "preclose", "turn", *(f"seed_{c}" for c in SEEDED), "seed_paid", "same_volume")
REFERENCE = ("next_date", "fetched", "next_preclose", "ex_event")
PRICE_KINDS = ("price_mismatch", "preclose_odd")  # settled first; the amount kinds see the prices they leave


def price_conflicts(bars: pl.DataFrame, old: pl.DataFrame) -> pl.DataFrame:
    """CONFLICT rows of the bars: price_mismatch where open, high, low or close differ from the store's bar that day
    (seed_*), preclose_odd where BaoStock's exchange preclose is not whole fen."""
    rows = _joined(bars, old)
    differ = pl.any_horizontal(pl.col(c) != pl.col(f"seed_{c}") for c in OHLC).fill_null(False)
    kinds = {"price_mismatch": differ, "preclose_odd": pl.col("preclose") % 10 != 0}
    return pl.concat(rows.filter(ok).with_columns(kind=pl.lit(k)).select(CONFLICT) for k, ok in kinds.items()).sort(
        *KEY, "kind"
    )


def amount_conflicts(bars: pl.DataFrame, old: pl.DataFrame, marks: pl.DataFrame) -> pl.DataFrame:
    """CONFLICT rows of the bars whose volume or amount the merge cannot keep, one kind each, first match: stale_volume
    and volume_truncated by their marks, amount_carried unless the seed's amount agrees, average_price where paid()
    fails."""
    rows = _joined(bars, old).join(marks, on=KEY, how="left")
    seed_agrees = (pl.col("amount") - pl.col("seed_amount")).abs() <= AMOUNT_RTOL * pl.col("seed_amount")
    kind = (
        pl.when(pl.col("stale"))
        .then(pl.lit("stale_volume"))
        .when(pl.col("truncated"))
        .then(pl.lit("volume_truncated"))
        .when(pl.col("carried") & ~seed_agrees.fill_null(False))
        .then(pl.lit("amount_carried"))
        .when(~paid().fill_null(False))
        .then(pl.lit("average_price"))
    )
    return rows.with_columns(kind=kind).filter(pl.col("kind").is_not_null()).select(CONFLICT).sort(KEY)


def with_references(raw: BaoStockRaw, rows: pl.DataFrame, calendar: pl.Series, adj: pl.DataFrame) -> pl.DataFrame:
    """rows plus REFERENCE: the next trading day (store calendar or raw layer), whether its snapshot is fetched, the
    code's exchange preclose that day, and whether an adj_factor step in (date, next_date] spoils the comparison."""
    on_disk = pl.Series("next_date", sorted(raw.daily_days()), dtype=pl.Date)
    days = pl.DataFrame(pl.concat([calendar.rename("next_date"), on_disk]).unique().sort())
    keyed = rows.select(KEY).unique().sort("date")
    keyed = keyed.join_asof(days, left_on="date", right_on="next_date", strategy="forward", allow_exact_matches=False)
    keyed = keyed.with_columns(fetched=pl.col("next_date").is_in(on_disk.implode()).fill_null(False))
    between = (pl.col("ex_date") > pl.col("date")) & (pl.col("ex_date") <= pl.col("next_date"))
    spoiled = keyed.join(_steps(adj), on="code").filter(between).select(*KEY, ex_event=pl.lit(True)).unique()
    references = (
        keyed.join(_precloses(raw, keyed), on=["code", "next_date"], how="left")
        .join(spoiled, on=KEY, how="left")
        .with_columns(pl.col("ex_event").fill_null(False))
    )
    return rows.join(references.select(*KEY, *REFERENCE), on=KEY, how="left")


def _joined(bars: pl.DataFrame, old: pl.DataFrame) -> pl.DataFrame:
    """bars plus the store's bar that day as seed_*, whether it passes paid() and whether the volumes agree."""
    store = old.select(*KEY, *(pl.col(c).alias(f"seed_{c}") for c in SEEDED))
    return bars.join(store, on=KEY, how="left").with_columns(
        seed_paid=paid("seed_").fill_null(False),
        same_volume=(pl.col("volume") - pl.col("seed_volume")).abs() <= VOLUME_RTOL * pl.col("seed_volume"),
    )


def _steps(adj: pl.DataFrame) -> pl.DataFrame:
    """code, ex_date of each adj_factor row whose factor differs from the code's previous one (1.0 before the first)."""
    previous = pl.col("factor").shift(1, fill_value=1.0).over("code")
    return adj.sort("code", "ex_date").filter(pl.col("factor") != previous).select("code", "ex_date")


def _precloses(raw: BaoStockRaw, keyed: pl.DataFrame) -> pl.DataFrame:
    """code, next_date, next_preclose of keyed's fetched (code, next_date); RawDataError when such a preclose breaks
    the price convention."""
    days = keyed.filter("fetched")["next_date"].unique().sort()
    if days.is_empty():
        return pl.DataFrame(schema={"code": pl.String, "next_date": pl.Date, "next_preclose": pl.Int32})
    scan = pl.scan_parquet([str(raw.daily(day)) for day in days])
    parsed = parse_daily(scan).select("code", next_date="date", next_preclose="preclose")
    rows = pl.concat([parsed, scan.select(text="preclose")], how="horizontal")
    rows = rows.join(keyed.lazy().select("code", "next_date"), on=["code", "next_date"], how="semi")
    check(rows, {"reference preclose": pl.col("text").str.contains(PRECLOSE_TEXT)})
    return rows.drop("text").collect()
