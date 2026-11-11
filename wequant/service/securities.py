"""data securities: codes of one store version matching a typed query (F-12, F-27), for search boxes and the "no
local data" check."""

from __future__ import annotations

import datetime as dt

import polars as pl
from pydantic import Field

from wequant.service.base import Context, DataRequest, Model, manifest, named

COLUMNS = ("code", "name", "exchange", "board", "first_date", "last_date")


class SecuritiesRequest(DataRequest):
    """Codes matching query, in code order."""

    query: str = ""
    """A code prefix (600, 600000, 600000.SH; any case) or part of a name; empty: every code."""
    limit: int = Field(20, ge=1, le=100)


class SecurityRow(Model):
    """One listed or delisted code."""

    code: str
    name: str | None
    """null until a source provides names."""
    exchange: str
    board: str
    first_date: dt.date
    last_date: dt.date


class Securities(Model):
    """The first limit matches and how many there are."""

    data_version: int
    data_version_name: str
    """The version's display name, YYYYMMDD.N: its last trade date, then which version of that day."""
    total: int
    rows: list[SecurityRow]


def data_securities(request: SecuritiesRequest, context: Context = Context()) -> Securities:
    """Codes whose code starts with the query or whose name contains it."""
    store = context.store()
    found = manifest(store, request.data_version)
    query = request.query.strip()
    matches = pl.col("code").str.starts_with(query.upper()) | pl.col("name").str.contains(query, literal=True)
    rows = store.read_table("securities", manifest=found).filter(matches.fill_null(False)).sort("code")
    listed = [SecurityRow(**row) for row in rows.head(request.limit).select(COLUMNS).iter_rows(named=True)]
    return Securities(**named(found), total=rows.height, rows=listed)
