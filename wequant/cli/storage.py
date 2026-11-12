"""The storage commands (round 2): settings, the version in use, pins, deleting runs, exports, imports, usage and
cleanup."""

from __future__ import annotations

from typing import Annotated

from cyclopts import App

from wequant import service
from wequant.cli.commands import COMMON, FLAT, Common, app, data, run, runs, strategies
from wequant.service.details import validated

settings = app.command(App(name="settings", help="The data root's settings: the run archive's limit, versions kept."))
storage = app.command(App(name="storage", help="How much the data root takes, and cleaning it up."))


@settings.command(name="show")
def settings_show(*, common: Common = COMMON) -> int:
    """The settings and why any value reads as its default."""
    return run(service.settings_show, service.SettingsRequest(), common)


@settings.command(name="set")
def settings_set(
    *, request: Annotated[service.SettingsUpdate, FLAT] = service.SettingsUpdate(), common: Common = COMMON
) -> int:
    """Set the run archive's limit (--runs-limit-gb) and the versions cleanup keeps (--keep-versions)."""
    return run(service.settings_update, request, common)


@storage.command(name="usage")
def storage_usage(*, common: Common = COMMON) -> int:
    """Bytes of the current data, other versions, panels and runs, the limits, and what cleanup would free."""
    return run(service.storage_usage, service.UsageRequest(), common)


@storage.command(name="cleanup")
def storage_cleanup(*, dry_run: bool = False, common: Common = COMMON) -> int:
    """Remove the versions, runs over the limit and panels cleanup does not keep.

    Parameters
    ----------
    dry_run
        Only count.
    """
    return run(service.storage_cleanup, service.CleanupRequest(dry_run=dry_run), common)


@data.command(name="use")
def data_use(version: int | None = None, /, *, common: Common = COMMON) -> int:
    """Read a kept version when a request names none; without VERSION, the current one again.

    Parameters
    ----------
    version
        A kept store version.
    """
    return run(service.data_use_version, lambda: validated(service.UseVersionRequest, {"version": version}), common)


@data.command(name="pin")
def data_pin(version: int, /, *, unpin: bool = False, common: Common = COMMON) -> int:
    """Pin a version so cleanup keeps it, or unpin it.

    Parameters
    ----------
    version
        A kept store version.
    unpin
        Unpin instead.
    """
    fields = {"version": version, "pinned": not unpin}
    return run(service.data_pin_version, lambda: validated(service.PinVersionRequest, fields), common)


@data.command(name="export")
def data_export(
    *, request: Annotated[service.ExportVersionRequest, FLAT] = service.ExportVersionRequest(), common: Common = COMMON
) -> int:
    """Write a store version (default: the one in use) to one .wqver file."""
    return run(service.data_export, request, common)


@data.command(name="import")
def data_import(path: str, /, *, common: Common = COMMON) -> int:
    """Add the version in a .wqver file as a new version; it becomes the one in use only with `data use`.

    Parameters
    ----------
    path
        A file data export wrote.
    """
    return run(service.data_import, lambda: validated(service.ImportRequest, {"path": path}), common)


@runs.command(name="pin")
def runs_pin(id: str, /, *, unpin: bool = False, common: Common = COMMON) -> int:
    """Pin a run so the archive's limit never removes it, or unpin it.

    Parameters
    ----------
    id
        A run id.
    unpin
        Unpin instead.
    """
    return run(service.runs_pin, lambda: validated(service.PinRunRequest, {"id": id, "pinned": not unpin}), common)


@runs.command(name="delete")
def runs_delete(id: str, /, *, common: Common = COMMON) -> int:
    """Remove an archived run.

    Parameters
    ----------
    id
        A run id.
    """
    return run(service.runs_delete, lambda: validated(service.DeleteRunRequest, {"id": id}), common)


@runs.command(name="export")
def runs_export(id: str, /, *, path: str | None = None, common: Common = COMMON) -> int:
    """Write an archived run and its strategy to one .wqrun file.

    Parameters
    ----------
    id
        A run id.
    path
        Where to write it (default: HOME/exports/ID.wqrun).
    """
    return run(service.runs_export, lambda: validated(service.ExportRunRequest, {"id": id, "path": path}), common)


@runs.command(name="import")
def runs_import(path: str, /, *, common: Common = COMMON) -> int:
    """Archive the run in a .wqrun file.

    Parameters
    ----------
    path
        A file runs export wrote.
    """
    return run(service.runs_import, lambda: validated(service.ImportRequest, {"path": path}), common)


@strategies.command(name="export")
def strategy_export(id: str, /, *, history: bool = False, path: str | None = None, common: Common = COMMON) -> int:
    """Write a saved strategy to one .wqstrategy file: its name and current version, or every version.

    Parameters
    ----------
    id
        A saved strategy id.
    history
        Every saved version.
    path
        Where to write it (default: HOME/exports/ID.wqstrategy).
    """
    fields = {"id": id, "history": history, "path": path}
    return run(service.strategy_export, lambda: validated(service.ExportStrategyRequest, fields), common)


@strategies.command(name="import")
def strategy_import(path: str, /, *, common: Common = COMMON) -> int:
    """Save the strategy in a .wqstrategy file, under a new name or id when its own are taken.

    Parameters
    ----------
    path
        A file strategy export wrote.
    """
    return run(service.strategy_import, lambda: validated(service.ImportRequest, {"path": path}), common)
