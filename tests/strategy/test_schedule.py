"""Decision rows: hand-computed calendars of the base day, then every k-th day, week end or month end before the last."""

import numpy as np
import pytest

from wequant.strategy import decision_rows

JANUARY = ["01-02", "01-03", "01-04", "01-05", "01-08", "01-09", "01-10", "01-11", "01-12", "01-15"]
MONTHS = ["01-29", "01-30", "01-31", "02-01", "02-02", "02-05", "02-28", "02-29", "03-01", "03-04"]


def days(month_days):
    return np.array([f"2024-{d}" for d in month_days], dtype="datetime64[D]")


@pytest.mark.parametrize(
    "dates, base, last, period, every, rows",
    [
        (JANUARY, 0, 9, "day", 3, [0, 3, 6]),
        (JANUARY, 0, 9, "week", 1, [0, 3, 8]),
        (JANUARY, 2, 9, "week", 1, [2, 3, 8]),
        (JANUARY, 0, 7, "week", 1, [0, 3]),  # ends on Thursday 01-11: that week's end is not known to be one
        (MONTHS, 0, 9, "month", 1, [0, 2, 7]),
    ],
    ids=["every_3rd_day", "week_ends", "from_a_later_base", "an_unfinished_week", "month_ends"],
)
def test_decision_rows_golden(dates, base, last, period, every, rows):
    got = decision_rows(days(dates), base, last, period, every)
    assert got.dtype == np.int64 and got.tolist() == rows
