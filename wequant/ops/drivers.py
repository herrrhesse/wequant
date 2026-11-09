"""Drivers that run a kernel over each stock's bars or each date's values: the one home of the skip-NaN rule."""

from __future__ import annotations

import os

import numba
import numpy as np
from numba import prange, types

from wequant.ops.inputs import Grid, Value, common_ndim, series, values

# Set before numba starts its threads: idle OpenMP workers otherwise spin, and on a busy machine a call then
# costs about 6 ms instead of 0.1 ms.
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")

KERNEL = types.FunctionType(types.void(types.float64[::1], types.float64[::1], types.int64, types.int64))
KERNEL2 = types.FunctionType(types.void(types.float64[::1], types.float64[::1], types.float64[::1], types.int64))
kernel = numba.njit(KERNEL.signature, cache=True)
kernel2 = numba.njit(KERNEL2.signature, cache=True)

_IN = types.Array(types.float64, 2, "A", readonly=True)
_OUT = types.float64[:, :]
_INT = types.int64


@numba.njit(cache=True)
def _gather(cells, index, bars):
    """Copy the finite cells and their positions to the front of bars and index; returns how many there are."""
    m = 0
    for i in range(len(cells)):
        # Branch-free: every cell is written and only a finite one is kept, which is much faster on C-order grids.
        index[m] = i
        bars[m] = cells[i]
        m += np.isfinite(cells[i])
    return m


@numba.njit(cache=True)
def _gather2(x, y, index, xs, ys):
    """_gather for the positions where both x and y are finite."""
    m = 0
    for i in range(len(x)):
        index[m] = i
        xs[m] = x[i]
        ys[m] = y[i]
        m += np.isfinite(x[i]) & np.isfinite(y[i])
    return m


@numba.njit(cache=True)
def _scatter(result, index, cells):
    """Write each result back to its position."""
    for k in range(len(result)):
        cells[index[k]] = result[k]


@numba.njit(types.void(_IN, _OUT, KERNEL, _INT, _INT), parallel=True, cache=True)
def by_column(x, out, kern, a, b):
    """kern(bars, result, a, b) on each column's finite cells, the results written back to their rows."""
    rows, cols = x.shape
    for j in prange(cols):
        index, bars, result = np.empty(rows, np.int64), np.empty(rows), np.empty(rows)
        m = _gather(x[:, j], index, bars)
        kern(bars[:m], result[:m], a, b)
        _scatter(result[:m], index, out[:, j])


@numba.njit(types.void(_IN, _IN, _OUT, KERNEL2, _INT), parallel=True, cache=True)
def by_column2(x, y, out, kern, n):
    """kern(xs, ys, result, n) on the rows where both x and y are finite, column by column."""
    rows, cols = x.shape
    for j in prange(cols):
        index, xs, ys, result = np.empty(rows, np.int64), np.empty(rows), np.empty(rows), np.empty(rows)
        m = _gather2(x[:, j], y[:, j], index, xs, ys)
        kern(xs[:m], ys[:m], result[:m], n)
        _scatter(result[:m], index, out[:, j])


@numba.njit(types.void(_IN, _OUT, KERNEL, _INT, _INT), parallel=True, cache=True)
def by_row(x, out, kern, a, b):
    """kern(values, result, a, b) on each row's finite cells, the results written back to their columns."""
    rows, cols = x.shape
    for i in prange(rows):
        index, cells, result = np.empty(cols, np.int64), np.empty(cols), np.empty(cols)
        m = _gather(x[i], index, cells)
        kern(cells[:m], result[:m], a, b)
        _scatter(result[:m], index, out[i])


def per_stock(x: Grid, kern, a: int = 0, b: int = 0) -> Grid:
    """kern over each stock's bars; NaN on days without a bar."""
    x = series(x)
    out = _nan_like(x)
    by_column(_columns(x), _columns(out), kern, a, b)
    return out


def per_stock2(x: Grid, y: Value, kern, n: int = 0) -> Grid:
    """kern over the bars x and y share; y may also be a scalar."""
    x = series(x)
    y = series(y) if np.ndim(y) else values(y)  # a constant has a value every day, whatever its type
    common_ndim(x, y)
    shape = np.broadcast_shapes(x.shape, y.shape)
    x, y = np.broadcast_to(x, shape), np.broadcast_to(y, shape)
    out = _nan_like(x)
    by_column2(_columns(x), _columns(y), _columns(out), kern, n)
    return out


def per_date(x: Grid, kern, a: int = 0, b: int = 0) -> Grid:
    """kern over each date's finite values; 2-D only."""
    x = series(x)
    if x.ndim != 2:
        raise ValueError("cross-sectional operators need a (dates, codes) grid")
    out = _nan_like(x)
    by_row(x, out, kern, a, b)
    return out


def _nan_like(x: np.ndarray) -> np.ndarray:
    # Allocated here: allocating a full-market array inside numba costs ~200 ms in page faults.
    return np.full(x.shape, np.nan, order="F" if x.flags.f_contiguous else "C")


def _columns(x: np.ndarray) -> np.ndarray:
    return x[:, np.newaxis] if x.ndim == 1 else x
