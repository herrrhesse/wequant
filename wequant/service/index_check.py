"""data index-check: read-only check of index_members' daily member counts against each index's nominal count."""

from __future__ import annotations

import datetime as dt
from typing import Literal

import numpy as np

from wequant.expr.vocab import INDEXES, STORED_AS
from wequant.panel import Panel, load_panel
from wequant.panel.coverage import NOMINAL, count_spans
from wequant.service.base import Check, Context, DataRequest, Model, manifest, named, read_version


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
    """Each index's coverage, counting the members member@ reads (a member code the store does not list is not
    counted); ok is False when any index has an anomalous span."""

    data_version: int
    data_version_name: str
    """The version's display name, YYYYMMDD.N: its last trade date, then which version of that day."""
    indexes: list[IndexCoverage]


def data_index_check(request: IndexCheckRequest, context: Context = Context()) -> IndexCheck:
    """Each index's daily member count over its stored span against its nominal size; ok False when any is off."""
    store = context.store()
    manifest(store, read_version(context, request.data_version))
    panel = load_panel(version=read_version(context, request.data_version), store=store, cache_dir=context.cache)
    indexes = [found for index in INDEXES if (found := _coverage(panel, index)) is not None]
    return IndexCheck(ok=not any(i.spans for i in indexes), **named(panel.manifest), indexes=indexes)


def _coverage(panel: Panel, index: str) -> IndexCoverage | None:
    """One index's coverage, None when the store has no member of it."""
    stored = STORED_AS.get(index, index)
    member = panel.member(stored)
    inside = np.flatnonzero((member >= 0).any(axis=1))
    if not inside.size:
        return None
    rows = slice(inside[0], inside[-1] + 1)
    nominal, dates = NOMINAL[index], panel.dates[rows]
    spans = count_spans(dates, (member[rows] == 1).sum(axis=1), nominal)
    return IndexCoverage(
        index=index,
        stored_as=stored,
        nominal=nominal,
        first=dates[0].item(),
        last=dates[-1].item(),
        days=len(dates),
        spans=[CountSpan(**span._asdict(), nominal=nominal) for span in spans],
    )
