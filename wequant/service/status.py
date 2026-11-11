"""data status: the store's versions, the tables of one version and whether its limit check is current."""

from __future__ import annotations

import datetime as dt

from pydantic import JsonValue

from wequant.data.store import OWN_KEYS, version_name, version_reason
from wequant.rules.tables import limit_check_current
from wequant.service.base import Context, DataRequest, Model, manifest
from wequant.service.labels import reason_text
from wequant.service.outputs import Params


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
    """The newest version."""
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


def data_status(request: StatusRequest, context: Context = Context()) -> DataStatus:
    """Describe one store version from the manifests alone."""
    store = context.store()
    shown, current = manifest(store, request.data_version), manifest(store)
    kept = [found for v in reversed(store.versions()) if (found := store.manifest(v)) is not None]
    return DataStatus(
        data_version=shown["version"],
        data_version_name=version_name(shown),
        current_version=current["version"],
        versions=store.versions(),
        version_list=[version_info(found) for found in kept],
        created_at=shown["created_at"],
        last_trade_date=shown.get("last_trade_date"),
        limit_check_current=limit_check_current(shown),
        tables=[_table(name, entry) for name, entry in sorted(shown["tables"].items())],
        meta={key: value for key, value in shown.items() if key not in (*OWN_KEYS, "last_trade_date")},
    )


def version_info(found: dict) -> VersionInfo:
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
        pinned=False,
    )


def _table(name: str, entry: dict) -> TableStatus:
    return TableStatus(name=name, files=len(entry["files"]), rows=sum(f["rows"] for f in entry["files"]))
