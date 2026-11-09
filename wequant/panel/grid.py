"""Placing long frames on the date × code grid."""

from __future__ import annotations

import numpy as np
import polars as pl


def cells(
    frame: pl.DataFrame, dates: np.ndarray, codes: np.ndarray, date_column: str = "date"
) -> tuple[np.ndarray, np.ndarray]:
    """Row and column of each frame row; ValueError for a date or code not on the grid."""
    return day_rows(frame[date_column].to_numpy(), dates), code_columns(frame["code"], codes)


def day_rows(days: np.ndarray, dates: np.ndarray) -> np.ndarray:
    """Row of each day in the sorted dates; ValueError for a day that is not one of them."""
    rows, found = find_sorted(dates, days)
    if not found.all():
        raise ValueError(f"days not on the calendar: {np.unique(days[~found])[:5]}")
    return rows


def find_sorted(keys: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Position of each value in the sorted keys, and whether it is there."""
    positions = np.searchsorted(keys, values)
    found = positions < len(keys)
    found[found] = keys[positions[found]] == values[found]
    return positions, found


def code_columns(code: pl.Series, codes: np.ndarray) -> np.ndarray:
    """Column of each code in codes; ValueError for a code that is not one of them."""
    try:
        return code.cast(pl.Enum(pl.Series(codes))).to_physical().to_numpy()
    except pl.exceptions.InvalidOperationError as error:
        raise ValueError(f"codes not on the grid: {error}") from None


def asof_rows(dates: np.ndarray, days: np.ndarray) -> np.ndarray:
    """First grid row on or after each day; len(dates) when it is after the last."""
    return np.searchsorted(dates, days, "left")


def scatter(shape: tuple[int, int], rows: np.ndarray, cols: np.ndarray, values: np.ndarray, fill) -> np.ndarray:
    """A grid of fill with values at (rows, cols), in the dtype of values."""
    values = np.asarray(values)
    grid = np.full(shape, fill, dtype=values.dtype)
    grid[rows, cols] = values
    return grid


def fill_forward(events: np.ndarray, initial: float) -> np.ndarray:
    """Each cell takes the last non-NaN value at or above it in its column; initial before the first."""
    events = np.asarray(events, dtype=np.float64)
    rows = np.arange(len(events), dtype=np.int32).reshape((-1,) + (1,) * (events.ndim - 1))
    last = np.maximum.accumulate(np.where(np.isnan(events), -1, rows), axis=0)
    filled = np.take_along_axis(events, np.maximum(last, 0), axis=0)
    return np.where(last >= 0, filled, initial)
