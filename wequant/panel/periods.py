"""Weekly and monthly bars made from the daily ones (S4.8), and their values placed back on the trading days.

A period is a natural week (Monday to Sunday) or month. A stock's bar of a period takes its first open, highest high,
lowest low, last close and summed volume and amount over the days it traded in it; its preclose is the close of its
previous bar, the daily preclose of its first day when it has none. A period ends on the row whose next trading day falls
in another period, the rule the rebalance schedule follows too; a value computed on the bars reaches the days from the
day its period ends, so the days inside a period read the last ended one and nothing reads ahead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from wequant.analytics.series import period_keys
from wequant.panel.status import TRADED

if TYPE_CHECKING:
    from wequant.panel.cache import PanelCache
    from wequant.panel.panel import Day, Panel

PERIODS = ("week", "month")
BAR_FIELDS = ("open", "high", "low", "close", "volume", "amount", "preclose")
ADJUSTED_FIELDS = ("open", "high", "low", "close", "preclose")
AGGREGATE = {  # how a bar field gathers the days of its period; preclose here is its first day's, before previous()
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
    "amount": "sum",
    "preclose": "first",
}


def following_day(day: Day) -> np.datetime64:
    """The weekday after day: the next trading day as estimated when no calendar knows it (exchanges never open on a
    weekend, a holiday may move it later)."""
    return np.busday_offset(np.datetime64(day, "D"), 1, roll="forward")


def period_ends(dates: np.ndarray, period: str, after: Day | None = None) -> np.ndarray:
    """(T,) bool: the rows whose next trading day falls in another period. The last row's next day is after when known,
    else the weekday after it, so a period ends there only when no weekday of it is left."""
    dates = np.asarray(dates, dtype="datetime64[D]")
    nexts = np.append(dates[1:], np.datetime64(after, "D") if after is not None else following_day(dates[-1]))
    return period_keys(dates, period) != period_keys(nexts, period)


def starts(dates: np.ndarray, period: str) -> np.ndarray:
    """(P,) int64: the first row of each period the dates fall in, ascending."""
    keys = period_keys(dates, period)
    return np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])


def aggregate(daily: np.ndarray, bars: np.ndarray, first_rows: np.ndarray, how: str) -> np.ndarray:
    """(P, N) float64: daily (T, N) gathered over the periods starting at first_rows, on the cells where bars is True;
    NaN for a column without a bar in the period, and for a sum when one of its bars has no value."""
    daily = np.asarray(daily, dtype=np.float64)
    if how in ("max", "min"):
        reduce = np.fmax if how == "max" else np.fmin
        return reduce.reduceat(np.where(bars, daily, np.nan), first_rows, axis=0)
    traded = np.logical_or.reduceat(bars, first_rows, axis=0)
    if how == "sum":
        total = np.add.reduceat(np.where(bars, np.nan_to_num(daily), 0.0), first_rows, axis=0)
        unknown = np.logical_or.reduceat(bars & np.isnan(daily), first_rows, axis=0)
        return np.where(traded & ~unknown, total, np.nan)
    rows = np.arange(len(daily), dtype=np.int32)[:, None]
    if how == "first":
        edge = np.minimum.reduceat(np.where(bars, rows, len(daily)), first_rows, axis=0)
    else:
        edge = np.maximum.reduceat(np.where(bars, rows, -1), first_rows, axis=0)
    picked = np.take_along_axis(daily, np.clip(edge, 0, len(daily) - 1), axis=0)
    return np.where(traded, picked, np.nan)


def previous(close: np.ndarray, first_preclose: np.ndarray) -> np.ndarray:
    """(P, N) preclose of each bar: the close of the column's previous bar, else its first day's daily preclose."""
    has_bar = ~np.isnan(close)
    rows = np.arange(len(close))[:, None]
    last = np.maximum.accumulate(np.where(has_bar, rows, -1), axis=0)  # the latest bar at or before each period
    before = np.vstack([np.full((1, close.shape[1]), -1), last[:-1]])
    prior = np.take_along_axis(close, np.maximum(before, 0), axis=0)
    return np.where(has_bar & (before >= 0), prior, first_preclose)


def full_builder(period: str, field: str, adjusted: bool):
    """Builder of one bar field on the store's whole calendar: the cached array <period>-[adj_]<field>, the first
    day's preclose for preclose."""

    def build(src: PanelCache) -> np.ndarray:
        daily = src.array(field)
        if adjusted:
            daily = daily * src.array("factor")
        bars = np.asarray(src.array("status")) == TRADED
        return aggregate(daily, bars, starts(src.array("dates"), period), AGGREGATE[field])

    return build


