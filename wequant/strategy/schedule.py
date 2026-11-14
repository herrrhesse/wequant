"""Decision rows: the days whose close decides the next day's targets."""

from __future__ import annotations

import numpy as np

from wequant.panel.periods import period_ends


def decision_rows(dates: np.ndarray, base: int, last: int, period: str, every: int) -> np.ndarray:
    """(D,) int64: base, then every k-th trading day, week end or month end after it, all before last; a week or month
    ends as WEEK and MONTH bars end (wequant.panel.periods), the next trading date being published in advance."""
    rows = np.arange(base + 1, last)
    if period != "day":
        rows = rows[period_ends(dates, period)[rows]]
    return np.concatenate(([base], rows[every - 1 :: every])).astype(np.int64)


def upcoming(dates: np.ndarray, base: int, period: str, every: int, first: int, count: int) -> np.ndarray:
    """Rows of the first count decision days on or after row first, decision_rows counting from base over dates (the
    last date, whose next is unknown, decides nothing)."""
    rows = decision_rows(dates, base, len(dates) - 1, period, every)
    return rows[rows >= first][:count]
