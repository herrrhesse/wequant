"""data verify submit | list: verification records, the evidence behind resolutions and data_issues (D20)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from wequant.data.schema import LEVELS, TABLES
from wequant.data.store import StoreLocked
from wequant.data.verifications import (
    SOURCE_TYPES,
    VERIFICATIONS,
    VERIFIERS,
    Verification,
    Verifications,
    checked,
    record_id,
)
from wequant.service.base import Code, Context, Model
from wequant.service.details import spec_details, usage
from wequant.service.errors import Exit, ServiceError
from wequant.service.runs import Page, PageRequest
from wequant.service.specs import IsoDate, Name
from wequant.strategy import SpecError, SpecErrors

Cell = int | float | str | None  # prices and amounts in yuan, volume in shares, turn in percent


class VerifiedCell(Model):
    """The cell a record checks."""

    table: Literal[tuple(TABLES)]
    code: Code
    date: IsoDate
    fields: list[str] = Field(min_length=1)
    """Columns of the table."""


class SourceRef(Model):
    """Where values came from."""

    name: Name
    """The label its values carry in values, e.g. sse, eastmoney, cninfo."""
    type: Literal[SOURCE_TYPES]
    url: str | None
    """null: none to give (e.g. a rule)."""
    fetched_at: str
    """ISO 8601."""


class Verifier(Model):
    """Who checked."""

    kind: Literal[VERIFIERS]
    name: Name
    model: str | None
    """The model's name for an agent; null for a person."""


class VerificationIn(Model):
    """One checked cell, as submitted."""

    object: VerifiedCell
    values: dict[str, dict[str, Cell]]
    """By label (baostock, seed or a source's name): field -> value."""
    sources: list[SourceRef] = Field(min_length=1)
    verifier: Verifier
    method: Name
    conclusion: Name
    correct: dict[str, Cell]
    """The verified values; empty when the record decides nothing."""
    level: Literal[LEVELS]


class VerificationOut(VerificationIn):
    """A stored record."""

    id: str
    """'v-' and the digest of its content: the same record always gets the same id."""
    submitted_at: str


class VerifySubmitRequest(Model):
    """Records to add."""

    records: list[VerificationIn] = Field(min_length=1)


class VerifySubmitResult(Model):
    """What the submission added."""

    added: int
    existing: int
    """Records already stored with the same content."""
    ids: list[str]
    """In submission order."""


class VerifyListRequest(PageRequest):
    """Stored records, filtered; null filters match all."""

    id: str | None = None
    table: Literal[tuple(TABLES)] | None = None
    code: Code | None = None
    start: IsoDate | None = None
    end: IsoDate | None = None
    source_type: Literal[SOURCE_TYPES] | None = None
    source: str | None = None
    """A source name."""
    verifier: str | None = None
    """A verifier's name."""


class VerifyPage(Page):
    """Records by submission, then line."""

    rows: list[VerificationOut]


def data_verify_submit(request: VerifySubmitRequest, context: Context = Context()) -> VerifySubmitResult:
    """Check and add records; those already stored are not added again."""
    records = [record.model_dump(mode="json") for record in request.records]
    details = []
    for i, record in enumerate(records):
        try:
            checked(record)
        except (SpecError, SpecErrors) as error:
            details += spec_details(error, ("records", i))
    if details:
        raise usage(details)
    try:
        added, existing = Verifications(context.store().home / VERIFICATIONS).submit(records)
    except StoreLocked as error:
        raise ServiceError(Exit.LOCKED, str(error)) from None
    ids = [record_id(checked(record)) for record in records]
    return VerifySubmitResult(added=len(added), existing=len(existing), ids=ids)


def data_verify_list(request: VerifyListRequest, context: Context = Context()) -> VerifyPage:
    """Stored records matching every given filter, paged."""
    found = [v for v in Verifications(context.store().home / VERIFICATIONS).read() if _matches(v, request)]
    window = found[request.offset : request.offset + request.limit]
    rows = [VerificationOut(id=v.id, submitted_at=v.submitted_at, **v.record) for v in window]
    return VerifyPage(total=len(found), offset=request.offset, limit=request.limit, rows=rows)


def _matches(v: Verification, r: VerifyListRequest) -> bool:
    sources, verifier = v.record["sources"], v.record["verifier"]["name"]
    return all(
        [
            r.id is None or v.id == r.id,
            r.table is None or v.table == r.table,
            r.code is None or v.code == r.code,
            r.start is None or v.date >= r.start,
            r.end is None or v.date <= r.end,
            r.source_type is None or any(s["type"] == r.source_type for s in sources),
            r.source is None or any(s["name"] == r.source for s in sources),
            r.verifier is None or verifier == r.verifier,
        ]
    )
