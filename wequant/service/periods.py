"""Weeks and months in a screen or an explanation (S4.8): the trading day after the store's last for a strategy that
reads weekly or monthly bars, the warning when that day leaves the last period open, and which period a value came from."""

from __future__ import annotations

import datetime as dt
from typing import Literal

import numpy as np

from wequant.expr import Expr
from wequant.expr.lookback import periods
from wequant.panel import Panel
from wequant.panel.periods import period_ends
from wequant.service.base import Context, Model
from wequant.service.outputs import Warning
from wequant.service.schedule import following
from wequant.strategy import Strategy, expressions

Period = Literal["week", "month"]
OPEN = {"week": "week_open", "month": "month_open"}  # the warning of a period whose end the last day cannot tell
PERIOD_TEXTS = {  # the English text of each period warning, filled from its params
    code: lambda p, period=period: (
        f"{p['day']} is the store's last day and whether the next trading day starts a new {period} is unknown: the "
        f"{period}ly values are those of the {period} ended {p['end']}"
    )
    for period, code in OPEN.items()
}


class PeriodValue(Model):
    """Which week or month a weekly or monthly value came from."""

    period: Period
    end: dt.date | None
    """The last trading day of that week or month; null: no ended one yet (for a stock: none it traded in)."""


def strategy_periods(strategy: Strategy) -> tuple[str, ...]:
    """The periods any root of the strategy reads bars of."""
    found = {p for expr in expressions(strategy).values() for p in periods(expr)}
    return tuple(p for p in ("week", "month") if p in found)


def period_after(strategy: Strategy, context: Context, manifest: dict) -> tuple[dt.date, str] | None:
    """The trading day after the store's last and its source (calendar or estimated), as strategy_schedule counts
    it, for a strategy that reads weekly or monthly bars; None for one that does not, whose panel stays as before."""
    return following(context.store(), manifest) if strategy_periods(strategy) else None


def open_periods(strategy: Strategy, panel: Panel, row: int, after: tuple[dt.date, str] | None) -> list[Warning]:
    """week_open and month_open: the decision day is the store's last, only estimated to be followed by a trading day
    in the same period, so the period's bars wait (the values are the previous period's)."""
    if after is None or after[1] != "estimated" or row != len(panel.dates) - 1:
        return []
    found = []
    for period in strategy_periods(strategy):
        ends = period_ends(panel.dates, period, panel.after)
        if not ends[row]:
            done = np.flatnonzero(ends[: row + 1])
            end = None if not done.size else panel.dates[done[-1]].item().isoformat()
            params = {"day": panel.dates[row].item().isoformat(), "end": end}
            found.append(Warning(code=OPEN[period], text=PERIOD_TEXTS[OPEN[period]](params), params=params))
    return found


def period_values(expr: Expr, panel: Panel, row: int, col: int | None = None) -> list[PeriodValue]:
    """The week or month each period of expr reads on a decision row: the calendar's last ended one, or with a column
    the stock's latest bar among them."""
    found = []
    for period in periods(expr):
        view = panel.periods(period)
        if col is None:
            done = int(np.cumsum(view.ends)[row]) - 1
        else:
            done = int(view.sources()[row, col])
        found.append(PeriodValue(period=period, end=view.dates[done].item() if done >= 0 else None))
    return found
