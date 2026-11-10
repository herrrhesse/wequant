"""Date × stock matrices of one store version, sliced from the cached full-grid arrays."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from functools import cached_property
from pathlib import Path

import numpy as np
import polars as pl

from wequant.data.delistings import classify, delisted, overrides_digest, overrides_text, parse_overrides
from wequant.data.schema import ANOMALY_VERSION
from wequant.data.store import Store
from wequant.panel.anomalies import anomaly_cells, exclusion
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
    exclude: tuple[str, ...] = (),
) -> Panel:
    """Date × stock matrices of one store version over [start, end] inclusive.

    Current version unless pinned; all stocks unless codes are given (kept in that order); the anomalous days of the
    levels in exclude read as missing data (Panel.anomalies).
    """
    store = store if store is not None else Store()
    manifest = store.manifest(version)
    if manifest is None:
        raise LookupError(f"{store.home} has no manifest" + ("" if version is None else f" for version {version}"))
    src = PanelCache(store, manifest, Path(cache_dir) if cache_dir is not None else store.home / "cache")
    return Panel(src, _row_range(src.array("dates"), start, end), _columns(src.array("codes"), codes), exclude)


class Panel:
    """Date × stock matrices of one store version; arrays load on first use and are read-only.

    status, listed, suspended and missing are descriptive where daily_status has no row; traded is point-in-time.
    manifest is the store manifest the panel reads; never change it. key names the data the panel serves: the store
    version's, the delisting overrides when there are any, and the anomaly levels read as missing.

    Reading anomalous days as missing is a robustness check, not a point-in-time rule: some anomaly labels rest on the
    next day's exchange preclose, so excluding day d uses what was known on d + 1.
    """

    def __init__(self, src: PanelCache, rows: slice, columns: np.ndarray | None, exclude: tuple[str, ...] = ()) -> None:
        self._src, self._rows, self._columns = src, rows, columns
        self._memo: dict[str, np.ndarray] = {}
        self._overrides = overrides_text(src.store.home)
        self.version, self.manifest, self.excluded = src.version, src.manifest, exclusion(exclude)
        suffix = f"-x{ANOMALY_VERSION}-{'-'.join(self.excluded)}" if self.excluded else ""
        self.key = src.key + overrides_digest(self._overrides) + suffix
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

    @cached_property
    def anomalies(self) -> pl.DataFrame:
        """ANOMALIES: every anomalous cell of the panel, by data_issues and ISSUE_KINDS; excluded where its level is
        in excluded and its class one a missing day removes."""
        return anomaly_cells(self._src, self.dates, self.codes, self.excluded)

    @cached_property
    def delistings(self) -> pl.DataFrame:
        """DELISTINGS of the panel's codes that left the market or look as if they did, from the store's securities,
        calendar and last bars and the user's overrides (wequant.data.delistings)."""
        securities = self._src.scan("securities").sort("code").collect()
        full = np.arange(securities.height) if self._columns is None else self._columns
        securities, calendar = securities[full], np.asarray(self._src.array("dates"))
        chosen = np.flatnonzero(delisted(securities, calendar))
        last_bar, last_close = _last_bars(self._src, full[chosen])
        codes = securities[chosen].with_columns(last_bar=pl.Series(last_bar, dtype=pl.Date), last_close=last_close)
        return classify(codes, calendar, parse_overrides(self._overrides))

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
        return self._missing(self._slice(self._limits[0]), np.nan)

    @cached_property
    def limit_down_price(self) -> np.ndarray:
        """(T, N) yuan: finite where LIMITED, -inf where FREE, NaN where UNKNOWN or without a bar."""
        return self._missing(self._slice(self._limits[1]), np.nan)

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
            grid = self._slice(self._src.array(name))
            missing = {"status": MISSING} | dict.fromkeys(RAW_FIELDS, np.nan)
            self._memo[name] = self._missing(grid, missing[name]) if name in missing else grid
        return self._memo[name]

    def _missing(self, grid: np.ndarray, fill: float) -> np.ndarray:
        """grid with the excluded cells set to fill: what a day without data reads as."""
        cells = self.anomalies.filter("excluded") if self.excluded else None
        if cells is None or cells.is_empty():
            return grid
        grid = np.array(grid)
        grid[cells["row"].to_numpy(), cells["col"].to_numpy()] = fill
        return _frozen(grid)

    def _slice(self, grid: np.ndarray) -> np.ndarray:
        view = np.asarray(grid[self._rows])
        return view if self._columns is None else _frozen(view[:, self._columns])

    def _vector(self, name: str) -> np.ndarray:
        vector = np.asarray(self._src.array(name))
        return vector if self._columns is None else _frozen(vector[self._columns])


def _last_bars(src: PanelCache, cols: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The day and raw close of each column's last bar in the store's calendar; NaT and NaN where it has none."""
    traded = np.asarray(src.array("status")[:, cols]) == TRADED
    row, ever = len(traded) - 1 - np.argmax(traded[::-1], axis=0), traded.any(axis=0)
    close = np.where(ever, src.array("close")[row, cols], np.nan)
    return np.where(ever, src.array("dates")[row], np.datetime64("NaT", "D")), close


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
