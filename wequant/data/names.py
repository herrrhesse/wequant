"""Stock names and official listing and delisting dates from BaoStock's stock_basic, for the securities table (S2.3).

Only codes the table already has change: a code without bars has no row there, and BaoStock has no Beijing codes.
"""

from __future__ import annotations

import polars as pl

from wequant.data.schema import TABLES

BASIC = {"code": pl.String, "name": pl.String, "list_date": pl.Date, "delist_date": pl.Date}


def basic_rows(stock_basic: pl.DataFrame) -> pl.DataFrame:
    """BASIC of each SH/SZ stock in a stock_basic response (its raw string columns); empty text is null."""
    stocks = stock_basic.filter((pl.col("type") == "1") & pl.col("code").str.contains(r"^s[hz]\.[0-9]{6}$"))
    text = lambda column: pl.when(pl.col(column) != "").then(pl.col(column))  # noqa: E731
    return stocks.select(
        code=pl.col("code").str.slice(3) + "." + pl.col("code").str.slice(0, 2).str.to_uppercase(),  # sh.600000
        name=text("code_name"),
        list_date=text("ipoDate").str.to_date(),
        delist_date=text("outDate").str.to_date(),
    ).cast(BASIC)


def with_basic(securities: pl.DataFrame, basic: pl.DataFrame) -> pl.DataFrame:
    """securities with each listed code's name, listing date and delisting date (null: listed again) from basic; a
    name basic lacks keeps the stored one, and codes basic does not list stay as they are."""
    new = basic.rename({c: f"{c}_new" for c in BASIC if c != "code"}).with_columns(listed=pl.lit(True))
    joined = securities.join(new, on="code", how="left", maintain_order="left")
    listed = pl.col("listed").fill_null(False)
    return joined.with_columns(
        name=pl.coalesce("name_new", "name"),
        list_date=pl.when(listed).then("list_date_new").otherwise("list_date"),
        delist_date=pl.when(listed).then("delist_date_new").otherwise("delist_date"),
    ).select(list(TABLES["securities"].schema))
