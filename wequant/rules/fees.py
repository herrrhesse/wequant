"""Statutory fees of a fill: stamp tax and transfer fee, in 厘 rounded half up to the fen."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from wequant.rules.book import Rate, RuleBook
from wequant.rules.rounding import round_half_up

PAR = 1000  # 厘 per share: a par value of 1 元 is assumed for every stock
FEN = 10  # 厘
SIDES = ("buy", "sell")


@dataclass(frozen=True)
class Fees:
    stamp_tax: int  # 厘, half up to the fen
    transfer_fee: int  # 厘, half up to the fen

    @property
    def total(self) -> int:
        return self.stamp_tax + self.transfer_fee


def trade_fees(book: RuleBook, exchange: str, day: dt.date, side: str, shares, price) -> Fees:
    """Statutory fees of one fill of whole shares at price 厘 on side ('buy'/'sell'); shares and price may be int64 arrays. Commission is broker config, not a rule."""
    if side not in SIDES:
        raise ValueError(f"side {side!r} is not buy or sell")
    stamp = book.in_force("stamp_tax", exchange, day)
    transfer = book.in_force("transfer_fee", exchange, day)
    return Fees(_fee(stamp, side, shares, price), _fee(transfer, side, shares, price))


def _fee(rate: Rate, side: str, shares, price):
    base = shares * PAR if rate.basis == "par" else shares * price
    fraction = rate.buy if side == "buy" else rate.sell
    return round_half_up(base * fraction.numerator, fraction.denominator * FEN) * FEN
