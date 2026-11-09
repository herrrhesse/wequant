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

    A valid partial sell is whole lots, which may include the position's whole odd remainder: odd lots go in one order.
    """
    if qty >= position:
        return position
    whole, shares = math.floor(position), math.floor(qty)
    odd = whole - max_buy(lot, whole)
    with_odd = odd + max_buy(lot, shares - odd) if odd <= shares else 0
    return max(max_buy(lot, shares), with_odd)
