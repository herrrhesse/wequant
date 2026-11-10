"""data delistings: every delisted code, how it is classified and on what basis, and how a holding of it is treated."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from wequant.account.delisting import delisting_plan
from wequant.data.delistings import KINDS, SOURCES, overrides_text
from wequant.panel import load_panel
from wequant.service.base import Context, DataRequest, Model, manifest
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


class Delistings(Model):
    """Every delisted code of the store version, with its basis and treatment."""

    data_version: int
    overrides: bool
    """An overrides file exists in the data root."""
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
    return Delistings(data_version=panel.version, overrides=overrides_text(store.home) is not None, rows=rows)
