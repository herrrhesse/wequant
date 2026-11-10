"""Decision rows: hand-computed calendars, and the period-end rule as a property."""

import datetime as dt

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from wequant.analytics.series import period_keys
from wequant.strategy import PERIODS, decision_rows

JANUARY = ["01-02", "01-03", "01-04", "01-05", "01-08", "01-09", "01-10", "01-11", "01-12", "01-15"]
MONTHS = ["01-29", "01-30", "01-31", "02-01", "02-02", "02-05", "02-28", "02-29", "03-01", "03-04"]


def days(month_days):
    return np.array([f"2024-{d}" for d in month_days], dtype="datetime64[D]")


@pytest.mark.parametrize(
    "dates, base, last, period, every, rows",
    [
        (JANUARY, 0, 9, "day", 3, [0, 3, 6]),
        (JANUARY, 0, 9, "week", 1, [0, 3, 8]),
        (JANUARY, 0, 9, "week", 2, [0, 8]),
        (JANUARY, 2, 9, "week", 1, [2, 3, 8]),
        (JANUARY, 0, 7, "week", 1, [0, 3]),  # ends on Thursday 01-11: that week's end is not known to be one
        (MONTHS, 0, 9, "month", 1, [0, 2, 7]),
        (MONTHS, 0, 9, "month", 2, [0, 7]),
    ],
)
def test_decision_rows_golden(dates, base, last, period, every, rows):
    got = decision_rows(days(dates), base, last, period, every)
    assert got.dtype == np.int64 and got.tolist() == rows


@given(
    st.lists(st.dates(dt.date(2023, 1, 1), dt.date(2024, 12, 31)), min_size=2, max_size=60, unique=True),
    st.sampled_from(PERIODS),
    st.integers(1, 4),
    st.data(),
)
def test_rows_start_at_base_stay_before_last_and_hold_every_period_end(dates, period, every, data):
    dates = np.array(sorted(dates), dtype="datetime64[D]")
    base = data.draw(st.integers(0, len(dates) - 2))
    last = data.draw(st.integers(base + 1, len(dates) - 1))
    rows = decision_rows(dates, base, last, period, every)
    assert rows[0] == base and np.all(np.diff(rows) > 0) and rows[-1] <= last - 1
    if every == 1:
        keys = np.arange(len(dates)) if period == "day" else period_keys(dates, period)
        ends = [r for r in range(base + 1, last) if keys[r] != keys[r + 1]]
        assert set(ends) <= set(rows.tolist())
