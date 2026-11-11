"""data issues: the rows the imports dropped, changed or flagged, as data_issues records them."""

from __future__ import annotations

import datetime as dt

import polars as pl
from pydantic import Field

from wequant.service.base import Context, DataRequest, Model, manifest, named


class IssuesRequest(DataRequest):
    """data_issues rows to list, by exact match on each given filter."""

    table: str | None = None
    """Affected table, e.g. bars."""
    kind: str | None = None
    """Issue kind, e.g. limit_breach."""
    codes: list[str] | None = None
    """Affected codes; data_issues keeps a malformed code as found."""
    start: dt.date | None = None
    """First day."""
    end: dt.date | None = None
    """Last day."""
    limit: int = Field(100, ge=0)
    """Rows to return at most; total and counts cover every match."""


class IssueCount(Model):
    """Matching rows of one (table, kind, action)."""

    table: str
    kind: str
    action: str
    rows: int


class IssueRow(Model):
    """One data_issues row; detail is its JSON text as stored."""

    table: str
    code: str
    date: dt.date
    kind: str
    action: str
    detail: str | None
    source: str


class Issues(Model):
    """The matching rows: their total, their counts and the first limit of them by (table, code, date, kind)."""

    data_version: int
    data_version_name: str
    """The version's display name, YYYYMMDD.N: its last trade date, then which version of that day."""
    total: int
    counts: list[IssueCount]
    rows: list[IssueRow]


def data_issues(request: IssuesRequest, context: Context = Context()) -> Issues:
    """The data_issues rows of one store version that match every given filter."""
    store = context.store()
    pinned = manifest(store, request.data_version)
    scan = store.scan("data_issues", start=request.start, end=request.end, codes=request.codes, manifest=pinned)
    for column in ("table", "kind"):
        if (value := getattr(request, column)) is not None:
            scan = scan.filter(pl.col(column) == value)
    found = scan.collect()
    counts = found.group_by("table", "kind", "action").len("rows").sort("table", "kind", "action")
    listed = found.sort("table", "code", "date", "kind").head(request.limit)
    return Issues.model_validate(
        {
            **named(pinned),
            "total": found.height,
            "counts": counts.to_dicts(),
            "rows": listed.to_dicts(),
        }
    )
