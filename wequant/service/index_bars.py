"""data index-bars: an index's daily bars from the index_bars table, with each day's change, for the today page."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

import polars as pl
from pydantic import Field

from wequant.service.base import Code, Context, DataRequest, Distinct, Model, manifest, named, read_version


class IndexBarsRequest(DataRequest):
    """Daily bars of a few indices over a date range."""

    codes: Annotated[list[Code], Distinct] = Field(min_length=1)
    """Index codes such as 000300.SH."""
    start: dt.date | None = None
    end: dt.date | None = None


class IndexBar(Model):
    """One index day: prices in index points, volume in shares, amount in yuan."""

    code: str
    date: dt.date
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    change: float | None
    """close over the index's previous stored close, minus 1; null on its first stored day."""


class IndexBars(Model):
    """The days of each index in the range, by code then date; codes without rows are listed apart."""

    data_version: int
    data_version_name: str
    rows: list[IndexBar]
    missing: list[str]
    """Codes the version has no bar of in the range."""


def data_index_bars(request: IndexBarsRequest, context: Context = Context()) -> IndexBars:
    """Each index's bars and daily change; the first day in the range changes from the stored day before it."""
    store = context.store()
    found = manifest(store, read_version(context, request.data_version))
    frame = store.scan("index_bars", codes=request.codes, end=request.end, manifest=found).sort("code", "date")
    points = {name: (pl.col(name) / 1000).round(3) for name in ("open", "high", "low", "close")}
    frame = frame.with_columns(change=pl.col("close") / pl.col("close").shift(1).over("code") - 1, **points)
    if request.start is not None:
        frame = frame.filter(pl.col("date") >= request.start)
    rows = frame.drop("source").collect()
    present = set(rows["code"])
    bars = [IndexBar(**row) for row in rows.iter_rows(named=True)]
    return IndexBars(**named(found), rows=bars, missing=[code for code in request.codes if code not in present])
