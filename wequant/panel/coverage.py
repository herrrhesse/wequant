"""Index membership counts against each index's nominal size: the runs of days that are off."""

from __future__ import annotations

import datetime as dt
from typing import NamedTuple

import numpy as np

NOMINAL = {"000300.SH": 300, "000905.SH": 500, "000906.SH": 800, "000852.SH": 1000, "000985.SH": None}  # None: no size


class CountSpan(NamedTuple):
    """Consecutive days with the same member count off the nominal size."""

    start: dt.date
    end: dt.date
    days: int
    count: int
    kind: str  # short, over or empty


def count_spans(dates: np.ndarray, counts: np.ndarray, nominal: int | None) -> list[CountSpan]:
    """The runs of equal consecutive counts that are off nominal: short or over it, empty at 0; without a nominal size
    only empty days are off."""
    counts = np.asarray(counts, dtype=np.int64)
    off = counts == 0 if nominal is None else counts != nominal
    breaks = np.flatnonzero(np.diff(counts) != 0) + 1
    spans = []
    for lo, hi in zip(np.concatenate(([0], breaks)), np.concatenate((breaks, [counts.size]))):
        if counts.size and off[lo]:
            count = int(counts[lo])
            kind = "empty" if count == 0 else "short" if count < nominal else "over"
            spans.append(CountSpan(dates[lo].item(), dates[hi - 1].item(), int(hi - lo), count, kind))
    return spans
