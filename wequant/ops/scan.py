"""Operators that carry state along each stock's bars: lags, smoothing, crossings and events."""

from __future__ import annotations

import numpy as np

from wequant.ops.drivers import kernel, kernel2, per_stock, per_stock2
from wequant.ops.inputs import Grid, window
from wequant.ops.logic import truth


def REF(x, n) -> Grid:
    """The value n bars back; n < 0 would look ahead and raises ValueError."""
    return per_stock(x, _ref, window(n, 0))


def EMA(x, n) -> Grid:
    """Exponential mean with weight 2 / (n + 1), from the first bar; it depends on the grid's first row (the anchor)."""
    return per_stock(x, _sma, window(n, 1) + 1, 2)


def SMA(x, n, m=1) -> Grid:
    """TDX SMA: y = (m·x + (n − m)·y') / n from the first bar; it depends on the grid's first row (the anchor)."""
    n, m = window(n, 1), window(m, 1)
    if m > n:
        raise ValueError(f"SMA weight m = {m} exceeds n = {n}")
    return per_stock(x, _sma, n, m)


def CROSS(a, b) -> Grid:
    """1 on the bar where a rises above b (a > b now, a <= b on the previous shared bar), else 0."""
    return per_stock2(a, b, _cross)


def BARSLAST(cond) -> Grid:
    """Bars since cond was last true, NaN before it first is; counts from the grid's first row (the anchor).

    A bool mask is no condition: pass panel.on_bars(mask) or IF(panel.traded, mask, nan).
    """
    return per_stock(truth(cond), _barslast)


def BARSCOUNT(x) -> Grid:
    """Bars so far, today included; counts from the grid's first row (the anchor)."""
    return per_stock(x, _barscount)


def VALUEWHEN(cond, x) -> Grid:
    """x on the last bar where cond was true, NaN before it first is; depends on the grid's first row (the anchor)."""
    return per_stock2(truth(cond), x, _valuewhen)


@kernel
def _ref(v, out, n, _b):
    for k in range(len(v)):
        out[k] = v[k - n] if k >= n else np.nan


@kernel
def _sma(v, out, n, m):
    for k in range(len(v)):
        out[k] = v[k] if k == 0 else (m * v[k] + (n - m) * out[k - 1]) / n


@kernel2
def _cross(a, b, out, _n):
    for k in range(len(a)):
        out[k] = k > 0 and a[k] > b[k] and a[k - 1] <= b[k - 1]


@kernel
def _barslast(cond, out, _a, _b):
    last = -1
    for k in range(len(cond)):
        if cond[k] != 0:
            last = k
        out[k] = k - last if last >= 0 else np.nan


@kernel
def _barscount(v, out, _a, _b):
    for k in range(len(v)):
        out[k] = k + 1


@kernel2
def _valuewhen(cond, x, out, _n):
    value = np.nan
    for k in range(len(cond)):
        if cond[k] != 0:
            value = x[k]
        out[k] = value
