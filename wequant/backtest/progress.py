"""How a long run reports progress and learns it should stop."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import NamedTuple

PROGRESS_DAYS = 20  # run days between two progress reports


class Progress(NamedTuple):
    """Where a run is: run days closed of all, the last closed day and the equity at its close."""

    done: int
    total: int
    day: dt.date
    equity: float


class Cancelled(Exception):
    """A run stopped by its cancel signal; it leaves no result."""


def check_cancel(cancel: Callable[[], bool] | None) -> None:
    """Raise Cancelled when cancel says so."""
    if cancel is not None and cancel():
        raise Cancelled("run cancelled")


def checkpoint(progress: Callable[[Progress], None] | None, cancel: Callable[[], bool] | None, state: Progress) -> None:
    """Report state, then check_cancel(cancel)."""
    if progress is not None:
        progress(state)
    check_cancel(cancel)
