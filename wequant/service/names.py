"""data names: stock names and official listing and delisting dates for the current version, from the stock_basic
file of the raw BaoStock layer (S2.3, "补充股票名称"). The user decides when to commit it."""

from __future__ import annotations

import polars as pl

from wequant.data.names import basic_rows, with_basic
from wequant.data.raw import BaoStockRaw
from wequant.data.store import StoreLocked
from wequant.service.base import Context, Model, manifest, name_of, named, not_found
from wequant.service.errors import Exit, ServiceError
from wequant.service.storage import tidy


class NamesRequest(Model):
    """Names and official dates of the current version's codes from raw/baostock/meta/stock_basic.parquet."""

    commit: bool = False
    """Write them in a new version (reason "names") under the writer lock; otherwise only count."""


class NamesReport(Model):
    """What the stock_basic file gives the current version's securities."""

    data_version: int
    data_version_name: str
    """The version read."""
    committed: int | None
    """The new version; null without commit or when nothing changes."""
    committed_name: str | None
    codes: int
    """Codes in securities."""
    listed: int
    """Codes with an official listing date afterwards: those stock_basic lists, and any that had one."""
    named_before: int
    named_after: int
    renamed: int
    """Codes whose stored name changes."""
    delisted: int
    """Codes with an official delisting date afterwards."""
    fetched_at: str | None
    """When the stock_basic file was fetched."""


def data_names(request: NamesRequest, context: Context = Context()) -> NamesReport:
    """Count, and with commit write, what stock_basic gives the current version's securities."""
    store = context.store()
    path = BaoStockRaw.at(store.home).stock_basic
    if not path.is_file():
        raise not_found("no_stock_basic", f"no {path.relative_to(store.home)}: fetch BaoStock's stock_basic first")
    stock_basic = pl.read_parquet(path)
    try:
        with store.lock():
            found = manifest(store)
            before = store.read_table("securities", manifest=found)
            after = with_basic(before, basic_rows(stock_basic))
            committed = None
            if request.commit and not after.equals(before):
                committed = store.commit({}, replace={"securities": after}, reason="names").version
    except StoreLocked as error:
        raise ServiceError(Exit.LOCKED, str(error)) from None
    if committed is not None:
        tidy(context)
    return NamesReport(
        **named(found),
        committed=committed,
        committed_name=None if committed is None else name_of(store, committed),
        codes=after.height,
        listed=after["list_date"].is_not_null().sum(),
        named_before=before["name"].is_not_null().sum(),
        named_after=after["name"].is_not_null().sum(),
        renamed=(before["name"] != after["name"]).fill_null(False).sum(),
        delisted=after["delist_date"].is_not_null().sum(),
        fetched_at=stock_basic["_fetched_at"].first() if "_fetched_at" in stock_basic.columns else None,
    )
