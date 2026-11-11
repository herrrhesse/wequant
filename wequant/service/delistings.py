"""data delistings: every delisted code, how it is classified and on what basis, and how a holding of it is treated."""

from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Literal

from wequant.account.delisting import TREATMENTS, delisting_plan
from wequant.data.delistings import KINDS, SOURCES, overrides_text
from wequant.panel import load_panel
from wequant.service.base import Context, DataRequest, Model, manifest, named
from wequant.service.details import to_execution
from wequant.service.errors import Exit, ServiceError
from wequant.service.specs import ExecutionSpec


class DelistingsRequest(DataRequest):
    """The delisted codes of a store version, treated under an execution block."""

    execution: ExecutionSpec = ExecutionSpec()


class DelistingRow(Model):
    """One delisted code."""

    code: str
    name: str | None
    delist_date: dt.date | None
    """The official delisting date; null until S2.3 provides it."""
    day: dt.date | None
    """The trading day at whose open a holding is handled; null: not yet in the calendar."""
    approximate: bool
    """day comes from the data's last date, not the official one."""
    last_bar: dt.date | None
    last_close: float | None
    """Yuan, unadjusted."""
    kind: Literal[KINDS]
    """forced, merger, suspension or unknown."""
    classified_by: Literal[SOURCES]
    """rule, or an override entry of the data root's overrides/delisting.toml."""
    evidence: str | None
    """The override's evidence; null for the rule."""
    source: str | None
    """Where the override's evidence comes from; null for the rule."""
    treatment: Literal["write_down", "exit", "hold"]
    recovery: float | None
    """Share of the last close returned as cash; null when held."""


class DelistingCounts(Model):
    """The rows in total and by kind, treatment and classifier (every key, zeros included), and how many are
    approximate."""

    total: int
    kind: dict[str, int]
    treatment: dict[str, int]
    classified_by: dict[str, int]
    approximate: int


class Delistings(Model):
    """Every delisted code of the store version, with its basis and treatment, and the counts a page leads with."""

    data_version: int
    data_version_name: str
    """The version's display name, YYYYMMDD.N: its last trade date, then which version of that day."""
    overrides: bool
    """An overrides file exists in the data root."""
    counts: DelistingCounts
    rows: list[DelistingRow]


def data_delistings(request: DelistingsRequest, context: Context = Context()) -> Delistings:
    """Every delisted code of a store version: its classification and the basis for it, and the treatment a holding of
    it gets under the execution block."""
    store = context.store()
    manifest(store, request.data_version)
    execution = to_execution(request.execution)
    panel = load_panel(version=request.data_version, store=store, cache_dir=context.cache)
    try:
        plan = delisting_plan(panel, execution)
    except ValueError as error:  # a malformed overrides file names its entry
        raise ServiceError(Exit.USAGE, str(error)) from None
    rows = [DelistingRow(**row) for row in plan.iter_rows(named=True)]
    overrides = overrides_text(store.home) is not None
    return Delistings(**named(panel.manifest), overrides=overrides, counts=counts(rows), rows=rows)


def counts(rows: list[DelistingRow]) -> DelistingCounts:
    """The rows counted by kind, treatment and classifier over every possible value, and the approximate ones."""

    def by(name: str, values: tuple[str, ...]) -> dict[str, int]:
        found = Counter(getattr(row, name) for row in rows)
        return {value: found[value] for value in values}

    return DelistingCounts(
        total=len(rows),
        kind=by("kind", KINDS),
        treatment=by("treatment", TREATMENTS),
        classified_by=by("classified_by", SOURCES),
        approximate=sum(row.approximate for row in rows),
    )
