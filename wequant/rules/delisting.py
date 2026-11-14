"""The delisting period (退市整理期) by the date its rules took effect (S2.10a), read from delisting.toml."""

from __future__ import annotations

import bisect
import datetime as dt
import functools
import tomllib
from dataclasses import dataclass
from importlib.resources import files


@dataclass(frozen=True)
class Period:
    """From since on, a delisting period lasts days trading days; source names the rules."""

    id: str
    since: dt.date
    days: int
    source: str


@functools.cache
def periods() -> tuple[Period, ...]:
    """The rows of delisting.toml, by since."""
    raw = tomllib.loads(files("wequant.rules").joinpath("delisting.toml").read_text(encoding="utf-8"))
    return tuple(sorted((Period(**row) for row in raw["period"]), key=lambda period: period.since))


def period_days(last: dt.date) -> int:
    """The trading days of a delisting period whose last trading day was last: the rows in force that day, 0 before
    the first. No stock with 退 in its name left between 2020-12-17 and 2021-03-03 (BaoStock's stock_basic of
    2026-11-14), so each period lies wholly under one row."""
    found = periods()
    index = bisect.bisect_right([period.since for period in found], last)
    return found[index - 1].days if index else 0
