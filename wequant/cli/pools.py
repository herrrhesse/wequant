"""The stock pool commands (round 3): list, show, save, delete, parse, from-index, export and import."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from cyclopts import App

from wequant import service
from wequant.cli.commands import COMMON, Common, app, run
from wequant.data.indexes import OFFERED
from wequant.service.details import validated

pools = app.command(App(name="pools", help="Stock pools: named lists of stocks a strategy can copy as its scope."))


@pools.command(name="list")
def pools_list(*, common: Common = COMMON) -> int:
    """Every stock pool, oldest first."""
    return run(service.pools_list, service.PoolsListRequest(), common)


@pools.command(name="show")
def pools_show(id: str, /, *, common: Common = COMMON) -> int:
    """One stock pool with its stocks' names.

    Parameters
    ----------
    id
        A stock pool id.
    """
    return run(service.pools_show, lambda: validated(service.PoolRequest, {"id": id}), common)


@pools.command(name="save")
def pools_save(*codes: str, name: str, id: str | None = None, common: Common = COMMON) -> int:
    """Save CODES as a new stock pool, or as pool --id renamed and refilled.

    Parameters
    ----------
    codes
        Codes such as 600000.SH, in the order to keep.
    name
        The pool's name.
    id
        The pool to change; without it, a new pool.
    """
    fields = {"id": id, "name": name, "codes": list(codes)}
    return run(service.pools_save, lambda: validated(service.PoolSaveRequest, fields), common)


@pools.command(name="delete")
def pools_delete(id: str, /, *, common: Common = COMMON) -> int:
    """Delete a stock pool; strategies that copied it keep their copies.

    Parameters
    ----------
    id
        A stock pool id.
    """
    return run(service.pools_delete, lambda: validated(service.PoolRequest, {"id": id}), common)


@pools.command(name="parse")
def pools_parse(text: str = "", /, *, file: Path | None = None, common: Common = COMMON) -> int:
    """Resolve each code or name of TEXT (or of --file) to a stock, or say why it names none.

    Parameters
    ----------
    text
        Codes such as 600519, sh600519 or 600519.SH, or stocks' names, split by commas, spaces or line breaks.
    file
        A CSV or TXT file instead: first column codes, second names.
    """
    return run(service.pools_parse, lambda: validated(service.ParseRequest, {"text": _text(text, file)}), common)


@pools.command(name="from-index")
def pools_from_index(index: Literal[OFFERED], /, *, data_version: int | None = None, common: Common = COMMON) -> int:
    """An index's members on the data's last day with members, to start a pool from.

    Parameters
    ----------
    index
        000300.SH (CSI 300) or 000905.SH (CSI 500).
    data_version
        Store version to read (default: the one in use).
    """
    fields = {"index": index, "data_version": data_version}
    return run(service.pools_from_index, lambda: validated(service.IndexPoolRequest, fields), common)


@pools.command(name="export")
def pools_export(id: str, /, *, common: Common = COMMON) -> int:
    """A stock pool as CSV text, code and name per row.

    Parameters
    ----------
    id
        A stock pool id.
    """
    return run(service.pools_export, lambda: validated(service.PoolRequest, {"id": id}), common)


@pools.command(name="import")
def pools_import(file: Path, /, *, name: str, common: Common = COMMON) -> int:
    """Save the stocks a CSV or TXT file names as a new stock pool, listing the items that name none.

    Parameters
    ----------
    file
        First column codes, second names; a header row is skipped.
    name
        The new pool's name.
    """
    fields = {"name": name, "text": _text("", file)}
    return run(service.pools_import, lambda: validated(service.PoolImportRequest, fields), common)


def _text(text: str, file: Path | None) -> str:
    return file.read_text(encoding="utf-8-sig") if file is not None else text
