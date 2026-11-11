"""data calendar: the trading days of one store version (F-27), for date pickers that allow trading days only."""

from __future__ import annotations

import datetime as dt

from wequant.service.base import Context, DataRequest, Model, manifest
from wequant.service.specs import IsoDate


class CalendarRequest(DataRequest):
    """Trading days within [start, end]."""

    start: IsoDate | None = None
    """First day (default: the first trading day)."""
    end: IsoDate | None = None
    """Last day (default: the last trading day)."""


class Calendar(Model):
    """A store version's trading days."""

    data_version: int
    first: dt.date | None
    """The version's first trading day, whatever the range; null: an empty calendar."""
    last: dt.date | None
    """Its last trading day."""
    dates: list[dt.date]
    """The trading days in the range, ascending."""


def data_calendar(request: CalendarRequest, context: Context = Context()) -> Calendar:
    """The version's first and last trading day and its trading days within the range."""
    store = context.store()
    found = manifest(store, request.data_version)
    days = store.read_table("calendar", manifest=found)["date"].sort()
    within = days.filter(days.is_between(request.start or dt.date.min, request.end or dt.date.max))
    first, last = (days.first(), days.last()) if days.len() else (None, None)
    return Calendar(data_version=found["version"], first=first, last=last, dates=within.to_list())
