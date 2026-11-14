"""The data root commands (round 3, F-30): use another data root, move or copy this one, browse folders, and the
first-run setup's saved progress."""

from __future__ import annotations

from typing import Annotated

from cyclopts import App

from wequant import service
from wequant.cli.commands import COMMON, FLAT, Common, app, data, run
from wequant.service.details import validated

fs = app.command(App(name="fs", help="Folders, to pick a data root: places to start from, subfolders, a new folder."))
setup = app.command(App(name="setup", help="The first-run setup: whether this data root needs it, and its progress."))


@data.command(name="use-home")
def data_use_home(path: str, /, *, common: Common = COMMON) -> int:
    """Read and write another folder that holds complete WeQuant data from now on (remembered for later commands).

    Parameters
    ----------
    path
        A WeQuant data root.
    """
    return run(service.data_use_home, lambda: validated(service.UseHomeRequest, {"path": path}), common)


@data.command(name="move")
def data_move(to: str, /, *, common: Common = COMMON) -> int:
    """Move the data root to a new or empty folder, check the copy, use it, then remove the old folder.

    Parameters
    ----------
    to
        A new or empty folder.
    """
    return run(service.data_relocate, lambda: validated(service.RelocateRequest, {"to": to, "mode": "move"}), common)


@data.command(name="copy")
def data_copy(to: str, /, *, common: Common = COMMON) -> int:
    """Copy the data root to a new or empty folder, check the copy and use it; the old folder stays.

    Parameters
    ----------
    to
        A new or empty folder.
    """
    return run(service.data_relocate, lambda: validated(service.RelocateRequest, {"to": to, "mode": "copy"}), common)


@fs.command(name="places")
def fs_places(*, common: Common = COMMON) -> int:
    """The home folder, then the drives (Windows) or the root and the mounted disks, with their free space."""
    return run(service.fs_places, service.PlacesRequest(), common)


@fs.command(name="list")
def fs_list(path: str, /, *, common: Common = COMMON) -> int:
    """A folder's subfolders, each marked when it holds WeQuant data; never a file's name.

    Parameters
    ----------
    path
        A folder.
    """
    return run(service.fs_list, lambda: validated(service.FolderRequest, {"path": path}), common)


@fs.command(name="mkdir")
def fs_mkdir(path: str, name: str, /, *, common: Common = COMMON) -> int:
    """Make the folder NAME inside PATH.

    Parameters
    ----------
    path
        An existing folder.
    name
        The new folder's name.
    """
    return run(service.fs_mkdir, lambda: validated(service.MkdirRequest, {"path": path, "name": name}), common)


@setup.command(name="status")
def setup_status(*, common: Common = COMMON) -> int:
    """The data root in use, where it comes from, whether it needs the first-run setup, and the setup's progress."""
    return run(service.setup_status, service.SetupStatusRequest(), common)


@setup.command(name="save")
def setup_save(
    *, request: Annotated[service.SetupSaveRequest, FLAT] = service.SetupSaveRequest(), common: Common = COMMON
) -> int:
    """Keep the first-run setup's progress (each option given replaces the saved one); --clear forgets it."""
    return run(service.setup_save, request, common)
