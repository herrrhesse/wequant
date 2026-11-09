import datetime as dt

import polars as pl
import pytest

from tests.frames import table_frame
from wequant.data.schema import TABLES


def test_iso_strings_become_dates():
    calendar = table_frame("calendar", [{"date": "2024-01-02"}, {"date": "2024-01-03"}])
    assert calendar.schema == {"date": pl.Date}
    assert calendar["date"].to_list() == [dt.date(2024, 1, 2), dt.date(2024, 1, 3)]


def test_constants_fill_whole_columns_and_the_rest_is_null():
    bars = table_frame(
        "bars",
        [{"code": "600000.SH", "date": "2024-01-02", "open": 10, "high": 10, "low": 10, "close": 10}],
        volume=100,
        amount=1000.0,
        preclose_derived=True,
        source="test",
    )
    row = bars.row(0, named=True)
    assert row["volume"] == 100 and row["source"] == "test" and row["preclose"] is None and row["is_st"] is None


def test_missing_non_null_columns_raise():
    with pytest.raises(ValueError, match="has nulls"):
        table_frame("securities", [{"code": "600000.SH"}])


def test_no_rows_give_an_empty_frame_with_the_schema():
    bars = table_frame("bars", [])
    assert bars.height == 0 and dict(bars.schema) == TABLES["bars"].schema
