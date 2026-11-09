"""Window operators over each stock's last n bars; NaN until n bars exist."""

from __future__ import annotations

import numba
import numpy as np

from wequant.ops.drivers import kernel, kernel2, per_stock, per_stock2
from wequant.ops.inputs import Grid, window
from wequant.ops.logic import EQ, GT, truth


def MA(x, n) -> Grid:
    """Mean of the last n bars."""
    return per_stock(x, _mean, window(n, 1))


def SUM(x, n) -> Grid:
    """Sum of the last n bars; n = 0 sums every bar since the grid's first row, so it depends on that anchor."""
    return per_stock(x, _sum, window(n, 0))


def WMA(x, n) -> Grid:
    """Weighted mean of the last n bars, weights n for the latest down to 1."""
    return per_stock(x, _wma, window(n, 1))


def STD(x, n) -> Grid:
    """Sample standard deviation of the last n bars."""
    return per_stock(x, _std, window(n, 2), 1)


def STDP(x, n) -> Grid:
    """Population standard deviation of the last n bars."""
    return per_stock(x, _std, window(n, 1), 0)


def AVEDEV(x, n) -> Grid:
    """Mean absolute deviation from the mean of the last n bars."""
    return per_stock(x, _avedev, window(n, 1))


def SLOPE(x, n) -> Grid:
    """Least-squares slope per bar of the last n bars."""
    return per_stock(x, _slope, window(n, 2))


def HHV(x, n) -> Grid:
    """Highest of the last n bars; n = 0 looks back to the grid's first row, so it depends on that anchor."""
    return per_stock(x, _highest, window(n, 0), 0)


def LLV(x, n) -> Grid:
    """Lowest of the last n bars; n = 0 looks back to the grid's first row, so it depends on that anchor."""
    return per_stock(x, _lowest, window(n, 0), 0)


def HHVBARS(x, n) -> Grid:
    """Bars since the highest of the last n bars (the latest on ties); n = 0 depends on the grid's first row."""
    return per_stock(x, _highest, window(n, 0), 1)


def LLVBARS(x, n) -> Grid:
    """Bars since the lowest of the last n bars (the latest on ties); n = 0 depends on the grid's first row."""
    return per_stock(x, _lowest, window(n, 0), 1)


def TS_RANK(x, n) -> Grid:
    """Average rank of the latest bar among the last n, divided by n: 1 when it is the highest."""
    return per_stock(x, _ts_rank, window(n, 1))


def CORR(x, y, n) -> Grid:
    """Correlation of x and y over their last n shared bars; NaN when either window is constant."""
    return per_stock2(x, y, _corr, window(n, 2))


def COUNT(cond, n) -> Grid:
    """Bars among the last n where cond is true (n = 0: since the grid's first row, so anchor-dependent).

    A bool mask is no condition: pass panel.on_bars(mask) or IF(panel.traded, mask, nan).
    """
    return SUM(truth(cond), n)


def EVERY(cond, n) -> Grid:
    """cond was true on each of the last n bars."""
    n = window(n, 1)
    return EQ(COUNT(cond, n), n)


def EXIST(cond, n) -> Grid:
    """cond was true on at least one of the last n bars."""
    return GT(COUNT(cond, window(n, 1)), 0)


@numba.njit(cache=True)
def _running_sum(v, out, n, divisor):
    total = 0.0
    for k in range(len(v)):
        total += v[k]
        if 0 < n <= k:
            total -= v[k - n]
        out[k] = total / divisor if k >= n - 1 else np.nan


@kernel
def _sum(v, out, n, _b):
    _running_sum(v, out, n, 1.0)


@kernel
def _mean(v, out, n, _b):
    _running_sum(v, out, n, n)


@kernel
def _wma(v, out, n, _b):
    out[: n - 1] = np.nan
    for k in range(n - 1, len(v)):
        total = 0.0
        for i in range(n):
            total += (n - i) * v[k - i]
        out[k] = total / (n * (n + 1) / 2)


@kernel
def _std(v, out, n, ddof):
    out[: n - 1] = np.nan
    for k in range(n - 1, len(v)):
        w = v[k - n + 1 : k + 1]
        mean = w.mean()
        squares = 0.0
        for value in w:
            squares += (value - mean) ** 2
        out[k] = np.sqrt(squares / (n - ddof))


@kernel
def _avedev(v, out, n, _b):
    out[: n - 1] = np.nan
    for k in range(n - 1, len(v)):
        w = v[k - n + 1 : k + 1]
        mean = w.mean()
        deviation = 0.0
        for value in w:
            deviation += abs(value - mean)
        out[k] = deviation / n


@kernel
def _slope(v, out, n, _b):
    middle = (n - 1) / 2
    out[: n - 1] = np.nan
    for k in range(n - 1, len(v)):
        w = v[k - n + 1 : k + 1]
        moment = 0.0
        for i in range(n):
            moment += (i - middle) * w[i]
        out[k] = moment / (n * (n * n - 1) / 12)


@numba.njit(cache=True)
def _extreme(v, out, n, sign, bars):
    """Highest (sign 1) or lowest (sign -1) of the last n bars, or the bars since it, from a monotonic deque."""
    queue = np.empty(len(v), np.int64)
    head = tail = 0
    for k in range(len(v)):
        while tail > head and sign * v[queue[tail - 1]] <= sign * v[k]:
            tail -= 1
        queue[tail] = k
        tail += 1
        if n > 0 and queue[head] <= k - n:
            head += 1
        best = queue[head]
        out[k] = np.nan if k < n - 1 else (k - best if bars else v[best])


@kernel
def _highest(v, out, n, bars):
    _extreme(v, out, n, 1.0, bars)


@kernel
def _lowest(v, out, n, bars):
    _extreme(v, out, n, -1.0, bars)


@kernel
def _ts_rank(v, out, n, _b):
    out[: n - 1] = np.nan
    for k in range(n - 1, len(v)):
        less = equal = 0
        for value in v[k - n + 1 : k + 1]:
            less += value < v[k]
            equal += value == v[k]
        out[k] = (less + (equal + 1) / 2) / n


@kernel2
def _corr(x, y, out, n):
    out[: n - 1] = np.nan
    for k in range(n - 1, len(x)):
        a, b = x[k - n + 1 : k + 1], y[k - n + 1 : k + 1]
        if a.max() == a.min() or b.max() == b.min():
            out[k] = np.nan
            continue
        mean_a, mean_b = a.mean(), b.mean()
        ab = aa = bb = 0.0
        for i in range(n):
            ab += (a[i] - mean_a) * (b[i] - mean_b)
            aa += (a[i] - mean_a) ** 2
            bb += (b[i] - mean_b) ** 2
        out[k] = ab / np.sqrt(aa * bb)
