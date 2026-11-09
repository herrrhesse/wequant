"""Comparisons and logic, cell by cell: 1.0 true, 0.0 false, NaN where an input has no value."""

from __future__ import annotations

import numpy as np

from wequant.ops.inputs import common_ndim, defined, elementwise, series, values


def GT(a, b) -> np.ndarray:
    """a > b."""
    return elementwise(np.greater, a, b)


def GE(a, b) -> np.ndarray:
    """a >= b."""
    return elementwise(np.greater_equal, a, b)


def LT(a, b) -> np.ndarray:
    """a < b."""
    return elementwise(np.less, a, b)


def LE(a, b) -> np.ndarray:
    """a <= b."""
    return elementwise(np.less_equal, a, b)


def EQ(a, b) -> np.ndarray:
    """a == b."""
    return elementwise(np.equal, a, b)


def AND(a, b) -> np.ndarray:
    """Both a and b are nonzero."""
    return elementwise(np.logical_and, a, b)


def OR(a, b) -> np.ndarray:
    """a or b is nonzero."""
    return elementwise(np.logical_or, a, b)


def NOT(a) -> np.ndarray:
    """a is zero."""
    return elementwise(np.logical_not, a)


def IF(cond, a, b) -> np.ndarray:
    """a where cond is nonzero, b where it is zero, NaN where cond has no value."""
    cond, a, b = values(cond), values(a), values(b)
    common_ndim(cond, a, b)
    return defined(np.where(cond != 0, a, b), cond)


def BETWEEN(x, lo, hi) -> np.ndarray:
    """x lies between lo and hi inclusive, in either order."""
    return elementwise(_between, x, lo, hi)


def truth(cond) -> np.ndarray:
    """A time-series condition as 1.0 (finite and nonzero) or 0.0, NaN without a bar; bool and int are rejected."""
    cond = series(cond)
    return defined(cond != 0, cond)


def _between(x, lo, hi):
    return ((lo <= x) & (x <= hi)) | ((hi <= x) & (x <= lo))