class PeriodView:
    """A panel's ended periods as bars, (P, N) grids an expression's WEEK or MONTH subtree reads as it reads a panel;
    to_days places a value of each bar back on the panel's days."""

    def __init__(self, panel: Panel, period: str) -> None:
        self.panel, self.period = panel, period
        self.first_rows = starts(panel.dates, period)
        self.ends = period_ends(panel.dates, period, panel.after)
        ended = int(self.ends[-1]) + len(self.first_rows) - 1  # every period but the last has ended
        self.first_rows = self.first_rows[:ended]
        self.end_rows = np.flatnonzero(self.ends)
        self.dates = panel.dates[self.end_rows]
        self.codes = panel.codes
        self.rules_key = panel.rules_key
        self.key = f"{panel.key}-{period}-{np.datetime_as_string(panel.dates[0], unit='D')}"
        self._memo: dict[str, np.ndarray] = {}

    def raw(self, field: str) -> np.ndarray:
        """(P, N) unadjusted bar field: yuan, volume in shares; NaN without a bar."""
        return self._field(field, adjusted=False)

    def adjusted(self, field: str) -> np.ndarray:
        """(P, N) back-adjusted bar field, gathered from the back-adjusted days."""
        if field not in ADJUSTED_FIELDS:
            raise ValueError(f"{field!r} is not a price field {ADJUSTED_FIELDS}")
        return self._field(field, adjusted=True)

    @property
    def traded(self) -> np.ndarray:
        """(P, N) bool: the column has a bar in the period."""
        return ~np.isnan(self.raw("close"))

    def sources(self) -> np.ndarray:
        """(T, N) int: the bar each day of the panel reads, -1 for none: the column's latest bar among the periods
        ended on or before the day."""
        if "sources" not in self._memo:
            if not len(self.dates):
                self._memo["sources"] = _frozen(np.full(self.panel.traded.shape, -1, dtype=np.int32))
                return self._memo["sources"]
            done = np.cumsum(self.ends) - 1
            rows = np.arange(len(self.dates), dtype=np.int32)[:, None]
            latest = np.maximum.accumulate(np.where(self.traded, rows, -1), axis=0)
            found = np.where(done[:, None] >= 0, latest[np.maximum(done, 0)], -1)
            self._memo["sources"] = _frozen(found.astype(np.int32))
        return self._memo["sources"]

    def to_days(self, values: np.ndarray) -> np.ndarray:
        """(T, N) float64: each day's value of the bar it reads, NaN where it reads none or has no bar itself."""
        found = self.sources()
        if not len(self.dates):
            return _frozen(np.full(found.shape, np.nan))
        picked = np.take_along_axis(np.asarray(values, dtype=np.float64), np.maximum(found, 0), axis=0)
        return _frozen(np.where((found >= 0) & self.panel.traded, picked, np.nan))

    def _field(self, field: str, adjusted: bool) -> np.ndarray:
        name = f"adj_{field}" if adjusted else field
        if name not in self._memo:
            if field == "preclose":
                close, first = self._field("close", adjusted), self._gathered("preclose", adjusted)
                grid = previous(close, first)
            else:
                grid = self._gathered(field, adjusted)
            self._memo[name] = _frozen(grid)
        return self._memo[name]

    def _gathered(self, field: str, adjusted: bool) -> np.ndarray:
        """(P, N) field gathered by AGGREGATE: the store's cached periods, the first one remade from the panel's own
        days when the panel starts inside it; all of them from the panel's days when it excludes anomalous days."""
        panel, how = self.panel, AGGREGATE[field]
        if not len(self.first_rows):
            return np.full((0, len(self.codes)), np.nan)
        own = panel.adjusted(field) if adjusted else panel.raw(field)
        limit = self.end_rows[-1] + 1  # the days of the ended periods
        if panel.excluded:
            return aggregate(own[:limit], panel.traded[:limit], self.first_rows, how)
        name = f"adj_{field}" if adjusted else field
        full, full_starts = panel.full_periods(self.period, name, full_builder(self.period, field, adjusted))
        calendar = panel.calendar_offset + self.first_rows
        rows = np.searchsorted(full_starts, calendar, "right") - 1
        grid = np.array(full[rows], dtype=np.float64)
        if full_starts[rows[0]] != calendar[0]:  # the panel starts inside its first period
            end = self.first_rows[1] if len(self.first_rows) > 1 else limit
            grid[0] = aggregate(own[:end], panel.traded[:end], np.array([0]), how)[0]
        return grid


def _frozen(array: np.ndarray) -> np.ndarray:
    array.flags.writeable = False
    return array
