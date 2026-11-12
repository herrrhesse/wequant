"""strategy schedule and data index-bars (round 2): a strategy's next rebalance days, and indices' daily bars with
their change. Synthetic homes only."""

import datetime as dt

import polars as pl
import pytest

from tests.service.conftest import DAYS
from wequant.data.raw import BaoStockRaw, raw_frame
from wequant.service import Context, IndexBarsRequest, ScheduleRequest, data_index_bars, strategy_schedule

JAN = {n: dt.date(2024, 1, n) for n in range(1, 32)}  # the market's last stored day is Thursday Jan 11


def schedule(home) -> list[tuple[dt.date, str]]:
    days = strategy_schedule(ScheduleRequest(saved={"id": "m"}), Context(home)).days  # m rebalances every week end
    return [(day.date, day.source) for day in days]


def test_without_a_calendar_each_week_s_rebalance_day_is_its_last_weekday_marked_estimated(market_home):
    assert schedule(market_home) == [(JAN[12], "estimated"), (JAN[19], "estimated"), (JAN[26], "estimated")]


def test_the_published_calendar_moves_a_rebalance_day_off_a_holiday(market_home):
    """BaoStock publishes to Sunday Jan 21 with Friday Jan 19 a holiday: that week ends on Thursday Jan 18."""
    published = [(JAN[n], "0" if n in (13, 14, 19, 20, 21) else "1") for n in range(12, 22)]
    frame = pl.DataFrame(
        [(d.isoformat(), t) for d, t in published], schema=["calendar_date", "is_trading_day"], orient="row"
    )
    raw = BaoStockRaw.at(market_home)
    raw.write(raw.trade_dates, raw_frame(frame, "query_trade_dates()", "test"))
    assert schedule(market_home) == [(JAN[12], "calendar"), (JAN[18], "calendar"), (JAN[26], "estimated")]


def test_an_index_day_changes_from_the_stored_day_before_the_first_in_range_too(market_home):
    """The CSI 300 rises 1% a day; 000905.SH is not stored."""
    result = data_index_bars(IndexBarsRequest(codes=["000300.SH", "000905.SH"], start=DAYS[2]), Context(market_home))
    assert [row.date.isoformat() for row in result.rows] == DAYS[2:] and result.missing == ["000905.SH"]
    assert all(row.change == pytest.approx(0.01, abs=1e-6) for row in result.rows)
    assert result.rows[0].close == pytest.approx(3000 * 1.01**2, abs=1e-3)
