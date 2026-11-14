"""The holding engine (S4.2): the rebalance engine's decisions, plus sell rules checked at every close on what is held.

A holding's cost is the average back-adjusted fill price of its run of days held (a buy while held averages in, a
sale leaves it); its days count from that run's first fill, that day being 1; its peak is its highest back-adjusted
close since. At each close a stop (stop loss, take profit, trailing stop, holding period) or the sell formula marks
a holding to sell; the keep formula holds back the sell formula, and the stops too when keep_over_stops. A marked
holding is sold whole the next day, tried again day by day until it is sold. At a rebalance the marked holdings are
not chosen, nor bought again; with mode "rules" every other holding stays chosen, taking its slot; with "rebalance"
one does when the keep formula holds or it is held fewer than min_hold_days. A stock sold waits cooldown_days before it
can be bought again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import polars as pl

from wequant.account import Account, Target, trade_target
from wequant.backtest.backtest import Backtest, span
from wequant.backtest.engine import targets
from wequant.backtest.progress import PROGRESS_DAYS, Progress, check_cancel, checkpoint
from wequant.backtest.result import assemble, benchmark_closes
from wequant.expr import Cache, evaluate
from wequant.runs.run import Run
from wequant.strategy import Section, Strategy, choose, decision_rows, pool_expr, select, stack, wrap
from wequant.strategy.section import verdicts

if TYPE_CHECKING:
    from wequant.panel import Panel

ENGINE = "holding"
HOLDING_VERSION = 1  # bump when how the rules decide sales changes meaning
RULES = ("stop_loss", "take_profit", "trailing_stop", "max_hold", "sell_formula")  # in the order a day checks them
EPSILON = 1e-9  # relative: a close exactly at a price rule's level (a -10% limit down on a 10% stop) sets it off,
# whichever way the back-adjusted prices' rounding leans
EXITS = {  # the exits frame: one row per day a holding is marked to sell, its order the next day
    "date": pl.Date,
    "code": pl.String,
    "rule": pl.String,
    "first": pl.Date,  # the day the rule first marked it; earlier than date while its sale could not fill
    "value": pl.Float64,  # stop_loss: the drop from cost; take_profit: the gain; trailing_stop: the drop from the peak
    "cost": pl.Float64,  # yuan, the cost in that day's unadjusted prices
    "close": pl.Float64,  # yuan, that day's unadjusted close
    "peak": pl.Float64,  # yuan, the highest close since bought, in that day's unadjusted prices
    "days": pl.Int64,  # max_hold: trading days held
    "limit": pl.Int64,  # max_hold: the holding period
    "name": pl.String,  # sell_formula: its name
    "period": pl.String,  # sell_formula: its period
}


class Book:
    """Each column's holding as the rules see it: average back-adjusted cost, first row held, highest back-adjusted
    close, the mark to sell and the row its last sale was on (for the cooldown)."""

    def __init__(self, n: int) -> None:
        self.cost, self.peak = np.full(n, np.nan), np.full(n, np.nan)
        self.first, self.sold = np.full(n, -1), np.full(n, -1)
        self.marked: dict[int, tuple] = {}  # column -> the exits row of its first mark

    def days(self, t: int) -> np.ndarray:
        """(N,) trading days held at row t, the first fill's day being 1; 0 where nothing is held."""
        return np.where(self.first >= 0, t - self.first + 1, 0)

    def filled(self, t: int, account: Account, batches: list, factor: np.ndarray) -> None:
        """The day's fills: buys average into the cost, a holding sold out or cleared ends (its cooldown starts)."""
        for batch in batches:  # a column is bought at most once a day, never on a day it is sold
            bought = (batch.qty > 0) & (batch.fills.shares > 0)
            for col, shares, price in zip(batch.cols[bought], batch.fills.shares[bought], batch.fills.price[bought]):
                before = account.shares[col] - shares if self.first[col] >= 0 else 0.0  # what was held at the open
                old = self.cost[col] if before else 0.0
                self.cost[col] = (old * before + price * factor[t, col] * shares) / (before + shares)
                self.first[col] = t if self.first[col] < 0 else self.first[col]
        gone = (self.first >= 0) & ~(account.shares > 0)
        self.sold[gone] = t
        for col in np.flatnonzero(gone).tolist():
            self.marked.pop(col, None)
        self.cost[gone], self.peak[gone], self.first[gone] = np.nan, np.nan, -1

    def closed(self, t: int, close: np.ndarray) -> None:
        """Day t's back-adjusted closes raise each holding's peak."""
        held = self.first >= 0
        self.peak[held] = np.fmax(self.peak[held], close[held])


