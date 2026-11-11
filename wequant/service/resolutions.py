"""data resolve list | approve | reject: the user's decisions on merge conflicts, in the data root's resolutions file.

These change only that file, never the store; the merge applies approved entries.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from typing import Literal

from wequant.data.merge.resolutions import STATUSES, decide, default_path, load
from wequant.data.schema import LEVELS
from wequant.data.store import StoreLocked, exclusive
from wequant.service.base import Code, Context, ErrorDetail, Model, not_found
from wequant.service.details import usage
from wequant.service.errors import Exit, ServiceError
from wequant.service.runs import Page, PageRequest
from wequant.service.specs import IsoDate, Name

SELECTORS = ("code", "date", "kind", "category", "start", "end")


class Resolution(Model):
    """One entry of the resolutions file."""

    code: str
    date: dt.date
    kind: str
    fields: list[str]
    take: str
    values: dict[str, int | float | None]
    """take corrected: field -> value in store units (prices in 厘)."""
    category: str
    level: Literal[LEVELS]
    verifications: list[str]
    evidence: str
    status: Literal[STATUSES]
    approval: str


class Selection(Model):
    """Which entries: every given selector must match."""

    code: Code | None = None
    date: IsoDate | None = None
    kind: str | None = None
    """A conflict kind, e.g. price_mismatch."""
    category: str | None = None
    """A group, e.g. D11-B."""
    start: IsoDate | None = None
    """First date of a range."""
    end: IsoDate | None = None
    """Last date of a range."""


class ResolveListRequest(Selection, PageRequest):
    """Entries, filtered and paged."""

    status: Literal[STATUSES] | None = None


class StatusCount(Model):
    """Entries of one category in one status."""

    category: str
    status: str
    entries: int


class ResolvePage(Page):
    """Entries in file order (date, code, kind), and the counts of all that match."""

    counts: list[StatusCount]
    rows: list[Resolution]


class DecisionRequest(Selection):
    """A decision on the selected entries."""

    note: Name
    """Who decides and on what basis, e.g. "user 2026-11-10: class B approved per D11"."""


class Decision(Model):
    """What a decision changed."""

    status: Literal[STATUSES]
    matched: int
    changed: int
    """Entries whose status or note changed; the others already had them."""


def data_resolve_list(request: ResolveListRequest, context: Context = Context()) -> ResolvePage:
    """The data root's entries that match, with counts by category and status."""
    entries = [e for e in _entries(context) if _matches(e, request)]
    entries = [e for e in entries if request.status is None or e["status"] == request.status]
    counts = Counter((e["category"], e["status"]) for e in entries)
    rows = [Resolution(**e) for e in entries[request.offset : request.offset + request.limit]]
    listed = [StatusCount(category=c, status=s, entries=n) for (c, s), n in sorted(counts.items())]
    return ResolvePage(total=len(entries), offset=request.offset, limit=request.limit, counts=listed, rows=rows)


def data_resolve_approve(request: DecisionRequest, context: Context = Context()) -> Decision:
    """Approve the selected entries with the note; an undetermined one refuses the whole decision."""
    return _decide(request, "approved", context)


def data_resolve_reject(request: DecisionRequest, context: Context = Context()) -> Decision:
    """Reject the selected entries with the note."""
    return _decide(request, "rejected", context)


def _decide(request: DecisionRequest, status: str, context: Context) -> Decision:
    if all(getattr(request, name) is None for name in SELECTORS):
        detail = ErrorDetail(code="requires", message=f"select entries by one of {SELECTORS}", loc=[])
        raise usage([detail])
    path = default_path(context.store().home)
    if not path.exists():
        raise not_found("no_resolutions", f"no resolutions file at {path}")
    try:
        with exclusive(path.parent / ".lock"):
            matched = sum(_matches(e, request) for e in _entries(context))
            changed = decide(path, lambda entry: _matches(_plain(entry), request), status, request.note)
    except StoreLocked as error:
        raise ServiceError(Exit.LOCKED, str(error)) from None
    except ValueError as error:
        raise usage([ErrorDetail(code="undetermined", message=str(error), loc=[])]) from None
    return Decision(status=status, matched=matched, changed=len(changed))


def _entries(context: Context) -> list[dict]:
    """The data root's entries as plain dicts; USAGE when the file is malformed."""
    try:
        frame = load(default_path(context.store().home))
    except ValueError as error:
        raise ServiceError(Exit.USAGE, f"the resolutions file is malformed: {error}") from None
    return [_plain(row) for row in frame.to_dicts()]


def _plain(entry: dict) -> dict:
    values = entry["values"]
    return entry | {
        "fields": list(entry["fields"]),
        "verifications": list(entry["verifications"]),
        "values": json.loads(values) if isinstance(values, str) else dict(values),
    }


def _matches(entry: dict, s: Selection) -> bool:
    day = entry["date"]
    return all(
        [
            s.code is None or entry["code"] == s.code,
            s.date is None or day == s.date,
            s.kind is None or entry["kind"] == s.kind,
            s.category is None or entry["category"] == s.category,
            s.start is None or day >= s.start,
            s.end is None or day <= s.end,
        ]
    )
