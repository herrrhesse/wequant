"""Valid order sizes under a board's lot rule."""

from __future__ import annotations

import math

from wequant.rules.book import Lot


def max_buy(lot: Lot, qty: int) -> int:
    """Largest valid buy order <= qty shares; 0 below the board minimum."""
    if qty < lot.min_qty:
        return 0
    return lot.min_qty + (qty - lot.min_qty) // lot.step * lot.step


def max_sell(lot: Lot, qty: float, position: float) -> float:
    """Largest valid sell <= qty of a (possibly fractional) position: whole lots, the whole odd remainder or all of it."""
    if qty >= position:
        return position
    lots = max_buy(lot, math.floor(qty))
    odd = position - max_buy(lot, math.floor(position))
    return max(lots, odd) if odd <= qty else lots
