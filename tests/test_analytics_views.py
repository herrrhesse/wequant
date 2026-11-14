"""A run's day and period views on a hand-made ledger: four days over a month end, a benchmark close missing."""

import datetime as dt

import polars as pl
import pytest

from wequant.analytics import daily_view, period_view

DAYS = [dt.date(2024, 1, 30), dt.date(2024, 1, 31), dt.date(2024, 2, 1), dt.date(2024, 2, 2)]
DAILY = pl.DataFrame(
    {
        "date": DAYS,
        "equity": [100.0, 110.0, 99.0, 108.9],
        "cash": [100.0, 30.0, 59.0, 108.9],
        "bought": [0.0, 80.0, 0.0, 0.0],
        "sold": [0.0, 0.0, 30.0, 40.0],
        "benchmark": [10.0, 11.0, None, 12.0],
    }
)
POSITIONS = pl.DataFrame(
    {
        "date": [DAYS[1], DAYS[1], DAYS[2]],
        "code": ["A", "B", "A"],
        "shares": [5.0, 3.0, 4.0],
        "value": [50.0, 30.0, 40.0],
    }
)


def test_the_daily_view_normalises_to_the_base_day_and_measures_from_the_running_high():
    view = daily_view(DAILY, POSITIONS)
    assert view["positions"].to_list() == [0, 2, 1, 0] and view["invested"].to_list() == [0.0, 80.0, 40.0, 0.0]
    assert view["nav"].to_list() == pytest.approx([1.0, 1.1, 0.99, 1.089])
    assert view["benchmark_nav"].to_list()[2] is None and view["benchmark_nav"].to_list()[3] == pytest.approx(1.2)
    assert view["excess"].to_list()[3] == pytest.approx(1.089 / 1.2 - 1) and view["excess"].to_list()[2] is None
    assert view["drawdown"].to_list() == pytest.approx([0.0, 0.0, 0.1, 0.01])
    assert view["exposure"].to_list() == pytest.approx([0.0, 80 / 110, 40 / 99, 0.0])


def test_periods_compound_their_days_and_measure_drawdown_from_the_previous_periods_last_nav():
    months = period_view(DAILY, "month")
    assert months["period"].to_list() == ["2024-01", "2024-02"]
    assert months["return"].to_list() == pytest.approx([0.1, 0.9 * 1.1 - 1])
    assert months["benchmark_return"].to_list()[0] == pytest.approx(0.1) and months["benchmark_return"][1] is None
    assert months["excess"].to_list()[0] == pytest.approx(0.0) and months["excess"][1] is None
    assert months["max_drawdown"].to_list() == pytest.approx([0.0, 0.1])  # February falls from January's 1.10
    year = period_view(DAILY, "year").row(0, named=True)
    assert (year["period"], year["benchmark_return"]) == ("2024", None)
    assert (year["return"], year["max_drawdown"]) == pytest.approx((0.089, 0.1))


def test_a_benchmark_starting_after_the_base_day_is_drawn_from_its_first_close_at_the_navs_level():
    """S2.10b: no level before the benchmark's first close, the strategy's nav on that day (no excess), then the
    index's own moves from there."""
    late = DAILY.with_columns(benchmark=pl.Series([None, 20.0, None, 22.0]))
    view = daily_view(late, POSITIONS)
    line, gap = view["benchmark_nav"].to_list(), view["excess"].to_list()
    assert line[0] is None and gap[0] is None and line[2] is None and gap[2] is None
    assert (line[1], gap[1]) == (pytest.approx(1.1), pytest.approx(0.0))
    assert line[3] / line[1] == pytest.approx(22.0 / 20.0) and gap[3] == pytest.approx(1.089 / (1.1 * 1.1) - 1)
