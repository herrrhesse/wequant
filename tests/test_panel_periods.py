"""S4.8: weekly and monthly bars made from the daily ones, and their values placed back on the trading days."""

import numpy as np
import pytest

from tests.backtest.market import build_store
from wequant.expr import MA, MONTH, REF, WEEK, Field, evaluate
from wequant.panel import load_panel
from wequant.panel.periods import aggregate, period_ends, previous, starts
from wequant.strategy import decision_rows

NAN = np.nan
DAYS = [
    *("2024-12-23", "2024-12-24", "2024-12-25", "2024-12-26", "2024-12-27"),  # rows 0-4
    *("2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03"),  # 5-8: one week across the new year
    *("2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"),  # 9-13
    "2025-01-13",  # 14: a week the holidays cut to one day
    *("2025-01-20", "2025-01-21", "2025-01-22", "2025-01-23", "2025-01-24"),  # 15-19
]
X, Y = "600000.SH", "000001.SZ"
CLOSES = [10 + t for t in range(len(DAYS))]  # X closes at 10, 11, ... and opens 0.5 below
HALTED = {2, *range(9, 14)}  # Y does not trade on row 2 and the whole week of rows 9-13


def bars(closes):
    return {
        X: [(c - 0.5, c) for c in closes],
        Y: [None if t in HALTED else (2 * c - 1, 2 * c) for t, c in enumerate(closes)],
    }


@pytest.fixture
def panel(tmp_path):
    return load_panel(store=build_store(tmp_path / "home", DAYS, bars(CLOSES)), cache_dir=tmp_path / "cache")


def column(panel, expr, code):
    return evaluate(expr, panel)[:, list(panel.codes).index(code)]


def test_a_bar_gathers_the_days_its_stock_traded_in_the_period():
    """First open, highest high, lowest low, last close, summed volume; a stock without a day in a period has no bar."""
    traded = np.array([[True, False], [False, False], [True, False]])  # the second stock never trades
    values = np.array([[3.0, 9.0], [NAN, 9.0], [5.0, 9.0]])
    found = {how: aggregate(values, traded, np.array([0]), how)[0] for how in ("first", "max", "min", "last")}
    assert {how: bar[0] for how, bar in found.items()} == {"first": 3.0, "max": 5.0, "min": 3.0, "last": 5.0}
    assert all(np.isnan(bar[1]) for bar in found.values())
    total = aggregate(np.array([[100.0], [NAN], [250.0]]), traded[:, :1], np.array([0]), "sum")
    assert total.tolist() == [[350.0]]


def test_a_sum_is_unknown_when_a_traded_day_has_no_value():
    """An amount missing on a day the stock traded leaves the period's amount unknown rather than understated."""
    traded = np.array([[True], [True]])
    assert np.isnan(aggregate(np.array([[100.0], [NAN]]), traded, np.array([0]), "sum")[0, 0])


def test_a_natural_week_is_one_bar_across_the_new_year_and_holidays():
    assert starts(np.array(DAYS, dtype="datetime64[D]"), "week").tolist() == [0, 5, 9, 14, 15]
    assert starts(np.array(DAYS, dtype="datetime64[D]"), "month").tolist() == [0, 7]


def test_a_bar_preclose_is_the_previous_bar_close_skipping_periods_without_one():
    """Before its first bar a stock's preclose is the first day's daily preclose, given separately."""
    close = np.array([[10.0], [NAN], [12.0], [13.0]])
    first_day = np.array([[9.5], [NAN], [11.9], [12.8]])
    assert previous(close, first_day)[[0, 2, 3], 0].tolist() == [9.5, 10.0, 12.0]


def test_a_day_reads_the_last_week_ended_on_or_before_it(panel):
    """Days inside a week read the week before; a week's last trading day reads its own week. The weeks end on rows 4,
    8, 13, 14 and 19 (the store's last day, a Friday: no weekday of its week is left)."""
    expected = [NAN] * 4 + [14] + [14] * 3 + [18] + [18] * 4 + [23] + [24] + [24] * 4 + [29]
    assert np.array_equal(column(panel, WEEK(Field("close")), X), expected, equal_nan=True)


def test_a_week_without_a_bar_is_skipped_by_weekly_ref(panel):
    """Y did not trade in the week of rows 9-13: on row 14 the previous weekly bar is the week ending on row 8."""
    assert column(panel, WEEK(REF(Field("close"), 1)), Y)[14] == 2 * CLOSES[8]


@pytest.mark.parametrize("day", [11, 13])  # inside a week, and its last day
def test_no_weekly_value_depends_on_a_later_day(tmp_path, panel, day):
    later = [c if t <= day else c * 3 for t, c in enumerate(CLOSES)]
    other = load_panel(store=build_store(tmp_path / "other", DAYS, bars(later)), cache_dir=tmp_path / "other-cache")
    expr = WEEK(MA(Field("adj_close"), 2))
    assert np.array_equal(evaluate(expr, panel)[: day + 1], evaluate(expr, other)[: day + 1], equal_nan=True)


def test_a_panel_starting_inside_a_week_makes_that_week_from_its_own_days(tmp_path):
    store = build_store(tmp_path / "home", DAYS, bars(CLOSES))
    panel = load_panel("2024-12-25", store=store, cache_dir=tmp_path / "cache")  # row 2, a Wednesday
    assert column(panel, WEEK(Field("open")), X)[2] == CLOSES[2] - 0.5  # the week's first open is Wednesday's


@pytest.mark.parametrize(
    ("last", "after", "ended"),
    [
        ("2025-01-24", None, True),  # a Friday: no weekday of its week is left
        ("2025-01-22", None, False),  # a Wednesday: Thursday may trade
        ("2025-01-22", "2025-01-27", True),  # a calendar says Thursday and Friday are holidays
        ("2025-01-22", "2025-01-23", False),
    ],
)
def test_the_last_day_ends_its_week_when_the_next_trading_day_is_in_another(last, after, ended):
    """The next trading day is the one a calendar knows, else estimated as the next weekday."""
    dates = np.array(["2025-01-20", "2025-01-21", last], dtype="datetime64[D]")
    assert period_ends(dates, "week", after)[-1] == ended


def test_weekly_values_take_in_the_week_on_exactly_the_weekly_decision_days(panel):
    """The rebalance schedule's week ends are the days a weekly value changes to that day's own week."""
    rows = decision_rows(panel.dates, 0, len(panel.dates) - 1, "week", 1)[1:]
    weekly, close = column(panel, WEEK(Field("close")), X), column(panel, Field("close"), X)
    takes_in = np.flatnonzero(weekly == close)
    assert takes_in[takes_in < len(panel.dates) - 1].tolist() == rows.tolist()


def test_a_month_end_reads_its_month_and_the_next_month_reads_it_until_its_own_end(panel):
    monthly = column(panel, MONTH(Field("close")), X)
    assert monthly[6] == CLOSES[6] and set(monthly[7:].tolist()) == {CLOSES[6]}  # January has not ended
