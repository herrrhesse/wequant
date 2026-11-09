"""Cross-sectional operators over each date's stocks with a value."""

from __future__ import annotations

import numpy as np

from wequant.ops.drivers import kernel, per_date
from wequant.ops.inputs import Grid


def CS_RANK(x) -> Grid:
    """Rank within the date from 1 (lowest); ties share their average rank."""
    return per_date(x, _rank, 0)


def CS_PCT(x) -> Grid:
    """CS_RANK divided by the number of stocks with a value that date: 1 for the highest."""
    return per_date(x, _rank, 1)


def CS_ZSCORE(x) -> Grid:
    """(x − mean) / population std within the date; NaN for the date when fewer than 2 values or all are equal."""
    return per_date(x, _zscore)


def CS_DEMEAN(x) -> Grid:
    """x minus the date's mean."""
    return per_date(x, _demean)


@kernel
def _rank(v, out, pct, _b):
    order = np.argsort(v, kind="mergesort")
    start = 0
    while start < len(v):
        end = start + 1
        while end < len(v) and v[order[end]] == v[order[start]]:
            end += 1
        rank = (start + 1 + end) / 2  # the mean of the 1-based ranks start + 1 .. end
        for i in range(start, end):
            out[order[i]] = rank / len(v) if pct else rank
        start = end


@kernel
def _zscore(v, out, _a, _b):
    if len(v) < 2 or v.max() == v.min():
        out[:] = np.nan
    else:
        out[:] = (v - v.mean()) / v.std()


@kernel
def _demean(v, out, _a, _b):
    out[:] = v - v.mean()
