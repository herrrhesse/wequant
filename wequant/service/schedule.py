"""strategy schedule: a strategy's next rebalance days, by its period, interval and anchor, so that the frontend never
computes dates itself. Days come from the store's calendar, then BaoStock's published calendar in the raw layer, then
weekdays, which are estimates: holidays may move them."""

from __future__ import annotations

import datetime as dt
from typing import Literal

import numpy as np
import polars as pl
from pydantic import Field

from wequant.data.raw import BaoStockRaw
from wequant.service.base import Context, DataRequest, Model, manifest, named, read_version
from wequant.service.strategies import StrategyInput, resolve
from wequant.strategy.schedule import upcoming

Source = Literal["stored", "calendar", "estimated"]
ESTIMATED_DAYS = 400  # weekdays added after the last known day: enough for a few monthly decisions


class ScheduleRequest(StrategyInput, DataRequest):
    """A strategy's next rebalance days."""

    count: int = Field(3, ge=1, le=20)


class ScheduledDay(Model):
    """One rebalance day: its close decides, the next trading day trades."""

    date: dt.date
    source: Source
    """stored: a trading day of the store; calendar: BaoStock's published calendar; estimated: a weekday after it,
    which a holiday may move (labels.day_sources)."""


class Schedule(Model):
    """The next count rebalance days on or after the store's last trading day."""

    data_version: int
    data_version_name: str
    period: str
    every: int
    days: list[ScheduledDay]
    calendar_until: dt.date | None
    """The last day BaoStock's published calendar covers; null without it in the raw layer."""


def strategy_schedule(request: ScheduleRequest, context: Context = Context()) -> Schedule:
    """The strategy's next rebalance days: counted as a backtest from its anchor (else the store's first day) counts
    them, the store's last trading day included."""
    strategy, _ = resolve(request, context.library())
    store = context.store()
    found = manifest(store, read_version(context, request.data_version))
    stored = store.read_table("calendar", columns=["date"], manifest=found)["date"].sort()
    published, until = _published(BaoStockRaw.at(store.home).trade_dates, stored[-1])
    estimated = _weekdays(max([stored[-1], *published, *([until] if until else [])]))
    known = pl.concat([_days(stored, "stored"), _days(published, "calendar"), _days(estimated, "estimated")])
    if strategy.anchor is not None:
        known = known.filter(pl.col("date") >= strategy.anchor)
    dates, first = known["date"].to_numpy().astype("datetime64[D]"), known["date"].search_sorted(stored[-1])
    rows = upcoming(dates, 0, strategy.period, strategy.every, first, request.count)
    picked = [ScheduledDay(**known.row(row, named=True)) for row in rows.tolist()]
    fields = {"period": strategy.period, "every": strategy.every, "calendar_until": until}
    return Schedule(**named(found), **fields, days=picked)


def _days(dates, source: Source) -> pl.DataFrame:
    """dates with their source."""
    return pl.DataFrame({"date": pl.Series(dates, dtype=pl.Date)}).with_columns(source=pl.lit(source, pl.String))


def _published(path, last: dt.date) -> tuple[list[dt.date], dt.date | None]:
    """(BaoStock's published trading days after last, the last day its calendar covers); none without the file."""
    if not path.exists():
        return [], None
    dates = pl.read_parquet(path, columns=["calendar_date", "is_trading_day"])
    days = dates.with_columns(day=pl.col("calendar_date").str.to_date())
    trading = days.filter((pl.col("is_trading_day") == "1") & (pl.col("day") > last))["day"].sort().to_list()
    return trading, days["day"].max()


def _weekdays(after: dt.date) -> list[dt.date]:
    """ESTIMATED_DAYS weekdays after a day."""
    first = np.datetime64(after, "D") + np.timedelta64(1, "D")
    days = np.arange(first, first + np.timedelta64(ESTIMATED_DAYS, "D"))
    return [d.item() for d in days[np.is_busday(days)]]
