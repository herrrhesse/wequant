"""Browsing folders for a data root (round 3, F-30): the usual places to start from, a folder's subfolders with the
free space of their disk, and a new folder. Only folders: no file name or content is ever listed."""

from __future__ import annotations

import os
import shutil
import string
from pathlib import Path
from typing import NamedTuple

from wequant.paths import WINDOWS
from wequant.storage.relocate import Refused, is_data_root

MOUNTS = ("/media", "/mnt", "/run/media", "/Volumes")  # where other disks show up on Linux and macOS
FORBIDDEN = set('<>:"/\\|?*')  # characters Windows refuses in a name; "/" is a separator everywhere


class Place(NamedTuple):
    kind: str  # home, root, drive or mount
    path: Path


class Folder(NamedTuple):
    name: str
    path: Path
    is_data_root: bool
    size_bytes: int | None  # a data root's size, else None


def places() -> list[Place]:
    """The home folder, then every drive (Windows) or the root and each mounted disk (Linux, macOS)."""
    found = [Place("home", Path.home())]
    if WINDOWS:
        drives = (Path(f"{letter}:\\") for letter in string.ascii_uppercase)
        return found + [Place("drive", drive) for drive in drives if drive.exists()]
    mounted = [Place("mount", p) for base in MOUNTS for p in _subfolders(Path(base)) if os.path.ismount(p)]
    return [*found, Place("root", Path("/")), *mounted]


def free_bytes(path: Path) -> int:
    """Free space on the disk that holds path."""
    return shutil.disk_usage(path).free


def subfolders(path: Path) -> list[Folder]:
    """path's visible subfolders by name, each marked when it is a data root (with its size); Refused no_directory, or
    PermissionError, for a folder that cannot be read."""
    if not path.is_dir():
        raise Refused("no_directory", f"{path} is not a folder")
    found = []
    for child in _subfolders(path):
        root = _is_root(child)
        found.append(Folder(child.name, child, root, _size(child) if root else None))
    return found


def make(parent: Path, name: str) -> Path:
    """The new folder name inside parent; Refused bad_name, exists or no_directory."""
    if not name.strip() or name in (".", "..") or FORBIDDEN & set(name) or name != name.rstrip(" ."):
        raise Refused("bad_name", f"{name!r} cannot name a folder")
    if not parent.is_dir():
        raise Refused("no_directory", f"{parent} is not a folder")
    folder = parent / name
    if folder.exists():
        raise Refused("exists", f"{folder} exists already")
    folder.mkdir()
    return folder


def _subfolders(path: Path) -> list[Path]:
    try:
        children = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except FileNotFoundError:
        return []
    return [p for p in children if not p.name.startswith(".") and _is_dir(p)]


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _is_root(path: Path) -> bool:
    try:
        return is_data_root(path)
    except OSError:
        return False


def _size(path: Path) -> int | None:
    try:
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    except OSError:
        return None
