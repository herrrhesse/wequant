"""Exact half-up rounding, the exchanges' rule for limit prices and fees."""

from __future__ import annotations


def round_half_up(numerator, denominator):
    """numerator / denominator rounded half up, for non-negative ints, int64 arrays or polars Int64 expressions."""
    return (2 * numerator + denominator) // (2 * denominator)
