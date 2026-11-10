"""Date × stock matrices of one store version, sliced from the cached full-grid arrays."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from functools import cached_property
from pathlib import Path

import numpy as np
import polars as pl

from wequant.data.store import Store
from wequant.panel.build import PRICE_FIELDS, PRICE_SCALE, RAW_FIELDS, limits, members
from wequant.panel.cache import PanelCache
from wequant.panel.grid import day_rows, find_sorted
from wequant.panel.status import MISSING, NO_ROW, NOT_LISTED, SUSPENDED, TRADED
from wequant.rules.book import default_book
from wequant.rules.limits import LIMITS_FORMAT

Day = dt.date | str | np.datetime64


def load_panel(
    start: Day | None = None,
    end: Day | None = None,
    codes: Sequence[str] | None = None,
    *,
    version: int | None = None,
    store: Store | None = None,
    cache_dir: Path | None = None,
) -> Panel:
    """Date × stock matrices of one store version over [start, end] inclusive.

    Current version unless pinned; all stocks unless codes are given (kept in that order).
    """
    store = store if store is not None else Store()
    manifest = store.manifest(version)
    if manifest is None:
        raise LookupError(f"{store.home} has no manifest" + ("" if version is None else f" for version {version}"))
    src = PanelCache(store, manifest, Path(cache_dir) if cache_dir is not None else store.home / "cache")
    return Panel(src, _row_range(src.array("dates"), start, end), _columns(src.array("codes"), codes))


class Panel:
    """Date × stock matrices of one store version; arrays load on first use and are read-only.

    status, listed, suspended and missing are descriptive where daily_status has no row; traded is point-in-time.
    manifest is the store manifest the panel reads; never change it.
    """

    def __init__(self, src: PanelCache, rows: slice, columns: np.ndarray | None) -> None:
        self._src, self._rows, self._columns = src, rows, columns
        self._memo: dict[str, np.ndarray] = {}
        self.version, self.key, self.manifest = src.version, src.key, src.manifest
        self.dates = np.asarray(src.array("dates")[rows])
        self.codes = self._vector("codes")

    # ------------------------------------------------------------ bars

    def raw(self, field: str) -> np.ndarray:
        """Unadjusted (T, N) float64: prices and amount in yuan, volume in shares; NaN where the stock did not trade."""
        if field not in RAW_FIELDS:
            raise ValueError(f"{field!r} is not one of {RAW_FIELDS}")
        return self._grid(field)

    def adjusted(self, field: str) -> np.ndarray:
        """Back-adjusted raw(field) × factor for a PRICE_FIELDS field; for signals, never for execution."""
        if field not in PRICE_FIELDS:
            raise ValueError(f"{field!r} is not a price field {PRICE_FIELDS}")
        name = f"adjusted-{field}"
        if name not in self._memo:
            self._memo[name] = _frozen(self.raw(field) * self.factor)
        return self._memo[name]

    @cached_property
    def factor(self) -> np.ndarray:
        """(T, N) back-adjustment factor in force: 1.0 before a code's first event, never NaN."""
        return self._grid("factor")

    @cached_property
    def boards(self) -> np.ndarray:
        """(N,) board of each code: main, chinext, star or bse."""
        return self._vector("boards")

    @cached_property
    def exchanges(self) -> np.ndarray:
        """(N,) exchange of each code: SH, SZ or BJ."""
        return self._vector("exchanges")

    # ------------------------------------------------------------ status and masks

    @cached_property
    def status(self) -> np.ndarray:
        """(T, N) int8 NOT_LISTED, TRADED, SUSPENDED or MISSING; descriptive where daily_status has no row."""
        return self._grid("status")

    @cached_property
    def listed(self) -> np.ndarray:
        """status != NOT_LISTED."""
        return _frozen(self.status != NOT_LISTED)

    @cached_property
    def traded(self) -> np.ndarray:
        """A bar exists: status == TRADED (point-in-time)."""
        return _frozen(self.status == TRADED)

    @cached_property
    def suspended(self) -> np.ndarray:
        """status == SUSPENDED."""
        return _frozen(self.status == SUSPENDED)

    @cached_property
    def missing(self) -> np.ndarray:
        """status == MISSING: daily_status says traded or a source bar was dropped, and the store has no bar."""
        return _frozen(self.status == MISSING)

    @cached_property
    def st(self) -> np.ndarray:
        """daily_status marks the stock ST."""
        return _frozen(self._grid("st") == 1)

    @cached_property
    def st_known(self) -> np.ndarray:
        """daily_status has a row for the cell."""
        return _frozen(self._grid("st") != NO_ROW)

    @cached_property
    def list_days(self) -> np.ndarray:
        """(T, N) int16 trading days since each code's first_date, that day counted as 1; 0 before it.

        first_date is the first day the source has data, not the official listing date: a lower bound for codes listed
        before the calendar starts.
        """
        return self._grid("list_days")

    @property
    def calendar_offset(self) -> int:
        """Rows of the store's calendar before the panel's first day."""
        return self._rows.start

    def member(self, index: str) -> np.ndarray:
        """(T, N) int8 membership of an index_members index: 1 a member, 0 not, -1 outside the index's stored span."""
        name = f"member-{index}"
        if name not in self._memo:
            self._memo[name] = self._slice(self._src.array(name, members(index)))
        return self._memo[name]

    def index_span(self, index: str) -> tuple[dt.date, dt.date] | None:
        """First start_date and last end_date of an index_members index, or None when the store has no member of it."""
        spans = self._src.scan("index_members").filter(pl.col("index_code") == index).collect()
        return None if spans.is_empty() else (spans["start_date"].min(), spans["end_date"].max())

    def on_bars(self, mask: np.ndarray) -> np.ndarray:
        """mask as an ops condition: float64 1.0/0.0 on traded cells, NaN elsewhere."""
        mask = np.asarray(mask)
        if mask.shape != self.traded.shape:
            raise ValueError(f"mask shape {mask.shape} != panel shape {self.traded.shape}")
        return _frozen(np.where(self.traded, mask, np.nan).astype(np.float64, copy=False))

    # ------------------------------------------------------------ limits

    @cached_property
    def rules_key(self) -> str:
        """Rule-book digest and limit format the limit arrays come from."""
        return f"{default_book().digest[:12]}-r{LIMITS_FORMAT}"

    @cached_property
    def limit_up_price(self) -> np.ndarray:
        """(T, N) yuan: finite where LIMITED, +inf where FREE, NaN where UNKNOWN or without a bar."""
        return self._slice(self._limits[0])

    @cached_property
    def limit_down_price(self) -> np.ndarray:
        """(T, N) yuan: finite where LIMITED, -inf where FREE, NaN where UNKNOWN or without a bar."""
        return self._slice(self._limits[1])

    @cached_property
    def limit_known(self) -> np.ndarray:
        """A limit rule is known: traded and LIMITED or FREE."""
        return _frozen(~np.isnan(self.limit_up_price))

    @cached_property
    def limit_up(self) -> np.ndarray:
        """Closed at the up limit; never where FREE or UNKNOWN."""
        return _frozen(self.raw("close") == self.limit_up_price)

    @cached_property
    def limit_down(self) -> np.ndarray:
        """Closed at the down limit; never where FREE or UNKNOWN."""
        return _frozen(self.raw("close") == self.limit_down_price)

    @cached_property
    def limit_locked(self) -> np.ndarray:
        """一字板: high == low and closed at a limit."""
        return _frozen((self.raw("high") == self.raw("low")) & (self.limit_up | self.limit_down))

    @cached_property
    def _limits(self) -> np.ndarray:
        return self._src.array(f"limits-{self.rules_key}", limits)

    # ------------------------------------------------------------ dates and indexes

    def row(self, day: Day) -> int:
        """Row of a trading day; KeyError if it is not one of the panel's dates."""
        target = np.datetime64(day, "D")
        row = int(np.searchsorted(self.dates, target))
        if row == len(self.dates) or self.dates[row] != target:
            raise KeyError(f"{target} is not a trading day of this panel")
        return row

    def index_close(self, code: str) -> np.ndarray:
        """(T,) close of an index in points aligned to dates; NaN where it has no bar."""
        start, end = self.dates[0].item(), self.dates[-1].item()
        bars = self._src.scan("index_bars", columns=["date", "close"], codes=[code], start=start, end=end).collect()
        close = np.full(len(self.dates), np.nan)
        close[day_rows(bars["date"].to_numpy(), self.dates)] = bars["close"].to_numpy() / PRICE_SCALE
        return _frozen(close)

    # ------------------------------------------------------------ slicing

    def _grid(self, name: str) -> np.ndarray:
        if name not in self._memo:
            self._memo[name] = self._slice(self._src.array(name))
        return self._memo[name]

    def _slice(self, grid: np.ndarray) -> np.ndarray:
        view = np.asarray(grid[self._rows])
        return view if self._columns is None else _frozen(view[:, self._columns])

    def _vector(self, name: str) -> np.ndarray:
        vector = np.asarray(self._src.array(name))
        return vector if self._columns is None else _frozen(vector[self._columns])


def _row_range(dates: np.ndarray, start: Day | None, end: Day | None) -> slice:
    lo = 0 if start is None else int(np.searchsorted(dates, np.datetime64(start, "D"), "left"))
    hi = len(dates) if end is None else int(np.searchsorted(dates, np.datetime64(end, "D"), "right"))
    if lo >= hi:
        raise KeyError(f"no trading day in [{start}, {end}]")
    return slice(lo, hi)


def _columns(all_codes: np.ndarray, codes: Sequence[str] | None) -> np.ndarray | None:
    if codes is None:
        return None
    wanted = np.asarray(codes, dtype=str)
    if len(set(wanted.tolist())) != len(wanted):
        raise ValueError(f"duplicate codes in {list(codes)}")
    cols, found = find_sorted(all_codes, wanted)
    if not found.all():
        raise KeyError(f"unknown codes: {wanted[~found].tolist()}")
    return cols


def _frozen(array: np.ndarray) -> np.ndarray:
    array.flags.writeable = False
    return array
