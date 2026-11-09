"""Building blocks on numpy arrays: returns, drawdowns, runs and calendar periods."""

from __future__ import annotations

import numpy as np


def simple_returns(levels: np.ndarray) -> np.ndarray:
    """levels[t] / levels[t - 1] - 1 for t >= 1; a NaN level makes that day and the next NaN."""
    levels = np.asarray(levels, dtype=np.float64)
    return levels[1:] / levels[:-1] - 1


def drawdowns(nav: np.ndarray) -> np.ndarray:
    """1 - nav / its running maximum: 0 on a new high."""
    nav = np.asarray(nav, dtype=np.float64)
    return 1 - nav / np.maximum.accumulate(nav)


def runs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Start and stop (exclusive) of each run of True values."""
    edges = np.diff(np.concatenate(([0], np.asarray(mask, dtype=np.int8), [0])))
    return np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)


def period_keys(dates: np.ndarray, period: str) -> np.ndarray:
    """The period each date falls in: "week" gives its Monday, "month" its datetime64[M]."""
    dates = np.asarray(dates, dtype="datetime64[D]")
    if period == "month":
        return dates.astype("datetime64[M]")
    if period == "week":
        days = dates.astype(np.int64)
        return (days - (days + 3) % 7).astype("datetime64[D]")  # day 0, 1970-01-01, was a Thursday
    raise ValueError(f"unknown period {period!r}")


def compound(returns: np.ndarray, keys: np.ndarray) -> np.ndarray:
    """The compound return of each run of equal consecutive keys; NaN if any of its returns is NaN."""
    returns, keys = np.asarray(returns, dtype=np.float64), np.asarray(keys)
    if not returns.size:
        return returns
    starts = np.flatnonzero(np.concatenate(([True], keys[1:] != keys[:-1])))
    return np.multiply.reduceat(1 + returns, starts) - 1


def annualize(growth: float, periods: int, periods_per_year: int) -> float:
    """The yearly return of growing by the factor growth over periods periods."""
    return float(growth ** (periods_per_year / periods) - 1)
