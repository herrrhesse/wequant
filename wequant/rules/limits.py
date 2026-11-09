"""Price limits in 厘 from the rule book.

A limit is the preclose moved by the band and rounded half up to the tick, then kept at least one tick from the
preclose and at least one tick above zero. These tick guards follow market practice; the exchange text was not read.
"""

from __future__ import annotations

import polars as pl

from wequant.rules.book import RuleBook, default_book
from wequant.rules.rounding import round_half_up

LIMITED, FREE, UNKNOWN = 1, 2, 3  # limit_state: a band applies / a rule sets no limit / no rule, no preclose or voided
LIMITS_FORMAT = 1  # bump when limit values change for the same store and rule book
INPUT = {
    "code": pl.String,
    "date": pl.Date,
    "board": pl.String,
    "preclose": pl.Int32,
    "is_st": pl.Boolean,
    "list_date": pl.Date,
    "listing_day": pl.Int32,
}
OUTPUT = ("limit_up", "limit_down", "limit_rule", "limit_state")


def up_price(pre: pl.Expr, bp: pl.Expr, tick: pl.Expr) -> pl.Expr:
    """Upper limit in 厘 (Int64): pre raised by bp basis points, half up to the tick, at least one tick above pre."""
    return pl.max_horizontal(round_half_up(pre * (10000 + bp), 10000 * tick) * tick, pre + tick)


def down_price(pre: pl.Expr, bp: pl.Expr, tick: pl.Expr) -> pl.Expr:
    """Lower limit in 厘 (Int64): pre lowered by bp basis points, half up to the tick, between one tick and pre - tick."""
    rounded = round_half_up(pre * (10000 - bp), 10000 * tick) * tick
    return pl.max_horizontal(pl.min_horizontal(rounded, pre - tick), tick)


def price_limits(frame: pl.DataFrame, book: RuleBook | None = None) -> pl.DataFrame:
    """frame (INPUT columns, extras and order kept) plus limit_up, limit_down (Int32 厘, null unless LIMITED), limit_rule (rule id or null) and limit_state (Int8)."""
    book = book or default_book()
    inputs = frame.select(pl.col(c).cast(t) for c, t in INPUT.items())
    days = _in_force(inputs.select("board", "date").unique(), book.frame("band"), "date")
    days = _in_force(days, book.frame("lot").select("board", "since", "tick"), "date")
    listings = _in_force(inputs.select("board", "list_date").unique(), book.frame("new_listing"), "list_date")
    limits = (
        inputs.lazy()
        .join(days.lazy(), on=["board", "date"], how="left", maintain_order="left")
        .join(listings.lazy(), on=["board", "list_date"], how="left", maintain_order="left")
        .with_columns(**_bands())
        .with_columns(_state())
        .with_columns(**_limits())
        .select(OUTPUT)
        .collect()
    )
    return frame.hstack(limits)


def _in_force(keys: pl.DataFrame, rules: pl.DataFrame, on: str) -> pl.DataFrame:
    """keys (board, on) with the fields of the rule of that board in force on column on (null where none is)."""
    keys = keys.sort(on, nulls_last=True)
    joined = keys.join_asof(rules, left_on=on, right_on="since", by="board", check_sortedness=False)
    return joined.drop("since")


def _bands() -> dict[str, pl.Expr]:
    free = (pl.col("listing_day") <= pl.col("free_days")).fill_null(False)
    first = ((pl.col("listing_day") == 1) & pl.col("first_up_bp").is_not_null()).fill_null(False)
    bp = pl.when(pl.col("is_st").fill_null(False)).then(pl.col("st_bp")).otherwise(pl.col("normal_bp"))
    pre, tick = pl.col("preclose").cast(pl.Int64), pl.col("tick")
    return {
        "_free": free,
        "_up": up_price(pre, pl.when(first).then(pl.col("first_up_bp")).otherwise(bp), tick),
        "_down": down_price(pre, pl.when(first).then(pl.col("first_down_bp")).otherwise(bp), tick),
        "limit_rule": pl.when(free | first).then(pl.col("new_listing_id")).otherwise(pl.col("band_id")),
    }


def _state() -> pl.Expr:
    return (
        pl.when(pl.col("limit_rule").is_null())
        .then(UNKNOWN)
        .when(pl.col("_free"))
        .then(FREE)
        .when(pl.col("preclose").is_null() | pl.col("tick").is_null())
        .then(UNKNOWN)
        .otherwise(LIMITED)
        .cast(pl.Int8)
        .alias("limit_state")
    )


def _limits() -> dict[str, pl.Expr]:
    limited = pl.col("limit_state") == LIMITED
    return {
        "limit_up": pl.when(limited).then(pl.col("_up")).cast(pl.Int32),
        "limit_down": pl.when(limited).then(pl.col("_down")).cast(pl.Int32),
    }
