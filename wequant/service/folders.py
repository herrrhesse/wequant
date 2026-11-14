"""fs places | list | mkdir (round 3, F-30): what the browser cannot see itself, to pick a data root's folder; only
folders, never a file's name or content. The service binds to 127.0.0.1 only, as before."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from wequant.service.base import Context, ErrorDetail, Model, not_found
from wequant.service.errors import Exit, ServiceError
from wequant.service.specs import Name
from wequant.storage import folders
from wequant.storage.relocate import Refused

PLACE_LABELS = {"home": "主目录", "root": "根目录"}  # drives read "C: 盘", mounted disks by their folder name


class PlacesRequest(Model):
    """No fields."""


class PlaceOut(Model):
    """A place to start browsing from."""

    label: str
    """Chinese, e.g. 主目录, C: 盘, or a mounted disk's name."""
    kind: str
    """home, root, drive or mount."""
    path: str
    free_bytes: int | None
    """Free space on its disk; null when it cannot be read."""


class Places(Model):
    """The usual places: home, then the drives (Windows) or the root and the mounted disks."""

    rows: list[PlaceOut]


class FolderRequest(Model):
    """A folder to list."""

    path: str


class FolderOut(Model):
    """One subfolder."""

    name: str
    path: str
    is_wequant_home: bool
    """It holds WeQuant data (a manifest or a kept version)."""
    size_bytes: int | None
    """A data root's size; null for other folders."""


class Listing(Model):
    """A folder's visible subfolders by name, with the free space of its disk; files are never listed."""

    path: str
    """The folder, absolute."""
    parent: str | None
    """null at a drive's or the file system's root."""
    free_bytes: int
    dirs: list[FolderOut]


class MkdirRequest(Model):
    """A new folder inside path."""

    path: str
    name: Name


class Made(Model):
    """The folder made."""

    path: str


def fs_places(request: PlacesRequest, context: Context = Context()) -> Places:
    """The places to start browsing from, with the free space of their disks."""
    return Places(rows=[_place(place) for place in folders.places()])


def fs_list(request: FolderRequest, context: Context = Context()) -> Listing:
    """A folder's subfolders, each marked when it is a WeQuant data root; no file names."""
    path = Path(request.path).expanduser().absolute()
    with refused():
        found = folders.subfolders(path)
        free = folders.free_bytes(path)
    rows = [
        FolderOut(name=f.name, path=str(f.path), is_wequant_home=f.is_data_root, size_bytes=f.size_bytes) for f in found
    ]
    return Listing(path=str(path), parent=None if path.parent == path else str(path.parent), free_bytes=free, dirs=rows)


def fs_mkdir(request: MkdirRequest, context: Context = Context()) -> Made:
    """Make a folder inside path."""
    with refused():
        return Made(path=str(folders.make(Path(request.path).expanduser().absolute(), request.name)))


@contextmanager
def refused() -> Iterator[None]:
    """A folder that is not there as NOT_FOUND no_directory, one that cannot be read or made as USAGE."""
    try:
        yield
    except Refused as error:
        if error.code == "no_directory":
            raise not_found("no_directory", str(error), ["path"]) from None
        raise ServiceError(Exit.USAGE, str(error), [ErrorDetail(code=error.code, message=str(error))]) from None
    except PermissionError as error:
        message = f"no permission to read or write {error.filename or 'the folder'}"
        raise ServiceError(
            Exit.USAGE, message, [ErrorDetail(code="no_access", message=message, loc=["path"])]
        ) from None


def _place(place: folders.Place) -> PlaceOut:
    label = PLACE_LABELS.get(place.kind) or (f"{str(place.path)[:2]} 盘" if place.kind == "drive" else place.path.name)
    try:
        free = folders.free_bytes(place.path)
    except OSError:
        free = None
    return PlaceOut(label=label, kind=place.kind, path=str(place.path), free_bytes=free)
