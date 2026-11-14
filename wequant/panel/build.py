"""Builders of the full-grid arrays; each reads the store only through its PanelCache, pinned to one manifest."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from wequant.panel.grid import asof_rows, cells, code_columns, fill_forward, scatter
from wequant.panel.status import NO_ROW, classify
from wequant.rules.limits import FREE, LIMITED
from wequant.rules.tables import load_limits

if TYPE_CHECKING:
    from wequant.panel.cache import PanelCache

RAW_FIELDS = ("open", "high", "low", "close", "preclose", "volume", "amount")
PRICE_FIELDS = ("open", "high", "low", "close", "preclose")
PRICE_SCALE = 1000.0  # store prices are 厘
HOLIDAY_WEEKDAYS = 22  # more weekdays than the exchanges close in any year (18 to 20 since 2010)


def calendar(src: PanelCache) -> np.ndarray:
    """Trading days in ascending order, datetime64[D]."""
    return src.scan("calendar", columns=["date"]).collect()["date"].sort().to_numpy()


def securities(column: str) -> Callable[[PanelCache], np.ndarray]:
    """Builder of one securities column in code order, as fixed-width strings."""

    def build(src: PanelCache) -> np.ndarray:
        return src.scan("securities").sort("code").select(column).collect()[column].to_numpy().astype(str)

    return build


def bar_field(field: str) -> Callable[[PanelCache], np.ndarray]:
    """Builder of one bars column on the full grid; prices in yuan, NaN without a bar."""
    scale = PRICE_SCALE if field in PRICE_FIELDS else 1.0

    def build(src: PanelCache) -> np.ndarray:
        frame = src.scan("bars", columns=["code", "date", field]).collect()
        return _place(src, frame, frame[field].cast(pl.Float64).to_numpy() / scale, np.nan)

    return build


def factor(src: PanelCache) -> np.ndarray:
    """Back-adjustment factor in force each day: the last event on or before it, 1.0 before the first."""
    dates, codes = src.array("dates"), src.array("codes")
    events = src.scan("adj_factor").sort("code", "ex_date").collect()
    events = (
        events.with_columns(row=pl.Series(asof_rows(dates, events["ex_date"].to_numpy())))
        .filter(pl.col("row") < len(dates))
        .unique(["code", "row"], keep="last", maintain_order=True)
    )
    cols = code_columns(events["code"], codes)
    grid = scatter((len(dates), len(codes)), events["row"].to_numpy(), cols, events["factor"].to_numpy(), np.nan)
    return fill_forward(grid, 1.0)


def status(src: PanelCache) -> np.ndarray:
    """int8 day status from bars, daily_status, dropped bars and data spans."""
    daily = src.scan("daily_status", columns=["code", "date", "tradestatus"]).collect()
    dropped = (
        src.scan("data_issues", columns=["table", "code", "date", "action"])
        .filter(pl.col("table") == "bars", pl.col("action") == "row_dropped")
        .filter(pl.col("code").is_in(src.array("codes").tolist()))
        .collect()
    )
    return classify(
        ~np.isnan(src.array("close")),
        _place(src, daily, daily["tradestatus"].to_numpy(), NO_ROW),
        _place(src, dropped, np.ones(dropped.height, dtype=bool), False),
        _span(src),
    )


def st(src: PanelCache) -> np.ndarray:
    """int8 grid: 1 ST, 0 not ST, NO_ROW where daily_status has no row."""
    daily = src.scan("daily_status", columns=["code", "date", "is_st"]).collect()
    return _place(src, daily, daily["is_st"].cast(pl.Int8).to_numpy(), NO_ROW)


def limits(src: PanelCache) -> np.ndarray:
    """(2, T, N) float64 yuan from load_limits: [0] up, [1] down; ±inf where FREE, NaN where UNKNOWN or no bar."""
    dates, codes = src.array("dates"), src.array("codes")
    frame = load_limits(src.store, manifest=src.manifest)
    rows, cols = cells(frame, dates, codes)
    state = frame["limit_state"].to_numpy()
    grid = np.full((2, len(dates), len(codes)), np.nan)
    grid[0, rows, cols] = _bound(frame["limit_up"], state, np.inf)
    grid[1, rows, cols] = _bound(frame["limit_down"], state, -np.inf)
    return grid


def list_days(src: PanelCache) -> np.ndarray:
    """int16 trading days from each code's listing day, that day counted as 1, suspended days included; 0 before it.
    The listing day is list_date when known, else first_date; one before the calendar adds a lower bound of the trading
    days between them (earlier_days)."""
    dates = src.array("dates")
    listed = _listed(src)
    first = asof_rows(dates, listed)
    earlier = earlier_days(listed, dates[0])
    rows = np.arange(len(dates), dtype=np.int32)[:, None]
    days = np.maximum(rows - first.astype(np.int32) + 1, 0) + np.where(rows >= first, earlier, 0)
    return np.minimum(days, np.iinfo(np.int16).max).astype(np.int16)


def list_days_bound(src: PanelCache) -> np.ndarray:
    """(N,) bool: the code's list_days is a lower bound, its listing day unknown or before the calendar."""
    dates, found = src.array("dates"), src.scan("securities").sort("code").select("list_date").collect()
    known = found["list_date"].to_numpy()
    return np.isnat(known) | (known < dates[0])


