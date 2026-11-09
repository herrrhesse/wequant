"""Arithmetic cell by cell, NaN where an input has no value or the result is not finite."""

from __future__ import annotations

import numpy as np

from wequant.ops.inputs import elementwise


def MAX(a, b) -> np.ndarray:
    """The larger of a and b."""
    return elementwise(np.maximum, a, b)


def MIN(a, b) -> np.ndarray:
    """The smaller of a and b."""
    return elementwise(np.minimum, a, b)


def DIV(a, b) -> np.ndarray:
    """a / b; NaN where b is zero."""
    return elementwise(np.divide, a, b)


def ABS(x) -> np.ndarray:
    """|x|."""
    return elementwise(np.abs, x)


def LN(x) -> np.ndarray:
    """Natural logarithm; NaN where x <= 0."""
    return elementwise(np.log, x)


def ADD(a, b) -> np.ndarray:
    """a + b."""
    return elementwise(np.add, a, b)


def SUB(a, b) -> np.ndarray:
    """a − b."""
    return elementwise(np.subtract, a, b)


def MUL(a, b) -> np.ndarray:
    """a × b."""
    return elementwise(np.multiply, a, b)
