"""How orders fill: the price model and the trading costs a run assumes."""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from fractions import Fraction
from functools import cached_property

import numpy as np

from wequant.rules.rounding import round_half_up

EXECUTION_VERSION = 2  # bump when an execution rule, a default, a reason or a status changes meaning
PRICES = ("open", "close")
FEN = 10  # 厘
LI = 1000  # 厘 per yuan


@dataclass(frozen=True)
class Execution:
    """Fill price and trading costs of a run; rates are read exactly as written (0.0003 is 3/10000)."""

    price: str = "open"  # the fill day's raw open (D4); "close" fills at its raw close
    commission_rate: float = 0.0003  # of the whole-share trade value, both sides
    min_commission: float = 5.0  # yuan per order that trades whole shares; a whole number of fen
    slippage_bps: float = 10.0  # 0.1% per side: a cost on the whole-share trade value; price and limits unmoved

    def __post_init__(self) -> None:
        """ValueError for a price outside PRICES or a cost out of range; numbers, numpy scalars included, are kept as float."""
        if not isinstance(self.price, str) or self.price not in PRICES:
            raise ValueError(f"price {self.price!r} is not one of {PRICES}")
        for name in ("commission_rate", "min_commission", "slippage_bps"):
            object.__setattr__(self, name, finite_number(name, getattr(self, name)))
        if not 0 <= self.commission_rate < 1:
            raise ValueError(f"commission_rate {self.commission_rate} is not in [0, 1)")
        if self.min_commission < 0 or (exact(self.min_commission) * LI) % FEN:
            raise ValueError(f"min_commission {self.min_commission} is not a non-negative whole number of fen")
        if not 0 <= self.slippage_bps < 10000:
            raise ValueError(f"slippage_bps {self.slippage_bps} is not in [0, 10000)")

    def to_dict(self) -> dict[str, str | float]:
        """Every field, numbers as float: the form a run archive and a spec store."""
        return {field.name: getattr(self, field.name) for field in fields(self)}

    def commission(self, value: int) -> int:
        """Commission in 厘 on a trade value in 厘: the rate half up to the fen, at least the minimum."""
        return max(_to_fen(value, self._rate), self._minimum)

    def slippage(self, value: int) -> int:
        """Slippage in 厘 on a trade value in 厘, half up to the fen."""
        return _to_fen(value, self._slippage)

    @cached_property
    def _rate(self) -> Fraction:
        return exact(self.commission_rate)

    @cached_property
    def _minimum(self) -> int:
        return int(exact(self.min_commission) * LI)

    @cached_property
    def _slippage(self) -> Fraction:
        return exact(self.slippage_bps) / 10000


def finite_number(name: str, value) -> float:
    """value as a float; ValueError unless it is a finite int, float or numpy number other than a bool."""
    if isinstance(value, bool | np.bool_) or not isinstance(value, int | float | np.integer | np.floating):
        raise ValueError(f"{name} {value!r} is not a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} {value!r} is not finite")
    return float(value) + 0.0  # -0.0 becomes 0.0: an archived form must not tell them apart


def exact(value: float) -> Fraction:
    """The decimal a float is written as: 0.0003 is 3/10000, not the nearest binary fraction."""
    return Fraction(repr(float(value)))


def _to_fen(value: int, rate: Fraction) -> int:
    return round_half_up(value * rate.numerator, rate.denominator * FEN) * FEN
