"""Atomic file writes: a file either does not exist yet or is complete."""

from __future__ import annotations

import io
import os
import secrets
from pathlib import Path

import polars as pl


def parquet_bytes(df: pl.DataFrame) -> bytes:
    buffer = io.BytesIO()
    df.write_parquet(buffer, compression="zstd")
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
