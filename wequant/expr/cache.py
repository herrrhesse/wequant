"""Evaluated expressions kept in memory, keyed by panel identity and canonical form."""

from __future__ import annotations

import hashlib
import threading
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from wequant.panel import Panel


class Cache:
    """Results by key; the least recently used leave once max_bytes is exceeded, the newest always stays. Safe to share
    between threads (a resident server's requests)."""

    def __init__(self, max_bytes: int = 4 << 30) -> None:
        self.max_bytes = max_bytes
        self.hits = self.misses = 0
        self._grids: dict[str, np.ndarray] = {}  # oldest first
        self._nbytes = 0
        self._lock = threading.Lock()

    @property
    def nbytes(self) -> int:
        """Bytes of the grids held."""
        return self._nbytes

    def get(self, key: str) -> np.ndarray | None:
        """The grid under key, now the most recently used, or None; counted as a hit or a miss."""
        with self._lock:
            grid = self._grids.pop(key, None)
            if grid is None:
                self.misses += 1
                return None
            self.hits += 1
            self._grids[key] = grid
            return grid

    def put(self, key: str, grid: np.ndarray) -> None:
        """Keep grid as the most recently used, then evict the least recently used beyond max_bytes."""
        with self._lock:
            self._drop(key)
            self._grids[key] = grid
            self._nbytes += grid.nbytes
            while len(self._grids) > 1 and self._nbytes > self.max_bytes:
                self._drop(next(iter(self._grids)))

    def _drop(self, key: str) -> None:
        grid = self._grids.pop(key, None)
        if grid is not None:
            self._nbytes -= grid.nbytes


def panel_key(panel: Panel) -> str:
    """What an expression's values depend on besides the expression: data, rule book, first and last date, codes."""
    codes = hashlib.sha256("\n".join(panel.codes.tolist()).encode()).hexdigest()[:12]
    first, last = (np.datetime_as_string(day, unit="D") for day in (panel.dates[0], panel.dates[-1]))
    return f"{panel.key}-{panel.rules_key}-{first}-{last}-{codes}"
