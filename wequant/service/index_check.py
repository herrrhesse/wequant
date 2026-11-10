"""data index-check: read-only check of index_members' daily member counts against each index's nominal count."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from wequant.service.base import Check, DataRequest, Model


class IndexCheckRequest(DataRequest):
    """The index members of one store version."""


class CountSpan(Model):
    """A run of days whose member count is off the nominal count."""

    start: dt.date
    end: dt.date
    days: int
    count: int
    """Members on each day of the span."""
    nominal: int | None
    kind: Literal["short", "over", "empty"]


class IndexCoverage(Model):
    """One index's coverage and its anomalous spans."""

    index: str
    """The standard code, e.g. 000300.SH."""
    stored_as: str
    """The code its rows carry in index_members, e.g. 399300.SZ."""
    nominal: int | None
    """The nominal member count; null: none (CSI All Share)."""
    first: dt.date
    last: dt.date
    days: int
    spans: list[CountSpan]


class IndexCheck(Check):
    """Each index's coverage; ok is False when any index has an anomalous span."""

    data_version: int
    indexes: list[IndexCoverage]
