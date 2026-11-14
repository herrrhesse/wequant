"""pools list | show | save | delete | parse | from-index | export | import (round 3): the user's stock pools, and how
a strategy that copied one compares with it now."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

import polars as pl

from wequant.data.indexes import LEGACY, OFFERED, stored_as
from wequant.pools import REASONS, NoPool, Parsed, PoolBook, StockPool, parse
from wequant.service.base import (
    Code,
    Context,
    DataRequest,
    Distinct,
    Model,
    guarded,
    manifest,
    named,
    not_found,
    read_version,
)
from wequant.service.details import spec_details, usage
from wequant.service.outputs import Warning
from wequant.service.specs import Name
from wequant.strategy import SpecError, Strategy

SHOWN_MISSING = 20  # codes a stocks_not_in_data warning names before "…"
SCOPE_TEXTS = {  # the English text of each scope warning, filled from its params
    "legacy_index": lambda p: f"{p['index']} is an older scope option; new strategies no longer offer it",
    "stocks_not_in_data": lambda p: f"{p['count']} listed codes are not stocks of the data: {p['codes']}",
}


class PoolStock(Model):
    """A stock of a pool or a parsed list, with its name."""

    code: str
    name: str | None
    """As the data has it now; null: the data has no name (or no such stock)."""


class PoolRow(Model):
    """One stock pool in the list."""

    id: str
    name: str
    count: int
    """Stocks on it."""
    created_at: str
    modified_at: str


class PoolsListRequest(Model):
    """Every stock pool."""


class PoolList(Model):
    """The stock pools, oldest first."""

    rows: list[PoolRow]


class PoolRequest(Model):
    """One stock pool."""

    id: Name


class PoolOut(Model):
    """A stock pool with its stocks, in the user's order."""

    id: str
    name: str
    stocks: list[PoolStock]
    created_at: str
    """ISO 8601 with offset."""
    modified_at: str
    missing: list[str]
    """Codes the data (the version in use) has no stock for; empty without data."""


class PoolSaveRequest(Model):
    """A new stock pool, or a pool renamed and refilled."""

    id: Name | None = None
    """null: a new pool with a generated id."""
    name: Name
    codes: Annotated[list[Code], Distinct] = []
    """Codes such as 600000.SH, in the order to keep."""


class ParseRequest(Model):
    """A pasted list, or the text of an uploaded CSV or TXT file (first column codes, second names)."""

    text: str
    """Items split at line breaks, commas, spaces, tabs and semicolons: codes such as 600519, 600519.SH, sh600519,
    sh.600519 or SH600519, or stocks' current names."""


class UnparsedItem(Model):
    """An item that names no one stock."""

    item: str
    reason: Literal[REASONS]
    """labels.parse_reasons: not_found, ambiguous, beijing, not_a_stock or header."""
    candidates: list[PoolStock]
    """The stocks an ambiguous item could mean; empty otherwise."""


class ParsedStocks(Model):
    """Every item accounted for: the stocks found, the items that named none, the repeats."""

    stocks: list[PoolStock]
    """In the order first named, each once."""
    unparsed: list[UnparsedItem]
    duplicates: list[str]
    """Items naming a stock already found, kept once."""


class IndexPoolRequest(DataRequest):
    """An index's members on the data's last day with members, as a list to start a pool from."""

    index: Literal[OFFERED]


class IndexPool(Model):
    """An index's members on one day."""

    index: str
    date: dt.date
    """The members' day: the version's last trading day, or the last day it has members for."""
    data_version: int
    data_version_name: str
    stocks: list[PoolStock]


class PoolCsv(Model):
    """A stock pool as a CSV file: a header row, then code and name per row."""

    file_name: str
    csv: str
    """UTF-8 text with a byte order mark, so that spreadsheet programs read the names."""


class PoolImportRequest(Model):
    """A new stock pool from a CSV or TXT file's text, read as parse reads it."""

    name: Name
    text: str


class PoolImported(Model):
    """The pool made, and the items that named no stock or repeated one."""

    pool: PoolOut
    unparsed: list[UnparsedItem]
    duplicates: list[str]


class PoolDrift(Model):
    """How a strategy's copied list differs from its stock pool now."""

    pool_id: str
    pool_name: str | None
    """The pool's name now; null: the pool was deleted."""
    added: list[str]
    """On the pool now, not on the strategy's list."""
    removed: list[str]
    """On the strategy's list, no longer on the pool."""


def pools_list(request: PoolsListRequest, context: Context = Context()) -> PoolList:
    """The stock pools, oldest first."""
    rows = [PoolRow(**_fields(p), count=len(p.codes)) for p in _book(context).pools()]
    return PoolList(rows=rows)


def pools_show(request: PoolRequest, context: Context = Context()) -> PoolOut:
    """One stock pool with its stocks' names."""
    with located():
        return _out(_book(context).load(request.id), context)


def pools_save(request: PoolSaveRequest, context: Context = Context()) -> PoolOut:
    """Save a new stock pool, or rename and refill one; strategies that copied it keep their copies."""
    with located(), guarded():
        return _out(_book(context).save(request.name, request.codes, request.id), context)


def pools_delete(request: PoolRequest, context: Context = Context()) -> PoolOut:
    """Delete a stock pool; strategies that copied it keep their copies."""
    with located(), guarded():
        return _out(_book(context).delete(request.id), context)


