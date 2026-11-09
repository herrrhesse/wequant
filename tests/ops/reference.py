"""Naive operator references written straight from the definitions, and the registry of every operator."""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from fractions import Fraction
from functools import partial
from math import fsum, nan
from typing import NamedTuple

import numpy as np

from wequant import ops

# Input roles of each kind: x a grid, y a grid or a scalar, c a condition.
ROLES = {
    "series": "x",
    "pair": "xy",
    "condition": "c",
    "when": "cy",
    "section": "x",
    "unary": "x",
    "binary": "xy",
    "choice": "cyy",
    "range": "xyy",
}
ELEMENTWISE = {"unary", "binary", "choice", "range"}


class Op(NamedTuple):
    call: Callable
    reference: Callable
    kind: str
    minimum: int | None  # smallest window n; None when the operator takes none


def per_valid(f, *grids, **window) -> np.ndarray:
    """f on each column's bars (rows where every input is finite), its results placed on those rows."""
    arrays = np.broadcast_arrays(*(np.asarray(g, dtype=float) for g in grids))
    out = np.full(arrays[0].shape, nan)
    for j in range(out.shape[1]):
        rows = np.flatnonzero(np.logical_and.reduce([np.isfinite(a[:, j]) for a in arrays]))
        out[rows, j] = f(*(a[rows, j].tolist() for a in arrays), **window)
    return out


def per_row(f, x) -> np.ndarray:
    """f on each row's finite values, its results placed in their columns."""
    out = np.full(x.shape, nan)
    for i in range(x.shape[0]):
        cols = np.flatnonzero(np.isfinite(x[i]))
        out[i, cols] = f(x[i, cols].tolist())
    return out


def cellwise(f, *xs) -> np.ndarray:
    """f on each cell's values as Python floats; NaN where it gives a value that is not finite."""
    arrays = np.broadcast_arrays(*(np.asarray(x, dtype=float) for x in xs))
    out = np.full(arrays[0].shape, nan)
    for index in np.ndindex(out.shape):
        value = f(*(float(a[index]) for a in arrays))
        out[index] = value if math.isfinite(value) else nan
    return out


def strict(f):
    """f on finite arguments, NaN when any argument is NaN or ±inf."""
    return lambda *xs: f(*xs) if all(map(math.isfinite, xs)) else nan


def windows(v: list, n: int) -> list:
    """The last n bars at each bar (all bars so far when n is 0), None until n bars exist."""
    return [v[(k + 1 - n) if n else 0 : k + 1] if k + 1 >= n else None for k in range(len(v))]


def rolling(f):
    """A one-stock reference applying f to each window."""
    return lambda v, n: [nan if w is None else f(w) for w in windows(v, n)]


def corr(x: list, y: list, n: int) -> list:
    return [
        nan if a is None or max(a) == min(a) or max(b) == min(b) else statistics.correlation(a, b)
        for a, b in zip(windows(x, n), windows(y, n), strict=True)
    ]


def ref(v: list, n: int) -> list:
    return [v[k - n] if k >= n else nan for k in range(len(v))]


def sma(v: list, n: int, m: int) -> list:
    """Exact in fractions: y = x on the first bar, then (m·x + (n − m)·y) / n."""
    out, y = [], None
    for x in v:
        y = Fraction(x) if y is None else (m * Fraction(x) + (n - m) * y) / n
        out.append(float(y))
    return out


def sma_weight(n: int) -> int:
    """The m the registry uses for SMA(x, n, m): m = n for n <= 2, m < n beyond."""
    return 1 + n // 2


def cross(a: list, b: list) -> list:
    return [float(k > 0 and a[k] > b[k] and a[k - 1] <= b[k - 1]) for k in range(len(a))]


def last_true(cond: list) -> list:
    """The index of the last true bar up to each bar, None before the first."""
    return [max((i for i in range(k + 1) if cond[i] != 0), default=None) for k in range(len(cond))]


def barslast(cond: list) -> list:
    return [nan if i is None else k - i for k, i in enumerate(last_true(cond))]


def valuewhen(cond: list, x: list) -> list:
    return [nan if i is None else x[i] for i in last_true(cond)]


def rank(row: list) -> list:
    return [sum(y < x for y in row) + (sum(y == x for y in row) + 1) / 2 for x in row]


def zscore(row: list) -> list:
    if len(row) < 2 or max(row) == min(row):
        return [nan] * len(row)
    mean, std = statistics.fmean(row), statistics.pstdev(row)
    return [(x - mean) / std for x in row]


def wma(w: list) -> float:
    return fsum(i * x for i, x in enumerate(w, 1)) / (len(w) * (len(w) + 1) / 2)


def avedev(w: list) -> float:
    return fsum(abs(x - statistics.fmean(w)) for x in w) / len(w)


def slope(w: list) -> float:
    return statistics.linear_regression(range(len(w)), w).slope


