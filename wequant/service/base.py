"""What every operation shares: the model base, the request bases, the caller's context and opening the store."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from wequant.data.codes import split as split_code
from wequant.data.store import Store
from wequant.panel import Panel, load_panel
from wequant.service.errors import Exit, ServiceError


class Model(BaseModel):
    """Base of every request and result: immutable, unknown fields refused, NaN and infinity refused."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False, use_attribute_docstrings=True)


class Check(Model):
    """A result whose check can fail; ok False makes the command exit 1."""

    ok: bool
    """True when the check found nothing to act on."""


def _code(code: str) -> str:
    split_code(code)
    return code


def _distinct(values: list) -> list:
    if len(set(values)) != len(values):
        raise ValueError(f"values repeat: {values}")
    return values


Code = Annotated[str, AfterValidator(_code)]  # a well-formed wequant code such as 600000.SH
Distinct = AfterValidator(_distinct)  # a list without repeats


class DataRequest(Model):
    """A request that reads one store version."""

    data_version: int | None = Field(None, ge=1)
    """Store version to read (default: the current one)."""


class PanelRequest(DataRequest):
    """A request for a few codes over a date range of one store version."""

    codes: Annotated[list[Code], Distinct] = Field(min_length=1)
    """Codes such as 600000.SH, in output order."""
    start: dt.date | None = None
    """First day (default: the first trading day)."""
    end: dt.date | None = None
    """Last day (default: the last trading day)."""

    @model_validator(mode="after")
    def _check_range(self) -> PanelRequest:
        if self.start and self.end and self.start > self.end:
            raise ValueError(f"start {self.start} is after end {self.end}")
        return self


@dataclass(frozen=True)
class Context:
    """The caller's settings for one call, never part of a request: the data root and the panel cache root."""

    home: Path | None = None  # None: $WEQUANT_HOME, else ~/.wequant
    cache: Path | None = None  # panels go under <cache>/panel/; None: <home>/cache

    def store(self) -> Store:
        return Store(self.home)


def manifest(store: Store, version: int | None = None) -> dict:
    """The manifest of version (default: the current one); NOT_FOUND without a store or that version."""
    found = store.manifest(version)
    if found is not None:
        return found
    kept = store.versions()
    if not kept:
        raise ServiceError(Exit.NOT_FOUND, f"no store at {store.home}")
    raise ServiceError(Exit.NOT_FOUND, f"store version {version} is not kept; kept: {kept}")


def open_panel(request: PanelRequest, context: Context, history: bool = False) -> Panel:
    """load_panel for the request, from the first trading day with history; NOT_FOUND for an unknown code or no day."""
    store = context.store()
    manifest(store, request.data_version)  # NOT_FOUND that names the kept versions
    start = None if history else request.start
    try:
        return load_panel(
            start, request.end, request.codes, version=request.data_version, store=store, cache_dir=context.cache
        )
    except LookupError as error:  # only these: a ValueError would be a bug, not missing data
        raise ServiceError(Exit.NOT_FOUND, error.args[0]) from None
