"""Simulated holdings (S4.2): a strategy's backtest played to a screen's day, what it holds at that close and each
sell rule's state. There is no tracked portfolio yet, so the page says these holdings are simulated."""

from __future__ import annotations

import datetime as dt
from typing import Literal

import numpy as np

from wequant.backtest import Backtest
from wequant.backtest.holding import Simulation, at_or_above, at_or_below, simulate
from wequant.panel import Panel
from wequant.service.base import Model, Names
from wequant.service.outputs import Warning
from wequant.service.specs import ExecutionSpec, IsoDate, Spec
from wequant.strategy import Strategy

YEAR = 242  # trading days a simulation starts before the screen's day by default
NOTE = "模拟持仓：从 {start} 起按这个策略回测到 {day} 得到的持仓，不是你的实际持仓"
Rule = Literal["stop_loss", "take_profit", "trailing_stop", "max_hold", "sell_formula", "keep_formula", "min_hold"]
SIMULATE_TEXTS = {  # the English text of each warning about simulated holdings, filled from its params
    "screen_without_holdings": lambda p: (
        "the strategy sells by its rules alone, which need its holdings; this screen holds nothing (give simulate)"
    ),
}


class SimulateSpec(Spec):
    """Play the strategy's backtest up to the screen's day and screen with what it holds."""

    start: IsoDate | None = None
    """The backtest's first day; null: about a year (242 trading days) before the screen's day."""
    cash: float = 1_000_000.0
    execution: ExecutionSpec = ExecutionSpec()


class RuleCheck(Model):
    """One sell rule on a holding at a close."""

    rule: Rule
    triggered: bool | None
    """Sells at this close (keep_formula and min_hold: holds a sale back); null: no value that day."""
    value: float | None
    """stop_loss and trailing_stop: the drop; take_profit: the gain; max_hold and min_hold: days held; a formula: 1/0."""
    threshold: float | None
    """The rule's setting; null for a formula."""
    blocked_by: Literal["keep"] | None
    """The keep formula holds this rule back today."""


class HoldingRow(Model):
    """A simulated holding at the screen day's close."""

    code: str
    name: str | None
    shares: float
    weight: float
    """Its value's share of equity at the close."""
    cost: float
    """Its average fill price, in that day's unadjusted yuan."""
    close: float | None
    change: float | None
    """Back-adjusted close over cost, less 1."""
    hold_days: int
    peak_close: float | None
    """Highest close since bought, in that day's unadjusted yuan."""
    action: Literal["sell", "keep"]
    """sell: a rule marked it at this close, or the next rebalance leaves it out."""
    cause: str | None
    """A rule (labels.sell_rules) or rebalance; null when kept."""
    rules: list[RuleCheck]


class Holdings(Model):
    simulated_from: dt.date
    note: str
    """The page's sentence: these holdings are simulated, from when."""
    rows: list[HoldingRow]


def simulated(strategy: Strategy, panel: Panel, row: int, spec: SimulateSpec) -> Simulation:
    """The strategy's backtest from spec.start (default a year before) to row's close."""
    start = spec.start or panel.dates[max(row - YEAR, 0)].item()
    backtest = Backtest(strategy, start=start, end=panel.dates[row].item(), cash=spec.cash, benchmark=None,
                        execution=_execution(spec.execution))  # fmt: skip
    return simulate(backtest, panel)


def holdings(
    strategy: Strategy, panel: Panel, found: Simulation, start: dt.date, target: np.ndarray, names: Names
) -> Holdings:
    """The simulation's holdings at its last close: each rule's state, and whether it is sold next."""
    t, book, account = found.last, found.book, found.account
    values, factor = account.values, panel.factor[t]
    rows = []
    for col in np.flatnonzero(account.shares > 0).tolist():
        marked = book.marked.get(col)
        leaves = not target[col]
        cost, peak, close = book.cost[col] / factor[col], book.peak[col] / factor[col], panel.raw("close")[t, col]
        rows.append(
            HoldingRow(
                code=str(panel.codes[col]),
                name=names.get(str(panel.codes[col])),
                shares=float(account.shares[col]),
                weight=float(values[col] / account.equity),
                cost=float(cost),
                close=_finite(close),
                change=_finite(panel.adjusted("close")[t, col] / book.cost[col] - 1),
                hold_days=int(book.days(t)[col]),
                peak_close=_finite(peak),
                action="sell" if marked or leaves else "keep",
                cause=marked[2] if marked else "rebalance" if leaves else None,
                rules=rule_checks(strategy, panel, found, col),
            )
        )
    day = panel.dates[t].item()
    return Holdings(simulated_from=start, note=NOTE.format(start=start, day=day), rows=rows)


def rule_checks(strategy: Strategy, panel: Panel, found: Simulation, col: int) -> list[RuleCheck]:
    """Each set rule's state on a holding at the simulation's last close."""
    sell, t, book, formulas = strategy.sell, found.last, found.book, found.formulas
    close, cost, peak, days = panel.adjusted("close")[t, col], book.cost[col], book.peak[col], int(book.days(t)[col])
    keep = bool(formulas["keep"][t, col] == 1) if "keep" in formulas else False
    stopped = "keep" if keep and sell.keep_over_stops else None
    checks = []

    def add(rule, on, value, threshold, triggered, blocked=None):
        if on:
            checks.append(RuleCheck(rule=rule, triggered=None if value is None else triggered, value=value,
                                    threshold=threshold, blocked_by=blocked))  # fmt: skip

    drop, gain, fall = _finite(1 - close / cost), _finite(close / cost - 1), _finite(1 - close / peak)
    stop, take, trail = sell.stop_loss or 0, sell.take_profit or 0, sell.trailing_stop or 0  # as the engine judges:
    add("stop_loss", sell.stop_loss, drop, sell.stop_loss, bool(at_or_below(close, cost * (1 - stop))), stopped)
    add("take_profit", sell.take_profit, gain, sell.take_profit, bool(at_or_above(close, cost * (1 + take))), stopped)
    add(
        "trailing_stop",
        sell.trailing_stop,
        fall,
        sell.trailing_stop,
        bool(at_or_below(close, peak * (1 - trail))),
        stopped,
    )
    add("max_hold", sell.max_hold_days, float(days), sell.max_hold_days, days >= (sell.max_hold_days or 0), stopped)
    for rule, key in (("sell_formula", "sell"), ("keep_formula", "keep")):
        if key in formulas:
            verdict = int(formulas[key][t, col])
            value = None if verdict < 0 else float(verdict)
            add(rule, True, value, None, verdict == 1, "keep" if key == "sell" and keep else None)
    add("min_hold", sell.min_hold_days, float(days), sell.min_hold_days, days < (sell.min_hold_days or 0))
    return checks


def no_holdings(strategy: Strategy) -> list[Warning]:
    """screen_without_holdings: a strategy selling by its rules alone, screened without simulated holdings."""
    if strategy.sell.mode != "rules":
        return []
    return [Warning(code="screen_without_holdings", text=SIMULATE_TEXTS["screen_without_holdings"]({}), params={})]


def _execution(spec: ExecutionSpec):
    from wequant.service.details import to_execution

    return to_execution(spec)


def _finite(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None
