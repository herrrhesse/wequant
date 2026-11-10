"""data status: the store's versions, the tables of one version and whether its limit check is current."""

from __future__ import annotations

import datetime as dt

from pydantic import JsonValue

from wequant.rules.tables import limit_check_current
from wequant.service.base import Context, DataRequest, Model, manifest

OWN_KEYS = ("version", "created_at", "last_trade_date", "tables")  # the manifest keys DataStatus has fields for


class StatusRequest(DataRequest):
    """One store version to describe."""


class TableStatus(Model):
    """One table of the version."""

    name: str
    files: int
    rows: int


class DataStatus(Model):
    """One store version: its tables and meta, next to the versions the store keeps."""

    data_version: int
    current_version: int
    versions: list[int]
    """Versions the store keeps."""
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
    pinned, current = manifest(store, request.data_version), manifest(store)
    return DataStatus(
        data_version=pinned["version"],
        current_version=current["version"],
        versions=store.versions(),
        created_at=pinned["created_at"],
        last_trade_date=pinned.get("last_trade_date"),
        limit_check_current=limit_check_current(pinned),
        tables=[_table(name, entry) for name, entry in sorted(pinned["tables"].items())],
        meta={key: value for key, value in pinned.items() if key not in OWN_KEYS},
    )


def _table(name: str, entry: dict) -> TableStatus:
    return TableStatus(name=name, files=len(entry["files"]), rows=sum(f["rows"] for f in entry["files"]))