def pools_parse(request: ParseRequest, context: Context = Context()) -> ParsedStocks:
    """Each item of a pasted list or file resolved to a stock of the data, or the reason it names none."""
    stocks = _stocks(context, required=True)
    return _parsed(parse(request.text, stocks), stocks)


def pools_from_index(request: IndexPoolRequest, context: Context = Context()) -> IndexPool:
    """The index's members on the data's last day with members."""
    store = context.store()
    found = manifest(store, read_version(context, request.data_version))
    spans = store.scan("index_members", manifest=found).filter(pl.col("index_code") == stored_as(request.index))
    spans = spans.collect()
    if spans.is_empty():
        raise not_found("no_data", f"store version {found['version']} has no {request.index} members", ["index"])
    last = store.read_table("calendar", columns=["date"], manifest=found)["date"].max()
    day = min(last, spans["end_date"].max())
    codes = spans.filter((pl.col("start_date") <= day) & (pl.col("end_date") >= day))["code"].sort().to_list()
    names = _stocks(context)
    stocks = [PoolStock(code=code, name=names.get(code)) for code in codes]
    return IndexPool(index=request.index, date=day, **named(found), stocks=stocks)


def pools_export(request: PoolRequest, context: Context = Context()) -> PoolCsv:
    """A stock pool as CSV text, code and name per row."""
    with located():
        pool = _book(context).load(request.id)
    names = _stocks(context)
    rows = ["代码,名称", *(f"{code},{names.get(code) or ''}" for code in pool.codes)]
    return PoolCsv(file_name=f"{pool.name}.csv", csv="﻿" + "\n".join(rows) + "\n")


def pools_import(request: PoolImportRequest, context: Context = Context()) -> PoolImported:
    """A new stock pool of the stocks a CSV or TXT file names, with the items that named none."""
    stocks = _stocks(context, required=True)
    parsed = _parsed(parse(request.text, stocks), stocks)
    with located(), guarded():
        pool = _book(context).save(request.name, [s.code for s in parsed.stocks])
    return PoolImported(pool=_out(pool, context), unparsed=parsed.unparsed, duplicates=parsed.duplicates)


def pool_drift(strategy: Strategy, context: Context) -> PoolDrift | None:
    """How the strategy's copied list differs from its stock pool now; None without a pool or without a difference."""
    stocks = strategy.pool.stocks
    if stocks is None or stocks.pool_id is None:
        return None
    try:
        pool = _book(context).load(stocks.pool_id)
    except NoPool:
        return PoolDrift(pool_id=stocks.pool_id, pool_name=None, added=[], removed=list(stocks.codes))
    copied, now = set(stocks.codes), set(pool.codes)
    if copied == now:
        return None
    added, removed = [c for c in pool.codes if c not in copied], [c for c in stocks.codes if c not in now]
    return PoolDrift(pool_id=pool.id, pool_name=pool.name, added=added, removed=removed)


def scope_warnings(strategy: Strategy, context: Context) -> list[Warning]:
    """legacy_index for an index no longer offered; stocks_not_in_data for listed codes the data lacks."""
    found, pool = {}, strategy.pool
    if pool.index in LEGACY:
        found["legacy_index"] = {"index": pool.index}
    if pool.stocks is not None and (stocks := _stocks(context)):
        if missing := [code for code in pool.stocks.codes if code not in stocks]:
            shown = "、".join(missing[:SHOWN_MISSING]) + ("…" if len(missing) > SHOWN_MISSING else "")
            found["stocks_not_in_data"] = {"count": len(missing), "codes": shown}
    return [Warning(code=code, text=SCOPE_TEXTS[code](params), params=params) for code, params in found.items()]


@contextmanager
def located() -> Iterator[None]:
    """A pool not found as NOT_FOUND unknown_pool at id; a refused name or code as USAGE."""
    try:
        yield
    except NoPool as error:
        raise not_found("unknown_pool", str(error), ["id"]) from None
    except SpecError as error:
        raise usage(spec_details(error)) from None


def _book(context: Context) -> PoolBook:
    return PoolBook(context.store().home, context.clock)


def _stocks(context: Context, required: bool = False) -> dict[str, str | None]:
    """code -> name of each stock of the version in use; empty without data, NOT_FOUND then when required."""
    store = context.store()
    version = read_version(context, None)
    if required:
        manifest(store, version)
    found = store.manifest(version)
    if found is None:
        return {}
    return dict(store.read_table("securities", columns=["code", "name"], manifest=found).iter_rows())


def _parsed(parsed: Parsed, stocks: dict[str, str | None]) -> ParsedStocks:
    stock = lambda code: PoolStock(code=code, name=stocks.get(code))  # noqa: E731
    unparsed = [
        UnparsedItem(item=u.item, reason=u.reason, candidates=[stock(c) for c in u.candidates]) for u in parsed.unparsed
    ]
    return ParsedStocks(stocks=[stock(c) for c in parsed.codes], unparsed=unparsed, duplicates=parsed.duplicates)


def _out(pool: StockPool, context: Context) -> PoolOut:
    names = _stocks(context)
    stocks = [PoolStock(code=code, name=names.get(code)) for code in pool.codes]
    missing = [code for code in pool.codes if names and code not in names]
    return PoolOut(**_fields(pool), stocks=stocks, missing=missing)


def _fields(pool: StockPool) -> dict:
    return {"id": pool.id, "name": pool.name, "created_at": pool.created_at, "modified_at": pool.modified_at}
