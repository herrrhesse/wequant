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
    """The whole (possibly fractional) position when qty covers it, else the largest valid order of whole shares <= qty.

    A valid partial sell is whole lots, or the position's whole odd remainder in whole shares (odd lots go in one order).
    """
    if qty >= position:
        return position
    lots = max_buy(lot, math.floor(qty))
    whole = math.floor(position)
    odd = whole - max_buy(lot, whole)
    return max(lots, odd) if odd <= qty else lots
