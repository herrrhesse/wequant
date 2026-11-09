"""Operator inputs: validation, conversion and the rule that NaN and ±inf mean no value."""

from __future__ import annotations

import numpy as np

Grid = np.ndarray  # float64 (dates, codes), or (dates,) for one security


def series(x) -> np.ndarray:
    """x as float64 with 1 or 2 dimensions; TypeError for bool or int input, which cannot mark a day without a bar."""
    array = np.asarray(x)
    if array.dtype.kind != "f":
        raise TypeError(f"time-series input must be float with NaN on days without a bar, not {array.dtype}")
    if array.ndim not in (1, 2):
        raise ValueError(f"time-series input needs 1 or 2 dimensions, not {array.ndim}")
    return array.astype(np.float64, copy=False)


def values(x) -> np.ndarray:
    """x as a float64 array (bools, ints and scalars accepted) for elementwise operators."""
    array = np.asarray(x)
    if array.dtype.kind not in "buif":
        raise TypeError(f"elementwise input must be bool, int or float, not {array.dtype}")
    return array.astype(np.float64, copy=False)


def window(n, minimum: int) -> int:
    """n as an int; TypeError if not an integer (bool rejected), ValueError below minimum."""
    if isinstance(n, bool) or not isinstance(n, int | np.integer):
        raise TypeError(f"window must be an integer, not {type(n).__name__}")
    if n < minimum:
        raise ValueError(f"window {n} is below the minimum {minimum}")
    return int(n)


def common_ndim(*xs) -> None:
    """ValueError unless all non-scalar inputs have the same ndim."""
    if len({np.ndim(x) for x in xs} - {0}) > 1:
        raise ValueError(f"inputs mix dimensions {[np.ndim(x) for x in xs]}")


def defined(result, *xs) -> np.ndarray:
    """result as float64, NaN wherever it or one of xs is not finite."""
    ok = np.isfinite(result)
    for x in xs:
        ok = ok & np.isfinite(x)
    return np.where(ok, result, np.nan)


def elementwise(op, *xs) -> np.ndarray:
    """op over the values of xs, NaN wherever an input or the result is not finite."""
    xs = [values(x) for x in xs]
    common_ndim(*xs)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        return defined(op(*xs), *xs)
