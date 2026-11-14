"""Moving or copying a data root to another folder (round 3, F-30), and telling a complete data root from any folder.

A copy goes file by file into an empty or new folder and is checked against the source before anything switches; a
cancel or a failure removes what it copied and leaves the source as it was. The panel cache is rebuilt, never copied.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from wequant.data.files import remove_tree, rename
from wequant.data.store import Store, version_files

SKIPPED = frozenset(("cache", ".lock"))  # top-level entries a copy leaves out: rebuilt, or held by the writer
CHUNK = 8 << 20  # bytes copied between two looks at cancel


class Refused(ValueError):
    """A folder that cannot take or be the data root: a stable code (no_directory, not_a_data_root,
    data_root_incomplete, not_empty, inside, no_space, copy_mismatch) and an English message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Cancelled(Exception):
    """The copy stopped at its cancel signal; what it had copied is gone."""


class Copied(NamedTuple):
    files: int
    bytes: int
    renamed: bool  # a move on one file system: the folder was renamed, nothing copied


def is_data_root(path: Path) -> bool:
    """path holds a WeQuant store: a current manifest or a kept version."""
    return (path / "manifest.json").is_file() or any((path / "manifests").glob("manifest.v*.json"))


def problem(path: Path) -> Refused | None:
    """Why path is not a complete data root to switch to; None when it is."""
    if not path.is_dir():
        return Refused("no_directory", f"{path} is not a folder")
    if not is_data_root(path):
        return Refused("not_a_data_root", f"{path} holds no WeQuant data")
    try:
        found = Store(path).manifest()
        missing = [entry["path"] for entry in version_files(found) if not (path / entry["path"]).is_file()]
    except (OSError, ValueError, KeyError, TypeError):
        return Refused("data_root_incomplete", f"{path}: the current version's manifest cannot be read")
    if missing:
        return Refused(
            "data_root_incomplete", f"{path}: {len(missing)} files of version {found['version']} are missing"
        )
    return None


def files_of(root: Path) -> list[Path]:
    """The files a copy takes, relative to root: all but SKIPPED and leftover temporary files, in a stable order."""
    found = []
    for entry in sorted(root.iterdir()):
        if entry.name in SKIPPED:
            continue
        paths = [entry] if entry.is_file() else sorted(p for p in entry.rglob("*") if p.is_file())
        found += [p.relative_to(root) for p in paths if not (p.name.startswith(".") and p.name.endswith(".tmp"))]
    return found


def check_target(source: Path, target: Path, size: int) -> None:
    """Refused unless target is a new or empty folder outside source (and source outside it) with room for size."""
    source, target = source.resolve(), target.resolve()
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise Refused("inside", f"{target} and the data root {source} contain each other")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise Refused("not_empty", f"{target} is not an empty folder")
    free = shutil.disk_usage(_existing(target)).free
    if free < size:
        raise Refused("no_space", f"{target} has {free} bytes free; the data takes {size}")


def copy_root(
    source: Path,
    target: Path,
    move: bool,
    progress: Callable[[int, int], None],
    cancel: Callable[[], bool],
) -> Copied:
    """Copy (or move by renaming, on one file system) the data root source into target, checked file by file;
    Cancelled or any failure leaves target as it was found and source untouched."""
    files = files_of(source)
    sizes = [(source / f).stat().st_size for f in files]
    total, created = sum(sizes), not target.exists()
    if move and created and _same_device(source, target):
        try:
            rename(source, target)
            return Copied(len(files), total, True)
        except OSError:  # e.g. Windows: a file of it is open; copy instead
            pass
    try:
        target.mkdir(parents=True, exist_ok=True)
        done = 0
        for file, size in zip(files, sizes):
            done = _copy(source / file, target / file, done, total, progress, cancel)
        _verify(source, target, files, sizes)
    except BaseException:
        _undo(target, created)
        raise
    return Copied(len(files), total, False)


def remove_source(source: Path, files: list[Path]) -> list[Path]:
    """After a move: the copied files, then the skipped entries and every folder left empty; the paths that stayed."""
    for file in files:
        (source / file).unlink(missing_ok=True)
    for name in SKIPPED:
        entry = source / name
        try:
            remove_tree(entry) if entry.is_dir() else entry.unlink(missing_ok=True)
        except OSError:
            pass
    for folder in sorted((p for p in source.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
    if source.is_dir() and not any(source.iterdir()):
        source.rmdir()
    return sorted(p for p in source.rglob("*") if p.is_file()) if source.exists() else []


def _copy(src: Path, dst: Path, done: int, total: int, progress, cancel) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as reader, open(dst, "wb") as writer:
        while chunk := reader.read(CHUNK):
            if cancel():
                raise Cancelled("copy cancelled; what it had copied is removed")
            writer.write(chunk)
            done += len(chunk)
            progress(done, total)
    shutil.copystat(src, dst)
    return done


def _verify(source: Path, target: Path, files: list[Path], sizes: list[int]) -> None:
    """Refused copy_mismatch unless every file has its size and the copy reads as the same current version."""
    wrong = [f for f, size in zip(files, sizes) if not (target / f).is_file() or (target / f).stat().st_size != size]
    if wrong:
        raise Refused("copy_mismatch", f"{len(wrong)} copied files differ from the source, e.g. {wrong[0]}")
    if Store(target).manifest() != Store(source).manifest():
        raise Refused("copy_mismatch", "the copy does not read as the source's current version")


def _undo(target: Path, created: bool) -> None:
    """Remove what a failed copy put into target: target itself when the copy made it, else its contents."""
    if created:
        remove_tree(target)
        return
    for entry in target.iterdir():
        remove_tree(entry) if entry.is_dir() else entry.unlink(missing_ok=True)


def _existing(path: Path) -> Path:
    while not path.exists():
        path = path.parent
    return path


def _same_device(source: Path, target: Path) -> bool:
    return os.stat(source).st_dev == os.stat(_existing(target)).st_dev
