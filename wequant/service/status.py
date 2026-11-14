"""data status: the store's versions, the tables of one version and whether its limit check is current."""

from __future__ import annotations

import datetime as dt

import polars as pl
from pydantic import JsonValue

from wequant.data.raw import BaoStockRaw
from wequant.data.store import OWN_KEYS, Store, version_name, version_reason
from wequant.data.update import due_days
from wequant.paths import absolute
from wequant.rules.tables import limit_check_current
from wequant.service.base import Context, DataRequest, Model, manifest, read_version
from wequant.service.labels import reason_text
from wequant.service.outputs import Params
from wequant.service.scopes import ScopeStatus, scope_status
from wequant.storage.settings import load as load_settings


class StatusRequest(DataRequest):
    """One store version to describe."""


class TableStatus(Model):
    """One table of the version."""

    name: str
    files: int
    rows: int


class VersionInfo(Model):
    """One version the store keeps."""

    id: int
    """The number requests take as data_version."""
    name: str
    """The display name, YYYYMMDD.N: the version's last trade date, then which version of that day; written when the
    version was made and never changed (legacy versions: the day and their own number)."""
    reason: str
    """Why it was made, in Chinese, e.g. 每日更新."""
    reason_code: str
    """The reason's code (labels.version_reasons) and its params."""
    reason_params: Params
    last_trade_date: dt.date | None
    created_at: str
    pinned: bool
    """Kept by every cleanup."""


class DataStatus(Model):
    """One store version: its tables and meta, next to every version the store keeps."""

    data_version: int
    data_version_name: str
    """The version's display name, YYYYMMDD.N."""
    current_version: int
    """The current version: the newest data, which updates build on."""
    version_in_use: int
    """What requests without a data_version read: the version set in use while it is kept, else the current one."""
    using_old_version: bool
    """A version other than the current one is in use; data_use_version with null goes back."""
    next_trading_day: dt.date | None
    """The first trading day after the current version's last, by BaoStock's calendar in the raw layer; null without
    that calendar or a day after it."""
    update_due: bool | None
    """data update has a closed trading day to fetch (a day closes at 18:00 Shanghai time); null without the
    calendar."""
    versions: list[int]
    """Versions the store keeps, oldest first."""
    version_list: list[VersionInfo]
    """The same versions with their names and reasons, newest first."""
    created_at: str
    last_trade_date: dt.date | None
    limit_check_current: bool
    """The version records a limit check of its own inputs with the current rule book."""
    tables: list[TableStatus]
    meta: dict[str, JsonValue]
    """Every other manifest key, such as source and limit_check."""
    home: str
    """The data root's folder, absolute (round 3: the data page's 存储位置)."""
    scope: ScopeStatus
    """The stocks the version holds bars for, in layers by first day, and the strategy scopes they cover (S2.11)."""


def data_status(request: StatusRequest, context: Context = Context()) -> DataStatus:
    """Describe one store version from the manifests alone."""
    store = context.store()
    shown, current = manifest(store, read_version(context, request.data_version)), manifest(store)
    in_use = read_version(context, None) or current["version"]
    kept = [found for v in reversed(store.versions()) if (found := store.manifest(v)) is not None]
    pinned = set(load_settings(store.home)[0].pinned_versions)
    return DataStatus(
        data_version=shown["version"],
        data_version_name=version_name(shown),
        current_version=current["version"],
        version_in_use=in_use,
        using_old_version=in_use != current["version"],
        **_update_days(store, current, context),
        versions=store.versions(),
        version_list=[version_info(found, found["version"] in pinned) for found in kept],
        created_at=shown["created_at"],
        last_trade_date=shown.get("last_trade_date"),
        limit_check_current=limit_check_current(shown),
        tables=[_table(name, entry) for name, entry in sorted(shown["tables"].items())],
        meta={key: value for key, value in shown.items() if key not in (*OWN_KEYS, "last_trade_date")},
        home=str(absolute(store.home)),
        scope=scope_status(store, shown),
    )


def version_info(found: dict, pinned: bool) -> VersionInfo:
    """The VersionInfo of a manifest."""
    reason = version_reason(found)
    return VersionInfo(
        id=found["version"],
        name=version_name(found),
        reason=reason_text(reason),
        reason_code=reason["code"],
        reason_params=reason["params"],
        last_trade_date=found.get("last_trade_date"),
        created_at=found["created_at"],
        pinned=pinned,
    )


def _update_days(store: Store, current: dict, context: Context) -> dict:
    """next_trading_day and update_due by the raw layer's BaoStock calendar, both null without it."""
    path = BaoStockRaw.at(store.home).trade_dates
    if not path.exists():
        return {"next_trading_day": None, "update_due": None}
    dates = pl.read_parquet(path, columns=["calendar_date", "is_trading_day"])
    last = store.read_table("calendar", columns=["date"], manifest=current)["date"].max()
    now = dt.datetime.fromisoformat(context.clock()) if context.clock else dt.datetime.now(dt.UTC)
    later = sorted(d for d in dates.filter(pl.col("is_trading_day") == "1")["calendar_date"].str.to_date() if d > last)
    return {"next_trading_day": later[0] if later else None, "update_due": bool(due_days(dates, last, now))}


def _table(name: str, entry: dict) -> TableStatus:
    return TableStatus(name=name, files=len(entry["files"]), rows=sum(f["rows"] for f in entry["files"]))
