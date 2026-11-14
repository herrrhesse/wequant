"""Member spans from BaoStock's member lists (round 3): a list dated L names an index's members from L on."""

import datetime as dt

import polars as pl

from wequant.data.members import member_rows
from wequant.data.schema import TABLES

BEFORE = dt.date(2024, 1, 5)  # the store's last day
DAYS = [dt.date(2024, 1, 8), dt.date(2024, 1, 9), dt.date(2024, 1, 10)]  # merged by the update
SPANS = pl.DataFrame(
    [
        ("399300.SZ", "600000.SH", dt.date(2023, 1, 3), BEFORE),  # stays
        ("399300.SZ", "600001.SH", dt.date(2023, 1, 3), BEFORE),  # leaves
        ("399300.SZ", "600002.SH", dt.date(2023, 1, 3), dt.date(2023, 6, 30)),  # left long ago
        ("000905.SH", "600003.SH", dt.date(2023, 1, 3), BEFORE),  # another index
    ],
    schema=TABLES["index_members"].schema,
    orient="row",
)


def rows(listed: dt.date) -> set[tuple]:
    return set(member_rows(SPANS, "399300.SZ", ["600000.SH", "600004.SH"], listed, BEFORE, DAYS).rows())


def test_a_list_dated_within_the_new_days_ends_the_leavers_the_day_before_and_starts_the_joiners_that_day():
    assert rows(dt.date(2024, 1, 9)) == {
        ("399300.SZ", "600000.SH", dt.date(2023, 1, 3), dt.date(2024, 1, 10)),
        ("399300.SZ", "600001.SH", dt.date(2023, 1, 3), dt.date(2024, 1, 8)),
        ("399300.SZ", "600004.SH", dt.date(2024, 1, 9), dt.date(2024, 1, 10)),
    }


def test_a_list_dated_before_the_new_days_holds_on_all_of_them():
    """The leaver's span already ended on the store's last day: it is not rewritten."""
    assert rows(dt.date(2023, 12, 15)) == {
        ("399300.SZ", "600000.SH", dt.date(2023, 1, 3), dt.date(2024, 1, 10)),
        ("399300.SZ", "600004.SH", dt.date(2024, 1, 8), dt.date(2024, 1, 10)),
    }