def earlier_days(listed: np.ndarray, start: np.datetime64) -> np.ndarray:
    """A lower bound of the trading days from each listing day to start (none for one on or after it): the weekdays
    between, less HOLIDAY_WEEKDAYS for each calendar year they touch."""
    before = listed < start
    weekdays = np.busday_count(np.where(before, listed, start).astype("datetime64[D]"), start.astype("datetime64[D]"))
    years = start.astype("datetime64[Y]").astype(int) - np.where(before, listed, start).astype("datetime64[Y]").astype(
        int
    )
    return np.where(before, np.maximum(weekdays - HOLIDAY_WEEKDAYS * (years + 1), 0), 0).astype(np.int32)


def _listed(src: PanelCache) -> np.ndarray:
    """Each code's listing day: list_date when known, else first_date (the first day of its data)."""
    found = src.scan("securities").sort("code").select("first_date", "list_date").collect()
    return found.select(pl.coalesce("list_date", "first_date"))["list_date"].to_numpy()


def members(index: str) -> Callable[[PanelCache], np.ndarray]:
    """Builder of an index_members index on the full grid: int8 1 from a member's start_date to its end_date, both
    included, 0 on other days of the index's stored span, -1 outside it."""

    def build(src: PanelCache) -> np.ndarray:
        dates, codes = src.array("dates"), src.array("codes")
        spans = src.scan("index_members").filter(pl.col("index_code") == index).collect()
        grid = np.full((len(dates), len(codes)), -1, dtype=np.int8)
        if spans.is_empty():
            return grid
        first = asof_rows(dates, spans["start_date"].to_numpy())
        last = np.searchsorted(dates, spans["end_date"].to_numpy(), "right")
        span = slice(first.min(), last.max())
        known = spans["code"].is_in(codes.tolist()).to_numpy()
        change = np.zeros((len(dates) + 1, len(codes)), dtype=np.int16)
        cols = code_columns(spans["code"].filter(known), codes)
        np.add.at(change, (first[known], cols), 1)
        np.add.at(change, (last[known], cols), -1)
        grid[span] = np.cumsum(change[:-1], axis=0, dtype=np.int16)[span] > 0
        return grid

    return build


def _bound(price: pl.Series, state: np.ndarray, free: float) -> np.ndarray:
    limited = price.cast(pl.Float64).to_numpy() / PRICE_SCALE
    return np.select([state == LIMITED, state == FREE], [limited, free], np.nan)


def _place(src: PanelCache, frame: pl.DataFrame, values: np.ndarray, fill) -> np.ndarray:
    dates, codes = src.array("dates"), src.array("codes")
    rows, cols = cells(frame, dates, codes)
    return scatter((len(dates), len(codes)), rows, cols, values, fill)


def _span(src: PanelCache) -> np.ndarray:
    """True from each code's first_date to its last_date."""
    dates = src.array("dates")
    spans = src.scan("securities").sort("code").select("first_date", "last_date").collect()
    first = asof_rows(dates, spans["first_date"].to_numpy())
    last = np.searchsorted(dates, spans["last_date"].to_numpy(), "right")
    rows = np.arange(len(dates))[:, None]
    return (rows >= first) & (rows < last)


BUILDERS: dict[str, Callable[[PanelCache], np.ndarray]] = {
    "dates": calendar,
    "codes": securities("code"),
    "boards": securities("board"),
    "exchanges": securities("exchange"),
    **{field: bar_field(field) for field in RAW_FIELDS},
    "factor": factor,
    "status": status,
    "st": st,
    "list_days": list_days,
    "list_days_bound": list_days_bound,
}
