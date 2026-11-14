"""Atomic file writes: a file either does not exist yet or is complete. Windows refuses to replace or delete a file
another handle has open or mapped (round 3, W0), so replacing and removing try a few times before they give up."""

from __future__ import annotations

import datetime as dt
import io
import os
import secrets
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import polars as pl

ROW_GROUP_ROWS = 262_144
PAUSES = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6)  # seconds between tries of a replace or a delete the system refuses


class FileInUse(PermissionError):
    """A file stayed open elsewhere (another program, or a reader on Windows) through every try of a replace or a
    delete; nothing was changed."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.path = Path(path)

    def __str__(self) -> str:
        return f"{self.path} is in use by another program; nothing was changed, try again once it is closed"


def utc_now() -> str:
    """Now in UTC to the second, ISO 8601: the default clock of what the store and the library write."""
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def parquet_bytes(df: pl.DataFrame) -> bytes:
    """Parquet bytes that depend only on the content: the writer splits row groups at chunk boundaries."""
    buffer = io.BytesIO()
    df.rechunk().write_parquet(buffer, compression="zstd", row_group_size=ROW_GROUP_ROWS)
    return buffer.getvalue()


def atomic_write(path: Path, data: bytes) -> None:
    """Write to a temporary name beside path, fsync, then rename over path; nothing partial is ever left at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    if os.name != "nt":  # directories cannot be opened for fsync on Windows
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def replace(source: Path, target: Path) -> None:
    """os.replace, tried again while the system refuses; FileInUse after the last try, target unchanged."""
    _retried(lambda: os.replace(source, target), target)


def rename(source: Path, target: Path) -> None:
    """os.rename (a directory too), tried again while the system refuses; FileInUse after the last try."""
    _retried(lambda: os.rename(source, target), source)


def remove_tree(path: Path) -> None:
    """shutil.rmtree, tried again while the system refuses; FileInUse after the last try (some files may be gone)."""
    _retried(lambda: shutil.rmtree(path) if Path(path).exists() else None, path)


def _retried(change: Callable[[], None], path: Path) -> None:
    for pause in (*PAUSES, None):
        try:
            change()
            return
        except PermissionError:
            if pause is None:
                raise FileInUse(path) from None
            time.sleep(pause)


class OutsideRoot(PermissionError):
    """A path a write or delete would touch resolves outside the data root, through a symbolic link: refused, so that
    no data elsewhere changes (a data root of links is fine to read)."""

    def __init__(self, path: Path, root: Path) -> None:
        super().__init__(path, root)
        self.path, self.root = Path(path), Path(root)

    def __str__(self) -> str:
        return f"{self.path} resolves outside the data root {self.root}; refusing to write or delete there"


def check_inside(root: Path, paths) -> None:
    """OutsideRoot for the first of paths (existing or not) whose resolved path is not inside root's."""
    real = Path(os.path.realpath(root))
    for path in paths:
        if not Path(os.path.realpath(path)).is_relative_to(real):
            raise OutsideRoot(path, root)
