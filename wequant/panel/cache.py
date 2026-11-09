"""Full-grid arrays of one store version, kept on disk as .npy files keyed by the manifest."""

from __future__ import annotations

import io
import re
import shutil
from collections.abc import Callable
from pathlib import Path

import numpy as np
import polars as pl

from wequant.data.files import atomic_write
from wequant.data.store import KEEP_VERSIONS, Store, tables_digest
from wequant.panel.build import BUILDERS

FORMAT = 1  # bump when the meaning of a cached array changes
_KEY = re.compile(r"f([0-9]+)-v([0-9]+)-[0-9a-f]{12}")


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
        """Read-only memory map of <name>.npy, built (default BUILDERS[name]) and written atomically on a miss."""
        path = self.directory / f"{name}.npy"
        if not path.exists():
            self._save(path, (build or BUILDERS[name])(self))
        return np.load(path, mmap_mode="r")

    def _save(self, path: Path, array: np.ndarray) -> None:
        if not self.directory.is_dir():
            self.directory.mkdir(parents=True, exist_ok=True)
            _prune(self.directory.parent, self.version)
        buffer = io.BytesIO()
        np.save(buffer, np.ascontiguousarray(array), allow_pickle=False)
        atomic_write(path, buffer.getvalue())


def _prune(root: Path, version: int) -> None:
    """Remove sibling directories of another FORMAT or of versions the store no longer keeps."""
    for path in root.iterdir():
        match = _KEY.fullmatch(path.name)
        if match and (int(match[1]) != FORMAT or int(match[2]) <= version - KEEP_VERSIONS):
            shutil.rmtree(path, ignore_errors=True)
