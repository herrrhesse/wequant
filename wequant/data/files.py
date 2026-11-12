"""Atomic file writes: a file either does not exist yet or is complete."""

from __future__ import annotations

import datetime as dt
import io
import os
import secrets
from pathlib import Path

import polars as pl

ROW_GROUP_ROWS = 262_144


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
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    if os.name != "nt":  # directories cannot be opened for fsync on Windows
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


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
