"""A backtest: a strategy, its window, cash, execution, benchmark and metric conventions, and the panel it runs on."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from wequant.account import Execution
from wequant.account.execution import FEN, LI, exact, finite_number
from wequant.analytics import MetricSpec
from wequant.backtest.data import DataOptions
from wequant.canonical import canonical
from wequant.panel import Panel, load_panel
from wequant.strategy import SpecError, Strategy, check_anchor, parse_form, to_form

if TYPE_CHECKING:
    from wequant.data.store import Store


@dataclass(frozen=True)
class Backtest:
    """A strategy traded from the close of start to end with cash, costs, data options, a benchmark and metric
    conventions."""

    strategy: Strategy
    start: dt.date | None = None  # base day: the first trading day on or after it; None: the panel's first day
    end: dt.date | None = None  # last day: the last trading day on or before it; None: the panel's last day
    cash: float = 1_000_000.0  # yuan, a positive whole number of fen
    execution: Execution = Execution()
    benchmark: str | None = "000300.SH"  # an index_bars code; None: no benchmark column
    metrics: MetricSpec = MetricSpec()
    data: DataOptions = DataOptions()

    def __post_init__(self) -> None:
        kinds = (("strategy", Strategy), ("execution", Execution), ("metrics", MetricSpec), ("data", DataOptions))
        for name, kind in kinds:
            if not isinstance(getattr(self, name), kind):
                raise ValueError(f"{name}: {getattr(self, name)!r} is not a {kind.__name__}")
        _check_window(self.start, self.end, self.strategy.anchor)
        object.__setattr__(self, "cash", _cash(self.cash))
        if self.benchmark is not None and (not isinstance(self.benchmark, str) or not self.benchmark):
            raise SpecError("wrong_type", ("benchmark",), f"{self.benchmark!r} is neither None nor an index code")

    @property
    def form(self) -> dict:
        """to_form(self)."""
        return to_form(self)

    @property
    def key(self) -> str:
        """canonical(self.form)."""
        return canonical(self.form)

    @classmethod
    def from_form(cls, form: Mapping) -> Backtest:
        """parse_form(cls, form)."""
        return parse_form(cls, form)


def panel_for(
    backtest: Backtest, *, store: Store | None = None, version: int | None = None, cache_dir: Path | None = None
) -> Panel:
    """The full panel run expects: every code from the strategy's anchor (the calendar's first day by default) to end,
    the anomalous days the data options exclude read as missing."""
    return load_panel(
        backtest.strategy.anchor,
        backtest.end,
        version=version,
        store=store,
        cache_dir=cache_dir,
        exclude=backtest.data.excluded,
    )


def span(backtest: Backtest, panel: Panel) -> tuple[int, int]:
    """Base row and last row on panel; SpecError for an anchor mismatch, a start before the panel or no run day,
    ValueError for a panel that excludes other anomaly levels than the data options do."""
    check_anchor(backtest.strategy, panel)
    if panel.excluded != backtest.data.excluded:
        raise ValueError(f"the panel excludes {panel.excluded}, the data options {backtest.data.excluded}")
    dates, start, end = panel.dates, backtest.start, backtest.end
    if start is not None and np.datetime64(start, "D") < dates[0]:
        raise SpecError("out_of_range", ("start",), f"{start} is before the panel's first day {dates[0]}")
    base = 0 if start is None else int(np.searchsorted(dates, np.datetime64(start, "D")))
    last = len(dates) - 1 if end is None else int(np.searchsorted(dates, np.datetime64(end, "D"), "right")) - 1
    if base >= last:
        message = f"no run day: the panel has no trading day after a base day between {start} and {end}"
        raise SpecError("out_of_range", ("end",), message)
    return base, last


def _check_window(start: object, end: object, anchor: dt.date | None) -> None:
    for name, day in (("start", start), ("end", end)):
        if day is not None and (not isinstance(day, dt.date) or isinstance(day, dt.datetime)):
            raise SpecError("wrong_type", (name,), f"{day!r} is not a date")
    if start is not None and end is not None and start > end:
        raise SpecError("out_of_range", ("start",), f"{start} is after end {end}")
    if start is not None and anchor is not None and start < anchor:
        raise SpecError("out_of_range", ("start",), f"{start} is before the strategy's anchor {anchor}")


def _cash(cash: object) -> float:
    """cash as a float + 0.0; SpecError unless it is a positive, finite whole number of fen."""
    try:
        value = finite_number("cash", cash)
    except OverflowError:  # an int too large for a float
        raise SpecError("out_of_range", ("cash",), f"{cash!r} is not a finite number") from None
    if value <= 0 or exact(value) * LI % FEN:
        raise SpecError("out_of_range", ("cash",), f"{cash!r} is not a positive whole number of fen")
    return value
