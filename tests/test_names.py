"""Names and official dates from BaoStock's stock_basic into securities (S2.3): each test names its rule."""

import datetime as dt

import polars as pl

from tests.baostock_fake import STOCK_BASIC
from tests.frames import table_frame
from wequant.data.names import basic_rows, with_basic

D = dt.date(2024, 1, 2)


def stock_basic(*rows: list[str]) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema={c: pl.String for c in STOCK_BASIC}, orient="row")


def securities(*rows: tuple[str, str | None, dt.date | None, dt.date | None]) -> pl.DataFrame:
    """Rows of (code, name, list_date, delist_date)."""
    fields = ("code", "name", "list_date", "delist_date")
    return table_frame("securities", [dict(zip(fields, row)) | {"first_date": D, "last_date": D} for row in rows],
                       exchange="SH", board="main", source="t")  # fmt: skip


def test_basic_rows_are_the_sh_and_sz_stocks_with_empty_text_as_null():
    found = basic_rows(
        stock_basic(
            ["sh.600000", "甲", "2000-01-04", "", "1", "1"],
            ["sz.000002", "", "2002-01-04", "2020-06-30", "1", "0"],
            ["sh.000001", "上证指数", "1991-07-15", "", "2", "1"],  # an index
            ["bj.920001", "北甲", "2021-11-15", "", "1", "1"],  # no Beijing codes in the store's BaoStock layer
        )
    )
    assert found.rows() == [
        ("600000.SH", "甲", dt.date(2000, 1, 4), None),
        ("000002.SZ", None, dt.date(2002, 1, 4), dt.date(2020, 6, 30)),
    ]


def test_a_listed_code_takes_the_name_and_both_official_dates_of_stock_basic():
    basic = basic_rows(stock_basic(["sh.600000", "新名", "2000-01-04", "", "1", "1"]))
    before = securities(("600000.SH", "旧名", None, dt.date(2023, 1, 3)))  # listed again since: no delisting date
    after = with_basic(before, basic)
    assert after.select("code", "name", "list_date", "delist_date").rows() == [
        ("600000.SH", "新名", dt.date(2000, 1, 4), None)
    ]


def test_a_name_stock_basic_lacks_keeps_the_stored_one_and_unlisted_codes_stay():
    basic = basic_rows(stock_basic(["sh.600000", "", "2000-01-04", "", "1", "1"]))
    before = securities(("600000.SH", "旧名", None, None), ("600001.SH", "乙", D, None))
    after = with_basic(before, basic)
    assert after.select("code", "name", "list_date").rows() == [
        ("600000.SH", "旧名", dt.date(2000, 1, 4)),
        ("600001.SH", "乙", D),  # stock_basic does not list it: unchanged
    ]
