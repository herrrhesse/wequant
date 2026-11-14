"""Full-grid arrays of one store version, kept on disk as .npy files keyed by the manifest."""

from __future__ import annotations

import io
import re
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import polars as pl

from wequant.data.files import FileInUse, atomic_write, remove_tree
from wequant.data.store import KEEP_VERSIONS, Store, tables_digest
from wequant.panel.build import BUILDERS

FORMAT = 2  # bump when the meaning of a cached array changes (2: list_days from the listing day, S2.11)
_KEY = re.compile(r"f([0-9]+)-v([0-9]+)-[0-9a-f]{12}")
_BUILDING: Counter[str] = Counter()  # panel keys a thread of this process is building arrays for
_LOCK = threading.Lock()


def cache_key(manifest: dict) -> str:
    """f<FORMAT>-v<version>-<first 12 hex of tables_digest(manifest)>."""
    return f"f{FORMAT}-v{manifest['version']}-{tables_digest(manifest)[:12]}"


class PanelCache:
    """Named full-grid arrays of one store version, built on first use and kept on disk."""

    def __init__(self, store: Store, manifest: dict, root: Path) -> None:
        self.store, self.manifest = store, manifest
        self.version, self.key = manifest["version"], cache_key(manifest)
        self.directory = Path(root) / "panel" / self.key

    def scan(self, table: str, **query) -> pl.LazyFrame:
        """store.scan pinned to this manifest."""
        return self.store.scan(table, manifest=self.manifest, **query)

    def array(self, name: str, build: Callable[[PanelCache], np.ndarray] | None = None) -> np.ndarray:
        """Read-only memory map of <name>.npy, built (default BUILDERS[name]) and written atomically on a miss. While
        it builds, no cleanup of this process removes the folder; one that does anyway (another process) costs the
        write, not the array."""
        path = self.directory / f"{name}.npy"
        try:
            return np.load(path, mmap_mode="r")
        except FileNotFoundError:
            pass
        with _building(self.key):
            array = (build or BUILDERS[name])(self)
            try:
                self._save(path, array)
                return np.load(path, mmap_mode="r")
            except FileNotFoundError:  # the folder went meanwhile: the array as built; the next call writes again
                array.setflags(write=False)
                return array

    def _save(self, path: Path, array: np.ndarray) -> None:
        if not self.directory.is_dir():
            self.directory.mkdir(parents=True, exist_ok=True)
            _prune(self.directory.parent, self.version)
        buffer = io.BytesIO()
        np.save(buffer, np.ascontiguousarray(array), allow_pickle=False)
        atomic_write(path, buffer.getvalue())


def keep_only(root: Path, manifest: dict) -> list[Path]:
    """Remove the panels under root/panel of every version but manifest's, except those being built; the removed
    directories."""
    key = cache_key(manifest)
    return _remove(Path(root) / "panel", lambda match: match[0] != key)


@contextmanager
def _building(key: str) -> Iterator[None]:
    """Mark the panel key as being built by this process while inside."""
    with _LOCK:
        _BUILDING[key] += 1
    try:
        yield
    finally:
        with _LOCK:
            _BUILDING[key] -= 1
            if not _BUILDING[key]:
                del _BUILDING[key]


def _prune(root: Path, version: int) -> None:
    """Remove sibling directories of another FORMAT or of versions the store no longer keeps."""
    _remove(root, lambda match: int(match[1]) != FORMAT or int(match[2]) <= version - KEEP_VERSIONS)


def _remove(directory: Path, doomed: Callable[[re.Match], bool]) -> list[Path]:
    """Remove the panel folders in directory whose key match doomed approves, never one being built; the removed."""
    with _LOCK:  # a build cannot start on a folder while it goes
        found = [(p, _KEY.fullmatch(p.name)) for p in directory.iterdir()] if directory.is_dir() else []
        removed = [path for path, match in found if match and doomed(match) and path.name not in _BUILDING]
        for path in removed:
            try:
                remove_tree(path)
            except FileInUse:  # mapped by a reader on Windows: the next cleanup takes it
                continue
        return removed