def ts_rank(w: list) -> float:
    return (sum(x < w[-1] for x in w) + (sum(x == w[-1] for x in w) + 1) / 2) / len(w)


def _series(call, f, minimum=None, kind="series") -> Op:
    return Op(call, partial(per_valid, f), kind, minimum)


def _section(call, f) -> Op:
    return Op(call, partial(per_row, f), "section", None)


def _cells(call, f, kind) -> Op:
    return Op(call, partial(cellwise, f), kind, None)


REGISTRY: dict[str, Op] = {
    "REF": _series(ops.REF, ref, 0),
    "MA": _series(ops.MA, rolling(statistics.fmean), 1),
    "EMA": _series(ops.EMA, lambda v, n: sma(v, n + 1, 2), 1),
    "SMA": _series(lambda x, n: ops.SMA(x, n, sma_weight(n)), lambda v, n: sma(v, n, sma_weight(n)), 1),
    "WMA": _series(ops.WMA, rolling(wma), 1),
    "SUM": _series(ops.SUM, rolling(fsum), 0),
    "HHV": _series(ops.HHV, rolling(max), 0),
    "LLV": _series(ops.LLV, rolling(min), 0),
    "HHVBARS": _series(ops.HHVBARS, rolling(lambda w: w[::-1].index(max(w))), 0),
    "LLVBARS": _series(ops.LLVBARS, rolling(lambda w: w[::-1].index(min(w))), 0),
    "STD": _series(ops.STD, rolling(statistics.stdev), 2),
    "STDP": _series(ops.STDP, rolling(statistics.pstdev), 1),
    "AVEDEV": _series(ops.AVEDEV, rolling(avedev), 1),
    "SLOPE": _series(ops.SLOPE, rolling(slope), 2),
    "COUNT": _series(ops.COUNT, rolling(lambda w: float(sum(c != 0 for c in w))), 0, "condition"),
    "EVERY": _series(ops.EVERY, rolling(lambda w: float(all(c != 0 for c in w))), 1, "condition"),
    "EXIST": _series(ops.EXIST, rolling(lambda w: float(any(c != 0 for c in w))), 1, "condition"),
    "TS_RANK": _series(ops.TS_RANK, rolling(ts_rank), 1),
    "CORR": _series(ops.CORR, corr, 2, "pair"),
    "CROSS": _series(ops.CROSS, cross, kind="pair"),
    "BARSLAST": _series(ops.BARSLAST, barslast, kind="condition"),
    "BARSCOUNT": _series(ops.BARSCOUNT, lambda v: [k + 1.0 for k in range(len(v))]),
    "VALUEWHEN": _series(ops.VALUEWHEN, valuewhen, kind="when"),
    "CS_RANK": _section(ops.CS_RANK, rank),
    "CS_PCT": _section(ops.CS_PCT, lambda row: [r / len(row) for r in rank(row)]),
    "CS_ZSCORE": _section(ops.CS_ZSCORE, zscore),
    "CS_DEMEAN": _section(ops.CS_DEMEAN, lambda row: [x - statistics.fmean(row) for x in row]),
    "GT": _cells(ops.GT, strict(lambda a, b: float(a > b)), "binary"),
    "GE": _cells(ops.GE, strict(lambda a, b: float(a >= b)), "binary"),
    "LT": _cells(ops.LT, strict(lambda a, b: float(a < b)), "binary"),
    "LE": _cells(ops.LE, strict(lambda a, b: float(a <= b)), "binary"),
    "EQ": _cells(ops.EQ, strict(lambda a, b: float(a == b)), "binary"),
    "AND": _cells(ops.AND, strict(lambda a, b: float(a != 0 and b != 0)), "binary"),
    "OR": _cells(ops.OR, strict(lambda a, b: float(a != 0 or b != 0)), "binary"),
    "NOT": _cells(ops.NOT, strict(lambda a: float(a == 0)), "unary"),
    "IF": _cells(ops.IF, lambda c, a, b: (a if c != 0 else b) if math.isfinite(c) else nan, "choice"),
    "BETWEEN": _cells(ops.BETWEEN, strict(lambda x, lo, hi: float(lo <= x <= hi or hi <= x <= lo)), "range"),
    "MAX": _cells(ops.MAX, strict(max), "binary"),
    "MIN": _cells(ops.MIN, strict(min), "binary"),
    "ABS": _cells(ops.ABS, strict(abs), "unary"),
    "DIV": _cells(ops.DIV, strict(lambda a, b: a / b if b else nan), "binary"),
    "LN": _cells(ops.LN, strict(lambda x: math.log(x) if x > 0 else nan), "unary"),
    "ADD": _cells(ops.ADD, strict(lambda a, b: a + b), "binary"),
    "SUB": _cells(ops.SUB, strict(lambda a, b: a - b), "binary"),
    "MUL": _cells(ops.MUL, strict(lambda a, b: a * b), "binary"),
}