class Simulation(NamedTuple):
    """A backtest played to its last day: the account and book at its close, and what run() assembles."""

    account: Account
    book: Book
    section: Section
    choices: list
    exits: list[tuple]
    formulas: dict[str, np.ndarray]
    closes: np.ndarray | None
    last: int


def run(
    backtest: Backtest,
    panel: Panel,
    *,
    cache: Cache | None = None,
    progress: Callable[[Progress], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    mark: Callable[[str], None] | None = None,
) -> Run:
    """The backtest on panel with the strategy's sell rules: the rebalance engine's run with an exits frame."""
    mark = mark or (lambda stage: None)
    found = simulate(backtest, panel, cache=cache, progress=progress, cancel=cancel, mark=mark)
    ledger, choice = found.account.ledger(), stack(found.choices)
    run = assemble(backtest, panel, ledger, found.section, choice, found.closes, ENGINE, HOLDING_VERSION, mark)
    return replace(run, exits=pl.DataFrame(found.exits, schema=EXITS, orient="row"))


def simulate(
    backtest: Backtest,
    panel: Panel,
    *,
    cache: Cache | None = None,
    progress: Callable[[Progress], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    mark: Callable[[str], None] | None = None,
) -> Simulation:
    """The backtest played day by day to its last close (the screen's simulated holdings start from it)."""
    strategy, mark = backtest.strategy, mark or (lambda stage: None)
    base, last = span(backtest, panel)
    closes = benchmark_closes(backtest, panel, base, last)
    check_cancel(cancel)
    rows = decision_rows(panel.dates, base, last, strategy.period, strategy.every)
    section = select(
        strategy, panel, rows, cache, on_root=lambda root: (check_cancel(cancel), mark(f"evaluate:{root}"))
    )
    formulas = _formulas(strategy, panel, cache)
    mark("cross_section")
    account = Account(panel, backtest.cash, panel.dates[base], backtest.execution)
    book, adjusted, factor = Book(len(panel.codes)), panel.adjusted("close"), panel.factor
    checkpoint(progress, cancel, Progress(0, last - base, panel.dates[base].item(), account.equity))
    plans, choices, exits = {int(row) + 1: i for i, row in enumerate(rows)}, [], []
    for t in range(base + 1, last + 1):
        account.begin(t)
        selling = np.zeros(len(panel.codes), dtype=bool)
        selling[list(book.marked)] = True
        held = account.shares > 0
        target = None
        if t in plans:
            choice = _choose(strategy, section, plans[t], held & ~selling, selling, book, formulas, t - 1)
            choices.append(choice)
            target = targets(section, plans[t], choice)
            target.weights[selling & held] = 0.0
        elif (selling & held).any():
            empty = np.zeros(0, dtype=np.int64)
            target = Target(np.where(selling & held, 0.0, np.nan), np.zeros(len(panel.codes)), empty)
        batches = trade_target(account, target, strategy.band) if target is not None else []
        equity = account.end()
        book.filled(t, account, batches, factor)
        book.closed(t, adjusted[t])
        exits += _marks(strategy, panel, book, formulas, t)
        if (t - base) % PROGRESS_DAYS == 0 or t == last:
            checkpoint(progress, cancel, Progress(t - base, last - base, panel.dates[t].item(), equity))
    mark("day_loop")
    return Simulation(account, book, section, choices, exits, formulas, closes, last)


def _formulas(strategy: Strategy, panel: Panel, cache: Cache | None) -> dict[str, np.ndarray]:
    """The enabled sell and keep formulas' verdicts on every row (int8: 1 true, 0 false, -1 no value), their
    cross-sectional operators ranking within the pool."""
    pool, found = pool_expr(strategy), {}
    for key, condition in (("sell", strategy.sell.sell_when), ("keep", strategy.sell.keep_when)):
        if condition is not None and condition.enabled:
            found[key] = verdicts(evaluate(wrap(condition.expr, pool), panel, cache))
    return found


def _choose(strategy, section, i: int, held: np.ndarray, selling: np.ndarray, book: Book, formulas: dict, row: int):
    """Decision i's choice under the rules: marked holdings neither kept nor bought, nor stocks cooling down; the
    holdings the mode keeps take their slots first. Without any of these, exactly the rebalance engine's choice."""
    sell = strategy.sell
    blocked = selling | ((book.sold >= 0) & (book.sold + sell.cooldown_days >= row + 1))
    if sell.mode == "rules":
        forced = held.copy()
    else:
        keep = formulas["keep"][row] == 1 if "keep" in formulas else np.zeros_like(held)
        young = (book.days(row) < sell.min_hold_days) if sell.min_hold_days is not None else np.zeros_like(held)
        forced = held & (keep | young)
    if not forced.any() and not blocked.any():
        return choose(strategy, section, i, held)
    return choose(strategy, section, i, held, forced, blocked)


def _marks(strategy: Strategy, panel: Panel, book: Book, formulas: dict, t: int) -> list[tuple]:
    """Mark the holdings a rule sells at day t's close; the exits rows of every holding marked, new or still unsold."""
    sell, close = strategy.sell, panel.adjusted("close")[t]
    free = (book.first >= 0) & ~np.isin(np.arange(len(close)), list(book.marked))
    keep = formulas["keep"][t] == 1 if "keep" in formulas else np.zeros_like(free)
    days, stops = book.days(t), free & ~keep if sell.keep_over_stops else free
    hits = {
        "stop_loss": stops & at_or_below(close, book.cost * (1 - sell.stop_loss)) if sell.stop_loss else None,
        "take_profit": stops & at_or_above(close, book.cost * (1 + sell.take_profit)) if sell.take_profit else None,
        "trailing_stop": stops & at_or_below(close, book.peak * (1 - sell.trailing_stop))
        if sell.trailing_stop
        else None,
        "max_hold": stops & (days >= sell.max_hold_days) if sell.max_hold_days else None,
        "sell_formula": free & ~keep & (formulas["sell"][t] == 1) if "sell" in formulas else None,
    }
    for rule in RULES:
        for col in np.flatnonzero(hits[rule]).tolist() if hits[rule] is not None else []:
            book.marked.setdefault(col, _row(rule, strategy, panel, book, col, t))
    day = panel.dates[t].item()
    return [(day, *row[1:]) for _, row in sorted(book.marked.items())]


def at_or_below(close: np.ndarray | float, level: np.ndarray | float) -> np.ndarray | bool:
    """close at or below level, within EPSILON of it counting as at it; False where either is NaN."""
    return close <= level * (1 + EPSILON)


def at_or_above(close: np.ndarray | float, level: np.ndarray | float) -> np.ndarray | bool:
    """close at or above level, within EPSILON of it counting as at it; False where either is NaN."""
    return close >= level * (1 - EPSILON)


def _row(rule: str, strategy: Strategy, panel: Panel, book: Book, col: int, t: int) -> tuple:
    """The exits row of a holding the rule marks at row t: prices in that day's unadjusted yuan."""
    adjusted, factor, day = panel.adjusted("close")[t, col], panel.factor[t, col], panel.dates[t].item()
    value = {
        "stop_loss": 1 - adjusted / book.cost[col],
        "take_profit": adjusted / book.cost[col] - 1,
        "trailing_stop": 1 - adjusted / book.peak[col],
    }.get(rule)
    days, limit = (int(book.days(t)[col]), strategy.sell.max_hold_days) if rule == "max_hold" else (None, None)
    formula = strategy.sell.sell_when if rule == "sell_formula" else None
    name, period = (formula.name, _period(formula.expr)) if formula is not None else (None, None)
    prices = (float(book.cost[col] / factor), float(panel.raw("close")[t, col]), float(book.peak[col] / factor))
    return (day, str(panel.codes[col]), rule, day, None if value is None else float(value), *prices, days, limit,
            name, period)  # fmt: skip


def _period(expr) -> str:
    """The period a sell formula's bars are: its text's, else a tree's period at its root, else day."""
    if expr.source is not None:
        return expr.source.period
    return {"WEEK": "week", "MONTH": "month"}.get(getattr(expr, "name", None), "day")
