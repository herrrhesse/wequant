"""What every operation shares: the model base, the request bases, the caller's context and opening the store."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from wequant.backtest.progress import Progress
from wequant.data.codes import split as split_code
from wequant.data.store import Store
from wequant.expr import Cache
from wequant.panel import NotInPanel, Panel, load_panel
from wequant.service.errors import Exit, ServiceError
from wequant.strategy.library import Library


class Model(BaseModel):
    """Base of every request and result: immutable, unknown fields refused, NaN and infinity refused."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False, use_attribute_docstrings=True)


class Check(Model):
    """A result whose check can fail; ok False makes the command exit 1."""

    ok: bool
    """True when the check found nothing to act on."""


class ErrorDetail(Model):
    """One located reason an operation failed."""

    code: str
    """A stable code, only ever added to: e.g. unknown_field, out_of_range, version_not_kept."""
    message: str
    """English; names the item when names repeat."""
    loc: list[str | int] = []
    """Where in the request, pydantic style: e.g. ["strategy", "filters", 2, "expr"]; empty for the whole request."""
    path: list[int] = []
    """Where inside an expression form ([name, arg0, arg1, ...]: argument i at index i + 1); empty for all of it."""


class ErrorBody(Model):
    """A failed operation: its exit code, why, and the located details; the error body of the HTTP API."""

    exit: Exit
    name: str
    """The Exit member's name, e.g. USAGE."""
    message: str
    detail: list[ErrorDetail] = []
    complete: bool = True
    """False: rules that relate fields to each other (requires, duplicate_name, ...) did not run because a field
    failed; fixing the listed errors may reveal more."""


def error_body(error: ServiceError) -> ErrorBody:
    """The ErrorBody of a ServiceError."""
    detail, complete = list(error.detail), error.complete
    return ErrorBody(exit=error.exit, name=error.exit.name, message=str(error), detail=detail, complete=complete)


def not_found(code: str, message: str, loc: list[str | int] = ()) -> ServiceError:
    """NOT_FOUND with its one located detail."""
    return ServiceError(Exit.NOT_FOUND, message, [ErrorDetail(code=code, message=message, loc=list(loc))])


def _code(code: str) -> str:
    try:
        split_code(code)
    except ValueError as error:
        raise PydanticCustomError("wrong_type", str(error)) from None
    return code


def _distinct(values: list) -> list:
    if len(set(values)) != len(values):
        raise PydanticCustomError("duplicate_value", "values repeat: {values}", {"values": values})
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
            raise PydanticCustomError("out_of_range", f"start {self.start} is after end {self.end}")
        return self


@dataclass(frozen=True)
class Context:
    """The caller's settings for one call, never part of a request: the data root, the panel cache root, a resident
    process's expression cache, a long run's progress and cancel callbacks, and a stage timer's mark."""

    home: Path | None = None  # None: $WEQUANT_HOME, else ~/.wequant
    cache: Path | None = None  # panels go under <cache>/panel/; None: <home>/cache
    expr_cache: Cache | None = None  # a resident process's expression results; None: computed afresh
    progress: Callable[[Progress], None] | None = None  # told where a long run is
    cancel: Callable[[], bool] | None = None  # asked whether to stop a long run
    mark: Callable[[str], None] | None = None  # told as each stage of the call ends (--profile)
    clock: Callable[[], str] | None = None  # stamps what the call writes; None: now (the demo home fixes it)

    @classmethod
    def resident(cls, home: Path | None = None, cache: Path | None = None, cache_bytes: int = 8 << 30) -> Context:
        """The context of a long-lived process: expression results stay in memory between calls, up to cache_bytes."""
        return cls(home, cache, expr_cache=Cache(cache_bytes))

    def store(self) -> Store:
        return Store(self.home, self.clock)

    def stage(self, name: str) -> None:
        """Stage name has ended."""
        if self.mark is not None:
            self.mark(name)

    def library(self) -> Library:
        """The saved strategies of the data root."""
        return Library(self.store().home / "strategies", self.clock)


def manifest(store: Store, version: int | None = None) -> dict:
    """The manifest of version (default: the current one); NOT_FOUND without a store or that version."""
    found = store.manifest(version)
    if found is not None:
        return found
    kept = store.versions()
    if not kept:
        raise not_found("no_store", f"no store at {store.home}")
    raise not_found("version_not_kept", f"store version {version} is not kept; kept: {kept}", ["data_version"])


def open_panel(request: PanelRequest, context: Context, history: bool = False) -> Panel:
    """load_panel for the request, from the first trading day with history; NOT_FOUND for an unknown code or no day."""
    store = context.store()
    manifest(store, request.data_version)  # NOT_FOUND that names the kept versions
    start = None if history else request.start
    try:
        return load_panel(
            start, request.end, request.codes, version=request.data_version, store=store, cache_dir=context.cache
        )
    except NotInPanel as error:
        raise not_found(error.code, error.args[0], ["codes"] if error.code == "unknown_code" else []) from None
